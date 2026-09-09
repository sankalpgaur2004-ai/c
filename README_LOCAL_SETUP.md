# ConvergeAI — Local Development Setup

This guide walks you through cloning the repo and running ConvergeAI locally on your machine.

---

## Prerequisites

Make sure you have the following installed before you begin:

- **Python 3.11+** — [python.org](https://www.python.org/downloads/)
- **Node.js 18+** — [nodejs.org](https://nodejs.org/)
- **Git** — [git-scm.com](https://git-scm.com/)
- Access to the GitLab repository at `https://git.circulants.com`
- An **OpenAI API key**

---

## Step 1 — Clone the Repository

```bash
git clone https://git.circulants.com/commercial-pocs/development/convergeai.git
cd convergeai
```

If you are on a branch other than main:

```bash
git checkout <branch-name>
```

---

## Step 2 — Backend Setup

### 2a. Create a virtual environment

```bash
cd backend
python -m venv venv
```

Activate it:

- **Windows:** `venv\Scripts\activate`
- **Mac/Linux:** `source venv/bin/activate`

### 2b. Install Python dependencies

```bash
pip install -r ../requirements.txt
```

> If you don't need Databricks or Snowflake connectors locally, you can skip those lines in `requirements.txt` to speed up installation.

### 2c. Create the backend `.env` file

Create a file named `.env` in the **project root** (not inside `backend/`):

```dotenv
# AI
OPENAI_API_KEY=sk-...

# Auth
JWT_SHARED_SECRET=your-shared-secret-here
ALLOW_DEV_LOGIN=true
COOKIE_DOMAIN=
NODE_ENV=development

# URLs
A2_URL=https://a2.circulants.ai
CAI_URL=http://localhost:5175
FRONTEND_URL=http://localhost:5175
CORS_ORIGINS=http://localhost:5175

# Optional — auto-connect a default SQLite DB on startup
AUTO_CONNECT_DB=false
DATABASE_PATH=
```

> `ALLOW_DEV_LOGIN=true` enables the local login bypass — see the **Local Login** section below. Never set this in production.

### 2d. Start the backend

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8005
```

You should see:
```
INFO:     Application startup complete.
```

---

## Step 3 — Frontend Setup

Open a new terminal from the project root:

### 3a. Install Node dependencies

```bash
cd frontend
npm install
```

### 3b. Start the frontend dev server

```bash
npm run dev
```

The frontend will start on `http://localhost:5175`.

> The Vite dev server proxies both `/api` and `/auth` routes to the backend at `http://127.0.0.1:8005` — you do not need to set `VITE_API_URL` locally.

---

## Step 4 — Local Login (Dev Bypass)

ConvergeAI uses Microsoft Entra SSO in production, which cannot work locally (the SAML callback URL is registered to the production domain only).

For local development, a dev login bypass is provided. Once both servers are running:

1. Open your browser and visit:
   ```
   http://localhost:5175/auth/dev-login
   ```

2. This creates a local session for `dev@local.com` and redirects you straight into the app.

3. You will be prompted to select a persona on first login — select one and you're in.

> You will need to repeat this step each time you restart the backend, as sessions are stored in SQLite and reset on restart. The frontend can hot-reload freely without re-logging in.

---

## Step 5 — Connect a Data Source

Once logged in, go to **Data Sources** in the sidebar and connect a database. For quick local testing, you can upload a `.sqlite` file directly.

---

## Project Structure Reference

See `README_PROJECT.md` for a full breakdown of every file and what it does.

---

## Common Issues

**Port already in use**

```bash
# Find and kill the process on port 8005
# Windows:
netstat -ano | findstr :8005
taskkill /PID <pid> /F

# Mac/Linux:
lsof -i :8005
kill -9 <pid>
```

**`ModuleNotFoundError` on startup**

Make sure your virtual environment is activated and you have run `pip install -r requirements.txt`.

**Cookie not being sent / 401 after dev login**

Make sure you are visiting `http://localhost:5175/auth/dev-login` (through the Vite proxy) and NOT `http://127.0.0.1:8005/auth/dev-login` directly. The cookie must be set on the same origin as the frontend.

**ChromaDB or sentence-transformers slow on first run**

The embedding model (`all-MiniLM-L6-v2`) is downloaded on first use — this is a one-time ~90MB download. Subsequent startups are instant.

---

## GitLab CI/CD — Local Notes

The `.gitlab-ci.yml` in this repo is configured for the production Azure VM deployment and will not run locally. For local dev, ignore it entirely — all you need is the `uvicorn` and `npm run dev` commands above.
