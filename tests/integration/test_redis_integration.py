import os
import time
from uuid import uuid4

import pytest

from services.redis_service import RedisService


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REDIS_INTEGRATION") != "1",
    reason="Integração Redis desativada. Use RUN_REDIS_INTEGRATION=1.",
)


@pytest.fixture
def redis_service(monkeypatch):
    prefix = f"teste_integracao:{uuid4().hex}:"

    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("REDIS_DB", "15")
    monkeypatch.setenv("REDIS_KEY_PREFIX", prefix)

    service = RedisService()
    service.client.ping()

    yield service

    try:
        if service.consumidor_alertas_ativo:
            service.parar_consumidor_alertas()

        # Remove somente as chaves criadas por esta execução.
        chaves = list(
            service.client.scan_iter(match=f"{prefix}*")
        )
        if chaves:
            service.client.delete(*chaves)

    finally:
        service.close()


def test_redis_real_persists_and_lists_vehicle(redis_service):
    service = redis_service

    resultado = service.atualizar_telemetria(
        "INT-CAM-01",
        -7.1195,
        -34.8450,
        65,
        5.2,
    )

    assert resultado["veiculo_id"] == "INT-CAM-01"

    chave = service._vehicle_key("INT-CAM-01")
    dados = service.client.hgetall(chave)

    assert dados["veiculo_id"] == "INT-CAM-01"
    assert dados["velocidade"] == "65"
    assert dados["temperatura"] == "5.2"

    frota = service.listar_frota()

    assert any(
        veiculo["veiculo_id"] == "INT-CAM-01"
        for veiculo in frota
    )


def test_redis_real_executes_alert_deduplication_and_history(redis_service):
    service = redis_service

    primeira_publicacao = service.publicar_alerta(
        "INT-CAM-02",
        "Temperatura acima do limite de integração",
        tipo="TESTE_INTEGRACAO",
        chave_ocorrencia="ocorrencia-unica-001",
    )

    segunda_publicacao = service.publicar_alerta(
        "INT-CAM-02",
        "Temperatura acima do limite de integração",
        tipo="TESTE_INTEGRACAO",
        chave_ocorrencia="ocorrencia-unica-001",
    )

    assert primeira_publicacao is True
    assert segunda_publicacao is False

    historico = service.obter_alertas_recentes()

    assert len(historico) == 1
    assert historico[0]["veiculo_id"] == "INT-CAM-02"
    assert historico[0]["tipo"] == "TESTE_INTEGRACAO"


def test_redis_real_pubsub_receives_published_alert(redis_service):
    service = redis_service

    assert service.iniciar_consumidor_alertas() is True

    # Aguarda o consumidor iniciar.
    limite = time.monotonic() + 5

    while time.monotonic() < limite:
        if service.consumidor_alertas_pronto:
            break
        time.sleep(0.02)

    assert service.consumidor_alertas_pronto is True

    # Aguarda o Redis confirmar a inscrição no canal.
    limite = time.monotonic() + 5
    inscrito = False

    while time.monotonic() < limite:
        assinantes = dict(
            service.client.pubsub_numsub(service.alert_channel)
        )

        if assinantes.get(service.alert_channel, 0) >= 1:
            inscrito = True
            break

        time.sleep(0.02)

    assert inscrito is True

    publicado = service.publicar_alerta(
        "INT-CAM-03",
        "Alerta recebido pelo consumidor real",
        tipo="TESTE_PUBSUB",
        chave_ocorrencia="pubsub-unico-001",
    )

    assert publicado is True

    # Aguarda o alerta chegar à fila do consumidor.
    limite = time.monotonic() + 5
    alertas = []

    while time.monotonic() < limite:
        alertas = service.obter_alertas_consumidos()

        if alertas:
            break

        time.sleep(0.02)

    assert len(alertas) == 1
    assert alertas[0]["veiculo_id"] == "INT-CAM-03"
    assert alertas[0]["tipo"] == "TESTE_PUBSUB"
    assert alertas[0]["alerta"] == "Alerta recebido pelo consumidor real"
