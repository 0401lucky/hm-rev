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

# 3. login via DevEco OAuth
uv run hm-api login

# 4. serve the OpenAI-compatible API
uv run hm-api serve --host 0.0.0.0 --port 8000 --key your-secret-key
```

> If you prefer not to open the browser automatically, use `uv run hm-api login --no-browser` and follow the printed URL.

---

## 📖 Commands

<div align="center">

| Command | Description |
|---------|-------------|
| `hm-api login [--proxy PROXY] [--no-browser]` | Authenticate with DevEco Code |
| `hm-api serve [--host HOST] [--port PORT] [--proxy PROXY] [--key KEY]` | Start the OpenAI-compatible proxy server |
| `hm-api status` | Show current login status |

</div>

### `serve` options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Bind host |
| `--port` | `8000` | Bind port |
| `--proxy` | — | Upstream HTTP/HTTPS proxy |
| `--key` | — | API key for client authentication (omit to disable) |

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
│   └── config.py        # Constants and defaults
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
