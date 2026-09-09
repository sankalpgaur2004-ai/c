# ConvergeAI — Deployment & CI/CD Guide

This guide covers deploying ConvergeAI to a Linux VM (Ubuntu 22.04+) and setting up the GitLab CI/CD pipeline for automated deployments.

---

## Architecture Overview

On the VM, ConvergeAI runs as two processes behind Nginx:

- **Backend** — FastAPI app served by Uvicorn on port `8002`, managed by systemd
- **Frontend** — Pre-built React static files served by Nginx directly
- **Nginx** — Reverse proxy on port 443 (HTTPS), routes `/api` and `/auth` to the backend, serves frontend for everything else

---

## Part 1 — Manual First-Time VM Setup

Do this once when setting up a new VM. After this, all subsequent deploys are handled by the CI/CD pipeline.

### 1.1 — System dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip nginx nodejs npm git rsync
```

Verify versions:
```bash
python3.11 --version   # 3.11+
node --version         # 18+
npm --version
```

### 1.2 — Create deployment directory

```bash
sudo mkdir -p /var/www/convergeai/backend
sudo mkdir -p /var/www/convergeai/frontend
sudo mkdir -p /var/www/convergeai/backend/data
sudo mkdir -p /var/www/convergeai/backend/uploads
sudo chown -R azureuser:azureuser /var/www/convergeai
```

### 1.3 — Create the `.env` file on the VM

This file is **never committed to Git** — it must be created manually on the VM:

```bash
sudo nano /var/www/convergeai/.env
```

Paste and fill in your values:

```dotenv
# AI
OPENAI_API_KEY=sk-...

# Auth (must match Platform A2's JWT_SHARED_SECRET exactly)
JWT_SHARED_SECRET=your-shared-secret-here
COOKIE_DOMAIN=.circulants.ai
NODE_ENV=production

# URLs
A2_URL=https://a2.circulants.ai
CAI_URL=https://cai-demo.circulants.ai
FRONTEND_URL=https://cai-demo.circulants.ai
CORS_ORIGINS=https://cai-demo.circulants.ai

# Optional default DB
AUTO_CONNECT_DB=false
DATABASE_PATH=

# Never set this in production
# ALLOW_DEV_LOGIN=true
```

```bash
sudo chmod 600 /var/www/convergeai/.env
sudo chown azureuser:azureuser /var/www/convergeai/.env
```

### 1.4 — Set up Python virtual environment

```bash
cd /var/www/convergeai/backend
python3.11 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

### 1.5 — Create the systemd service

```bash
sudo nano /etc/systemd/system/convergeai-backend.service
```

Paste:

```ini
[Unit]
Description=ConvergeAI Backend
After=network.target

[Service]
Type=simple
User=azureuser
Group=azureuser
WorkingDirectory=/var/www/convergeai/backend
ExecStart=/var/www/convergeai/backend/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8002
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable convergeai-backend.service
sudo systemctl start convergeai-backend.service
sudo systemctl status convergeai-backend.service
```

### 1.6 — Configure Nginx

```bash
sudo nano /etc/nginx/sites-available/convergeai
```

Paste:

```nginx
# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name cai-demo.circulants.ai;
    return 301 https://$host$request_uri;
}

# Main HTTPS server
server {
    listen 443 ssl;
    server_name cai-demo.circulants.ai;

    ssl_certificate     /etc/letsencrypt/live/cai-demo.circulants.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cai-demo.circulants.ai/privkey.pem;

    # Frontend — serve React build
    root /var/www/convergeai/frontend;
    index index.html;

    # Backend — proxy /api and /auth to FastAPI
    location /api/ {
        proxy_pass         http://127.0.0.1:8002;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    location /auth/ {
        proxy_pass         http://127.0.0.1:8002;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    # Frontend — SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/convergeai /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 1.7 — SSL Certificate (Let's Encrypt)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d cai-demo.circulants.ai
```

---

## Part 2 — GitLab CI/CD Pipeline Setup

The pipeline automates all deployments — every push to `main` deploys automatically.

### 2.1 — Install GitLab Runner on the VM

```bash
curl -L https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh | sudo bash
sudo apt install -y gitlab-runner
```

### 2.2 — Register the Runner

Go to your GitLab project → **Settings → CI/CD → Runners** → click **New project runner**.

Copy the registration token, then on the VM:

```bash
sudo gitlab-runner register
```

When prompted:

- **GitLab URL:** `https://git.circulants.com`
- **Registration token:** paste from GitLab
- **Description:** `azure-vm-runner`
- **Tags:** `azure`
- **Executor:** `shell`

Verify the runner is active:

```bash
sudo gitlab-runner status
sudo gitlab-runner list
```

### 2.3 — Grant the Runner sudo permissions

The pipeline needs to run systemctl and rsync as root. Add the runner user to sudoers:

```bash
sudo visudo
```

Add at the bottom:

```
gitlab-runner ALL=(ALL) NOPASSWD: ALL
azureuser ALL=(ALL) NOPASSWD: ALL
```

### 2.4 — The `.gitlab-ci.yml` file

This file lives in the repo root and defines the pipeline. Here is the full production pipeline for ConvergeAI:

```yaml
# ConvergeAI — GitLab CI/CD Pipeline
# Target: Azure VM via Shell Runner
# Deploys to: https://cai-demo.circulants.ai

default:
  tags:
    - azure

variables:
  DEPLOY_DIR: "/var/www/convergeai"
  VM_USER: "azureuser"

stages:
  - deploy

deploy_production:
  stage: deploy
  environment:
    name: production
    url: https://cai-demo.circulants.ai
  script:
    # 1. Sync backend files
    - echo '[DEPLOY] Syncing backend files...'
    - sudo rsync -az --delete
        --exclude='venv/'
        --exclude='__pycache__/'
        --exclude='data/'
        --exclude='uploads/'
        --exclude='.env'
        backend/ $DEPLOY_DIR/backend/

    # 2. Sync frontend build
    - echo '[DEPLOY] Syncing frontend build...'
    - sudo rsync -az --delete frontend/dist/ $DEPLOY_DIR/frontend/

    # 3. Inject secrets from GitLab CI variables into .env
    - echo '[DEPLOY] Writing environment file...'
    - |
      sudo tee $DEPLOY_DIR/.env > /dev/null << EOF
      OPENAI_API_KEY=$OPENAI_API_KEY
      JWT_SHARED_SECRET=$JWT_SHARED_SECRET
      COOKIE_DOMAIN=$COOKIE_DOMAIN
      NODE_ENV=production
      A2_URL=$A2_URL
      CAI_URL=$CAI_URL
      FRONTEND_URL=$FRONTEND_URL
      CORS_ORIGINS=$CORS_ORIGINS
      AUTO_CONNECT_DB=$AUTO_CONNECT_DB
      DATABASE_PATH=$DATABASE_PATH
      PORT=8002
      EOF
    - sudo chmod 600 $DEPLOY_DIR/.env
    - sudo chown $VM_USER:$VM_USER $DEPLOY_DIR/.env

    # 4. Install/update Python dependencies
    - echo '[DEPLOY] Installing Python dependencies...'
    - cd $DEPLOY_DIR/backend
    - sudo venv/bin/pip install --quiet --upgrade pip
    - sudo venv/bin/pip install --quiet -r requirements.txt
    - sudo chown -R $VM_USER:$VM_USER $DEPLOY_DIR/backend

    # 5. Restart backend service
    - echo '[DEPLOY] Restarting backend...'
    - sudo systemctl daemon-reload
    - sudo systemctl restart convergeai-backend.service
    - sleep 3
    - sudo systemctl is-active convergeai-backend.service

    # 6. Reload nginx
    - echo '[DEPLOY] Reloading nginx...'
    - sudo nginx -t
    - sudo systemctl reload nginx

    - echo '[DEPLOY] Done! https://cai-demo.circulants.ai'

  only:
    - main
```

### 2.5 — Set CI/CD Variables in GitLab

Go to your GitLab project → **Settings → CI/CD → Variables** and add the following. Mark sensitive ones as **Masked**:

| Variable | Description | Masked |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key | Yes |
| `JWT_SHARED_SECRET` | Shared secret with Platform A2 | Yes |
| `COOKIE_DOMAIN` | `.circulants.ai` | No |
| `A2_URL` | `https://a2.circulants.ai` | No |
| `CAI_URL` | `https://cai-demo.circulants.ai` | No |
| `FRONTEND_URL` | `https://cai-demo.circulants.ai` | No |
| `CORS_ORIGINS` | `https://cai-demo.circulants.ai` | No |
| `AUTO_CONNECT_DB` | `false` | No |
| `DATABASE_PATH` | leave blank unless using a default SQLite DB | No |

### 2.6 — Build the frontend before deploy

The pipeline syncs `frontend/dist/` to the VM, so you must build the frontend before pushing. Either:

**Option A — Build locally and commit `dist/`**
```bash
cd frontend
npm run build
git add dist/
git commit -m "build: update frontend dist"
git push origin main
```

**Option B — Add a build stage to the pipeline**

Add a `build` stage before `deploy` in `.gitlab-ci.yml`:

```yaml
stages:
  - build
  - deploy

build_frontend:
  stage: build
  tags:
    - azure
  script:
    - cd frontend
    - npm install
    - npm run build
  artifacts:
    paths:
      - frontend/dist/
    expire_in: 1 hour
  only:
    - main
```

Option B is cleaner and recommended for production.

---

## Part 3 — Useful VM Commands

### Check backend status
```bash
sudo systemctl status convergeai-backend.service
```

### View backend logs (live)
```bash
sudo journalctl -u convergeai-backend.service -f
```

### Restart backend manually
```bash
sudo systemctl restart convergeai-backend.service
```

### Check nginx status
```bash
sudo systemctl status nginx
sudo nginx -t
```

### View nginx access logs
```bash
sudo tail -f /var/log/nginx/access.log
```

### Check what's running on port 8002
```bash
sudo lsof -i :8002
```

### Renew SSL certificate
```bash
sudo certbot renew
```

---

## Part 4 — Rollback

If a deployment breaks production, roll back by reverting the last commit and pushing:

```bash
git revert HEAD
git push origin main
```

This triggers the pipeline again with the previous code.

Alternatively, on the VM directly:

```bash
# Restart the previous version if code is already on disk
sudo systemctl restart convergeai-backend.service
```
