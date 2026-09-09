# ConvergeAI—Platform Overview & Technical Reference

---

## What is ConvergeAI?

ConvergeAI is an AI-powered business intelligence platform that lets anyone in an organisation ask questions about their data — without writing a single line of SQL or knowing how databases work.

Whether the data lives in a corporate database, a spreadsheet uploaded as a CSV, a PDF report, or a Word document, ConvergeAI brings it all into one place. Users ask questions in plain English, and the platform returns answers as charts, tables, and AI-generated summaries — tailored to the role of the person asking.

The platform is designed to serve multiple types of business users simultaneously. An executive asking *"What was our revenue growth last quarter?"* gets a boardroom-ready summary. A sales manager asking *"Which territories missed target this month?"* gets a performance breakdown. A data analyst asking the same question gets statistical depth and methodology notes. The same data, the same query — different lenses, different outputs, all from one platform.

ConvergeAI is currently live at [https://cai-demo.circulants.ai](https://cai-demo.circulants.ai), deployed on an Azure VM alongside Platform A2 which handles authentication via Microsoft Entra SSO.

---

## Current Capabilities

| Capability | Status |
|---|---|
| Natural language to SQL query execution | Live |
| Multi-source federated querying (join across databases) | Live |
| CSV / SQLite file upload and query | Live |
| PostgreSQL, MySQL connection | Live |
| Databricks connection | Live |
| Snowflake connection | Live |
| Document upload and RAG-based context (PDF, DOCX, TXT) | Live |
| AI-generated summaries with persona framing | Live |
| Auto-generated charts and visualisations | Live |
| Business rules and knowledge base injection | Live |
| Power BI dashboard embedding | Live (display only) |
| Power BI dashboard querying via natural language | In progress |
| Microsoft Entra SSO authentication | Live |

---

## Who Uses It and How

### Executive
Connects to the organisation's data warehouse or CRM database. Asks high-level questions about revenue, growth, market position. Gets concise, board-ready summaries with trend charts. No SQL, no data exports, no waiting for an analyst.

### Sales Manager
Connects to sales databases or uploads territory CSVs. Asks questions about team performance, target attainment, pipeline health. Gets territory comparisons, outlier highlights, and actionable breakdowns.

### Field Representative
Accesses account-level data — HCP engagement, product performance per account. Gets concrete, visit-ready insights pulled directly from live data rather than stale weekly reports.

### Data Analyst
Connects multiple sources at once — a production database plus a Databricks data lake, for example. Runs cross-source queries, gets statistical depth, methodology notes, and data quality observations in every response.

---

## How It Works — The Data Flow

```
User types a question
        ↓
Frontend sends question + active data source(s) to backend
        ↓
SQL Generator (OpenAI GPT-4o) converts question → SQL
using the schema context of the connected source(s)
        ↓
Federated Query Engine executes the SQL
across one or multiple connected sources via DuckDB
        ↓
Results returned as a DataFrame
        ↓
┌──────────────────────────────────────────┐
│  Three parallel outputs generated:        │
│  1. Data table (raw results)              │
│  2. Chart (auto-selected, Plotly)         │
│  3. AI summary (GPT-4o, persona-aware)    │
└──────────────────────────────────────────┘
        ↓
All three returned to frontend and rendered in the chat
```

If documents have been uploaded, their relevant chunks are retrieved via RAG and injected into the SQL generation step as additional business context before the query is built.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Azure VM                            │
│                                                         │
│  ┌──────────────┐        ┌────────────────────────┐     │
│  │  Platform A2  │        │      ConvergeAI         │     │
│  │  (Node.js)    │        │                        │     │
│  │  Port 3000    │        │  Frontend  Backend      │     │
│  │  SAML SSO     │        │  (React)   (FastAPI)    │     │
│  └──────┬────────┘        │  Nginx     Port 8002    │     │
│         │  JWT token      └────────────────────────┘     │
│         └──────────────────────────────────────────┘     │
│                                                         │
│  Nginx (443) routes:                                    │
│    /api/*  → FastAPI :8002                              │
│    /auth/* → FastAPI :8002                              │
│    /*      → React build (static)                       │
└─────────────────────────────────────────────────────────┘
```

Authentication flow: User logs in to Platform A2 via Microsoft Entra → A2 issues a short-lived JWT → ConvergeAI validates the JWT and creates its own session. Both platforms run on the same VM in separate directories.

---

## Repository Structure

```
convergeai/
│
├── backend/                          # Python FastAPI application
│   ├── main.py                       # App entry point, all route registration
│   ├── auth.py                       # JWT validation, session cookie management
│   ├── user_store.py                 # SQLite store — users, sessions, chats, sources
│   ├── database_manager.py           # DB abstraction (SQLite/PG/MySQL/Databricks/Snowflake)
│   ├── datasources.py                # /api/datasources router — connect, query, manage
│   ├── federated_query_engine.py     # DuckDB-backed cross-source query execution
│   ├── sql_generator.py              # Natural language → SQL via OpenAI
│   ├── analysis_engine.py            # SQL results → AI summary via OpenAI (persona-aware)
│   ├── visualization_engine.py       # Auto chart selection and Plotly config generation
│   ├── document_manager.py           # PDF/DOCX/TXT upload, extraction, storage
│   ├── rag_engine.py                 # ChromaDB vector store, sentence-transformer embeddings
│   ├── hydration.py                  # Restore user data source connections on server start
│   ├── data/                         # SQLite database files (gitignored)
│   └── uploads/                      # Uploaded documents (gitignored)
│
├── frontend/                         # React + TypeScript application
│   ├── src/
│   │   ├── App.tsx                   # Root — auth state machine, session context
│   │   ├── config.ts                 # API_BASE config (Vite proxy in dev, env var in prod)
│   │   ├── main.tsx                  # React entry point
│   │   ├── index.css                 # Global styles
│   │   ├── pages/
│   │   │   ├── AnalysisPage.tsx      # Main chat / query interface
│   │   │   ├── DataSourcesPage.tsx   # Connect and manage data sources
│   │   │   ├── DocumentsPage.tsx     # Upload and manage documents
│   │   │   ├── KnowledgeBasePage.tsx # Business rules + Power BI dashboard embedding
│   │   │   ├── DataOnboardingPage.tsx # First-run data source setup
│   │   │   ├── PersonaSelectionPage.tsx # Role selection on first login
│   │   │   └── LoginPage.tsx         # Login UI (cosmetic — real auth is SSO)
│   │   ├── components/
│   │   │   ├── Sidebar.tsx           # Navigation, chat sessions, logout
│   │   │   ├── ChatMessage.tsx       # Single message — question, SQL, table, chart, summary
│   │   │   ├── ChatInput.tsx         # Message input box with source selector
│   │   │   ├── DataTable.tsx         # Paginated, sortable result table
│   │   │   ├── DynamicChart.tsx      # Plotly chart renderer
│   │   │   ├── PersonaDropdown.tsx   # In-chat persona switcher
│   │   │   ├── TableFilterPanel.tsx  # Column filter panel for data tables
│   │   │   ├── DataSourcesForm.tsx   # Connection form for each DB type
│   │   │   └── DataSourcesModal.tsx  # Modal wrapper for data source management
│   │   └── hooks/
│   │       └── useSessions.ts        # Chat session state management, backend sync
│   ├── public/
│   ├── index.html
│   ├── vite.config.ts                # Dev server config, proxy rules
│   ├── tsconfig.json
│   └── package.json
│
├── .env                              # Environment variables (NEVER commit this)
├── .gitignore
├── .gitlab-ci.yml                    # CI/CD pipeline — auto deploy on push to main
└── requirements.txt                  # Python backend dependencies
```

---

## Component Deep Dive

### Backend

---

#### `main.py` — Application Entry Point

The FastAPI app is defined and assembled here. On startup it initialises the SQLite user database, purges expired sessions, and runs `hydrate_all_users()` to restore every user's data source connections from the previous server run into memory.

All auth endpoints live here (`/auth/sso`, `/auth/login`, `/auth/logout`, `/auth/dev-login`). All chat and message persistence endpoints live here. The datasources router is imported and mounted. CORS middleware is configured from the `CORS_ORIGINS` environment variable.

**Precautions:**
- `ALLOW_DEV_LOGIN` must never be set to `true` in production. The dev login endpoint bypasses all authentication.
- The `.env` file path is resolved relative to the parent of the `backend/` directory — if the folder structure changes, update line 18 (`_env_path`).
- The startup hydration can be slow if many users have many data sources. Monitor startup time after scaling.

---

#### `auth.py` — Authentication & Session Management

Handles two things: validating the short-lived JWT issued by Platform A2 during SSO login, and managing the `cai_session` cookie that keeps users logged in to ConvergeAI.

The JWT is validated using `PyJWT` with the `JWT_SHARED_SECRET` shared between A2 and ConvergeAI. Once validated, a session token is created and stored in SQLite. Subsequent requests carry this token as an `httpOnly` cookie.

FastAPI dependencies `get_current_user` and `get_optional_user` are used on every protected route. Any route that needs the logged-in user calls one of these.

**Precautions:**
- `JWT_SHARED_SECRET` must be identical on both Platform A2 and ConvergeAI. A mismatch will silently reject all SSO logins and redirect users in a loop.
- `COOKIE_DOMAIN` must be set to `.circulants.ai` in production so the cookie works across subdomains. Leave it blank locally.
- Session tokens are 8 hours by default. Adjust in `create_session()` in `user_store.py` if needed.

---

#### `user_store.py` — Persistent User Data Store

SQLite-backed store for all user-related data. Five tables: `users`, `user_sessions`, `user_data_sources`, `user_chats`, `user_messages`. Also stores the YAML schema for each connected data source in `user_schema`.

Every user is identified by a deterministic ID — a SHA-256 hash of their email address — so the same user always maps to the same record even across sessions and server restarts.

The `upsert_user` function creates the user on first login and updates their name fields on subsequent logins (in case Entra profile data changes).

**Precautions:**
- The SQLite file lives at `backend/data/users.db`. This directory is gitignored. Back it up regularly on the VM — losing it means losing all user sessions, chat history, and saved data source configurations.
- Do not run multiple uvicorn workers pointing at the same SQLite file without enabling WAL mode (it is already enabled). For multi-worker deployments, migrate to PostgreSQL.
- The `persona` column was added via migration (lines 97–101). If you add new columns in the future, follow the same pattern to avoid breaking existing deployments.

---

#### `database_manager.py` — Database Abstraction Layer

The core of the platform. Handles connections to SQLite, PostgreSQL, MySQL, Databricks, and Snowflake. For each source it:

- Establishes and caches the connection
- Introspects the schema (tables, columns, types, sample values)
- Generates a YAML schema file used by the SQL generator as context
- Executes queries and returns DataFrames
- Manages column-level metadata enhancements (business descriptions, example values, query patterns)

SQLAlchemy is used for PostgreSQL and MySQL. Databricks uses the `databricks-sql-connector`. Snowflake uses `snowflake-sqlalchemy`. SQLite uses the standard library.

**Precautions:**
- Database credentials (host, user, password, connection strings) are stored in the `user_data_sources` table as JSON. Ensure the SQLite file has restricted file permissions on the VM (`chmod 600`).
- Databricks and Snowflake connectors are imported lazily (inside the method) so the app starts without them if they are not installed. Only install their packages if you actually use those source types.
- Schema YAML files are stored per-user per-source alias. If you change the alias of a source, a new schema file is created — clean up old ones to avoid disk accumulation.
- Query results are not paginated at the database level. Very large result sets will be loaded entirely into memory. The row limit for federated queries is currently set to `None` (no limit) in `federated_query_engine.py`. Set a sensible limit for production to avoid memory issues.

---

#### `datasources.py` — Data Source API Router

FastAPI router mounted at `/api/datasources`. Handles all operations: connecting a new source, disconnecting, listing active sources, uploading a CSV/SQLite file, running a query, fetching schema, updating selected tables, and checking whether a user has any sources.

Maintains two in-memory per-user registries: `_user_sources` (a dict of alias → DatabaseManager) and `_user_engine` (the FederatedQueryEngine for that user). These are populated on login via hydration and updated as the user connects or disconnects sources.

**Precautions:**
- In-memory state is lost on server restart. The `hydration.py` module restores it from the database on startup. If the startup hydration fails for a user (e.g. their DB credentials expired), they will need to reconnect manually.
- The `_user_sources` and `_user_engine` dicts are module-level globals. In a multi-worker uvicorn setup, each worker has its own copy — state will not be shared. Use a single worker or move state to a shared store (Redis, etc.) before scaling horizontally.

---

#### `federated_query_engine.py` — Cross-Source Query Execution

Allows a single SQL query to join data from multiple connected sources — for example, joining a Snowflake data warehouse table with a locally uploaded CSV, or combining a PostgreSQL CRM with a Databricks data lake.

It works by fetching the relevant tables from each source into in-memory Pandas DataFrames, registering them as DuckDB views with alias-prefixed names, rewriting the SQL to use those view names, and executing the rewritten query inside DuckDB.

Only SELECT queries are permitted. No DDL or DML passes through. All fetched data is held in-process RAM only — nothing from remote sources is persisted to disk.

**Precautions:**
- For large tables, fetching entire tables into RAM for federation can be expensive. The `FEDERATION_ROW_LIMIT` constant controls this. Currently set to `None` (no limit). Set a cap appropriate to your VM's available memory.
- Cross-source joins require that the SQL uses the `alias.table` format so the engine knows which source each table belongs to.

---

#### `sql_generator.py` — Natural Language to SQL

Uses OpenAI GPT-4o to convert the user's plain English question into a SQL query. The schema YAML for the connected source(s) is passed as context so the model knows the exact table names, column names, and data types.

Also handles follow-up query context — if the user's question is a follow-up to a previous one, the previous question and SQL are included to help the model disambiguate.

**Precautions:**
- SQL generation quality depends entirely on the quality of the schema context. If column names are cryptic (`col_001`, `actv_dt`), the model will struggle. Use the column metadata enhancement feature in the Data Sources page to add business descriptions to columns.
- The generated SQL is not sanitised for injection — it is always executed against read-only connections where possible. Ensure database users used for connections have SELECT-only privileges.
- Token costs scale with schema size. Very large schemas (hundreds of tables, thousands of columns) should use the `selected_tables` feature to limit the context sent to the model.

---

#### `analysis_engine.py` — AI Summary Generation

Takes the query results (as a DataFrame) and the original question, and generates a natural language summary using GPT-4o. The summary is tailored based on the user's selected persona:

- **Executive** — strategic language, KPI focus, board-ready, no jargon
- **Sales Manager** — team metrics, target attainment, territory comparisons, outlier flags
- **Field Rep** — account-level detail, HCP engagement, actionable for next customer visit
- **Analyst** — statistical depth, methodology notes, data quality caveats

Also generates follow-up question suggestions based on the current result to guide the user's next query.

**Precautions:**
- Each query generates one OpenAI API call for summary + follow-ups combined. Monitor API costs as usage scales.
- The model used is `gpt-4o`. If cost is a concern, this can be changed to `gpt-4o-mini` in the `__init__` method of `AnalysisEngine`.
- Currently each query is treated as standalone — prior conversation context is intentionally not passed (see the commented-out block). This is a deliberate design choice but means the AI cannot refer back to earlier answers in the same chat.

---

#### `visualization_engine.py` — Automatic Chart Generation

Analyses the query result DataFrame and the original question to determine the most appropriate chart type, then generates a Plotly chart configuration. Supports metric cards (single values), bar charts, line charts, pie charts, scatter plots, and multi-series charts.

Chart type selection is heuristic-based: date columns trigger time-series charts, categorical columns with counts trigger bar charts, single-value results trigger metric cards, and so on.

**Precautions:**
- Chart generation is best-effort — for ambiguous result shapes it may produce a suboptimal chart type. The data table is always shown alongside the chart so users always have access to raw results.
- The chart colour palette is hardcoded to match the ConvergeAI frontend theme. If the frontend theme changes, update `CHART_COLORS` in this file.

---

#### `document_manager.py` — Document Upload and Extraction

Handles file uploads from users — PDF, DOCX, and TXT. Extracts text content using `pdfplumber` (PDF) and `python-docx` (DOCX), stores file metadata in a JSON manifest, and saves files to `backend/uploads/`.

Extracted text is passed to `rag_engine.py` for embedding and indexing.

**Precautions:**
- Uploaded files are stored on the VM filesystem under `backend/uploads/`. This directory is gitignored. Back it up as part of VM maintenance.
- There is currently no file size limit enforced at the application layer. Very large PDFs will slow down text extraction and embedding. Consider adding a file size check before processing.
- Scanned PDFs (image-only, no embedded text) will extract empty text. `pdfplumber` does not perform OCR. Add `pytesseract` or a similar library if OCR support is needed.

---

#### `rag_engine.py` — Retrieval-Augmented Generation

Embeds uploaded documents into a ChromaDB vector store using the `all-MiniLM-L6-v2` sentence transformer model. At query time, retrieves the most relevant document chunks based on semantic similarity to the user's question, and injects them into the SQL generation prompt as business context.

This allows users to ask questions that reference information in documents — for example, a question about a product may be enriched with context from a product specification PDF uploaded to the knowledge base.

**Precautions:**
- ChromaDB stores its vector index on disk (default: `backend/data/chroma/`). Back this directory up alongside `users.db`.
- The embedding model (`all-MiniLM-L6-v2`) is downloaded from HuggingFace on first run — approximately 90MB. Ensure the VM has internet access on first startup, or pre-download the model.
- All users currently share the same ChromaDB collection. There is no per-user isolation of document embeddings. A document uploaded by one user can contribute context to another user's queries. Implement per-user collections if document isolation is required.

---

#### `hydration.py` — Session State Restoration

On server startup (`hydrate_all_users`) and on each individual user login (`hydrate_user`), this module reads saved data source configurations from the SQLite database and re-creates the in-memory `DatabaseManager` and `FederatedQueryEngine` instances for each user.

Without this, every server restart would require all users to reconnect their data sources manually.

**Precautions:**
- If a data source credential has expired or the remote database is unreachable at startup, that source will fail to hydrate and is skipped. The user will need to reconnect it manually. Errors are logged but do not block other sources from loading.
- Hydration happens synchronously on startup. A large number of users with many sources will increase startup time noticeably.

---

### Frontend

---

#### `App.tsx` — Auth State Machine

The root component manages a five-state auth flow: `checking` (calling `/api/auth/me`) → `unauthenticated` (redirect to login) → `select_persona` (first login, no persona set) → `checking_schema` (checking if data sources exist) → `authenticated` (full app rendered).

Also provides `SessionContext` — a React context carrying the current user, all chat sessions, and global actions (new session, switch session, logout, update persona) — to every component in the tree.

**Precautions:**
- If `/api/auth/me` returns 401, the app redirects to `/auth/login` which triggers the SSO flow. In local dev this redirect requires the Vite proxy to have `/auth` mapped to the backend (see `vite.config.ts`).
- The logout handler (`handleLogout`) redirects to `https://a2.circulants.ai/` after clearing the session. If A2's URL changes, update this hardcoded value.

---

#### `KnowledgeBasePage.tsx` — Business Rules + Power BI

This page serves two purposes. First, it is the business rules and knowledge base editor — users can add plain-English rules and context that get injected into the SQL generation process (e.g. "Revenue is always reported net of returns"). Second, it hosts the **Power BI dashboard embedding** feature.

Power BI integration currently works as follows: users provide their Azure credentials (client ID, secret, tenant ID, workspace ID, report ID, dataset ID), the backend generates an embed token via the Power BI REST API, and the dashboard is rendered inline using the `powerbi-client` library.

**This feature is currently display-only.** The embedded dashboard is live and interactive (filters, drill-downs work within Power BI's own UI), but natural language querying of the dashboard data via ConvergeAI is in development.

**Precautions:**
- Power BI embed tokens are short-lived. If the dashboard goes blank after some time, the token has expired — the user needs to re-enter credentials or the token refresh mechanism needs to be implemented.
- Azure client secrets used for Power BI are passed to the backend via API call and used to generate the embed token. They are not stored. Ensure HTTPS is enforced end to end.
- The Power BI querying feature (natural language → dashboard data) is in progress. Do not expose this as production-ready to clients yet.

---

#### `config.ts` — API Base URL

`API_BASE` is an empty string in development (all requests go through Vite's proxy to the local backend) and is set to the backend URL in production via the `VITE_API_URL` environment variable.

In production, Nginx routes `/api` and `/auth` to the backend, so `VITE_API_URL` is typically left unset and `API_BASE` remains empty — the same origin handles both frontend and backend.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key. Used by SQL generator and analysis engine. |
| `JWT_SHARED_SECRET` | Yes | Must match Platform A2 exactly. Used to validate SSO tokens. |
| `A2_URL` | Yes | Platform A2 base URL. Used for SSO redirects. |
| `CAI_URL` | Yes | ConvergeAI backend public URL. Used in SSO token redirect. |
| `FRONTEND_URL` | Yes | ConvergeAI frontend URL. Backend redirects here after login. |
| `CORS_ORIGINS` | Yes | Comma-separated list of allowed origins for CORS. |
| `COOKIE_DOMAIN` | Yes | `.circulants.ai` in prod, blank locally. |
| `NODE_ENV` | Yes | `production` or `development`. Controls secure cookie flag. |
| `ALLOW_DEV_LOGIN` | Dev only | Set to `true` to enable the dev login bypass. Never in prod. |
| `AUTO_CONNECT_DB` | No | `true` to auto-connect a default SQLite DB on startup. |
| `DATABASE_PATH` | No | Path to a default SQLite DB. Used only if `AUTO_CONNECT_DB=true`. |

---

## Things to Be Careful About

**Never commit `.env`** — it contains API keys and shared secrets. It is gitignored. On the VM, ensure it has `chmod 600` permissions.

**Back up the `data/` directory** — `users.db` contains all user accounts, sessions, saved data source configurations, and chat history. `chroma/` contains the document vector index. Neither is in Git. If the VM is rebuilt without a backup, all of this is lost.

**SQLite is single-writer** — it works well for the current deployment but will become a bottleneck under high concurrency or with multiple uvicorn workers. Migrate to PostgreSQL before scaling.

**In-memory state per worker** — connected data sources live in module-level dicts in `datasources.py`. Running multiple uvicorn workers (via `--workers N`) will result in users losing their in-memory state when a request is routed to a different worker. Run with a single worker unless this is addressed.

**Power BI credentials are not stored** — they are entered each session and used to generate a one-time embed token. If your organisation requires persistent dashboard access, implement server-side token caching or a credential store.

**RAG document isolation** — all users' documents are currently indexed in the same ChromaDB collection. A document uploaded by one user can influence another user's query context. If user-level document privacy is required, implement per-user ChromaDB collections keyed by `user_id`.

**SQL injection surface** — generated SQL is executed against connected databases. Database users provided during source connection should have SELECT-only privileges. Never connect ConvergeAI using an admin or write-capable database credential.
