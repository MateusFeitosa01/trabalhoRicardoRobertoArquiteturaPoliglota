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
2. **Torre de Controle — Telemetria em Tempo Real (Redis):** Monitoramento de GPS, velocidade e temperatura dos caminhões em trânsito com simulador de sensores e disparo de alertas quando a temperatura ultrapassa o limite cadastrado no MongoDB.
3. **Otimizador de Rotas Logísticas (Neo4j):** Simulação interativa de bloqueios de trechos na malha viária com recálculo automático da menor rota e custo estimado via Cypher (`shortestPath`).
4. **Despacho & Recebimento de Cargas (Integração):** Fluxo transacional entre bancos que registra a conclusão da entrega no histórico do **MongoDB** e altera o status do veículo para `DISPONIVEL` no **Redis**.

---

## 👥 Equipe
* Integrante 1
* Integrante 2
* Integrante 3
* Integrante 4