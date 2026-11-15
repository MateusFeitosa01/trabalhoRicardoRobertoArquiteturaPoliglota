from unittest.mock import MagicMock

import pytest

from services.neo4j_service import Neo4jService


def build_service(existing_ids, route):
    service = Neo4jService.__new__(Neo4jService)
    service.driver = MagicMock()
    session = MagicMock()
    service.driver.session.return_value.__enter__.return_value = session

    locations_result = MagicMock()
    locations_result.single.return_value = {"ids": existing_ids}
    route_result = MagicMock()
    route_result.single.return_value = route
    session.run.side_effect = [locations_result, route_result]

    return service, session


@pytest.mark.parametrize(
    ("criterion", "primary_order", "tiebreak_order"),
    [
        (
            "custo",
            "CASE WHEN $criterio = 'custo' THEN custo_total ELSE distancia_total END",
            "CASE WHEN $criterio = 'custo' THEN distancia_total ELSE custo_total END",
        ),
        (
            "distancia",
            "CASE WHEN $criterio = 'custo' THEN distancia_total ELSE custo_total END",
            "CASE WHEN $criterio = 'custo' THEN custo_total ELSE distancia_total END",
        ),
    ],
)
def test_calcular_melhor_rota_uses_criterion_and_tiebreaker(
    criterion,
    primary_order,
    tiebreak_order,
):
    route_data = {
        "caminho": ["Origem", "Destino"],
        "distancia_total": 10,
        "custo_total": 20.0,
    }
    service, session = build_service(["A", "B"], route_data)

    route = service.calcular_melhor_rota("A", "B", [], criterio=criterion)

    assert route == {**route_data, "criterio": criterion}
    query = session.run.call_args_list[1].args[0]
    assert primary_order in query
    assert tiebreak_order in query
    assert session.run.call_args_list[1].kwargs["criterio"] == criterion


def test_calcular_melhor_rota_excludes_blocked_relationships_before_ranking():
    service, session = build_service(
        ["A", "B"],
        {"caminho": ["Origem", "Alternativa", "Destino"], "distancia_total": 16, "custo_total": 31.0},
    )

    route = service.calcular_melhor_rota("A", "B", ["R1"])

    query = session.run.call_args_list[1].args[0]
    assert "MATCH p = (origem)-[:ROTA*1..10]->(destino)" in query
    assert "r.ativa = true" in query
    assert "NOT r.id IN $bloqueados" in query
    assert session.run.call_args_list[1].kwargs["bloqueados"] == ["R1"]
    assert route["caminho"] == ["Origem", "Alternativa", "Destino"]


def test_calcular_melhor_rota_returns_none_when_no_path_is_available():
    service, _ = build_service(["A", "B"], None)

    assert service.calcular_melhor_rota("A", "B", ["R1"]) is None


def test_calcular_melhor_rota_rejects_invalid_criterion():
    service, session = build_service(["A", "B"], None)

    with pytest.raises(ValueError, match="critério"):
        service.calcular_melhor_rota("A", "B", [], criterio="tempo")

    session.run.assert_not_called()


@pytest.mark.parametrize("existing_ids", [[], ["A"], ["B"]])
def test_calcular_melhor_rota_rejects_unknown_origin_or_destination(existing_ids):
    service, session = build_service(existing_ids, None)

    with pytest.raises(ValueError, match="existir"):
        service.calcular_melhor_rota("A", "B", [])

    assert session.run.call_count == 1


def test_calcular_melhor_rota_rejects_identical_origin_and_destination():
    service, session = build_service(["A"], None)

    with pytest.raises(ValueError, match="diferentes"):
        service.calcular_melhor_rota("A", "A", [])

    session.run.assert_not_called()
