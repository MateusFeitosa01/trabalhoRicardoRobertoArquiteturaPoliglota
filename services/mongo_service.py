import os
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from uuid import uuid4

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()


class MongoService:
    def __init__(self):
        self.client = MongoClient(os.getenv("MONGO_URI"))
        self.db = self.client[os.getenv("MONGO_DB", "logistica_db")]
        self.lotes = self.db["lotes"]
        self.manifestos = self.db["manifestos"]
        try:
            self.lotes.create_index("codigo_lote", unique=True)
            self.manifestos.create_index(
                "manifesto_id",
                unique=True,
                partialFilterExpression={"manifesto_id": {"$type": "string"}},
            )
            self.manifestos.create_index(
                "despacho_id",
                unique=True,
                partialFilterExpression={"despacho_id": {"$type": "string"}},
            )
        except DuplicateKeyError as exc:
            raise ValueError(
                "Não foi possível criar os índices únicos: existem identificadores duplicados."
            ) from exc
        self._migrar_documentacao_legada()

    def _migrar_documentacao_legada(self):
        for lote in self.lotes.find({"documentacao": {"$exists": False}}):
            dados_migrados = deepcopy(lote)
            self._normalizar_documentacao(dados_migrados)
            self.lotes.update_one(
                {"_id": lote["_id"], "documentacao": {"$exists": False}},
                {"$set": {"documentacao": dados_migrados["documentacao"]}},
            )

    @staticmethod
    def _evento(tipo: str, detalhes: str, responsavel: str | None = None):
        agora = datetime.now(timezone.utc)
        return {
            "tipo_evento": tipo,
            "data_hora": agora,
            "responsavel": responsavel,
            "detalhes": detalhes,
            "evento": tipo,
            "timestamp": agora,
        }

    @staticmethod
    def _validar_lote(dados_lote: dict):
        campos_obrigatorios = (
            "codigo_lote",
            "produto",
            "veiculo_id",
            "origem_id",
            "destino_id",
        )
        for campo in campos_obrigatorios:
            valor = dados_lote.get(campo)
            if not isinstance(valor, str) or not valor.strip():
                raise ValueError(f"O campo '{campo}' é obrigatório.")
            dados_lote[campo] = valor.strip()

        peso = dados_lote.get("peso_kg")
        if isinstance(peso, bool) or not isinstance(peso, (int, float)) or not isfinite(peso) or peso <= 0:
            raise ValueError("O peso deve ser um número finito maior que zero.")

        temperatura = dados_lote.get("temp_max_c")
        if (
            isinstance(temperatura, bool)
            or not isinstance(temperatura, (int, float))
            or not isfinite(temperatura)
        ):
            raise ValueError("A temperatura máxima deve ser um número finito.")

    @staticmethod
    def _normalizar_documentacao(dados_lote: dict):
        documentacao = dados_lote.get("documentacao") or {}
        if not isinstance(documentacao, dict):
            raise ValueError("A documentação deve ser um objeto.")

        nota_fiscal = documentacao.get("nota_fiscal") or {}
        if not isinstance(nota_fiscal, dict):
            raise ValueError("A nota fiscal estruturada deve ser um objeto.")

        campos_nota_fiscal = {
            "numero": "",
            "serie": "",
            "chave_acesso": "",
            "emitente": "",
            "destinatario": "",
            "valor_total": 0.0,
        }
        campos_nota_fiscal.update(deepcopy(nota_fiscal))
        nota_legada = dados_lote.get("nota_fiscal", "")
        if isinstance(nota_legada, dict):
            nota_legada = nota_legada.get("numero", "")
        if not isinstance(nota_legada, str):
            raise ValueError("O campo legado 'nota_fiscal' deve ser texto.")
        numero_estruturado = campos_nota_fiscal["numero"]
        if not isinstance(numero_estruturado, str):
            raise ValueError("O número estruturado da nota fiscal deve ser texto.")
        nota_legada = nota_legada.strip()
        numero_estruturado = numero_estruturado.strip()
        numero_canonico = numero_estruturado or nota_legada
        campos_nota_fiscal["numero"] = numero_canonico

        documentos_tecnicos = documentacao.get("documentos_tecnicos", [])
        if not isinstance(documentos_tecnicos, list):
            raise ValueError("'documentos_tecnicos' deve ser uma lista.")

        dados_lote["nota_fiscal"] = numero_canonico
        dados_lote["documentacao"] = {
            **deepcopy(documentacao),
            "nota_fiscal": campos_nota_fiscal,
            "documentos_tecnicos": deepcopy(documentos_tecnicos),
        }

    @staticmethod
    def _data_utc(valor):
        if valor is None:
            return datetime.now(timezone.utc)
        if isinstance(valor, str):
            try:
                valor = datetime.fromisoformat(valor.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("A data de emissão deve ser uma data ISO válida.") from exc
        if not isinstance(valor, datetime):
            raise ValueError("A data de emissão deve ser datetime ou texto ISO.")
        if valor.tzinfo is None:
            return valor.replace(tzinfo=timezone.utc)
        return valor.astimezone(timezone.utc)

    @staticmethod
    def _historico(dados, tipo_evento, detalhes, responsavel=None):
        historico = dados.get("historico", [])
        if not isinstance(historico, list):
            raise ValueError("O histórico deve ser uma lista.")
        historico.append(MongoService._evento(tipo_evento, detalhes, responsavel))
        return historico

    @staticmethod
    def _erro_duplicidade(mensagem, exc):
        raise ValueError(mensagem) from exc

    def listar_lotes(self, status=None):
        filtro = {"excluido": {"$ne": True}}
        if status:
            filtro["status"] = status
        return list(self.lotes.find(filtro, {"_id": 0}))

    def consultar_lote(self, codigo_lote: str):
        return self.lotes.find_one(
            {"codigo_lote": codigo_lote, "excluido": {"$ne": True}},
            {"_id": 0},
        )

    def cadastrar_lote(self, dados_lote: dict, responsavel: str | None = None):
        lote = deepcopy(dados_lote)
        self._validar_lote(lote)
        self._normalizar_documentacao(lote)
        lote["status"] = "EM_TRANSITO"
        lote["historico"] = self._historico(
            lote,
            "LOTE_CRIADO_E_DESPACHADO",
            f"Atribuído ao veículo {lote['veiculo_id']}",
            responsavel,
        )
        try:
            self.lotes.insert_one(lote)
        except DuplicateKeyError as exc:
            self._erro_duplicidade("Já existe um lote com esse código.", exc)

    def atualizar_lote(
        self,
        codigo_lote: str,
        dados_atualizacao: dict,
        responsavel: str | None = None,
    ):
        lote_atual = self.consultar_lote(codigo_lote)
        if lote_atual is None:
            raise LookupError(f"O lote '{codigo_lote}' não existe.")

        atualizacao = deepcopy(dados_atualizacao)
        if "codigo_lote" in atualizacao:
            if atualizacao["codigo_lote"] != codigo_lote:
                raise ValueError("Não é permitido alterar o código do lote.")
            atualizacao.pop("codigo_lote")
        campos_protegidos = {"_id", "status", "historico", "excluido", "entrega"}
        if campos_protegidos.intersection(atualizacao):
            raise ValueError("A atualização contém campos que não podem ser alterados.")
        if not atualizacao:
            raise ValueError("Informe pelo menos um dado para atualizar.")

        lote_atualizado = deepcopy(lote_atual)
        self._normalizar_documentacao(lote_atualizado)
        tem_nota_legada = "nota_fiscal" in atualizacao
        nota_legada = atualizacao.pop("nota_fiscal", None)
        if tem_nota_legada and not isinstance(nota_legada, str):
            raise ValueError("O campo legado 'nota_fiscal' deve ser texto.")
        documentacao_recebida = atualizacao.pop("documentacao", None)
        if documentacao_recebida is not None:
            if not isinstance(documentacao_recebida, dict):
                raise ValueError("A documentação deve ser um objeto.")
            documentacao_atual = deepcopy(lote_atualizado["documentacao"])
            nota_atual = documentacao_atual.get("nota_fiscal", {})
            nota_recebida = documentacao_recebida.get("nota_fiscal", {})
            if "nota_fiscal" in documentacao_recebida and not isinstance(nota_recebida, dict):
                raise ValueError("A nota fiscal estruturada deve ser um objeto.")
            documentacao_atual.update(documentacao_recebida)
            documentacao_atual["nota_fiscal"] = {**nota_atual, **nota_recebida}
            lote_atualizado["documentacao"] = documentacao_atual

        lote_atualizado.update(atualizacao)
        numero_estruturado = lote_atualizado["documentacao"]["nota_fiscal"].get("numero", "")
        if not isinstance(numero_estruturado, str):
            raise ValueError("O número estruturado da nota fiscal deve ser texto.")
        nota_estruturada_recebida = (
            documentacao_recebida.get("nota_fiscal", {}).get("numero")
            if documentacao_recebida
            and isinstance(documentacao_recebida.get("nota_fiscal", {}), dict)
            else None
        )
        if isinstance(nota_estruturada_recebida, str):
            nota_estruturada_recebida = nota_estruturada_recebida.strip()
        if nota_estruturada_recebida:
            numero_canonico = nota_estruturada_recebida
        elif tem_nota_legada:
            numero_canonico = nota_legada
        elif numero_estruturado:
            numero_canonico = numero_estruturado
        else:
            numero_canonico = lote_atualizado["nota_fiscal"]
        lote_atualizado["nota_fiscal"] = numero_canonico
        lote_atualizado["documentacao"]["nota_fiscal"]["numero"] = numero_canonico

        self._validar_lote(lote_atualizado)
        self._normalizar_documentacao(lote_atualizado)
        atualizacao["documentacao"] = lote_atualizado["documentacao"]
        atualizacao["nota_fiscal"] = lote_atualizado["nota_fiscal"]
        evento = self._evento(
            "LOTE_ATUALIZADO",
            f"Dados atualizados: {', '.join(sorted(atualizacao))}",
            responsavel,
        )
        try:
            resultado = self.lotes.update_one(
                {
                    "codigo_lote": codigo_lote,
                    "status": lote_atual.get("status"),
                    "excluido": {"$ne": True},
                },
                {"$set": atualizacao, "$push": {"historico": evento}},
            )
        except DuplicateKeyError as exc:
            self._erro_duplicidade("Já existe um lote com esse código.", exc)
        if resultado.matched_count != 1:
            raise RuntimeError("O lote foi alterado durante a atualização; tente novamente.")
        return True

    def excluir_lote(self, codigo_lote: str, responsavel: str | None = None):
        lote = self.consultar_lote(codigo_lote)
        if lote is None:
            raise LookupError(f"O lote '{codigo_lote}' não existe.")
        if lote.get("status") in {"EM_TRANSITO", "ENTREGUE"}:
            raise ValueError("Não é permitido excluir lote em trânsito ou já entregue.")

        evento = self._evento(
            "LOTE_EXCLUIDO",
            "Lote marcado como excluído.",
            responsavel,
        )
        resultado = self.lotes.update_one(
            {
                "codigo_lote": codigo_lote,
                "status": lote.get("status"),
                "excluido": {"$ne": True},
            },
            {
                "$set": {
                    "excluido": True,
                    "data_exclusao": evento["data_hora"],
                },
                "$push": {"historico": evento},
            },
        )
        if resultado.matched_count != 1:
            raise RuntimeError("O lote foi alterado durante a exclusão; tente novamente.")
        return True

    def registrar_entrega(self, codigo_lote: str, recebedor: str, observacao: str):
        if not isinstance(recebedor, str) or not recebedor.strip():
            raise ValueError("O recebedor é obrigatório.")
        if not isinstance(observacao, str):
            raise ValueError("A observação da entrega deve ser texto.")
        recebedor = recebedor.strip()
        lote = self.lotes.find_one(
            {"codigo_lote": codigo_lote, "excluido": {"$ne": True}},
            {"status": 1},
        )
        if lote is None:
            raise LookupError(f"O lote '{codigo_lote}' não existe.")
        if lote.get("status") != "EM_TRANSITO":
            raise ValueError("Somente lotes em trânsito podem ser entregues.")

        data_entrega = datetime.now(timezone.utc)
        evento = self._evento(
            "ENTREGA_CONCLUIDA",
            f"Recebido por {recebedor}. Obs: {observacao}",
            recebedor,
        )
        evento["data_hora"] = data_entrega
        evento["timestamp"] = data_entrega
        resultado = self.lotes.update_one(
            {
                "codigo_lote": codigo_lote,
                "status": "EM_TRANSITO",
                "excluido": {"$ne": True},
            },
            {
                "$set": {
                    "status": "ENTREGUE",
                    "entrega": {
                        "recebedor": recebedor,
                        "observacao": observacao,
                        "data_hora": data_entrega,
                    },
                    "liberacao_veiculo": {
                        "status": "PENDENTE",
                        "data_hora": data_entrega,
                    },
                },
                "$push": {"historico": evento},
            },
        )
        if resultado.matched_count != 1:
            raise RuntimeError(
                "O status do lote mudou durante a entrega; atualize os dados e tente novamente."
            )


    def registrar_liberacao_veiculo(self, codigo_lote: str):
        lote = self.consultar_lote(codigo_lote)
        if lote is None:
            raise LookupError(f"O lote '{codigo_lote}' não existe.")
        if lote.get("status") != "ENTREGUE":
            raise ValueError("O lote precisa estar entregue para liberar o veículo.")
        if lote.get("liberacao_veiculo", {}).get("status") != "PENDENTE":
            raise ValueError("Não existe liberação de veículo pendente para este lote.")

        data_liberacao = datetime.now(timezone.utc)
        evento = self._evento(
            "VEICULO_LIBERADO",
            "Liberação do veículo confirmada no Redis.",
            "Sistema",
        )
        evento["data_hora"] = data_liberacao
        evento["timestamp"] = data_liberacao

        resultado = self.lotes.update_one(
            {
                "codigo_lote": codigo_lote,
                "status": "ENTREGUE",
                "liberacao_veiculo.status": "PENDENTE",
                "excluido": {"$ne": True},
            },
            {
                "$set": {
                    "liberacao_veiculo.status": "CONCLUIDA",
                    "liberacao_veiculo.data_hora": data_liberacao,
                },
                "$push": {"historico": evento},
            },
        )
        if resultado.matched_count != 1:
            raise RuntimeError(
                "A liberação do veículo foi alterada durante a operação; atualize os dados."
            )
        return True

    def cadastrar_manifesto(self, dados_manifesto: dict):
        manifesto = deepcopy(dados_manifesto)
        manifesto_id = manifesto.get("manifesto_id") or str(uuid4())
        if not isinstance(manifesto_id, str) or not manifesto_id.strip():
            raise ValueError("O identificador do manifesto é obrigatório.")
        despacho_id = manifesto.get("despacho_id")
        if not isinstance(despacho_id, str) or not despacho_id.strip():
            raise ValueError("O identificador do despacho é obrigatório.")
        manifesto_id = manifesto_id.strip()
        despacho_id = despacho_id.strip()

        campos_obrigatorios = (
            "codigo_lote",
            "veiculo_id",
            "origem_id",
            "destino_id",
            "responsavel_despacho",
        )
        for campo in campos_obrigatorios:
            valor = manifesto.get(campo)
            if not isinstance(valor, str) or not valor.strip():
                raise ValueError(f"O campo '{campo}' é obrigatório no manifesto.")
            manifesto[campo] = valor.strip()

        lote = self.lotes.find_one(
            {"codigo_lote": manifesto["codigo_lote"]},
            {"_id": 0},
        )
        if lote is None:
            raise LookupError(f"O lote '{manifesto['codigo_lote']}' não existe.")
        if lote.get("excluido") is True:
            raise ValueError("Não é possível despachar um lote excluído.")
        if lote.get("status") != "EM_TRANSITO":
            raise ValueError("O lote precisa estar em trânsito para registrar o despacho.")
        for campo in ("veiculo_id", "origem_id", "destino_id"):
            valor_lote = lote.get(campo)
            if not isinstance(valor_lote, str) or valor_lote.strip() != manifesto[campo]:
                raise ValueError(
                    f"O campo '{campo}' do manifesto diverge dos dados do lote."
                )

        manifesto["manifesto_id"] = manifesto_id
        manifesto["despacho_id"] = despacho_id
        manifesto["data_emissao"] = self._data_utc(manifesto.get("data_emissao"))
        manifesto["status"] = manifesto.get("status") or "EM_TRANSITO"
        manifesto["historico"] = self._historico(
            manifesto,
            "MANIFESTO_CRIADO",
            f"Despacho do lote {manifesto['codigo_lote']} pelo veículo {manifesto['veiculo_id']}.",
            manifesto["responsavel_despacho"],
        )
        try:
            self.manifestos.insert_one(manifesto)
        except DuplicateKeyError as exc:
            self._erro_duplicidade(
                "Já existe um manifesto para esse lote/despacho ou identificador.",
                exc,
            )
        return manifesto_id

    def consultar_manifesto(self, manifesto_id: str):
        return self.manifestos.find_one(
            {"manifesto_id": manifesto_id},
            {"_id": 0},
        )

    def listar_manifestos(self, codigo_lote=None, status=None):
        filtro = {}
        if codigo_lote:
            filtro["codigo_lote"] = codigo_lote
        if status:
            filtro["status"] = status
        return list(self.manifestos.find(filtro, {"_id": 0}))
