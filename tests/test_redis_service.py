import json
import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import RedisError

from services.redis_service import RedisService


def build_service():
    client = MagicMock()
    with patch("services.redis_service.redis.Redis", return_value=client):
        service = RedisService()
    return service, client


def telemetry_hash(**overrides):
    data = {
        "veiculo_id": "CAM-01",
        "lat": "-7.56",
        "lng": "-35.00",
        "velocidade": "76",
        "temperatura": "5.4",
        "status": "EM_ROTA",
    }
    data.update(overrides)
    return data


def test_service_does_not_start_background_workers_on_construction():
    service, _ = build_service()

    assert service._simulation_thread is None
    assert service._consumer_thread is None


def test_atualizar_telemetria_persists_a_complete_vehicle_hash():
    service, client = build_service()

    result = service.atualizar_telemetria("CAM-01", -7.56, -35.0, 76, 5.4)

    expected = {
        "veiculo_id": "CAM-01",
        "lat": "-7.56",
        "lng": "-35.0",
        "velocidade": "76",
        "temperatura": "5.4",
        "status": "EM_ROTA",
    }
    assert result == expected
    client.hset.assert_called_once_with("veiculo:CAM-01", mapping=expected)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("veiculo_id", " "),
        ("lat", 91),
        ("lng", -181),
        ("velocidade", -1),
        ("velocidade", True),
        ("temperatura", float("nan")),
        ("status", ""),
    ],
)
def test_atualizar_telemetria_rejects_invalid_values_before_writing(field, value):
    service, client = build_service()
    values = {
        "veiculo_id": "CAM-01",
        "lat": -7.56,
        "lng": -35.0,
        "velocidade": 76,
        "temperatura": 5.4,
        "status": "EM_ROTA",
    }
    values[field] = value

    with pytest.raises(ValueError):
        service.atualizar_telemetria(**values)

    client.hset.assert_not_called()


def test_listar_frota_skips_and_logs_incomplete_legacy_hashes(caplog):
    service, client = build_service()
    client.scan_iter.return_value = ["veiculo:CAM-01", "veiculo:CAM-02"]
    client.hgetall.side_effect = [
        telemetry_hash(),
        {"veiculo_id": "CAM-02", "status": "DISPONIVEL", "velocidade": "0"},
    ]

    frota = service.listar_frota()

    assert len(frota) == 1
    assert frota[0]["veiculo_id"] == "CAM-01"
    assert "Hash do veículo incompleto" in caplog.text


def test_simular_atualizacao_updates_existing_hash_with_complete_data():
    service, client = build_service()
    client.hgetall.return_value = telemetry_hash()

    updated = service.simular_atualizacao("CAM-01")

    assert set(updated) == RedisService.VEHICLE_FIELDS
    client.hset.assert_called_once()
    saved = client.hset.call_args.kwargs["mapping"]
    assert saved["veiculo_id"] == "CAM-01"
    assert saved["status"] == "EM_ROTA"
    assert -90 <= float(saved["lat"]) <= 90
    assert -180 <= float(saved["lng"]) <= 180
    assert 50 <= int(saved["velocidade"]) <= 95


def test_simular_atualizacao_rejects_missing_or_available_vehicle():
    service, client = build_service()
    client.hgetall.return_value = {}

    with pytest.raises(LookupError):
        service.simular_atualizacao("CAM-404")

    client.hgetall.return_value = telemetry_hash(status="DISPONIVEL")
    with pytest.raises(ValueError, match="disponível"):
        service.simular_atualizacao("CAM-01")

    client.hset.assert_not_called()


def test_background_simulation_can_be_started_and_stopped_explicitly():
    service, _ = build_service()
    updated = threading.Event()
    service.listar_frota = MagicMock(return_value=[telemetry_hash()])
    service.simular_atualizacao = MagicMock(side_effect=lambda _: updated.set() or telemetry_hash())

    assert service.iniciar_simulacao(intervalo_segundos=0.01)
    assert updated.wait(1)
    assert service.parar_simulacao()
    assert not service.simulacao_ativa
    service.simular_atualizacao.assert_called_once_with("CAM-01")


def test_concurrent_simulation_starts_create_only_one_worker():
    service, _ = build_service()
    barrier = threading.Barrier(3)
    results = []

    def start_simulation():
        barrier.wait()
        results.append(service.iniciar_simulacao(intervalo_segundos=60))

    starters = [threading.Thread(target=start_simulation) for _ in range(2)]
    for starter in starters:
        starter.start()
    barrier.wait()
    for starter in starters:
        starter.join()

    assert sorted(results) == [False, True]
    assert service.parar_simulacao()


def test_concurrent_simulation_stops_are_serialized():
    service, _ = build_service()
    assert service.iniciar_simulacao(intervalo_segundos=60)
    barrier = threading.Barrier(3)
    results = []

    def stop_simulation():
        barrier.wait()
        results.append(service.parar_simulacao())

    stoppers = [threading.Thread(target=stop_simulation) for _ in range(2)]
    for stopper in stoppers:
        stopper.start()
    barrier.wait()
    for stopper in stoppers:
        stopper.join()

    assert sorted(results) == [False, True]
    assert not service.simulacao_ativa


def test_simulation_interval_must_be_positive():
    service, _ = build_service()

    with pytest.raises(ValueError):
        service.iniciar_simulacao(intervalo_segundos=0)


def test_simulation_worker_failure_is_logged_and_exposed_to_the_caller(caplog):
    service, _ = build_service()
    service.listar_frota = MagicMock(side_effect=RuntimeError("provider failed"))

    assert service.iniciar_simulacao(intervalo_segundos=0.01)
    limite = time.monotonic() + 1
    while time.monotonic() < limite and not service.simulacao_com_erro:
        time.sleep(0.005)

    assert service.simulacao_erro == "provider failed"
    assert "interrompida por erro" in caplog.text
    with pytest.raises(RuntimeError, match="provider failed"):
        service.parar_simulacao()
    assert not service.simulacao_com_erro


def test_liberar_veiculo_does_not_create_hash_when_vehicle_is_missing():
    service, client = build_service()
    client.hgetall.return_value = {}

    assert service.liberar_veiculo("CAM-404") is False

    client.hset.assert_not_called()


def test_liberar_veiculo_rejects_incomplete_hash_without_mutating_it():
    service, client = build_service()
    client.hgetall.return_value = {"veiculo_id": "CAM-01", "status": "EM_ROTA"}

    with pytest.raises(ValueError, match="incompleto"):
        service.liberar_veiculo("CAM-01")

    client.hset.assert_not_called()


def test_liberar_veiculo_updates_only_complete_existing_hash():
    service, client = build_service()
    client.hgetall.return_value = telemetry_hash()

    assert service.liberar_veiculo("CAM-01")

    client.hset.assert_called_once_with(
        "veiculo:CAM-01",
        mapping={"status": "DISPONIVEL", "velocidade": "0"},
    )


def test_publicar_alerta_persists_history_and_suppresses_duplicate_occurrence():
    service, client = build_service()
    client.eval.side_effect = [1, 0]

    assert service.publicar_alerta(
        "CAM-01",
        "Temperatura alta",
        tipo="TEMPERATURA",
        chave_ocorrencia="temperatura:LOTE-101",
    )
    assert not service.publicar_alerta(
        "CAM-01",
        "Temperatura subiu novamente",
        tipo="TEMPERATURA",
        chave_ocorrencia="temperatura:LOTE-101",
    )

    assert client.eval.call_count == 2
    assert client.eval.call_args_list[0].args[:5] == (
        RedisService._PUBLISH_ALERT_SCRIPT,
        3,
        f"{RedisService.ALERT_DEDUP_PREFIX}"
        + json.loads(client.eval.call_args_list[0].args[5])["alerta_id"],
        RedisService.ALERT_HISTORY_KEY,
        RedisService.ALERT_CHANNEL,
    )
    payload = json.loads(client.eval.call_args_list[0].args[5])
    assert payload["veiculo_id"] == "CAM-01"
    assert payload["tipo"] == "TEMPERATURA"
    client.set.assert_not_called()
    client.lpush.assert_not_called()
    client.ltrim.assert_not_called()
    client.publish.assert_not_called()


def test_alert_persistence_failure_does_not_leave_a_deduplication_key():
    service, client = build_service()
    client.eval.side_effect = RedisError("simulated persistence failure")

    with pytest.raises(RedisError, match="simulated persistence failure"):
        service.publicar_alerta("CAM-01", "Temperatura alta", tipo="TEMPERATURA")

    client.eval.assert_called_once()
    client.set.assert_not_called()
    client.lpush.assert_not_called()
    client.ltrim.assert_not_called()
    client.publish.assert_not_called()


def test_obter_alertas_recentes_decodes_redis_history():
    service, client = build_service()
    client.lrange.return_value = ['{"veiculo_id":"CAM-01","alerta":"Desvio"}']

    assert service.obter_alertas_recentes() == [
        {"veiculo_id": "CAM-01", "alerta": "Desvio"}
    ]


def test_verificar_alertas_temperatura_uses_lot_specific_deduplication():
    service, _ = build_service()
    service.publicar_alerta = MagicMock(return_value=True)

    published = service.verificar_alertas_temperatura(
        "CAM-01",
        9,
        [{"codigo_lote": "LOTE-101", "temp_max_c": 8, "veiculo_id": "CAM-01"}],
    )

    assert published == [True]
    service.publicar_alerta.assert_called_once()
    assert service.publicar_alerta.call_args.kwargs["chave_ocorrencia"] == (
        "temperatura:LOTE-101"
    )


def test_fast_speed_drop_publishes_possible_accident_alert():
    service, client = build_service()
    client.hgetall.return_value = telemetry_hash(velocidade="90")
    client.eval.return_value = 1

    service.atualizar_telemetria("CAM-01", -7.56, -35.0, 40, 5.4)

    payload = json.loads(client.eval.call_args.args[5])
    assert payload["tipo"] == "POSSIVEL_ACIDENTE"


def test_speeding_publishes_alert():
    service, client = build_service()
    client.hgetall.return_value = {}
    client.eval.return_value = 1

    service.atualizar_telemetria("CAM-01", -7.56, -35.0, 111, 5.4)

    payload = json.loads(client.eval.call_args.args[5])
    assert payload["tipo"] == "VELOCIDADE"


def test_alert_failure_does_not_undo_already_saved_telemetry():
    service, client = build_service()
    client.hgetall.return_value = {}
    client.eval.side_effect = RedisError("simulated publish failure")

    with pytest.raises(RedisError, match="simulated publish failure"):
        service.atualizar_telemetria("CAM-01", -7.56, -35.0, 111, 5.4)

    client.hset.assert_called_once()
    assert client.hset.call_args.kwargs["mapping"]["velocidade"] == "111"


def test_route_deviation_alert_requires_route_coordinates_and_is_deduplicated():
    service, client = build_service()
    client.hgetall.return_value = telemetry_hash(lat="1", lng="1")
    client.eval.return_value = 1

    service.verificar_desvio_rota("CAM-01", [])
    assert not client.eval.called

    assert service.verificar_desvio_rota("CAM-01", [(0, 0), (0, 0.01)])

    payload = json.loads(client.eval.call_args.args[5])
    assert payload["tipo"] == "DESVIO_ROTA"
    assert client.eval.call_count == 1


class FakePubSub:
    def __init__(self):
        self.messages = queue.Queue()
        self.closed = False
        self.subscribed = threading.Event()

    def subscribe(self, channel):
        self.channel = channel
        self.subscribed.set()

    def get_message(self, timeout):
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self.closed = True


def test_pubsub_consumer_receives_messages_without_blocking_start_and_stops_cleanly():
    service, client = build_service()
    pubsub = FakePubSub()
    client.pubsub.return_value = pubsub

    assert service.iniciar_consumidor_alertas()
    assert pubsub.subscribed.wait(1)
    limite_pronto = time.monotonic() + 1
    while time.monotonic() < limite_pronto and not service._consumer_ready.is_set():
        time.sleep(0.005)
    assert service.consumidor_alertas_pronto
    pubsub.messages.put(
        {
            "type": "message",
            "data": json.dumps({"veiculo_id": "CAM-01", "alerta": "Aviso"}),
        }
    )

    alertas = []
    limite = time.monotonic() + 1
    while time.monotonic() < limite and not alertas:
        alertas = service.obter_alertas_consumidos()
        time.sleep(0.005)

    assert alertas == [{"veiculo_id": "CAM-01", "alerta": "Aviso"}]
    assert service.parar_consumidor_alertas()
    assert pubsub.closed


def test_pubsub_worker_failure_is_logged_and_exposed_to_the_caller(caplog):
    service, client = build_service()
    pubsub = FakePubSub()
    pubsub.subscribe = MagicMock(side_effect=RuntimeError("subscribe failed"))
    client.pubsub.return_value = pubsub

    assert service.iniciar_consumidor_alertas()
    limite = time.monotonic() + 1
    while time.monotonic() < limite and not service.consumidor_alertas_com_erro:
        time.sleep(0.005)

    assert service.consumidor_alertas_erro == "subscribe failed"
    assert "interrompido por erro" in caplog.text
    with pytest.raises(RuntimeError, match="subscribe failed"):
        service.parar_consumidor_alertas()
    assert pubsub.closed


def test_close_stops_both_workers_without_starting_them():
    service, client = build_service()
    pubsub = FakePubSub()
    client.pubsub.return_value = pubsub
    assert service.iniciar_simulacao(intervalo_segundos=60)
    assert service.iniciar_consumidor_alertas()
    assert pubsub.subscribed.wait(1)

    assert service.close()

    assert service._simulation_thread is None
    assert service._consumer_thread is None
    assert pubsub.closed
    client.close.assert_called_once()
    assert service.close() is False
    with pytest.raises(RuntimeError, match="encerrado"):
        service.iniciar_simulacao(intervalo_segundos=1)
