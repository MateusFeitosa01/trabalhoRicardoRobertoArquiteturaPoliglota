import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import datetime
from services.mongo_service import MongoService
from services.redis_service import RedisService
from services.neo4j_service import Neo4jService

def run_seed():
    print("1. Populando MongoDB...")
    mongo = MongoService()
    mongo.lotes.delete_many({})
    mongo.lotes.create_index("codigo_lote", unique=True)
    mongo.lotes.insert_many([
        {
            "codigo_lote": "LOTE-101",
            "produto": "Insumos Hospitalares Refrigerados",
            "peso_kg": 1200,
            "temp_max_c": 8.0,
            "nota_fiscal": "NF-99881",
            "veiculo_id": "CAM-01",
            "origem_id": "CD-REC",
            "destino_id": "CLI-JP",
            "status": "EM_TRANSITO",
            "historico": [{"evento": "DESPACHADO", "timestamp": datetime.now().isoformat(), "detalhes": "Saída CD Recife"}]
        },
        {
            "codigo_lote": "LOTE-102",
            "produto": "Eletrônicos de Alta Densidade",
            "peso_kg": 850,
            "temp_max_c": 25.0,
            "nota_fiscal": "NF-99882",
            "veiculo_id": "CAM-02",
            "origem_id": "CD-REC",
            "destino_id": "CLI-CG",
            "status": "EM_TRANSITO",
            "historico": [{"evento": "DESPACHADO", "timestamp": datetime.now().isoformat(), "detalhes": "Saída CD Recife"}]
        }
    ])

    print("2. Populando Redis...")
    redis_srv = RedisService()
    redis_srv.client.flushdb()
    redis_srv.atualizar_telemetria("CAM-01", -7.5606, -35.0021, 76, 5.4, "EM_ROTA")
    redis_srv.atualizar_telemetria("CAM-02", -7.2307, -35.8817, 68, 21.0, "EM_ROTA")

    print("3. Populando Neo4j...")
    neo = Neo4jService()
    with neo.driver.session() as session:
        session.run("MATCH (n {projeto: 'G_LOGISTICA'}) DETACH DELETE n")
        session.run("""
        CREATE (cd:CentroDistribuicao {id: 'CD-REC', nome: 'CD Recife', projeto: 'G_LOGISTICA'})
        CREATE (pt1:PontoTransbordo {id: 'PT-GOI', nome: 'Hub Goiana', projeto: 'G_LOGISTICA'})
        CREATE (pt2:PontoTransbordo {id: 'PT-ITA', nome: 'Hub Itabaiana', projeto: 'G_LOGISTICA'})
        CREATE (cli1:Cliente {id: 'CLI-JP', nome: 'Cliente João Pessoa', projeto: 'G_LOGISTICA'})
        CREATE (cli2:Cliente {id: 'CLI-CG', nome: 'Cliente Campina Grande', projeto: 'G_LOGISTICA'})

        CREATE (cd)-[:ROTA {id: 'R1', distancia_km: 65, custo: 180.0, ativa: true}]->(pt1)
        CREATE (pt1)-[:ROTA {id: 'R2', distancia_km: 55, custo: 150.0, ativa: true}]->(cli1)
        CREATE (cd)-[:ROTA {id: 'R3', distancia_km: 90, custo: 240.0, ativa: true}]->(pt2)
        CREATE (pt2)-[:ROTA {id: 'R4', distancia_km: 60, custo: 170.0, ativa: true}]->(cli1)
        CREATE (pt2)-[:ROTA {id: 'R5', distancia_km: 70, custo: 190.0, ativa: true}]->(cli2)
        CREATE (cli1)-[:ROTA {id: 'R6', distancia_km: 130, custo: 310.0, ativa: true}]->(cli2)
        """)
    neo.close()
    print("Seed concluído com sucesso nos 3 bancos!")

if __name__ == "__main__":
    run_seed()