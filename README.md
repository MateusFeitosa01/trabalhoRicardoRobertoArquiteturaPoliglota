# 🚚 Torre de Controle Logística — Arquitetura Poliglota NoSQL

Projeto prático final da disciplina **Implementação de Bancos de Dados NoSQL** (Prof. Ricardo Roberto de Lima), implementando o **Estudo de Caso 04: Sistema Logístico & Rastreamento de Cadeia de Suprimentos (Supply Chain)** utilizando o conceito de *Polyglot Persistence* (Persistência Poliglota).

---

## 🏗️ Arquitetura da Solução

A aplicação utiliza **Python** e **Streamlit** como camada central de orquestração, conectando-se simultaneamente a três paradigmas distintos de bancos de dados NoSQL:

```text
                  +-----------------------------------+
                  |   Interface Streamlit (Python)    |
                  +-----------------------------------+
                    /               |               \
                   /                |                \
    +-----------------+    +-----------------+    +------------------+
    | MongoDB Driver  |    |  Redis Client   |    |  Neo4j Driver    |
    +-----------------+    +-----------------+    +------------------+
            |                       |                      |
            v                       v                      v
    +-----------------+    +-----------------+    +------------------+
    |    MongoDB      |    |      Redis      |    |      Neo4j       |
    | (Catálogo/Logs) |    | (Cache/Sessão)  |    | (Grafo/Conexões) |
    +-----------------+    +-----------------+    +------------------+
```

### 🎯 Justificativa da Persistência Poliglota

| Banco de Dados | Modelo NoSQL | Papel no Sistema | Por que foi escolhido? |
| :--- | :--- | :--- | :--- |
| **MongoDB** | Orientado a Documentos | Gestão de lotes, especificações fiscais/técnicas e histórico de auditoria. | Flexibilidade de esquema (JSON/BSON) para armazenar diferentes tipos de cargas e anexar eventos de auditoria (`$push`) em um único documento sem necessidade de `JOINs`. |
| **Redis** | Chave-Valor (*In-Memory*) | Telemetria em tempo real (GPS, velocidade, temperatura) e alertas via Pub/Sub. | Latência sub-milissegundo para leituras e escritas frequentes de sensores IoT da frota, além de mensageria nativa para alertas críticos de desvio térmico. |
| **Neo4j** | Grafos | Malha logística (`:CentroDistribuicao`, `:PontoTransbordo`, `:Cliente`) e cálculo de rotas. | Uso de *Index-Free Adjacency* e algoritmos Cypher (`shortestPath`) para recalcular rotas instantaneamente diante de bloqueios na malha viária. |

---

## 📁 Estrutura do Repositório

```text
projeto-nosql-logistica/
├── docker-compose.yml       # Orquestração dos containers locais (MongoDB, Redis e Neo4j)
├── requirements.txt         # Dependências Python do projeto
├── .env.example             # Exemplo de configuração dos bancos
├── app.py                   # Interface web Streamlit com os 4 módulos obrigatórios
├── scripts/
│   └── seed.py              # Script de criação de índices e carga inicial nos 3 bancos
└── services/
    ├── mongo_service.py     # Operações CRUD e logs de auditoria no MongoDB
    ├── redis_service.py     # Hashes de telemetria e fila/PubSub de alertas no Redis
    └── neo4j_service.py     # Consultas Cypher de malha viária e menor caminho no Neo4j
```

---

## 🚀 Como Executar o Projeto

### 1. Pré-requisitos
* **Docker** e **Docker Compose** instalados.
* **Python 3.10+** instalado.

### 2. Subir os Bancos de Dados
Na raiz do projeto, inicie os serviços via Docker Compose:
```bash
docker compose up -d
```

### 3. Configurar Variáveis de Ambiente
Copie o arquivo de exemplo para `.env` na raiz do projeto. Os valores padrão correspondem aos serviços locais do Docker Compose:
```bash
cp .env.example .env
```
No Windows PowerShell, use `Copy-Item .env.example .env`. Se usar um banco remoto, atualize as variáveis correspondentes no `.env`. Esse arquivo não deve ser compartilhado nem enviado ao Git.

### 4. Instalar Dependências e Popular os Bancos
Crie o ambiente virtual, instale as bibliotecas e execute o script de carga inicial (`seed.py`):
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Cria índices e popula dados iniciais no MongoDB, Redis e Neo4j
python scripts/seed.py
```

### 5. Iniciar a Aplicação Streamlit
```bash
streamlit run app.py
```
Acesse no navegador: `http://localhost:8501`

---

## 🖥️ Funcionalidades Implementadas

1. **Gestão de Lotes & Inventário (MongoDB):** Cadastro de novos lotes com limites térmicos, dados fiscais e visualização do histórico completo de auditoria em JSON.
2. **Torre de Controle — Telemetria em Tempo Real (Redis):** Monitoramento de GPS, velocidade e temperatura dos caminhões em trânsito com simulador de sensores, alertas Pub/Sub e histórico recente no Redis.
3. **Otimizador de Rotas Logísticas (Neo4j):** Simulação interativa de bloqueios de trechos na malha viária com recálculo automático da menor rota e custo estimado via Cypher (`shortestPath`).
4. **Despacho & Recebimento de Cargas (Integração):** Fluxo transacional entre bancos que registra a conclusão da entrega no histórico do **MongoDB** e altera o status do veículo para `DISPONIVEL` no **Redis**.

### Controles Redis

Na segunda aba do Streamlit, use os controles para iniciar e parar a simulação contínua e o consumidor Pub/Sub. Ambos começam parados; a assinatura do canal `alertas_logistica` roda em uma thread de fundo, sem bloquear a interface. Os workers pertencem ao recurso Redis compartilhado entre as sessões. O histórico recente fica na lista `historico_alertas` do Redis e permanece disponível mesmo quando o consumidor está parado. No encerramento do processo Streamlit, a instância compartilhada para os workers e fecha o cliente Redis.

O método `atualizar_telemetria()` valida e grava todos os campos do hash do veículo de uma vez. `liberar_veiculo()` só altera um hash existente e completo. A gravação do alerta no histórico, sua deduplicação por cinco minutos e a publicação no canal são executadas em um script Lua atômico; o histórico é limitado aos dez alertas mais recentes. A verificação de possível acidente considera velocidade acima de 110 km/h ou queda de pelo menos 40 km/h a partir de uma velocidade anterior igual ou superior a 50 km/h.

Alertas de temperatura comparam a telemetria aos limites dos lotes em trânsito no MongoDB. O método `verificar_desvio_rota()` aceita uma sequência de coordenadas planejadas, mas a malha atual do Neo4j não contém coordenadas geográficas; por isso, o desvio só pode ser calculado quando esses dados forem fornecidos.

---

## Testes Automatizados

Os testes unitários validam as regras dos serviços MongoDB, Redis e Neo4j. Também existem testes de integração com Redis real.

Com o ambiente virtual ativado, execute:

```bash
python -m compileall -q .
RUN_REDIS_INTEGRATION=1 python -m pytest -q
```

Os testes de integração do Redis utilizam banco e prefixo próprios para isolar suas chaves. Não é necessário executar `seed.py` para rodar os testes.

## Roteiro de Demonstração

1. Mostrar os três bancos ativos pelo Docker Compose e abrir a aplicação Streamlit.
2. Cadastrar ou consultar um lote no MongoDB e apresentar seu histórico de auditoria.
3. Iniciar a simulação da frota e demonstrar telemetria e alertas no Redis.
4. Calcular uma rota no Neo4j, selecionar um critério e simular bloqueios de trechos.
5. Registrar ou consultar o manifesto de um lote em trânsito.
6. Confirmar a entrega e demonstrar a atualização no MongoDB e a liberação do veículo no Redis.
7. Explicar a recuperação: se a liberação falhar, a operação fica `PENDENTE` e pode ser tentada novamente sem duplicar a entrega.

**Atenção:** execute `seed.py` somente quando desejar preparar ou recriar os dados de demonstração. Faça backup antes, pois o script pode remover dados do grafo do projeto no Neo4j.


## 👥 Equipe
1- Josenilton Cirne Ramalho Neto
2- Rony Arthur Meireles Medeiros
3- Mateus Moreira y Moreira Feitosa
4- Petrus Alecsander da Silva Arimatea
5- Nelson Francisco Suassuna Neto
6- Carlos Eduardo Menezes Cavalcante