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

- **Part 1: deterministic controls (`client/app.py`, no LLM).** What the client and server enforce regardless of who makes the request: tool permission policies, binding human-in-the-loop approval, audit logs on both sides, server-side path validation, roots, and elicitation (server-initiated structured input). Runs with no LLM and no token cost.
- **Part 2: model in the loop (`client/host.py`, planned).** The same client, server and policies with an LLM making the calls. Covers the risks that only exist when a model reads what a server sends: tool poisoning, prompt injection through tool results, resources and prompts, and changed tool definitions. Resources and prompts are covered here, as injection routes.
- **Part 3: remote-server authorization (separate repo, not started).** OAuth and token handling for MCP servers over HTTP.

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

Above the table, **Client root** sets the folder this client declares to the server as an MCP root (`workspace/`, one of its subfolders, `/ (whole disk)`, or a typed path); the line under it shows the exact URI sent. The server asks for the client's roots on every file tool call and works only where a root overlaps its own `workspace/`. A root inside the workspace narrows the server; a wider root such as `/` gives it nothing extra; a root elsewhere leaves it nothing. Roots are advisory in MCP: the client only declares them, and it is the server's own code that enforces them.

File paths are always relative to `workspace/`; a root restricts where they may land but doesn't move the starting folder. With the root `workspace/ROOT/`, `ROOT/a.txt` is allowed and `a.txt` (which means `workspace/a.txt`) is refused. Selecting a tool fills `filepath` with the root's prefix to make this visible.

### Tests

```bash
pytest                         # everything (about 1 minute)
pytest -m "not integration"    # only the tests that don't start a server (seconds)
```

The tests are grouped by security control (`tests/test_permissions.py`, `test_approval.py`, `test_audit.py`, `test_workspace.py`, `test_roots.py`, `test_elicitation.py`, `test_connection.py`, `test_gui.py`) and each test is named after the guarantee it proves, so the files read as a list of what Part 1 promises. They run against a temporary copy of `client/` and `server/`, so they never touch your `workspace/`, logs or `permissions.json`. GitHub Actions runs pylint and the tests on Ubuntu and macOS for every push and pull request.

`main` is the trunk and takes direct pushes, so the same checks also run locally first: `.githooks/pre-push` (enabled by the `core.hooksPath` line in Setup) runs pylint, then the unit tests, then the integration tests, and stops the push at the first failure. CI stays as the backstop for anything the hook can't catch, such as macOS-only failures or a `git push --no-verify`.

CI also runs the tests under `coverage` (settings in `.coveragerc`) and sends the report to [SonarQube Cloud](https://sonarcloud.io/summary/new_code?id=dominicfarr_mcp-secuirty-working-notes-example), configured in `sonar-project.properties`. A failed quality gate fails the CI run. Coverage follows the server child process and maps the tests' temporary copy back to `client/` and `server/`. To see it locally:

```bash
coverage run -m pytest && coverage combine && coverage report
```

### How a tool call travels

A tool call doesn't carry the root. Roots go the other way: the server asks the client for them during the call. The Tools tab's timeline shows this round trip.

```
1. client → server   initialize           "I support roots and elicitation"  (once, when connecting)
2. client → server   tools/call           delete_file(filepath)              ← call opens
3. server → client   roots/list           "which folders may I use?"         (the client answers itself)
4. client → server   roots/list result    ["file:///…/workspace"]
5. server → client   elicitation/create   message + schema                   (for the human)
6. client → server   elicitation result   {action: "accept", content: {…}}
7. server → client   tools/call result    "Deleted …" or "Access denied …"   ← call closes
```

**Where things run.** This app is the MCP client. The server is a separate process it starts on this machine over stdio, so both see the same disk, and a file is written wherever the server process runs: here, your machine. A remote (HTTP) server would run, and write, on its own machine, where `file://` roots naming folders on your machine would mean nothing. File roots are mainly for local servers.

**What roots protect.** Roots protect the machine the server runs on, which for a local server is the user's machine: the client says "only touch this project folder", for example so that a prompt-injected `read_file("~/.ssh/id_rsa")` is refused. They only work if the server honours them; a malicious server ignores them, so the real protection for the user is not running untrusted servers, plus OS permissions and sandboxing. Roots don't protect the server; it protects itself with its own configuration (here, the `workspace/` limit).

### Elicitation: server-initiated structured input

Sometimes the server needs something only the user can give, and only finds out while it's carrying out the call. `delete_file` asks the user to confirm: it sends a message ("Confirm deletion of notes.txt (2.3 KB, modified …)") and a **schema** describing the fields it needs, their types and validation rules (`confirm_name`, `reason` of 5–200 characters, `keep_backup`). The client builds a form from that schema, checks the user's input against it, and sends back structured data; the user can also decline or cancel. The tool call waits, paused, until the answer arrives.

- **Around the caller.** The question goes through the client to the human, not to whatever made the call. A confirmation passed as a tool argument could be filled in by the caller (in Part 2, an LLM); an elicitation answer can't.
- **Two layers of consent.** The client's permission policy decides whether the call is sent at all; the server's question confirms details only the server knows. `delete_file` defaults to `deny` in the client, so set it to `ask` or `allow` to try this.
- **Validate on both sides.** The client checks the answer against the schema before sending it; the server checks it again, because it can't trust the client, and also checks what a schema can't express (`confirm_name` must match the file path).
- **Fail closed.** A client that can't be asked (no elicitation support) can't delete; a question left unanswered for 2 minutes is treated as cancelled.

The **Permissions** tab sets this client's policy for each tool; its tool list loads on page load.

The **Audit** tab shows both audit logs, the client's above the server's, each in a scrolling table. Each side keeps its own record: the client writes `client/client_audit.log` (its decisions and outcomes, grouped by request ID) and the server writes `server/logs/server_audit.log` (every tool call it received). Both are JSON lines. A request the client denies never reaches the server, so it appears only in the client log.

**Demo only:** a real MCP client can't see the server's log. The app shows it only because client and server run on one machine; it reads the file from disk, not through MCP. The tab marks the server panel accordingly.
