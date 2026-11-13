import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

class Neo4jService:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
        )

    def close(self):
        self.driver.close()

    def listar_locais(self):
        query = """
        MATCH (n {projeto: 'G_LOGISTICA'})
        RETURN n.id AS id, n.nome AS nome, labels(n)[0] AS tipo
        ORDER BY n.nome
        """
        with self.driver.session() as session:
            return [dict(r) for r in session.run(query)]

    def listar_trechos(self):
        query = """
        MATCH (a {projeto: 'G_LOGISTICA'})-[r:ROTA]->(b {projeto: 'G_LOGISTICA'})
        RETURN a.id AS origem_id, a.nome AS origem, b.id AS destino_id, b.nome AS destino,
               r.id AS trecho_id, r.distancia_km AS distancia, r.custo AS custo, r.ativa AS ativa
        """
        with self.driver.session() as session:
            return [dict(r) for r in session.run(query)]

    def calcular_melhor_rota(self, origem_id: str, destino_id: str, trechos_bloqueados: list):
        # Usa shortestPath ignorando trechos marcados como bloqueados na interface
        query = """
        MATCH (origem {id: $origem_id, projeto: 'G_LOGISTICA'}),
              (destino {id: $destino_id, projeto: 'G_LOGISTICA'})
        MATCH p = shortestPath((origem)-[:ROTA*..10]->(destino))
        WHERE ALL(r IN relationships(p) WHERE NOT r.id IN $bloqueados AND r.ativa = true)
        RETURN [n IN nodes(p) | n.nome] AS caminho,
               reduce(dist = 0, r IN relationships(p) | dist + r.distancia_km) AS distancia_total,
               reduce(custo = 0.0, r IN relationships(p) | custo + r.custo) AS custo_total
        """
        with self.driver.session() as session:
            result = session.run(
                query,
                origem_id=origem_id,
                destino_id=destino_id,
                bloqueados=trechos_bloqueados
            ).single()
            return dict(result) if result else None