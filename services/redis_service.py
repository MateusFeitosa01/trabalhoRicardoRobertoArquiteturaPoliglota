import os
import json
import redis
from dotenv import load_dotenv

load_dotenv()

class RedisService:
    def __init__(self):
        self.client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            decode_responses=True
        )

    def atualizar_telemetria(self, veiculo_id: str, lat: float, lng: float, velocidade: int, temperatura: float, status: str = "EM_ROTA"):
        chave = f"veiculo:{veiculo_id}"
        self.client.hset(chave, mapping={
            "veiculo_id": veiculo_id,
            "lat": str(lat),
            "lng": str(lng),
            "velocidade": str(velocidade),
            "temperatura": str(temperatura),
            "status": status
        })

    def listar_frota(self):
        chaves = self.client.keys("veiculo:*")
        return [self.client.hgetall(chave) for chave in chaves]

    def publicar_alerta(self, veiculo_id: str, mensagem: str):
        payload = json.dumps({"veiculo_id": veiculo_id, "alerta": mensagem})
        self.client.publish("alertas_logistica", payload)
        # Armazena também numa lista recente para exibição imediata no dashboard
        self.client.lpush("historico_alertas", payload)
        self.client.ltrim("historico_alertas", 0, 9)

    def obter_alertas_recentes(self):
        itens = self.client.lrange("historico_alertas", 0, 9)
        return [json.loads(i) for i in itens]

    def liberar_veiculo(self, veiculo_id: str):
        self.client.hset(f"veiculo:{veiculo_id}", mapping={
            "status": "DISPONIVEL",
            "velocidade": "0"
        })