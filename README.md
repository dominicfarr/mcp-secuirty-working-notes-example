# MCP Secuirty Reference

[![tests](https://github.com/dominicfarr/mcp-secuirty-working-notes-example/actions/workflows/tests.yml/badge.svg)](https://github.com/dominicfarr/mcp-secuirty-working-notes-example/actions/workflows/tests.yml)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=dominicfarr_mcp-secuirty-working-notes-example&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=dominicfarr_mcp-secuirty-working-notes-example&metric=coverage)](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=dominicfarr_mcp-secuirty-working-notes-example&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=dominicfarr_mcp-secuirty-working-notes-example&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example)

A small, runnable reference for where security lives in MCP: what the client decides, what the server must check, and what changes when a model makes the calls. Client and server run locally over stdio; every guarantee has a test named after it.

Write-up: [MCP Security series](https://www.domfarr.com/2026/10/01/mcp-security.html) (Part 1), and [MCP stopped waiting](https://www.domfarr.com/2026/10/19/mcp-security-mrtr.html) on the move to MCP 2026-07-28.

![MCP Security](https://github.com/dominicfarr/dominicfarr.github.io/blob/main/assets/mcp-security.svg?raw=true)

## The parts

The project is organised by threat rather than by MCP primitive:

| Part | What it shows | Read |
|---|---|---|
| **1. Deterministic controls** (`client/app.py`, no LLM) | Tool permission policies, binding human-in-the-loop approval, audit logs on both sides, server-side path validation, the allowed folder, and elicitation over multi round-trip requests | [docs/part-1-controls.md](docs/part-1-controls.md) |
| **2. Model in the loop** (`client/host.py`) | The same client and server with an LLM making the calls. With its defences off it is a baseline: a planted instruction in a file the model reads steers it into a write nobody asked for. Two client-side rules stop that: changes ask once untrusted content is in the chat, and a call you said no to isn't retried in the same turn. Tool poisoning and changed tool definitions are future work | [docs/part-2-host.md](docs/part-2-host.md) |
| **3. Remote-server authorization** (separate repo, not started) | OAuth and token handling for MCP servers over HTTP | |

## Quick start

```bash
python3 -m venv mcp_security_env && source mcp_security_env/bin/activate
pip install -r requirements-dev.txt

python3 client/app.py server/server.py     # Part 1: http://127.0.0.1:7863
python3 client/host.py server/server.py    # Part 2: http://127.0.0.1:7864 (needs a model, below)
pytest                                     # every guarantee, about 1 minute
```

Part 2's model defaults to a local one, free: `brew install ollama`, start it, then `ollama pull qwen2.5:7b`. OpenAI and Anthropic work too; see [Part 2](docs/part-2-host.md#running-it).

## More

- [Development](docs/development.md): setup details, tests and CI, the lock file, project layout, and protocol versions (this repo speaks MCP **2026-07-28** only).
- Design specs and implementation plans for each change: [docs/superpowers/](docs/superpowers/).
