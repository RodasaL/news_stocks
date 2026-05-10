# News Stocks

Aplicação web full-stack que agrega notícias financeiras para os ativos na tua watchlist, com classificação inteligente ETF/Stock via Google Gemini, preços em tempo real e filtragem semântica de relevância para ETFs.

---

## Funcionalidades

- **Watchlist** — adiciona e remove símbolos (stocks e ETFs, incluindo europeus)
- **Notícias agregadas** — Google News, Yahoo Finance, MarketWatch, Reddit r/stocks e r/investing
- **Preços em tempo real** — preço actual e variação 24h via Yahoo Finance
- **Classificação automática** — Google Gemini 2.5 Flash identifica se o símbolo é Stock ou ETF
- **Filtragem semântica de ETFs** — as notícias de ETFs são pontuadas por relevância (0–100%) com base no índice, descrição e keywords do ETF
- **Metadata de ETFs** — para ETFs novos, o Gemini guarda automaticamente o índice rastreado, asset class, tickers relacionados e keywords de pesquisa
- **ETFs europeus** — sufixos de bolsa detectados automaticamente (`.DE`, `.L`, `.MI`, `.PA`, `.AS`, etc.)
- **Cache inteligente** — classificações persistentes (não expiram) + cache de 30 minutos para notícias e análise de relevância
- **Interface premium** — cards circulares estilo portfólio, tema light Bloomberg/Reuters

---

## Instalação e arranque

### Pré-requisitos

- Python 3.11+
- Node.js 18+
- (opcional) `make` — ver abaixo

### 1. Clonar e configurar o ambiente

```bash
git clone <repo-url>
cd news_stocks

cp .env.example .env
# Edita .env e adiciona a tua GEMINI_API_KEY
```

### 2. Instalar dependências

```bash
npm run setup
```

Este comando cria o `.venv` Python, instala as dependências do backend e do frontend automaticamente.

### 3. Lançar em desenvolvimento

```bash
npm run dev
```

Arranca backend e frontend no mesmo terminal com output colorido:

- **Backend** → http://localhost:8000
- **Frontend** → http://localhost:5173

---

## Comandos disponíveis

| Comando | Descrição |
|---------|-----------|
| `npm run setup` | Instala todas as dependências (Python venv + npm) |
| `npm run dev` | Lança backend + frontend em paralelo |
| `npm run dev:backend` | Só o backend (porta 8000) |
| `npm run dev:frontend` | Só o frontend (porta 5173) |
| `make setup` | Igual a `npm run setup` |
| `make dev` | Igual a `npm run dev` |
| `make prod` | Build e lança com Docker |
| `docker-compose up --build` | Produção via Docker |

> **Windows — `make` não instalado?**
> Instala via [Chocolatey](https://chocolatey.org/): `choco install make`
> Ou via [Scoop](https://scoop.sh/): `scoop install make`

---

## Produção (Docker)

```bash
make prod
# ou
docker-compose up --build
```

- **Frontend** → http://localhost (nginx)
- **Backend** → http://localhost:8000 (proxied via nginx em `/api/`)

---

## Configuração (`.env`)

```env
# Obrigatório para classificação e análise de ETFs
GEMINI_API_KEY=your_key_here

# Opcional
VITE_API_URL=http://localhost:8000
LOG_LEVEL=INFO
```

Obtém uma chave Gemini gratuita em: https://ai.google.dev/gemini-api/docs/quickstart

**Limites do free tier (maio 2026):**
- 2 requests/minuto
- 250 requests/dia
- Sem billing account no projeto Google

> A app funciona sem chave Gemini — os tipos aparecem como `...` e as notícias são mostradas sem filtragem de relevância.

---

## API Endpoints

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/` | Health check |
| `GET` | `/api/watchlist` | Devolver watchlist actual |
| `POST` | `/api/watchlist` | Actualizar watchlist `{ "symbols": ["AAPL", "SPY"] }` |
| `GET` | `/api/news` | Notícias para todos os símbolos (cache 30 min) |
| `GET` | `/api/prices` | Preço actual e variação 24h (Yahoo Finance) |
| `GET` | `/api/asset/{symbol}` | Classificar um símbolo (ETF ou Stock) |
| `POST` | `/api/assets/batch` | Classificar lista de símbolos numa só chamada |
| `GET` | `/api/assets/watchlist/batch` | Classificar toda a watchlist numa só chamada |
| `GET` | `/api/news/etf/{symbol}` | Notícias filtradas por relevância para um ETF |
| `POST` | `/api/news/etfs/batch` | Análise de relevância em batch para vários ETFs |

---

## Fontes de notícias

Todas gratuitas, sem API key:

| Fonte | Tipo |
|-------|------|
| Google News RSS | Índice de notícias em tempo real |
| Yahoo Finance RSS | Notícias financeiras por símbolo |
| MarketWatch RSS | Comentário de mercado |
| Reddit r/stocks | Discussões da comunidade |
| Reddit r/investing | Estratégias de investimento |

Deduplicação automática por hash MD5 do título + link.

---

## Estrutura do projecto

```
news_stocks/
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI entrypoint
│   │   ├── routes.py             # Todos os endpoints API
│   │   ├── models.py             # Modelos Pydantic
│   │   ├── news_fetcher.py       # Fetcher assíncrono multi-fonte
│   │   ├── asset_classifier.py   # Classificador Gemini + cache ETF
│   │   └── etf_metadata.json     # Base de dados local de ETFs
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.jsx               # Componente React principal
│   │   ├── main.jsx              # Entrypoint React
│   │   └── styles.css            # Estilos (tema light premium)
│   ├── vite.config.js
│   ├── Dockerfile
│   └── nginx.conf
├── scripts/
│   └── setup.js                  # Script de setup cross-platform
├── package.json                  # Scripts npm raiz + concurrently
├── Makefile                      # Atalhos make
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Stack técnica

| Camada | Tecnologia |
|--------|-----------|
| Backend | FastAPI · Python 3.11 · uvicorn |
| IA | Google Gemini 2.5 Flash (`google-genai`) |
| HTTP | httpx (async) |
| RSS parsing | feedparser |
| Frontend | React 18 · Vite 5 |
| Estilos | CSS puro (sem framework) |
| Dev tooling | concurrently |
| Produção | Docker · docker-compose · nginx |
| Dados de preços | Yahoo Finance (chart API, sem chave) |
| Dados de notícias | Google News · Yahoo Finance · MarketWatch · Reddit RSS |

---

## Como funciona o fluxo

```
Utilizador adiciona símbolo
        │
        ▼
POST /api/watchlist
        │
        ▼
Clica "Fetch & Analyse"
        │
        ├──▶ GET /api/prices          → Yahoo Finance → variação 24h
        ├──▶ GET /api/assets/watchlist/batch → Gemini → ETF ou Stock?
        │                                       └─▶ se ETF novo → enriquece metadata
        └──▶ GET /api/news
                │
                ├─ fetch Google News RSS (por símbolo ou queries ETF)
                ├─ fetch Yahoo Finance RSS
                ├─ fetch MarketWatch RSS
                ├─ fetch Reddit r/stocks
                └─ fetch Reddit r/investing
                        │
                        ▼
               deduplicação MD5
                        │
                        ▼
            se ETF → Gemini analisa relevância (0–100%)
                        │
                        ▼
                 cache 30 minutos
                        │
                        ▼
               mostrar no frontend
```
