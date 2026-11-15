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

    def calcular_melhor_rota(
        self,
        origem_id: str,
        destino_id: str,
        trechos_bloqueados: list[str],
        criterio: str = "custo",
    ):
        if criterio not in ("custo", "distancia"):
            raise ValueError("O critério deve ser 'custo' ou 'distancia'.")
        if origem_id == destino_id:
            raise ValueError("A origem e o destino devem ser diferentes.")

        localidades_query = """
        MATCH (n {projeto: 'G_LOGISTICA'})
        WHERE n.id IN [$origem_id, $destino_id]
        RETURN collect(DISTINCT n.id) AS ids
        """
        query = """
        MATCH (origem {id: $origem_id, projeto: 'G_LOGISTICA'}),
              (destino {id: $destino_id, projeto: 'G_LOGISTICA'})
        MATCH p = (origem)-[:ROTA*1..10]->(destino)
        WHERE ALL(r IN relationships(p) WHERE
            r.ativa = true
            AND r.id IS NOT NULL
            AND NOT r.id IN $bloqueados
            AND r.distancia_km IS NOT NULL
            AND r.custo IS NOT NULL
        )
        WITH p,
             reduce(dist = 0, r IN relationships(p) | dist + r.distancia_km) AS distancia_total,
             reduce(custo = 0.0, r IN relationships(p) | custo + r.custo) AS custo_total
        RETURN [n IN nodes(p) | n.nome] AS caminho,
               distancia_total,
               custo_total
        ORDER BY
            CASE WHEN $criterio = 'custo' THEN custo_total ELSE distancia_total END,
            CASE WHEN $criterio = 'custo' THEN distancia_total ELSE custo_total END
        LIMIT 1
        """
        with self.driver.session() as session:
            localidades = session.run(
                localidades_query,
                origem_id=origem_id,
                destino_id=destino_id,
            ).single()
            ids_existentes = localidades["ids"] if localidades else []
            if origem_id not in ids_existentes or destino_id not in ids_existentes:
                raise ValueError("A origem e o destino devem existir no grafo.")

            result = session.run(
                query,
                origem_id=origem_id,
                destino_id=destino_id,
                bloqueados=trechos_bloqueados,
                criterio=criterio,
            ).single()
            if result is None:
                return None

            rota = dict(result)
            rota["criterio"] = criterio
            return rota