import atexit
import logging
import random
import streamlit as st
import pandas as pd
from services.mongo_service import MongoService
from services.redis_service import RedisService
from services.neo4j_service import Neo4jService

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Supply Chain NoSQL", layout="wide")
st.title("Torre de Controle Logística — Arquitetura Poliglota NoSQL")

mongo = MongoService()


@st.cache_resource
def obter_redis_service():
    service = RedisService()
    atexit.register(_encerrar_redis_service, service)
    return service


def _encerrar_redis_service(service):
    try:
        service.close()
    except Exception:
        logger.exception("Falha ao encerrar o serviço Redis compartilhado.")


redis_srv = obter_redis_service()
neo4j_srv = Neo4jService()

aba1, aba2, aba3, aba4 = st.tabs([
    "1. Gestão de Lotes (MongoDB)",
    "2. Telemetria em Tempo Real (Redis)",
    "3. Otimizador de Rotas (Neo4j)",
    "4. Despacho & Recebimento (Integração)"
])

# --- TELA 1: MONGODB ---
with aba1:
    st.subheader("Catálogo de Lotes e Documentação Fiscal (MongoDB)")
    col1, col2 = st.columns([1, 2])

    with col1:
        with st.form("form_novo_lote"):
            st.markdown("**Cadastrar Novo Lote**")
            codigo = st.text_input("Código do Lote", value=f"LOTE-{random.randint(200, 999)}")
            produto = st.text_input("Produto", value="Vacinas Termolábeis")
            peso = st.number_input("Peso (kg)", min_value=1, value=500)
            temp_max = st.number_input("Temperatura Máxima (°C)", value=8.0)
            nf = st.text_input("Nota Fiscal", value="NF-55410")
            veiculo = st.selectbox("Veículo", ["CAM-01", "CAM-02"])
            destino = st.selectbox("Destino", ["CLI-JP", "CLI-CG"])
            if st.form_submit_button("Salvar no MongoDB"):
                mongo.cadastrar_lote({
                    "codigo_lote": codigo,
                    "produto": produto,
                    "peso_kg": peso,
                    "temp_max_c": temp_max,
                    "nota_fiscal": nf,
                    "veiculo_id": veiculo,
                    "origem_id": "CD-REC",
                    "destino_id": destino
                })
                st.success(f"Lote {codigo} salvo!")
                st.rerun()

    with col2:
        lotes = mongo.listar_lotes()
        if lotes:
            df_lotes = pd.DataFrame(lotes).drop(columns=["historico"], errors="ignore")
            st.dataframe(df_lotes, use_container_width=True)
            lote_detalhe = st.selectbox("Ver histórico JSON de auditoria:", [l["codigo_lote"] for l in lotes])
            doc = next(l for l in lotes if l["codigo_lote"] == lote_detalhe)
            st.json(doc)

# --- TELA 2: REDIS ---
with aba2:
    @st.fragment(run_every=5)
    def torre_controle_redis():
        st.subheader("Telemetria da Frota & Alertas Pub/Sub (Redis)")
        col_simulacao, col_consumidor = st.columns(2)
        with col_simulacao:
            if redis_srv.simulacao_ativa or redis_srv.simulacao_com_erro:
                if st.button("Parar simulação contínua"):
                    try:
                        redis_srv.parar_simulacao()
                    except (RuntimeError, TimeoutError) as exc:
                        st.error(str(exc))
            elif st.button("Iniciar simulação contínua"):
                try:
                    redis_srv.iniciar_simulacao(
                        lotes_provider=lambda: mongo.listar_lotes("EM_TRANSITO")
                    )
                except (RuntimeError, ValueError) as exc:
                    st.error(str(exc))
            if redis_srv.simulacao_ativa:
                st.caption("Simulação ativa.")
            elif redis_srv.simulacao_com_erro:
                st.error(f"A simulação foi interrompida: {redis_srv.simulacao_erro}")
            else:
                st.caption("Simulação parada.")

        with col_consumidor:
            try:
                consumidor_pronto = redis_srv.consumidor_alertas_pronto
            except RuntimeError as exc:
                consumidor_pronto = False
                st.error(str(exc))
            if redis_srv.consumidor_alertas_ativo or redis_srv.consumidor_alertas_com_erro:
                if st.button("Parar consumidor Pub/Sub"):
                    try:
                        redis_srv.parar_consumidor_alertas()
                    except (RuntimeError, TimeoutError) as exc:
                        st.error(str(exc))
            elif st.button("Iniciar consumidor Pub/Sub"):
                try:
                    redis_srv.iniciar_consumidor_alertas()
                except RuntimeError as exc:
                    st.error(str(exc))
            if redis_srv.consumidor_alertas_ativo:
                st.caption(
                    "Consumidor Pub/Sub ativo."
                    if consumidor_pronto
                    else "Conectando ao canal Pub/Sub..."
                )
            elif redis_srv.consumidor_alertas_com_erro:
                st.error(
                    "O consumidor Pub/Sub foi interrompido: "
                    f"{redis_srv.consumidor_alertas_erro}"
                )
            else:
                st.caption("Consumidor Pub/Sub parado.")

        for alerta_recebido in redis_srv.obter_alertas_consumidos(limite=10):
            st.info(
                f"Pub/Sub [{alerta_recebido['veiculo_id']}]: "
                f"{alerta_recebido['alerta']}"
            )

        frota = redis_srv.listar_frota()

        cols = st.columns(len(frota) if frota else 1)
        for idx, v in enumerate(frota):
            with cols[idx]:
                st.metric(
                    label=f"Veículo {v['veiculo_id']} ({v['status']})",
                    value=f"{v['temperatura']} °C",
                    delta=f"{v['velocidade']} km/h"
                )
                st.caption(f"GPS: {v['lat']}, {v['lng']}")

                if st.button(f"Simular Sensor ({v['veiculo_id']})", key=f"sim_{v['veiculo_id']}"):
                    try:
                        atualizado = redis_srv.simular_atualizacao(v["veiculo_id"])
                        lotes_veiculo = [l for l in mongo.listar_lotes("EM_TRANSITO") if l["veiculo_id"] == v["veiculo_id"]]
                        redis_srv.verificar_alertas_temperatura(
                            v["veiculo_id"],
                            float(atualizado["temperatura"]),
                            lotes_veiculo,
                        )
                        st.rerun()
                    except (LookupError, ValueError) as exc:
                        st.error(str(exc))

        st.divider()
        st.markdown("**Alertas Recentes (Canal Pub/Sub `alertas_logistica`)**")
        alertas = redis_srv.obter_alertas_recentes()
        if alertas:
            for a in alertas:
                st.error(f"[{a['veiculo_id']}] {a['alerta']}")
        else:
            st.info("Nenhum alerta de desvio ou temperatura registrado.")

        if frota:
            df_mapa = pd.DataFrame([{"lat": float(v["lat"]), "lon": float(v["lng"])} for v in frota])
            st.map(df_mapa)

    torre_controle_redis()

# --- TELA 3: NEO4J ---
with aba3:
    st.subheader("Otimizador de Rotas & Simulação de Bloqueios (Neo4j)")
    locais = neo4j_srv.listar_locais()
    trechos = neo4j_srv.listar_trechos()
    opcoes_locais = {f"{l['nome']} ({l['id']})": l['id'] for l in locais}
    criterios = {"Menor custo": "custo", "Menor distância": "distancia"}

    c1, c2 = st.columns(2)
    with c1:
        criterio_label = st.selectbox("Critério de otimização", list(criterios.keys()))
        criterio = criterios[criterio_label]

        if len(opcoes_locais) >= 2:
            origem_label = st.selectbox("Origem", list(opcoes_locais.keys()), key="rota_origem")
            origem_id = opcoes_locais[origem_label]
            destinos_disponiveis = [
                label for label, local_id in opcoes_locais.items()
                if local_id != origem_id
            ]
            destino_label = st.selectbox(
                "Destino",
                destinos_disponiveis,
                key="rota_destino",
            )
            destino_id = opcoes_locais[destino_label]
        else:
            st.info("O grafo precisa ter pelo menos duas localidades para calcular uma rota.")

        st.markdown("**Simular Bloqueios na Malha Viária:**")
        bloqueados = []
        for t in trechos:
            if st.checkbox(f"Bloquear {t['trecho_id']}: {t['origem']} -> {t['destino']} ({t['distancia']} km)", key=t['trecho_id']):
                bloqueados.append(t['trecho_id'])

        if len(opcoes_locais) >= 2 and st.button("Calcular Melhor Rota"):
            rota = neo4j_srv.calcular_melhor_rota(
                origem_id,
                destino_id,
                bloqueados,
                criterio=criterio,
            )
            if rota is not None:
                st.success(f"**Caminho encontrado:** {' ➔ '.join(rota['caminho'])}")
                st.write(f"**Distância total:** {rota['distancia_total']} km")
                st.write(f"**Custo total:** R$ {rota['custo_total']:.2f}")
                st.write(f"**Critério utilizado:** {criterio_label}")
            else:
                st.error("Não existe rota disponível entre essas localidades com os trechos ativos e bloqueios selecionados.")

    with c2:
        st.markdown("**Trechos Cadastrados no Grafo**")
        st.dataframe(pd.DataFrame(trechos), use_container_width=True)

# --- TELA 4: INTEGRAÇÃO DOS 3 BANCOS ---

with aba4:
    st.subheader("Despacho, Acompanhamento e Entrega")

    lotes_ativos = mongo.listar_lotes("EM_TRANSITO")

    if not lotes_ativos:
        st.info("Não há lotes em trânsito no momento.")
    else:
        lote_sel = st.selectbox(
            "Selecione o lote",
            [l["codigo_lote"] for l in lotes_ativos],
        )
        dados_lote = next(
            l for l in lotes_ativos
            if l["codigo_lote"] == lote_sel
        )

        st.write(
            f"**Produto:** {dados_lote['produto']} | "
            f"**Veículo:** {dados_lote['veiculo_id']} | "
            f"**Origem:** {dados_lote['origem_id']} | "
            f"**Destino:** {dados_lote['destino_id']}"
        )

        st.divider()
        st.markdown("### 1. Consulta do despacho (MongoDB)")

        manifestos = mongo.listar_manifestos(codigo_lote=lote_sel)

        if manifestos:
            manifesto = next(
                (
                    m for m in manifestos
                    if m.get("status") == "EM_TRANSITO"
                ),
                manifestos[0],
            )

            st.success("Manifesto encontrado no MongoDB.")
            st.write(f"**Manifesto:** {manifesto['manifesto_id']}")
            st.write(f"**Despacho:** {manifesto['despacho_id']}")
            st.write(
                f"**Responsável pelo despacho:** "
                f"{manifesto['responsavel_despacho']}"
            )

            if manifesto.get("caminho"):
                st.write(
                    f"**Rota registrada:** "
                    f"{' → '.join(manifesto['caminho'])}"
                )
                st.write(
                    f"**Distância:** "
                    f"{manifesto.get('distancia_total', 0)} km"
                )
                st.write(
                    f"**Custo:** "
                    f"R$ {manifesto.get('custo_total', 0):.2f}"
                )
                st.write(
                    f"**Critério:** {manifesto.get('criterio', 'Não informado')}"
                )

        else:
            st.warning(
                "Este lote ainda não possui manifesto registrado."
            )

            st.markdown("### 2. Registrar despacho (Neo4j + MongoDB)")

            responsavel_despacho = st.text_input(
                "Responsável pelo despacho",
                value="Carlos Silva",
                key="responsavel_despacho",
            )

            criterios_despacho = {
                "Menor custo": "custo",
                "Menor distância": "distancia",
            }

            criterio_despacho_label = st.selectbox(
                "Critério para calcular a rota",
                list(criterios_despacho.keys()),
                key="criterio_despacho",
            )

            trechos = neo4j_srv.listar_trechos()

            trechos_bloqueados = st.multiselect(
                "Trechos bloqueados",
                options=[t["trecho_id"] for t in trechos],
                key="trechos_bloqueados_despacho",
            )

            if st.button(
                "Calcular rota e registrar despacho",
                key="registrar_despacho",
            ):
                try:
                    if not responsavel_despacho.strip():
                        raise ValueError(
                            "Informe o responsável pelo despacho."
                        )

                    rota = neo4j_srv.calcular_melhor_rota(
                        dados_lote["origem_id"],
                        dados_lote["destino_id"],
                        trechos_bloqueados,
                        criterio=criterios_despacho[
                            criterio_despacho_label
                        ],
                    )

                    if rota is None:
                        st.error(
                            "Não existe rota disponível para este lote "
                            "com os bloqueios selecionados."
                        )
                    else:
                        despacho_id = f"DSP-{lote_sel}"

                        manifesto_id = mongo.cadastrar_manifesto({
                            "despacho_id": despacho_id,
                            "codigo_lote": lote_sel,
                            "veiculo_id": dados_lote["veiculo_id"],
                            "origem_id": dados_lote["origem_id"],
                            "destino_id": dados_lote["destino_id"],
                            "responsavel_despacho": (
                                responsavel_despacho.strip()
                            ),
                            "caminho": rota["caminho"],
                            "distancia_total": rota["distancia_total"],
                            "custo_total": rota["custo_total"],
                            "criterio": rota["criterio"],
                            "trechos_bloqueados": trechos_bloqueados,
                        })

                        st.success(
                            f"Despacho registrado! Manifesto: {manifesto_id}"
                        )
                        st.rerun()

                except Exception as exc:
                    logger.exception(
                        "Falha ao registrar despacho do lote %s.",
                        lote_sel,
                    )
                    st.error(f"Não foi possível registrar o despacho: {exc}")

        st.divider()
        st.markdown("### 3. Acompanhamento da frota (Redis)")

        frota = redis_srv.listar_frota()
        veiculo = next(
            (
                v for v in frota
                if v["veiculo_id"] == dados_lote["veiculo_id"]
            ),
            None,
        )

        if veiculo:
            c1, c2, c3 = st.columns(3)

            c1.metric(
                "Status do veículo",
                veiculo["status"],
            )
            c2.metric(
                "Velocidade",
                f"{veiculo['velocidade']} km/h",
            )
            c3.metric(
                "Temperatura",
                f"{veiculo['temperatura']} °C",
            )

            st.caption(
                f"Última posição disponível no Redis: "
                f"{veiculo['lat']}, {veiculo['lng']}"
            )
        else:
            st.warning(
                "Não há telemetria disponível para este veículo no Redis."
            )

        st.divider()
        st.markdown("### 4. Confirmar entrega (MongoDB + Redis)")

        if not manifestos:
            st.info(
                "Registre o despacho antes de confirmar a entrega."
            )
        else:
            recebedor = st.text_input(
                "Nome do responsável pelo recebimento",
                value="Carlos Silva",
                key="recebedor_entrega",
            )

            obs = st.text_input(
                "Observação de auditoria",
                value="Carga íntegra, temperatura em conformidade.",
                key="observacao_entrega",
            )

            if st.button(
                "Confirmar recebimento e liberar veículo",
                key="confirmar_entrega",
            ):
                try:
                    if not recebedor.strip():
                        raise ValueError(
                            "Informe o responsável pelo recebimento."
                        )

                    mongo.registrar_entrega(
                        lote_sel,
                        recebedor.strip(),
                        obs.strip(),
                    )

                except Exception as exc:
                    logger.exception(
                        "Falha ao registrar entrega do lote %s.",
                        lote_sel,
                    )
                    st.error(f"Não foi possível registrar a entrega: {exc}")

                else:
                    try:
                        liberado = redis_srv.liberar_veiculo(
                            dados_lote["veiculo_id"]
                        )

                        if not liberado:
                            st.warning(
                                f"A entrega de {lote_sel} foi registrada "
                                "no MongoDB, mas o Redis não confirmou "
                                "a liberação. A operação ficou pendente "
                                "e poderá ser tentada novamente acima."
                            )
                        else:
                            mongo.registrar_liberacao_veiculo(lote_sel)

                            st.success(
                                f"Entrega de {lote_sel} registrada no "
                                "MongoDB e liberação do veículo "
                                f"{dados_lote['veiculo_id']} concluída!"
                            )
                            st.rerun()

                    except Exception as exc:
                        logger.exception(
                            "Entrega do lote %s registrada, mas houve "
                            "falha ao liberar o veículo.",
                            lote_sel,
                        )
                        st.warning(
                            f"A entrega de {lote_sel} foi registrada "
                            "no MongoDB, mas a liberação do veículo "
                            f"continua pendente. Detalhe: {exc}"
                        )
