import os
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

class MongoService:
    def __init__(self):
        self.client = MongoClient(os.getenv("MONGO_URI"))
        self.db = self.client[os.getenv("MONGO_DB", "logistica_db")]
        self.lotes = self.db["lotes"]

    def listar_lotes(self, status=None):
        filtro = {"status": status} if status else {}
        return list(self.lotes.find(filtro, {"_id": 0}))

    def cadastrar_lote(self, dados_lote: dict):
        dados_lote["status"] = "EM_TRANSITO"
        dados_lote["historico"] = [
            {
                "evento": "LOTE_CRIADO_E_DESPACHADO",
                "timestamp": datetime.now().isoformat(),
                "detalhes": f"Atribuído ao veículo {dados_lote['veiculo_id']}"
            }
        ]
        self.lotes.insert_one(dados_lote)

    def registrar_entrega(self, codigo_lote: str, recebedor: str, observacao: str):
        novo_evento = {
            "evento": "ENTREGA_CONCLUIDA",
            "timestamp": datetime.now().isoformat(),
            "detalhes": f"Recebido por {recebedor}. Obs: {observacao}"
        }
        self.lotes.update_one(
            {"codigo_lote": codigo_lote},
            {
                "$set": {"status": "ENTREGUE"},
                "$push": {"historico": novo_evento}
            }
        )