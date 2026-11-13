import random
import streamlit as st
import pandas as pd
from services.mongo_service import MongoService
from services.redis_service import RedisService
from services.neo4j_service import Neo4jService

st.set_page_config(page_title="Supply Chain NoSQL", layout="wide")
st.title("Torre de Controle Logística — Arquitetura Poliglota NoSQL")

mongo = MongoService()
redis_srv = RedisService()
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
    st.subheader("Telemetria da Frota & Alertas Pub/Sub (Redis)")
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
                nova_temp = round(random.uniform(4.0, 14.0), 1)
                nova_lat = round(float(v['lat']) + random.uniform(0.01, 0.05), 4)
                nova_lng = round(float(v['lng']) + random.uniform(0.01, 0.05), 4)
                nova_vel = random.randint(50, 95)

                redis_srv.atualizar_telemetria(v['veiculo_id'], nova_lat, nova_lng, nova_vel, nova_temp)

                # Verifica limite de temperatura cadastrado no MongoDB
                lotes_veiculo = [l for l in mongo.listar_lotes("EM_TRANSITO") if l["veiculo_id"] == v["veiculo_id"]]
                for l in lotes_veiculo:
                    if nova_temp > l["temp_max_c"]:
                        redis_srv.publicar_alerta(
                            v["veiculo_id"],
                            f"ALERTA CRÍTICO: Temperatura {nova_temp}°C excedeu limite de {l['temp_max_c']}°C do {l['codigo_lote']}!"
                        )
                st.rerun()

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

# --- TELA 3: NEO4J ---
with aba3:
    st.subheader("Otimizador de Rotas & Simulação de Bloqueios (Neo4j)")
    locais = neo4j_srv.listar_locais()
    trechos = neo4j_srv.listar_trechos()
    opcoes_locais = {f"{l['nome']} ({l['id']})": l['id'] for l in locais}

    c1, c2 = st.columns(2)
    with c1:
        origem_label = st.selectbox("Origem", list(opcoes_locais.keys()), index=0)
        destino_label = st.selectbox("Destino", list(opcoes_locais.keys()), index=len(opcoes_locais)-1)

        st.markdown("**Simular Bloqueios na Malha Viária:**")
        bloqueados = []
        for t in trechos:
            if st.checkbox(f"Bloquear {t['trecho_id']}: {t['origem']} -> {t['destino']} ({t['distancia']} km)", key=t['trecho_id']):
                bloqueados.append(t['trecho_id'])

        if st.button("Calcular Menor Rota (Cypher shortestPath)"):
            rota = neo4j_srv.calcular_melhor_rota(opcoes_locais[origem_label], opcoes_locais[destino_label], bloqueados)
            if rota:
                st.success(f"**Rota Ideal:** {' ➔ '.join(rota['caminho'])}")
                st.write(f"**Distância Total:** {rota['distancia_total']} km | **Custo Estimado:** R$ {rota['custo_total']:.2f}")
            else:
                st.error("Nenhuma rota disponível com os bloqueios selecionados!")

    with c2:
        st.markdown("**Trechos Cadastrados no Grafo**")
        st.dataframe(pd.DataFrame(trechos), use_container_width=True)

# --- TELA 4: INTEGRAÇÃO DOS 3 BANCOS ---
with aba4:
    st.subheader("Confirmar Entrega & Atualizar Ecossistema")
    lotes_ativos = mongo.listar_lotes("EM_TRANSITO")

    if not lotes_ativos:
        st.info("Não há lotes em trânsito no momento.")
    else:
        lote_sel = st.selectbox("Selecione o Lote para Baixa", [l["codigo_lote"] for l in lotes_ativos])
        dados_lote = next(l for l in lotes_ativos if l["codigo_lote"] == lote_sel)

        st.write(f"**Produto:** {dados_lote['produto']} | **Veículo:** {dados_lote['veiculo_id']} | **Destino:** {dados_lote['destino_id']}")
        recebedor = st.text_input("Nome do Responsável pelo Recebimento", value="Carlos Silva")
        obs = st.text_input("Observação de Auditoria", value="Carga íntegra, temperatura conformidade OK.")

        if st.button("Confirmar Recebimento (MongoDB + Redis)"):
            # 1. Atualiza status e log no MongoDB
            mongo.registrar_entrega(lote_sel, recebedor, obs)
            # 2. Libera o veículo no Redis
            redis_srv.liberar_veiculo(dados_lote["veiculo_id"])
            st.success(f"Entrega de {lote_sel} registrada no MongoDB e veículo {dados_lote['veiculo_id']} liberado no Redis!")
            st.rerun()