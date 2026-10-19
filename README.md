# MCP Secuirty Reference

[![tests](https://github.com/dominicfarr/mcp-secuirty-working-notes-example/actions/workflows/tests.yml/badge.svg)](https://github.com/dominicfarr/mcp-secuirty-working-notes-example/actions/workflows/tests.yml)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=dominicfarr_mcp-secuirty-working-notes-example&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=dominicfarr_mcp-secuirty-working-notes-example&metric=coverage)](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=dominicfarr_mcp-secuirty-working-notes-example&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=dominicfarr_mcp-secuirty-working-notes-example&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example)

Write-up: [MCP Security series](https://www.domfarr.com/2026/10/01/mcp-security.html), five posts walking through Part 1 with links back to this code.

A simple reference for the MCP security model, focused on client-side permissions. It shows the client's permission policy for each tool, and how every tool call to the server is allowed, denied or held for your approval according to that policy.

Both client and server are local processes and the MCP transport mechanism is STDIO.

A simple app provides a GUI to view the server's capabilities (tools) and the client's policy for each one. A tool with an `ask` policy needs human-in-the-loop approval before it is called, and a `deny` policy is a hard stop. The GUI can also edit `permissions.json` to override the default policies.

![MCP Security](https://github.com/dominicfarr/dominicfarr.github.io/blob/main/assets/mcp-security.svg?raw=true)

### Project structure

The project is organised by threat rather than by MCP primitive:

- **Part 1: deterministic controls (`client/app.py`, no LLM).** What the client and server enforce regardless of who makes the request: tool permission policies, binding human-in-the-loop approval, audit logs on both sides, server-side path validation, the allowed folder (formerly roots), and elicitation (server-initiated structured input, over multi round-trip requests). Runs with no LLM and no token cost.
- **Part 2: model in the loop (`client/host.py`, planned).** The same client, server and policies with an LLM making the calls. Covers the risks that only exist when a model reads what a server sends: tool poisoning, prompt injection through tool results, resources and prompts, and changed tool definitions. Resources and prompts are covered here, as injection routes.
- **Part 3: remote-server authorization (separate repo, not started).** OAuth and token handling for MCP servers over HTTP.

### Versions

The repo speaks MCP protocol revision **2026-07-28** only: the client connects with the version pinned, and the server refuses handshake-era clients (`test_client_speaks_2026_07_28`, `test_handshake_client_is_refused`). The server's questions use multi round-trip requests: a tool returns `input_required` with its questions and sealed state, and the client re-sends the call with the answers (see [How a tool call travels](#how-a-tool-call-travels)).

Part 1 was written against mcp 1.16 and fastmcp 2.12 on the handshake protocol, at commit `03a9398`; the [blog series](https://www.domfarr.com/2026/10/01/mcp-security.html) links to that commit. `7c1324c` is the last handshake-era commit on current libraries (mcp 2.3, fastmcp 4.1, gradio 6).

MCP roots are deprecated as of 2026-07-28 (SEP-2577), so the folder the server may use is now server configuration: the client launches it with `--allowed-folder`.

The client declares URL-mode elicitation (mcp 2.3 always does) but declines every URL-mode request and records the URL in its audit log; it only presents forms, and shows any text a server supplies as plain text, never as a clickable link.

### Layout

The client and server are kept in separate folders, as they would be separate parties in a real deployment:

```
client/      app.py, client.py, tools_view.py, permissions.py, audit.py, host.py
             permissions.json, client_audit.log
server/      server.py, audit.py, logs/server_audit.log   (server-private)
workspace/   the user's files: the only folder the server's file tools may use
```

Each side has its own `audit.py`; the two copies write the same JSON-lines format so the logs can be correlated.

### Setup

```bash
python3 -m venv mcp_security_env
source mcp_security_env/bin/activate
pip install -r requirements-dev.txt
git config core.hooksPath .githooks   # run lint and tests before every push
```

`requirements.txt` holds the runtime packages (`openai` is only needed for `client/host.py`); `requirements-dev.txt` adds `pytest`, `pylint` and `coverage`.

Upgrading an existing venv from fastmcp 2.x: fastmcp 4 ships its code in `fastmcp-slim`, and pip removes the old package's files after installing it, leaving `ImportError: cannot import name 'Context' from 'fastmcp'`. Repair it with `pip install --force-reinstall --no-deps fastmcp-slim==4.1.1`, or create a fresh venv.

CI installs from `requirements-dev.lock` instead: every package, including transitive ones, pinned with its hashes and installed only from wheels, so no package's setup script runs. After changing either requirements file, regenerate the lock (CI fails if a pinned version is missing from it, for example on a Dependabot PR):

```bash
uv pip compile --universal --generate-hashes --python-version 3.13 requirements-dev.txt -o requirements-dev.lock
```

### Launch

Start the app with the venv activated. It launches `server/server.py` as a child process.

```bash
python3 client/app.py server/server.py

* Running on local URL:  http://127.0.0.1:7863
```

Open that link to see the app.

The **Tools** tab loads the server's tools on page load. The table shows what the server reports (tool name and description) next to this client's own policy for each tool (allow / ask / deny, from the built-in defaults or your overrides in `permissions.json`). Select a tool, edit its arguments and send a request: the timeline shows the client's policy decision. An `ask` request waits for you to approve or reject that exact request; nothing reaches the server until you do.

Above the table, **Folder the server is launched with** sets the folder the client passes to the server as `--allowed-folder` (`workspace/`, one of its subfolders, `/ (whole disk)`, or a typed path); the line under it shows the exact argument. Changing it restarts the server. The server works only where that folder overlaps its own `workspace/`: a folder inside the workspace narrows the server; a wider one such as `/` gives it nothing extra; a folder elsewhere leaves it nothing. The client only chooses the folder; it is the server's own code that enforces it.

File paths are always relative to `workspace/`; the folder restricts where they may land but doesn't move the starting folder. With the folder `workspace/FOLDER/`, `FOLDER/a.txt` is allowed and `a.txt` (which means `workspace/a.txt`) is refused. Selecting a tool fills `filepath` with the folder's prefix to make this visible.

### Tests

```bash
pytest                         # everything (about 1 minute)
pytest -m "not integration"    # only the tests that don't start a server (seconds)
```

The tests are grouped by security control (`tests/test_permissions.py`, `test_approval.py`, `test_audit.py`, `test_workspace.py`, `test_folder.py`, `test_elicitation.py`, `test_request_state.py`, `test_connection.py`, `test_gui.py`) and each test is named after the guarantee it proves, so the files read as a list of what Part 1 promises. They run against a temporary copy of `client/` and `server/`, so they never touch your `workspace/`, logs or `permissions.json`. GitHub Actions runs pylint and the tests on Ubuntu and macOS for every push and pull request.

`main` is the trunk and takes direct pushes, so the same checks also run locally first: `.githooks/pre-push` (enabled by the `core.hooksPath` line in Setup) runs pylint, then the unit tests, then the integration tests, and stops the push at the first failure. CI stays as the backstop for anything the hook can't catch, such as macOS-only failures or a `git push --no-verify`.

CI also runs the tests under `coverage` (settings in `.coveragerc`) and sends the report to [SonarQube Cloud](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example), configured in `sonar-project.properties`. A failed quality gate fails the CI run. Coverage follows the server child process and maps the tests' temporary copy back to `client/` and `server/`. To see it locally:

```bash
coverage run -m pytest && coverage combine && coverage report
```

### How a tool call travels

On MCP 2026-07-28 the server never sends the client a request of its own. When it needs the user's input, it ends the call with `input_required`, and the client asks again. The Tools tab's timeline shows each round.

```
1. client → server   tools/call       delete_file(filepath)                      ← round 1
2. server → client   result: input_required
                       inputRequests: { confirm: elicitation/create form }
                       requestState:  sealed { path, file identity }            ← call over; server forgets it
3. (the user answers in the client; nothing on the server waits)
4. client → server   tools/call       delete_file(filepath), new request id      ← round 2: same arguments
                       + inputResponses { confirm: {action: accept, content: …} }
                       + requestState (echoed unchanged)
5. server            unseals the state (rejects tampered or expired state, or state from another request),
                     validates the answer, resolves the path again, checks the file is the one asked about
6. server → client   result: "Deleted …" or an error                             ← call closes
```

What used to live in the server's memory while it waited (the path, the identity of the file the user was asked about) now travels through the client. So the server seals it (encrypted, integrity-checked, bound to this request, expiring after 2 minutes) and treats round 2 as a new, untrusted request: everything is checked again. The seal doesn't make the state single-use: replaying the same round 2 within the 2 minutes passes it. What stops a replayed delete is the identity check, because the confirmed file is gone (or renamed to its backup).

**Where things run.** This app is the MCP client. The server is a separate process it starts on this machine over stdio, so both see the same disk, and a file is written wherever the server process runs: here, your machine. A remote (HTTP) server would run, and write, on its own machine, where a folder on your machine would mean nothing.

**The allowed folder.** The folder protects the machine the server runs on, which for a local server is the user's machine: the client says "only touch this project folder", for example so that a prompt-injected `read_file("~/.ssh/id_rsa")` is refused. It only works if the server honours it; a malicious server ignores it, so the real protection for the user is not running untrusted servers, plus OS permissions and sandboxing. MCP roots did this job as a request the server made mid-call; they are deprecated as of 2026-07-28, and the spec recommends server configuration instead.

### Elicitation: server-initiated structured input

Sometimes the server needs something only the user can give, and only finds out while it's carrying out the call. `delete_file` asks the user to confirm: it sends a message ("Confirm deletion of notes.txt (2.3 KB, modified …)") and a **schema** describing the fields it needs, their types and validation rules (`confirm_name`, `reason` of 5–200 characters, `keep_backup`). The client builds a form from that schema, checks the user's input against it, and sends back structured data; the user can also decline or cancel. The server doesn't wait: it ends round 1 with the question and sealed state, and the client re-sends the call with the answer.

- **Around the caller.** The question goes through the client to the human, not to whatever made the call. A confirmation passed as a tool argument could be filled in by the caller (in Part 2, an LLM); an elicitation answer can't.
- **Two layers of consent.** The client's permission policy decides whether the call is sent at all; the server's question confirms details only the server knows. `delete_file` defaults to `deny` in the client, so set it to `ask` or `allow` to try this.
- **Validate on both sides.** The client checks the answer against the schema before sending it; the server checks it again, because it can't trust the client, and also checks what a schema can't express (`confirm_name` must match the file path).
- **Fail closed.** A client that can't be asked (no elicitation support) can't delete; a confirmation is valid for 2 minutes (the sealed state's expiry), and a later answer is refused.

The **Permissions** tab sets this client's policy for each tool; its tool list loads on page load.

The **Audit** tab shows both audit logs, the client's above the server's, each in a scrolling table. Each side keeps its own record: the client writes `client/client_audit.log` (its decisions and outcomes, grouped by request ID) and the server writes `server/logs/server_audit.log` (every tool call it received). Both are JSON lines. A request the client denies never reaches the server, so it appears only in the client log.

**Demo only:** a real MCP client can't see the server's log. The app shows it only because client and server run on one machine; it reads the file from disk, not through MCP. The tab marks the server panel accordingly.
