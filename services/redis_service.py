import hashlib
import json
import logging
import math
import os
import queue
import random
import threading

import redis
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class RedisService:
    ALERT_CHANNEL = "alertas_logistica"
    ALERT_HISTORY_KEY = "historico_alertas"
    ALERT_DEDUP_PREFIX = "alerta_dedup:"
    ALERT_DEDUP_TTL_SECONDS = 300
    VEHICLE_FIELDS = {
        "veiculo_id",
        "lat",
        "lng",
        "velocidade",
        "temperatura",
        "status",
    }
    MAX_VELOCIDADE_KMH = 110
    QUEDA_BRUSCA_KMH = 40
    _PUBLISH_ALERT_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
local history_type = redis.call('TYPE', KEYS[2]).ok
if history_type ~= 'none' and history_type ~= 'list' then
    return redis.error_reply('alert history key must be a list')
end
redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[2])
local pushed = redis.pcall('LPUSH', KEYS[2], ARGV[1])
if type(pushed) == 'table' and pushed.err then
    redis.call('DEL', KEYS[1])
    return redis.error_reply(pushed.err)
end
local trimmed = redis.pcall('LTRIM', KEYS[2], 0, 9)
if type(trimmed) == 'table' and trimmed.err then
    redis.pcall('LREM', KEYS[2], 1, ARGV[1])
    redis.call('DEL', KEYS[1])
    return redis.error_reply(trimmed.err)
end
redis.call('PUBLISH', KEYS[3], ARGV[1])
return 1
"""

    def __init__(self):
        self.key_prefix = os.getenv("REDIS_KEY_PREFIX", "").strip()
        self.alert_channel = f"{self.key_prefix}{self.ALERT_CHANNEL}"
        self.alert_history_key = f"{self.key_prefix}{self.ALERT_HISTORY_KEY}"
        self.alert_dedup_prefix = f"{self.key_prefix}{self.ALERT_DEDUP_PREFIX}"
        self.client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
        )
        self._lifecycle_lock = threading.RLock()
        self._simulation_lock = self._lifecycle_lock
        self._simulation_stop = threading.Event()
        self._simulation_thread = None
        self._simulation_error = None
        self._consumer_lock = self._lifecycle_lock
        self._consumer_stop = threading.Event()
        self._consumer_ready = threading.Event()
        self._consumer_thread = None
        self._consumer_error = None
        self._consumed_alerts = queue.Queue(maxsize=1000)
        self._closing = False
        self._closed = False

    def _vehicle_key(self, veiculo_id):
        return f"{self.key_prefix}veiculo:{veiculo_id}"

    @staticmethod
    def _validar_numero(nome, valor, minimo=None, maximo=None):
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError(f"{nome} deve ser um número.")
        if not math.isfinite(valor):
            raise ValueError(f"{nome} deve ser um número finito.")
        if minimo is not None and valor < minimo:
            raise ValueError(f"{nome} deve ser maior ou igual a {minimo}.")
        if maximo is not None and valor > maximo:
            raise ValueError(f"{nome} deve ser menor ou igual a {maximo}.")

    @classmethod
    def _validar_telemetria(cls, veiculo_id, lat, lng, velocidade, temperatura, status):
        if not isinstance(veiculo_id, str) or not veiculo_id.strip():
            raise ValueError("O identificador do veículo é obrigatório.")
        if not isinstance(status, str) or not status.strip():
            raise ValueError("O status do veículo é obrigatório.")
        cls._validar_numero("Latitude", lat, -90, 90)
        cls._validar_numero("Longitude", lng, -180, 180)
        cls._validar_numero("Velocidade", velocidade, 0)
        cls._validar_numero("Temperatura", temperatura)
        if not isinstance(velocidade, int):
            raise ValueError("Velocidade deve ser um número inteiro.")
        return veiculo_id.strip(), status.strip()

    @classmethod
    def _validar_hash_veiculo(cls, dados):
        faltantes = cls.VEHICLE_FIELDS.difference(dados)
        if faltantes:
            raise ValueError(
                f"Hash do veículo incompleto; faltam os campos: {', '.join(sorted(faltantes))}."
            )
        if any(not str(dados[campo]).strip() for campo in cls.VEHICLE_FIELDS):
            raise ValueError("O hash do veículo contém campos vazios.")
        try:
            lat = float(dados["lat"])
            lng = float(dados["lng"])
            velocidade = int(dados["velocidade"])
            temperatura = float(dados["temperatura"])
        except (TypeError, ValueError) as exc:
            raise ValueError("O hash do veículo contém valores de telemetria inválidos.") from exc
        cls._validar_telemetria(
            dados["veiculo_id"],
            lat,
            lng,
            velocidade,
            temperatura,
            dados["status"],
        )
        return {
            **dados,
            "lat": str(lat),
            "lng": str(lng),
            "velocidade": str(velocidade),
            "temperatura": str(temperatura),
        }

    def atualizar_telemetria(
        self,
        veiculo_id: str,
        lat: float,
        lng: float,
        velocidade: int,
        temperatura: float,
        status: str = "EM_ROTA",
    ):
        veiculo_id, status = self._validar_telemetria(
            veiculo_id,
            lat,
            lng,
            velocidade,
            temperatura,
            status,
        )
        chave = self._vehicle_key(veiculo_id)
        anterior = self.client.hgetall(chave)
        dados = {
            "veiculo_id": veiculo_id,
            "lat": str(lat),
            "lng": str(lng),
            "velocidade": str(velocidade),
            "temperatura": str(temperatura),
            "status": status,
        }
        self.client.hset(chave, mapping=dados)
        self._avaliar_velocidade(veiculo_id, velocidade, anterior)
        return dados

    def listar_frota(self):
        frota = []
        for chave in self.client.scan_iter(match=f"{self.key_prefix}veiculo:*"):
            dados = self.client.hgetall(chave)
            try:
                frota.append(self._validar_hash_veiculo(dados))
            except ValueError as exc:
                logger.warning("Ignorando %s: %s", chave, exc)
        return frota

    def simular_atualizacao(self, veiculo_id: str):
        dados = self.client.hgetall(self._vehicle_key(veiculo_id))
        if not dados:
            raise LookupError(f"O veículo '{veiculo_id}' não existe no Redis.")
        dados = self._validar_hash_veiculo(dados)
        if dados["status"] == "DISPONIVEL":
            raise ValueError("Não é possível simular sensores de um veículo disponível.")

        latitude = min(90, max(-90, float(dados["lat"]) + random.uniform(-0.02, 0.02)))
        longitude = min(180, max(-180, float(dados["lng"]) + random.uniform(-0.02, 0.02)))
        return self.atualizar_telemetria(
            veiculo_id,
            round(latitude, 4),
            round(longitude, 4),
            random.randint(50, 95),
            round(random.uniform(4.0, 14.0), 1),
            dados["status"],
        )

    def iniciar_simulacao(self, intervalo_segundos=5.0, lotes_provider=None):
        if (
            isinstance(intervalo_segundos, bool)
            or not isinstance(intervalo_segundos, (int, float))
            or not math.isfinite(intervalo_segundos)
            or intervalo_segundos <= 0
        ):
            raise ValueError("O intervalo da simulação deve ser positivo e finito.")
        with self._simulation_lock:
            self._validar_servico_aberto()
            if self._simulation_thread and self._simulation_thread.is_alive():
                return False
            self._propagar_erro_worker(self._simulation_error, "simulação")
            self._simulation_error = None
            self._simulation_stop.clear()
            self._simulation_thread = threading.Thread(
                target=self._executar_simulacao,
                args=(float(intervalo_segundos), lotes_provider),
                name="redis-telemetria-simulacao",
                daemon=True,
            )
            self._simulation_thread.start()
        return True

    def _executar_simulacao(self, intervalo_segundos, lotes_provider):
        try:
            while not self._simulation_stop.wait(intervalo_segundos):
                lotes = lotes_provider() if lotes_provider else []
                for veiculo in self.listar_frota():
                    if self._simulation_stop.is_set():
                        return
                    if veiculo["status"] == "DISPONIVEL":
                        continue
                    atualizado = self.simular_atualizacao(veiculo["veiculo_id"])
                    self.verificar_alertas_temperatura(
                        veiculo["veiculo_id"],
                        float(atualizado["temperatura"]),
                        lotes,
                    )
        except Exception as exc:
            logger.exception("A simulação de telemetria foi interrompida por erro.")
            self._simulation_error = exc
            self._simulation_stop.set()

    @property
    def simulacao_ativa(self):
        return bool(self._simulation_thread and self._simulation_thread.is_alive())

    @property
    def simulacao_com_erro(self):
        return self._simulation_error is not None

    @property
    def simulacao_erro(self):
        return str(self._simulation_error) if self._simulation_error is not None else None

    def parar_simulacao(self, timeout=2.0):
        with self._simulation_lock:
            thread = self._simulation_thread
            if thread is None:
                self._propagar_erro_worker(self._simulation_error, "simulação")
                return False
            self._simulation_stop.set()
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("A simulação não encerrou dentro do tempo limite.")
            if self._simulation_thread is thread:
                self._simulation_thread = None
            erro = self._simulation_error
            self._simulation_error = None
        self._propagar_erro_worker(erro, "simulação")
        return True

    def verificar_alertas_temperatura(self, veiculo_id, temperatura, lotes):
        self._validar_numero("Temperatura", temperatura)
        alertas_publicados = []
        for lote in lotes:
            if lote.get("veiculo_id") != veiculo_id:
                continue
            limite = lote.get("temp_max_c")
            self._validar_numero("Temperatura máxima do lote", limite)
            if temperatura > limite:
                codigo_lote = lote.get("codigo_lote")
                if not isinstance(codigo_lote, str) or not codigo_lote.strip():
                    raise ValueError("O lote precisa ter codigo_lote para gerar o alerta.")
                mensagem = (
                    f"ALERTA CRÍTICO: Temperatura {temperatura}°C excedeu limite de "
                    f"{limite}°C do {codigo_lote}!"
                )
                alertas_publicados.append(
                    self.publicar_alerta(
                        veiculo_id,
                        mensagem,
                        tipo="TEMPERATURA",
                        chave_ocorrencia=f"temperatura:{codigo_lote}",
                    )
                )
        return alertas_publicados

    def _avaliar_velocidade(self, veiculo_id, velocidade, anterior):
        if velocidade > self.MAX_VELOCIDADE_KMH:
            self.publicar_alerta(
                veiculo_id,
                f"Velocidade acima do limite: {velocidade} km/h.",
                tipo="VELOCIDADE",
                chave_ocorrencia="velocidade_acima_limite",
            )
        if anterior.get("status") not in {"EM_ROTA", "EM_TRANSITO"}:
            return
        try:
            velocidade_anterior = int(anterior["velocidade"])
        except (KeyError, TypeError, ValueError):
            return
        if (
            velocidade_anterior >= 50
            and velocidade_anterior - velocidade >= self.QUEDA_BRUSCA_KMH
        ):
            self.publicar_alerta(
                veiculo_id,
                f"Possível acidente: queda brusca de velocidade de "
                f"{velocidade_anterior} para {velocidade} km/h.",
                tipo="POSSIVEL_ACIDENTE",
                chave_ocorrencia="queda_brusca_velocidade",
            )

    def verificar_desvio_rota(self, veiculo_id, rota_planejada, tolerancia_m=1000):
        self._validar_numero("Tolerância de desvio", tolerancia_m, 0)
        if not isinstance(rota_planejada, (list, tuple)):
            raise ValueError("A rota planejada deve ser uma sequência de coordenadas.")
        if len(rota_planejada) < 2:
            return False
        dados = self.client.hgetall(self._vehicle_key(veiculo_id))
        if not dados:
            raise LookupError(f"O veículo '{veiculo_id}' não existe no Redis.")
        dados = self._validar_hash_veiculo(dados)
        latitude = float(dados["lat"])
        longitude = float(dados["lng"])
        pontos = []
        for ponto in rota_planejada:
            if not isinstance(ponto, (list, tuple)) or len(ponto) != 2:
                raise ValueError("Cada ponto da rota deve conter latitude e longitude.")
            lat, lng = ponto
            self._validar_numero("Latitude da rota", lat, -90, 90)
            self._validar_numero("Longitude da rota", lng, -180, 180)
            pontos.append((float(lat), float(lng)))

        raio_terra_m = 6_371_000
        latitude_rad = math.radians(latitude)
        px = raio_terra_m * math.radians(longitude) * math.cos(latitude_rad)
        py = raio_terra_m * math.radians(latitude)
        coordenadas = [
            (
                raio_terra_m * math.radians(lng) * math.cos(latitude_rad),
                raio_terra_m * math.radians(lat),
            )
            for lat, lng in pontos
        ]
        menor_distancia = float("inf")
        for (x1, y1), (x2, y2) in zip(coordenadas, coordenadas[1:]):
            dx = x2 - x1
            dy = y2 - y1
            comprimento = dx * dx + dy * dy
            if comprimento == 0:
                proporcao = 0
            else:
                proporcao = max(
                    0,
                    min(1, ((px - x1) * dx + (py - y1) * dy) / comprimento),
                )
            distancia = math.hypot(px - (x1 + proporcao * dx), py - (y1 + proporcao * dy))
            menor_distancia = min(menor_distancia, distancia)

        if menor_distancia <= tolerancia_m:
            return False
        assinatura_rota = hashlib.sha256(
            json.dumps(pontos, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self.publicar_alerta(
            veiculo_id,
            f"Desvio de rota detectado: {menor_distancia:.0f} m da rota planejada.",
            tipo="DESVIO_ROTA",
            chave_ocorrencia=f"desvio:{assinatura_rota}",
        )

    def publicar_alerta(self, veiculo_id: str, mensagem: str, tipo="GERAL", chave_ocorrencia=None):
        if not isinstance(veiculo_id, str) or not veiculo_id.strip():
            raise ValueError("O identificador do veículo é obrigatório para o alerta.")
        if not isinstance(mensagem, str) or not mensagem.strip():
            raise ValueError("A mensagem do alerta é obrigatória.")
        if not isinstance(tipo, str) or not tipo.strip():
            raise ValueError("O tipo do alerta é obrigatório.")
        veiculo_id = veiculo_id.strip()
        tipo = tipo.strip()
        identidade = chave_ocorrencia or mensagem.strip()
        digest = hashlib.sha256(
            f"{veiculo_id}:{tipo}:{identidade}".encode("utf-8")
        ).hexdigest()
        payload = json.dumps(
            {
                "alerta_id": digest,
                "veiculo_id": veiculo_id,
                "tipo": tipo,
                "alerta": mensagem.strip(),
            },
            ensure_ascii=False,
        )
        return bool(
            self.client.eval(
                self._PUBLISH_ALERT_SCRIPT,
                3,
                f"{self.alert_dedup_prefix}{digest}",
                self.alert_history_key,
                self.alert_channel,
                payload,
                self.ALERT_DEDUP_TTL_SECONDS,
            )
        )

    def iniciar_consumidor_alertas(self):
        with self._consumer_lock:
            self._validar_servico_aberto()
            if self._consumer_thread and self._consumer_thread.is_alive():
                return False
            self._propagar_erro_worker(self._consumer_error, "consumidor Pub/Sub")
            self._consumer_error = None
            self._consumer_stop.clear()
            self._consumer_ready.clear()
            self._consumer_thread = threading.Thread(
                target=self._consumir_alertas,
                name="redis-alertas-pubsub",
                daemon=True,
            )
            self._consumer_thread.start()
        return True

    def _consumir_alertas(self):
        pubsub = None
        try:
            pubsub = self.client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(self.alert_channel)
            self._consumer_ready.set()
            while not self._consumer_stop.is_set():
                mensagem = pubsub.get_message(timeout=0.1)
                if mensagem and mensagem.get("type") == "message":
                    payload = mensagem["data"]
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    alerta = json.loads(payload)
                    if not isinstance(alerta, dict):
                        raise ValueError("A mensagem Pub/Sub deve conter um objeto JSON.")
                    try:
                        self._consumed_alerts.put_nowait(alerta)
                    except queue.Full:
                        logger.warning("Fila de alertas Pub/Sub cheia; alerta não enfileirado.")
        except Exception as exc:
            logger.exception("O consumidor Redis Pub/Sub foi interrompido por erro.")
            self._consumer_error = exc
            self._consumer_ready.set()
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception as exc:
                    self._consumer_error = self._consumer_error or exc
                    logger.exception("Não foi possível fechar a conexão Pub/Sub.")

    @property
    def consumidor_alertas_ativo(self):
        return bool(self._consumer_thread and self._consumer_thread.is_alive())

    @property
    def consumidor_alertas_com_erro(self):
        return self._consumer_error is not None

    @property
    def consumidor_alertas_erro(self):
        return str(self._consumer_error) if self._consumer_error is not None else None

    @property
    def consumidor_alertas_pronto(self):
        self._propagar_erro_worker(self._consumer_error, "consumidor Pub/Sub")
        return self._consumer_ready.is_set() and self.consumidor_alertas_ativo

    def parar_consumidor_alertas(self, timeout=2.0):
        with self._consumer_lock:
            thread = self._consumer_thread
            if thread is None:
                self._propagar_erro_worker(self._consumer_error, "consumidor Pub/Sub")
                return False
            self._consumer_stop.set()
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("O consumidor Pub/Sub não encerrou dentro do tempo limite.")
            if self._consumer_thread is thread:
                self._consumer_thread = None
            erro = self._consumer_error
            self._consumer_error = None
        self._propagar_erro_worker(erro, "consumidor Pub/Sub")
        return True

    def obter_alertas_consumidos(self, limite=None):
        if limite is not None and (
            isinstance(limite, bool)
            or not isinstance(limite, int)
            or limite < 1
        ):
            raise ValueError("O limite deve ser um inteiro positivo.")
        alertas = []
        while limite is None or len(alertas) < limite:
            try:
                alertas.append(self._consumed_alerts.get_nowait())
            except queue.Empty:
                break
        return alertas

    def obter_alertas_recentes(self):
        itens = self.client.lrange(self.alert_history_key, 0, 9)
        return [json.loads(item) for item in itens]

    def liberar_veiculo(self, veiculo_id: str):
        if not isinstance(veiculo_id, str) or not veiculo_id.strip():
            raise ValueError("O identificador do veículo é obrigatório.")
        veiculo_id = veiculo_id.strip()
        chave = self._vehicle_key(veiculo_id)
        dados = self.client.hgetall(chave)
        if not dados:
            return False
        self._validar_hash_veiculo(dados)
        self.client.hset(
            chave,
            mapping={
                "status": "DISPONIVEL",
                "velocidade": "0",
            },
        )
        return True

    def close(self):
        with self._lifecycle_lock:
            if self._closed:
                return False
            self._closing = True
            erros = []
            for parar in (self.parar_simulacao, self.parar_consumidor_alertas):
                try:
                    parar()
                except (RuntimeError, TimeoutError) as exc:
                    erros.append(exc)
            if any(
                thread is not None and thread.is_alive()
                for thread in (self._simulation_thread, self._consumer_thread)
            ):
                raise TimeoutError(
                    "O serviço Redis não pode ser fechado enquanto houver workers ativos."
                ) from (erros[0] if erros else None)
            self.client.close()
            self._closed = True
            if erros:
                raise erros[0]
            return True

    def _validar_servico_aberto(self):
        if self._closing or self._closed:
            raise RuntimeError("O serviço Redis está encerrando ou já foi encerrado.")

    @staticmethod
    def _propagar_erro_worker(erro, nome):
        if erro is not None:
            raise RuntimeError(f"O {nome} foi interrompido por erro: {erro}") from erro
