# Development

[← README](../README.md) · [Part 1: controls](part-1-controls.md) · [Part 2: model in the loop](part-2-host.md) · [Development](development.md)

## Setup

```bash
python3 -m venv mcp_security_env
source mcp_security_env/bin/activate
pip install -r requirements-dev.txt
git config core.hooksPath .githooks   # run lint and tests before every push
```

`requirements.txt` holds the runtime packages (`openai` is the Python client library `client/host.py` uses to talk to an OpenAI-compatible chat API; by default that is local Ollama, so no OpenAI account is needed); `requirements-dev.txt` adds `pytest`, `pylint` and `coverage`.

Upgrading an existing venv from fastmcp 2.x: fastmcp 4 ships its code in `fastmcp-slim`, and pip removes the old package's files after installing it, leaving `ImportError: cannot import name 'Context' from 'fastmcp'`. Repair it with `pip install --force-reinstall --no-deps fastmcp-slim==4.1.1`, or create a fresh venv.

CI installs from `requirements-dev.lock` instead: every package, including transitive ones, pinned with its hashes and installed only from wheels, so no package's setup script runs. After changing either requirements file, regenerate the lock (CI fails if a pinned version is missing from it, for example on a Dependabot PR):

```bash
uv pip compile --universal --generate-hashes --python-version 3.13 requirements-dev.txt -o requirements-dev.lock
```

## Tests

```bash
pytest                         # everything (about 1 minute)
pytest -m "not integration"    # only the tests that don't start a server (seconds)
```

The tests are grouped by security control (`tests/test_permissions.py`, `test_approval.py`, `test_audit.py`, `test_workspace.py`, `test_folder.py`, `test_elicitation.py`, `test_request_state.py`, `test_connection.py`, `test_gui.py`, and for Part 2 `test_host.py` and `test_model.py`) and each test is named after the guarantee it proves, so the files read as a list of what the project promises. `test_live_model.py` talks to a real model and is skipped unless `HOST_MODEL` is set (see [Part 2](part-2-host.md)). They run against a temporary copy of `client/` and `server/`, so they never touch your `workspace/`, logs or `permissions.json`. GitHub Actions runs pylint and the tests on Ubuntu and macOS for every push and pull request.

`main` is the trunk and takes direct pushes, so the same checks also run locally first: `.githooks/pre-push` (enabled by the `core.hooksPath` line in Setup) runs pylint, then the unit tests, then the integration tests, and stops the push at the first failure. CI stays as the backstop for anything the hook can't catch, such as macOS-only failures or a `git push --no-verify`.

CI also runs the tests under `coverage` (settings in `.coveragerc`) and sends the report to [SonarQube Cloud](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example), configured in `sonar-project.properties`. A failed quality gate fails the CI run. Coverage follows the server child process and maps the tests' temporary copy back to `client/` and `server/`. To see it locally:

```bash
coverage run -m pytest && coverage combine && coverage report
```

## Layout

The client and server are kept in separate folders, as they would be separate parties in a real deployment:

```
client/      app.py, client.py, tools_view.py, permissions.py, audit.py
             host.py, agent.py, model.py   (Part 2: the Chat tab, its turn loop, the model)
             permissions.json, client_audit.log
server/      server.py, audit.py, logs/server_audit.log   (server-private)
workspace/   the user's files: the only folder the server's file tools may use
```

Each side has its own `audit.py`; the two copies write the same JSON-lines format so the logs can be correlated.

## Versions

The repo speaks MCP protocol revision **2026-07-28** only: the client connects with the version pinned, and the server refuses handshake-era clients (`test_client_speaks_2026_07_28`, `test_handshake_client_is_refused`). The server's questions use multi round-trip requests: a tool returns `input_required` with its questions and sealed state, and the client re-sends the call with the answers (see [How a tool call travels](part-1-controls.md#how-a-tool-call-travels)).

Part 1 was written against mcp 1.16 and fastmcp 2.12 on the handshake protocol, at commit `03a9398`; the [blog series](https://www.domfarr.com/2026/10/01/mcp-security.html) links to that commit. `7c1324c` is the last handshake-era commit on current libraries (mcp 2.3, fastmcp 4.1, gradio 6). What changed and why the guarantees still hold: [MCP stopped waiting](https://www.domfarr.com/2026/10/19/mcp-security-mrtr.html).

MCP roots are deprecated as of 2026-07-28 (SEP-2577), so the folder the server may use is now server configuration: the client launches it with `--allowed-folder`.

The client declares URL-mode elicitation (mcp 2.3 always does) but declines every URL-mode request and records the URL in its audit log; it only presents forms, and shows any text a server supplies as plain text, never as a clickable link.
