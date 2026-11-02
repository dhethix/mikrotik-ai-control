<div align="center">

# MikroTik AI Control

**AI agent + web dashboard to operate MikroTik RouterOS via REST API**

[![Docker Hub](https://img.shields.io/badge/Docker%20Hub-dhethix%2Fmikrotik--ai--control-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/dhethix/mikrotik-ai-control)
[![Platform](https://img.shields.io/badge/platform-linux%2Famd64%20%7C%20linux%2Farm64-blue)](https://hub.docker.com/r/dhethix/mikrotik-ai-control/tags)
[![UI](https://img.shields.io/badge/UI-EN%20%2F%20ID-7c5cff)](#)
[![License](https://img.shields.io/badge/license-Community-gray)](#disclaimer)

[Docker Hub](https://hub.docker.com/r/dhethix/mikrotik-ai-control) · [Tags](https://hub.docker.com/r/dhethix/mikrotik-ai-control/tags) · [OpenRouter](https://openrouter.ai) · [Groq](https://console.groq.com)

</div>

---

## What it is

**MikroTik AI Control** is a containerized assistant that connects to your **real MikroTik router** over HTTPS REST. You chat in a dark, admin-style web UI; the agent calls RouterOS tools (resources, interfaces, PPPoE, containers, and more) with **dry-run** and **confirm** safety by default.

| Layer | Stack |
|--------|--------|
| UI | React + Vite (English / Indonesian) |
| API | FastAPI + SQLite sessions & settings |
| LLM | **OpenRouter** and/or **Groq** (free-tier capable models) |
| Router | MikroTik RouterOS REST (`/rest/...`) |

> **Not** an official MikroTik, OpenRouter, or Groq product. Community project — use at your own risk.

---

## Features

- **Web dashboard** — chat, sessions, live metrics (CPU, RAM, PPPoE, throughput)
- **Dual providers** — OpenRouter + Groq; switch model in Settings
- **EN / ID UI** — language stored in SQLite; survives container restart
- **Safety** — Dry-run & Confirm (default **ON**)
- **Persistent data** — sessions, API keys, router credentials, UI language under `/app/data`
- **No secrets in the image** — configure via UI (or optional env)
- **Multi-arch images** — `linux/amd64` and `linux/arm64`

---

## Quick start (Docker Hub)

```bash
docker pull dhethix/mikrotik-ai-control:latest

docker run -d --name mikrotik-ai \
  -p 8000:8000 \
  -v mikrotik-ai-data:/app/data \
  dhethix/mikrotik-ai-control:latest
```

Open **http://localhost:8000**

1. Click **Settings** (gear)
2. Paste an **OpenRouter** or **Groq** API key → validate
3. Enter MikroTik **IP / user / password**
4. **Save & apply** — start a **New Session** and chat

**Docker Hub:** [https://hub.docker.com/r/dhethix/mikrotik-ai-control](https://hub.docker.com/r/dhethix/mikrotik-ai-control)

---

## docker-compose

```yaml
services:
  mikrotik-ai:
    image: dhethix/mikrotik-ai-control:latest
    ports:
      - "8000:8000"
    volumes:
      - mikrotik-ai-data:/app/data
    restart: unless-stopped

volumes:
  mikrotik-ai-data:
```

---

## Image tags

| Tag | Meaning |
|-----|---------|
| `latest` | Newest stable multi-arch |
| `0.2.0` | Versioned multi-arch release |
| `0.2.0-amd64` | AMD64 only (PC, CHR, many x86 containers) |
| `0.2.0-arm64` | ARM64 only (Pi, ARM VPS, some ARM routers) |

```bash
docker pull dhethix/mikrotik-ai-control:0.2.0
docker pull dhethix/mikrotik-ai-control:0.2.0-amd64
docker pull dhethix/mikrotik-ai-control:0.2.0-arm64
```

Browse tags: [hub.docker.com/r/dhethix/mikrotik-ai-control/tags](https://hub.docker.com/r/dhethix/mikrotik-ai-control/tags)

---

## API keys (free-tier friendly)

### Groq (fast)

1. [console.groq.com/keys](https://console.groq.com/keys)
2. **Create API Key** → copy `gsk_...`

### OpenRouter (many models)

1. [openrouter.ai/keys](https://openrouter.ai/keys)
2. **Create Key** → copy `sk-or-v1-...`

Paste the key in the web **Settings** panel (preferred over baking env into the image).

---

## RouterOS preparation

```routeros
/ip service enable www-ssl
/user group add name=ai-group policy=api,read,write,test,policy,password
/user add name=ai-agent group=ai-group password="STRONG_PASSWORD"
```

Point the agent at the router IP, that user, and password in **Settings**.

---

## Run on MikroTik container

1. Pull a **single-arch** tag that matches the router (`-amd64` or `-arm64`).
2. Save a Docker V2 archive (must include `manifest.json`):

```bash
docker pull dhethix/mikrotik-ai-control:0.2.0-amd64
docker save dhethix/mikrotik-ai-control:0.2.0-amd64 -o mikrotik-agent.tar
```

3. Upload the tar, create the container, **mount** host storage to **`/app/data`**, publish port **8000**.

---

## Optional environment

Defaults are safe for first boot. Prefer **Settings** in the UI.

| Variable | Default | Notes |
|----------|---------|--------|
| `MODE` | `http` | HTTP + web UI |
| `HTTP_HOST` | `0.0.0.0` | Listen address |
| `HTTP_PORT` | `8000` | Listen port |
| `VERIFY_SSL` | `false` | Router TLS verify |
| `MIKROTIK_PORT` | `443` | REST port |
| `DRY_RUN` | `true` | Simulate writes |
| `REQUIRE_CONFIRMATION` | `true` | Confirm before write |
| `DATA_DIR` | `/app/data` | Persistent volume path |

---

## Build from source

```bash
# UI
cd web && npm install && npm run build   # or build-ui.bat / build-ui.sh

# Image (local)
docker build --platform linux/amd64 -t mikrotik-ai-control:local .

# Multi-arch push to Hub (maintainers)
export HUB_USER=dhethix
export IMAGE=mikrotik-ai-control
export VERSION=0.2.0
bash build-push-hub.sh
```

---

## Project layout

```text
├── agent.py           # FastAPI agent, tools, SQLite
├── Dockerfile
├── static/            # Built web UI (served by agent)
├── web/               # Vite/React source
├── build-export.sh    # Flat image tar for RouterOS
├── build-push-hub.sh  # multi-arch Hub publish
├── DOCKERHUB.md       # Hub full description
└── data/              # runtime DB (volume mount in production)
```

---

## Safety & disclaimer

- Dry-run and confirm reduce accidental writes; turn them off only when you understand the impact.
- API keys and router passwords live in the **volume** database — protect `/app/data`.
- With safety off, the agent can change live RouterOS configuration.
- **No warranty.** Community software. Not affiliated with MikroTik, OpenRouter, or Groq.

---

## Links

| Resource | URL |
|----------|-----|
| **Docker Hub** | [https://hub.docker.com/r/dhethix/mikrotik-ai-control](https://hub.docker.com/r/dhethix/mikrotik-ai-control) |
| Tags | [hub.docker.com/r/dhethix/mikrotik-ai-control/tags](https://hub.docker.com/r/dhethix/mikrotik-ai-control/tags) |
| OpenRouter | [openrouter.ai](https://openrouter.ai) |
| Groq | [console.groq.com](https://console.groq.com) |

---

<div align="center">

**Pull · Configure · Chat with your router**

```bash
docker pull dhethix/mikrotik-ai-control:latest
```

</div>
