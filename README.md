<div align="center">

# 🤖 MikroTik AI Control

**AI agent and web dashboard to operate MikroTik RouterOS via REST API, using OpenRouter and/or Groq.**

[![Docker Hub](https://img.shields.io/badge/Docker%20Hub-dhethix%2Fmikrotik--ai--control-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/dhethix/mikrotik-ai-control)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white)](#requirements)
[![UI Language](https://img.shields.io/badge/UI-EN%20%2F%20ID-7c5cff)](#features)
[![License](https://img.shields.io/badge/license-Community-gray)](#disclaimer)

[Docker Hub](https://hub.docker.com/r/dhethix/mikrotik-ai-control) · [Image Tags](https://hub.docker.com/r/dhethix/mikrotik-ai-control/tags) · [OpenRouter](https://openrouter.ai/keys) · [Groq Console](https://console.groq.com/keys)

</div>

---

## 📸 Dashboard Preview

<div align="center">
  <img src="https://raw.githubusercontent.com/dhethix/mikrotik-ai-control/main/Dashboard.jpg" alt="MikroTik AI Control Dashboard Preview" width="100%">
</div>

---

## 📁 Repository contents

| File / folder | Role |
|---------------|------|
| `agent.py` | FastAPI backend, LLM tools, SQLite sessions & settings |
| `static/` | Web UI (built assets served by the agent) |
| `requirements.txt` | Python dependencies |
| `start.sh` | Simple process entry (`python -u agent.py`) |
| `env.example` | Optional environment variable sample |

> ℹ️ This repo is **runtime source** (backend + UI assets). It does not ship Docker Hub docs or a Vite app tree.

---

## 📋 Requirements

- **Python 3.12+** (or Docker image from Hub)
- **RouterOS** with REST (`www-ssl`) enabled and an API-capable user
- **API Key:** [OpenRouter](https://openrouter.ai/keys) and/or [Groq](https://console.groq.com/keys)

---

## 🚀 Run with the official image (recommended)

```bash
docker pull dhethix/mikrotik-ai-control:latest

docker run -d --name mikrotik-ai \
  -p 8000:8000 \
  -v mikrotik-ai-data:/app/data \
  dhethix/mikrotik-ai-control:latest
```

1. Open **http://localhost:8000**
2. Go to **Settings**
3. Enter your **API key** + **MikroTik host/user/password**
4. Click **Save**.

📌 **Available Image Tags:** `latest`, `0.2.0`, `0.2.0-amd64`, `0.2.0-arm64`  
👉 Browse tags: [hub.docker.com/r/dhethix/mikrotik-ai-control/tags](https://hub.docker.com/r/dhethix/mikrotik-ai-control/tags)

---

## 🐍 Run from this source (Python)

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Linux/macOS:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
# optional: copy env.example to .env and edit

# Run agent
chmod +x start.sh   # Linux/macOS
./start.sh          # or: python -u agent.py
```

* **Default:** HTTP UI on `0.0.0.0:8000`, persistent data saved under `./data` or `DATA_DIR`.
* **Important:** Mount/persist the data directory in production so sessions and settings survive container restarts.

---

## 🐳 Optional: Docker build yourself

A `Dockerfile` is **not** included in this repo (to keep the tree focused on application source). Here is a minimal example if you want to build locally:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt agent.py start.sh ./
COPY static ./static
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && pip install --no-cache-dir -r requirements.txt \
 && mkdir -p /app/data && chmod +x /app/start.sh
ENV MODE=http HTTP_HOST=0.0.0.0 HTTP_PORT=8000
CMD ["./start.sh"]
```

```bash
docker build -t mikrotik-ai-control:local .
docker run -d -p 8000:8000 -v mikrotik-ai-data:/app/data mikrotik-ai-control:local
```

### 📦 Optional: docker-compose

```yaml
services:
  mikrotik-ai:
    image: dhethix/mikrotik-ai-control:latest
    # or build: { context: . } if you added a Dockerfile
    ports:
      - "8000:8000"
    volumes:
      - mikrotik-ai-data:/app/data
    restart: unless-stopped

volumes:
  mikrotik-ai-data:
```

### 💾 Optional: export for RouterOS container

Use a **single-arch** Hub tag, then export:

```bash
docker pull dhethix/mikrotik-ai-control:0.2.0-amd64
docker save dhethix/mikrotik-ai-control:0.2.0-amd64 -o mikrotik-agent.tar
```

Import on the router; mount storage to **`/app/data`**, and publish port **8000**.  
*(For a flat single-layer export workflow, use the tooling from the full project release or Hub image as above.)*

### 📄 Optional: `.dockerignore`

Only relevant if you add a Dockerfile. Typical ignores:
```text
.git
.venv
data/
.env
*.md
```

---

## ✨ Features

- 💾 **SQLite Storage** — Sessions and settings saved in `/app/data` or `DATA_DIR`
- ⚡ **Dual AI Providers** — OpenRouter + Groq support
- 🛡️ **Safety Guard** — Dry-run & confirm safety mode
- 📊 **Live Metrics** — Real-time telemetry from the router
- 🌐 **Multi-language** — UI language toggle (EN / ID)

---

## ⚙️ Configuration

Prefer using the **Settings** panel in the Web UI (keys and router credentials are stored securely in the DB on the data volume).

**Optional Environment Variables** (see `env.example`):  
`MODE`, `HTTP_HOST`, `HTTP_PORT`, `VERIFY_SSL`, `MIKROTIK_PORT`, `DRY_RUN`, `REQUIRE_CONFIRMATION`, `DATA_DIR`, `API_TOKEN`.

---

## ⚠️ Disclaimer

Community project. Not affiliated with MikroTik, OpenRouter, or Groq. Use at your own risk.

---

## 🔗 Links

- **Docker Hub:** [hub.docker.com/r/dhethix/mikrotik-ai-control](https://hub.docker.com/r/dhethix/mikrotik-ai-control)
- **Image Tags:** [hub.docker.com/r/dhethix/mikrotik-ai-control/tags](https://hub.docker.com/r/dhethix/mikrotik-ai-control/tags)
