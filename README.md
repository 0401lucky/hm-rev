<div align="center">

# `hm-api` ⚡

**DevEco Code OpenAI-compatible API CLI**

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![uv](https://img.shields.io/badge/uv-powered-8A2BE2?logo=astral)](https://docs.astral.sh/uv/)
[![License](https://img.shields.io/badge/License-AGPL--3.0%20%2B%20Non--Commercial-red)](LICENSE)

<p align="center">
  <strong><code>login</code> · <code>serve</code> · <code>status</code></strong>
</p>

</div>

---

## ✨ Features

- **OpenAI-compatible** — `/v1/models` and `/v1/chat/completions`
- **Web panel** — authorize DevEco login from the browser dashboard
- **Streaming & non-streaming** — automatic SSE forwarding and `/no-stream` fallback
- **Built-in auth** — optional `--key` API key protection
- **Proxy support** — pass upstream HTTP/HTTPS proxy to `httpx`
- **Encrypted credentials** — local token stored safely under `./cred`
- **Async powered** — FastAPI + `httpx` + `uvloop`

---

## 🚀 Quick Start

```bash
# 1. clone
git clone https://github.com/CuzTeam/hm-rev.git
cd hm-rev

# 2. install dependencies (requires uv)
uv sync

# 3. start the API and web panel
uv run hm-api serve --host 0.0.0.0 --port 8000 --key your-secret-key

# 4. open the web panel and authorize DevEco login
# http://localhost:8000
```

> If you prefer not to open the browser automatically, use `uv run hm-api login --no-browser` and follow the printed URL.

---

## 🐳 Docker

```bash
# Start with docker compose
HM_API_KEY=your-secret-key docker compose up --build

# Or build and run directly
docker build -t hm-api .
docker run --rm -p 8000:8000 -v hm-api-cred:/app/cred \
  -e HM_API_KEY=your-secret-key hm-api
```

Open `http://localhost:8000`, then click `授权登录`.

The OAuth callback uses the browser-visible port. If you map a different host
port, such as `8080:8000`, open `http://localhost:8080` for login.

---

## ☁️ Zeabur

Deploy this repository with the Dockerfile, expose port `8000`, and set:

```env
HM_API_KEY=your-secret-key
HM_PROXY=
```

Add a persistent volume at:

```text
/app/cred
```

The volume keeps encrypted credentials across restarts and redeploys.

For cloud login, keep a local callback bridge running before clicking
`授权登录` on the Zeabur web panel:

```bash
uv run hm-api bridge --target https://your-app.zeabur.app --port 8000
```

DevEco redirects OAuth callbacks to `localhost`. The bridge captures that local
callback and forwards the token to your Zeabur service. If the failed callback
URL contains `tempToken`, you can also paste it into `回调 URL / tempToken` and
click `导入授权`.

---

## 📖 Commands

<div align="center">

| Command | Description |
|---------|-------------|
| `hm-api login [--proxy PROXY] [--no-browser]` | Authenticate with DevEco Code |
| `hm-api serve [--host HOST] [--port PORT] [--proxy PROXY] [--key KEY]` | Start the API server and web panel |
| `hm-api bridge --target URL [--port PORT]` | Forward local OAuth callback to a cloud deployment |
| `hm-api status` | Show current login status |

</div>

### `serve` options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Bind host |
| `--port` | `8000` | Bind port |
| `--proxy` | — | Upstream HTTP/HTTPS proxy |
| `--key` | — | API key for client authentication (omit to disable) |

`serve` also accepts `HM_HOST`, `HM_PORT`, `HM_PROXY`, and `HM_API_KEY`.

---

## 🔌 Usage Example

```bash
# list available models
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer your-secret-key"

# chat completion (non-streaming)
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "GLM-5.1",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 🛡️ Credentials

All authentication data is encrypted and stored locally under `./cred`.

<div align="center">

⚠️ **Never commit the `cred/` directory.** It is already ignored by `.gitignore`.

</div>

---

## 📦 Project Structure

```text
hm-rev/
├── src/hm_api/          # CLI and server source code
│   ├── cli.py           # Typer CLI entry
│   ├── server.py        # FastAPI OpenAI-compatible proxy
│   ├── login.py         # DevEco OAuth login flow
│   ├── crypto.py        # Credential encryption
│   ├── config.py        # Constants and defaults
│   └── web/             # Browser authorization panel
├── Dockerfile           # Container image for API + panel
├── docker-compose.yml   # Local Docker deployment
├── pyproject.toml       # Project metadata and dependencies
├── uv.lock              # Locked dependency tree
├── LICENSE              # AGPL-3.0 + Non-Commercial clause
└── README.md            # This file
```

---

## 📜 License

<div align="center">

This project is licensed under **AGPL-3.0 with additional Non-Commercial restrictions**.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

</div>

You may use, modify, and distribute this software **for non-commercial purposes only**.
Commercial use — including but not limited to selling, offering paid services, or
incorporating it into commercial products — is **strictly prohibited**.

See [LICENSE](LICENSE) for full terms.

---

<div align="center">

Made with 💜 by <a href="https://github.com/CuzTeam">CuzTeam</a>

and Thanks to the Linux.do community

</div>
