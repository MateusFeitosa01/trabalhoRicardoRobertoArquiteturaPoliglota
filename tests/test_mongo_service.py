from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

from services.mongo_service import MongoService


def build_service():
    service = MongoService.__new__(MongoService)
    service.lotes = MagicMock()
    service.manifestos = MagicMock()
    return service


def lote_valido(**overrides):
    lote = {
        "codigo_lote": "LOTE-201",
        "produto": "Carga refrigerada",
        "peso_kg": 25,
        "temp_max_c": 8.0,
        "nota_fiscal": "NF-201",
        "veiculo_id": "CAM-01",
        "origem_id": "CD-REC",
        "destino_id": "CLI-JP",
    }
    lote.update(overrides)
    return lote


def test_constructor_creates_unique_indexes_without_deleting_documents():
    collections = {"lotes": MagicMock(), "manifestos": MagicMock()}
    database = MagicMock()
    database.__getitem__.side_effect = collections.__getitem__
    client = MagicMock()
    client.__getitem__.return_value = database

    with patch("services.mongo_service.MongoClient", return_value=client):
        MongoService()

    collections["lotes"].create_index.assert_called_once_with("codigo_lote", unique=True)
    collections["manifestos"].create_index.assert_any_call(
        "manifesto_id",
        unique=True,
        partialFilterExpression={"manifesto_id": {"$type": "string"}},
    )
    collections["manifestos"].create_index.assert_any_call(
        "despacho_id",
        unique=True,
        partialFilterExpression={"despacho_id": {"$type": "string"}},
    )
    collections["lotes"].delete_many.assert_not_called()
    collections["manifestos"].delete_many.assert_not_called()


def test_legacy_documentation_migration_adds_structure_without_dropping_old_fields():
    service = build_service()
    legacy = {
        "_id": "mongo-id",
        "codigo_lote": "LOTE-201",
        "nota_fiscal": "NF-201",
        "historico": [{"evento": "DESPACHADO", "timestamp": "legacy-timestamp"}],
    }
    service.lotes.find.return_value = [legacy]

    service._migrar_documentacao_legada()

    query, update = service.lotes.update_one.call_args.args
    assert query == {"_id": "mongo-id", "documentacao": {"$exists": False}}
    assert update["$set"]["documentacao"]["nota_fiscal"]["numero"] == "NF-201"
    assert legacy["nota_fiscal"] == "NF-201"
    assert legacy["historico"][0]["timestamp"] == "legacy-timestamp"


def test_cadastrar_lote_valido_preserves_legacy_fields_and_adds_documentation_and_audit():
    service = build_service()
    original = lote_valido()
    original_copy = deepcopy(original)

    service.cadastrar_lote(original, responsavel="Ana")

    inserted = service.lotes.insert_one.call_args.args[0]
    assert original == original_copy
    assert inserted["nota_fiscal"] == "NF-201"
    assert inserted["documentacao"] == {
        "nota_fiscal": {
            "numero": "NF-201",
            "serie": "",
            "chave_acesso": "",
            "emitente": "",
            "destinatario": "",
            "valor_total": 0.0,
        },
        "documentos_tecnicos": [],
    }
    assert inserted["status"] == "EM_TRANSITO"
    evento = inserted["historico"][-1]
    assert evento["tipo_evento"] == "LOTE_CRIADO_E_DESPACHADO"
    assert evento["responsavel"] == "Ana"
    assert evento["data_hora"].tzinfo == timezone.utc


def test_cadastrar_lote_prefers_structured_invoice_number_and_syncs_legacy_field():
    service = build_service()

    service.cadastrar_lote(
        lote_valido(
            documentacao={"nota_fiscal": {"numero": "NF-ESTRUTURADA"}},
        )
    )

    inserted = service.lotes.insert_one.call_args.args[0]
    assert inserted["nota_fiscal"] == "NF-ESTRUTURADA"
    assert inserted["documentacao"]["nota_fiscal"]["numero"] == "NF-ESTRUTURADA"


def test_cadastrar_lote_reports_duplicate_code_clearly():
    service = build_service()
    service.lotes.insert_one.side_effect = DuplicateKeyError("duplicate")

    with pytest.raises(ValueError, match="Já existe um lote"):
        service.cadastrar_lote(lote_valido())


@pytest.mark.parametrize(
    "field",
    [
        "codigo_lote",
        "produto",
        "peso_kg",
        "temp_max_c",
        "veiculo_id",
        "origem_id",
        "destino_id",
    ],
)
def test_cadastrar_lote_rejects_missing_required_fields(field):
    service = build_service()
    dados = lote_valido()
    dados.pop(field)

    with pytest.raises(ValueError):
        service.cadastrar_lote(dados)

    service.lotes.insert_one.assert_not_called()


@pytest.mark.parametrize(
    "field",
    ["codigo_lote", "produto", "veiculo_id", "origem_id", "destino_id"],
)
def test_cadastrar_lote_rejects_empty_required_text_fields(field):
    service = build_service()

    with pytest.raises(ValueError):
        service.cadastrar_lote(lote_valido(**{field: "  "}))

    service.lotes.insert_one.assert_not_called()


@pytest.mark.parametrize("weight", [0, -1, True, float("nan")])
def test_cadastrar_lote_rejects_invalid_weight(weight):
    service = build_service()

    with pytest.raises(ValueError, match="peso"):
        service.cadastrar_lote(lote_valido(peso_kg=weight))

    service.lotes.insert_one.assert_not_called()


@pytest.mark.parametrize("temperature", [None, True, float("nan"), "8"])
def test_cadastrar_lote_rejects_invalid_max_temperature(temperature):
    service = build_service()

    with pytest.raises(ValueError, match="temperatura"):
        service.cadastrar_lote(lote_valido(temp_max_c=temperature))

    service.lotes.insert_one.assert_not_called()


def test_consultar_lote_finds_by_code_and_hides_soft_deleted_lots():
    service = build_service()

    service.consultar_lote("LOTE-201")

    service.lotes.find_one.assert_called_once_with(
        {"codigo_lote": "LOTE-201", "excluido": {"$ne": True}},
        {"_id": 0},
    )


def test_listar_lotes_supports_filtered_and_unfiltered_queries():
    service = build_service()

    service.listar_lotes()
    service.listar_lotes("EM_TRANSITO")

    assert service.lotes.find.call_args_list[0].args[0] == {"excluido": {"$ne": True}}
    assert service.lotes.find.call_args_list[1].args[0] == {
        "excluido": {"$ne": True},
        "status": "EM_TRANSITO",
    }


def test_atualizar_lote_validates_and_records_audit_event():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="AGUARDANDO")
    service.lotes.update_one.return_value.matched_count = 1

    assert service.atualizar_lote(
        "LOTE-201",
        {"produto": "Carga atualizada"},
        responsavel="Ana",
    )

    query, update = service.lotes.update_one.call_args.args
    assert query["codigo_lote"] == "LOTE-201"
    assert update["$set"]["produto"] == "Carga atualizada"
    assert update["$set"]["documentacao"]["nota_fiscal"]["numero"] == "NF-201"
    assert update["$push"]["historico"]["tipo_evento"] == "LOTE_ATUALIZADO"
    assert update["$push"]["historico"]["responsavel"] == "Ana"


def test_atualizar_lote_does_not_allow_changing_its_code():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="AGUARDANDO")

    with pytest.raises(ValueError, match="código"):
        service.atualizar_lote("LOTE-201", {"codigo_lote": "LOTE-202"})

    service.lotes.update_one.assert_not_called()


def test_atualizar_lote_keeps_structured_documentation_and_legacy_invoice_in_sync():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(
        status="AGUARDANDO",
        documentacao={
            "nota_fiscal": {"numero": "NF-201", "serie": "2"},
            "documentos_tecnicos": ["laudo.pdf"],
        },
    )
    service.lotes.update_one.return_value.matched_count = 1

    service.atualizar_lote(
        "LOTE-201",
        {"documentacao": {"nota_fiscal": {"numero": "NF-202"}}},
    )

    _, update = service.lotes.update_one.call_args.args
    assert update["$set"]["nota_fiscal"] == "NF-202"
    assert update["$set"]["documentacao"]["nota_fiscal"]["numero"] == "NF-202"
    assert update["$set"]["documentacao"]["nota_fiscal"]["serie"] == "2"
    assert update["$set"]["documentacao"]["documentos_tecnicos"] == ["laudo.pdf"]


def test_atualizar_lote_prefers_structured_number_when_both_invoice_fields_are_updated():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="AGUARDANDO")
    service.lotes.update_one.return_value.matched_count = 1

    service.atualizar_lote(
        "LOTE-201",
        {
            "nota_fiscal": "NF-LEGADA",
            "documentacao": {"nota_fiscal": {"numero": "NF-ESTRUTURADA"}},
        },
    )

    _, update = service.lotes.update_one.call_args.args
    assert update["$set"]["nota_fiscal"] == "NF-ESTRUTURADA"
    assert update["$set"]["documentacao"]["nota_fiscal"]["numero"] == "NF-ESTRUTURADA"


def test_atualizar_lote_supports_legacy_invoice_update_on_old_document():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="AGUARDANDO")
    service.lotes.update_one.return_value.matched_count = 1

    service.atualizar_lote("LOTE-201", {"nota_fiscal": "NF-202"})

    _, update = service.lotes.update_one.call_args.args
    assert update["$set"]["nota_fiscal"] == "NF-202"
    assert update["$set"]["documentacao"]["nota_fiscal"]["numero"] == "NF-202"


def test_excluir_lote_marks_allowed_lot_deleted_and_keeps_audit_history():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="CRIADO")
    service.lotes.update_one.return_value.matched_count = 1

    assert service.excluir_lote("LOTE-201", responsavel="Ana")

    _, update = service.lotes.update_one.call_args.args
    assert update["$set"]["excluido"] is True
    assert update["$push"]["historico"]["tipo_evento"] == "LOTE_EXCLUIDO"
    assert update["$push"]["historico"]["responsavel"] == "Ana"


@pytest.mark.parametrize("status", ["EM_TRANSITO", "ENTREGUE"])
def test_excluir_lote_blocks_in_transit_or_delivered_lots(status):
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status=status)

    with pytest.raises(ValueError, match="Não é permitido excluir"):
        service.excluir_lote("LOTE-201")

    service.lotes.update_one.assert_not_called()


def test_registrar_entrega_atomically_updates_transit_lot_and_records_details():
    service = build_service()
    service.lotes.find_one.return_value = {"status": "EM_TRANSITO"}
    service.lotes.update_one.return_value.matched_count = 1

    service.registrar_entrega("LOTE-201", "  Carlos  ", "Carga íntegra")

    query, update = service.lotes.update_one.call_args.args
    assert query["codigo_lote"] == "LOTE-201"
    assert query["status"] == "EM_TRANSITO"
    assert update["$set"]["status"] == "ENTREGUE"
    assert update["$set"]["entrega"]["recebedor"] == "Carlos"
    assert update["$set"]["entrega"]["observacao"] == "Carga íntegra"
    assert update["$set"]["entrega"]["data_hora"].tzinfo == timezone.utc
    assert update["$push"]["historico"]["tipo_evento"] == "ENTREGA_CONCLUIDA"
    assert update["$set"]["entrega"]["recebedor"] == "Carlos"
    assert update["$push"]["historico"]["responsavel"] == "Carlos"
    assert update["$set"]["liberacao_veiculo"]["status"] == "PENDENTE"
    assert update["$set"]["liberacao_veiculo"]["data_hora"].tzinfo == timezone.utc


def test_registrar_entrega_rejects_duplicate_delivery():
    service = build_service()
    service.lotes.find_one.return_value = {"status": "ENTREGUE"}

    with pytest.raises(ValueError, match="Somente lotes em trânsito"):
        service.registrar_entrega("LOTE-201", "Carlos", "")

    service.lotes.update_one.assert_not_called()


def test_registrar_entrega_reports_missing_lot():
    service = build_service()
    service.lotes.find_one.return_value = None

    with pytest.raises(LookupError, match="não existe"):
        service.registrar_entrega("AUSENTE", "Carlos", "")

    service.lotes.update_one.assert_not_called()


def test_registrar_entrega_rejects_non_text_observation():
    service = build_service()

    with pytest.raises(ValueError, match="observação.*texto"):
        service.registrar_entrega("LOTE-201", "Carlos", None)

    service.lotes.find_one.assert_not_called()
    service.lotes.update_one.assert_not_called()


def test_registrar_entrega_detects_status_change_before_atomic_update():
    service = build_service()
    service.lotes.find_one.return_value = {"status": "EM_TRANSITO"}
    service.lotes.update_one.return_value.matched_count = 0

    with pytest.raises(RuntimeError, match="status do lote mudou"):
        service.registrar_entrega("LOTE-201", "Carlos", "")


def test_registrar_liberacao_veiculo_marks_pending_release_as_completed():
    service = build_service()
    service.lotes.find_one.return_value = {
        "codigo_lote": "LOTE-201",
        "status": "ENTREGUE",
        "liberacao_veiculo": {"status": "PENDENTE"},
    }
    service.lotes.update_one.return_value.matched_count = 1

    assert service.registrar_liberacao_veiculo("LOTE-201") is True

    query, update = service.lotes.update_one.call_args.args
    assert query["status"] == "ENTREGUE"
    assert query["liberacao_veiculo.status"] == "PENDENTE"
    assert update["$set"]["liberacao_veiculo.status"] == "CONCLUIDA"
    assert update["$set"]["liberacao_veiculo.data_hora"].tzinfo == timezone.utc
    assert update["$push"]["historico"]["tipo_evento"] == "VEICULO_LIBERADO"


def test_registrar_liberacao_veiculo_rejects_lot_not_delivered():
    service = build_service()
    service.lotes.find_one.return_value = {
        "codigo_lote": "LOTE-201",
        "status": "EM_TRANSITO",
    }

    with pytest.raises(ValueError, match="precisa estar entregue"):
        service.registrar_liberacao_veiculo("LOTE-201")

    service.lotes.update_one.assert_not_called()


def test_registrar_liberacao_veiculo_rejects_already_completed_release():
    service = build_service()
    service.lotes.find_one.return_value = {
        "codigo_lote": "LOTE-201",
        "status": "ENTREGUE",
        "liberacao_veiculo": {"status": "CONCLUIDA"},
    }

    with pytest.raises(ValueError, match="Não existe liberação"):
        service.registrar_liberacao_veiculo("LOTE-201")

    service.lotes.update_one.assert_not_called()


def test_registrar_liberacao_veiculo_detects_concurrent_update():
    service = build_service()
    service.lotes.find_one.return_value = {
        "codigo_lote": "LOTE-201",
        "status": "ENTREGUE",
        "liberacao_veiculo": {"status": "PENDENTE"},
    }
    service.lotes.update_one.return_value.matched_count = 0

    with pytest.raises(RuntimeError, match="alterada durante a operação"):
        service.registrar_liberacao_veiculo("LOTE-201")


def manifesto_valido(**overrides):
    manifesto = {
        "manifesto_id": "MAN-201",
        "despacho_id": "DSP-201",
        "codigo_lote": "LOTE-201",
        "veiculo_id": "CAM-01",
        "origem_id": "CD-REC",
        "destino_id": "CLI-JP",
        "responsavel_despacho": "Ana",
    }
    manifesto.update(overrides)
    return manifesto


def test_cadastrar_and_consultar_manifesto_records_required_fields_and_audit():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="EM_TRANSITO")
    service.manifestos.find_one.return_value = {"manifesto_id": "MAN-201"}

    manifesto_id = service.cadastrar_manifesto(manifesto_valido())
    manifesto = service.consultar_manifesto(manifesto_id)

    inserted = service.manifestos.insert_one.call_args.args[0]
    assert manifesto_id == "MAN-201"
    assert manifesto == {"manifesto_id": "MAN-201"}
    assert inserted["data_emissao"].tzinfo == timezone.utc
    assert inserted["status"] == "EM_TRANSITO"
    assert inserted["historico"][-1]["tipo_evento"] == "MANIFESTO_CRIADO"
    assert inserted["historico"][-1]["responsavel"] == "Ana"


def test_two_distinct_dispatches_for_the_same_lot_are_allowed():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="EM_TRANSITO")

    service.cadastrar_manifesto(manifesto_valido())
    second_manifest = manifesto_valido(
        manifesto_id="MAN-202",
        despacho_id="DSP-202",
        data_emissao="2026-11-16T10:00:00Z",
    )
    service.cadastrar_manifesto(second_manifest)

    inserted = [call.args[0] for call in service.manifestos.insert_one.call_args_list]
    assert [doc["despacho_id"] for doc in inserted] == ["DSP-201", "DSP-202"]
    assert [doc["codigo_lote"] for doc in inserted] == ["LOTE-201", "LOTE-201"]


def test_listar_manifestos_filters_by_lot_and_status():
    service = build_service()

    service.listar_manifestos(codigo_lote="LOTE-201", status="EM_TRANSITO")

    assert service.manifestos.find.call_args.args[0] == {
        "codigo_lote": "LOTE-201",
        "status": "EM_TRANSITO",
    }


def test_cadastrar_manifesto_prevents_duplicate_dispatch():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="EM_TRANSITO")
    service.manifestos.insert_one.side_effect = DuplicateKeyError("duplicate")

    with pytest.raises(ValueError, match="Já existe um manifesto"):
        service.cadastrar_manifesto(manifesto_valido())


def test_cadastrar_manifesto_rejects_nonexistent_lot():
    service = build_service()
    service.lotes.find_one.return_value = None

    with pytest.raises(LookupError, match="não existe"):
        service.cadastrar_manifesto(manifesto_valido())

    service.manifestos.insert_one.assert_not_called()


def test_cadastrar_manifesto_rejects_deleted_lot():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(
        status="EM_TRANSITO",
        excluido=True,
    )

    with pytest.raises(ValueError, match="lote excluído"):
        service.cadastrar_manifesto(manifesto_valido())

    service.manifestos.insert_one.assert_not_called()


def test_cadastrar_manifesto_rejects_incompatible_lot_status():
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(status="ENTREGUE")

    with pytest.raises(ValueError, match="estado|trânsito"):
        service.cadastrar_manifesto(manifesto_valido())

    service.manifestos.insert_one.assert_not_called()


@pytest.mark.parametrize(
    ("field", "lot_value"),
    [
        ("veiculo_id", "CAM-02"),
        ("origem_id", "CD-ALT"),
        ("destino_id", "CLI-CG"),
    ],
)
def test_cadastrar_manifesto_rejects_mismatched_lot_route(field, lot_value):
    service = build_service()
    service.lotes.find_one.return_value = lote_valido(
        status="EM_TRANSITO",
        **{field: lot_value},
    )

    with pytest.raises(ValueError, match=field):
        service.cadastrar_manifesto(manifesto_valido())

    service.manifestos.insert_one.assert_not_called()
