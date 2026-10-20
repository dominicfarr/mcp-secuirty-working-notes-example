# Part 1: deterministic controls

[← README](../README.md) · [Part 1: controls](part-1-controls.md) · [Part 2: model in the loop](part-2-host.md) · [Development](development.md)

What the client and server enforce regardless of who makes the request: tool permission policies, binding human-in-the-loop approval, audit logs on both sides, server-side path validation, the allowed folder (formerly roots), and elicitation. No LLM, no token cost. The [blog series](https://www.domfarr.com/2026/10/01/mcp-security.html) walks through it.

## The app

Start the app with the venv activated. It launches `server/server.py` as a child process.

```bash
python3 client/app.py server/server.py

* Running on local URL:  http://127.0.0.1:7863
```

Open that link to see the app.

The **Tools** tab loads the server's tools on page load. The table shows what the server reports (tool name and description) next to this client's own policy for each tool (allow / ask / deny, from the built-in defaults or your overrides in `permissions.json`). Select a tool, edit its arguments and send a request: the timeline shows the client's policy decision. An `ask` request waits for you to approve or reject that exact request; nothing reaches the server until you do.

Above the table, **Folder the server is launched with** sets the folder the client passes to the server as `--allowed-folder` (`workspace/`, one of its subfolders, `/ (whole disk)`, or a typed path); the line under it shows the exact argument. Changing it restarts the server. The server works only where that folder overlaps its own `workspace/`: a folder inside the workspace narrows the server; a wider one such as `/` gives it nothing extra; a folder elsewhere leaves it nothing. The client only chooses the folder; it is the server's own code that enforces it.

File paths are always relative to `workspace/`; the folder restricts where they may land but doesn't move the starting folder. With the folder `workspace/FOLDER/`, `FOLDER/a.txt` is allowed and `a.txt` (which means `workspace/a.txt`) is refused. Selecting a tool fills `filepath` with the folder's prefix to make this visible.

## How a tool call travels

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

## Elicitation: server-initiated structured input

Sometimes the server needs something only the user can give, and only finds out while it's carrying out the call. `delete_file` asks the user to confirm: it sends a message ("Confirm deletion of notes.txt (2.3 KB, modified …)") and a **schema** describing the fields it needs, their types and validation rules (`confirm_name`, `reason` of 5–200 characters, `keep_backup`). The client builds a form from that schema, checks the user's input against it, and sends back structured data; the user can also decline or cancel. The server doesn't wait: it ends round 1 with the question and sealed state, and the client re-sends the call with the answer.

- **Around the caller.** The question goes through the client to the human, not to whatever made the call. A confirmation passed as a tool argument could be filled in by the caller (in Part 2, an LLM); an elicitation answer can't.
- **Two layers of consent.** The client's permission policy decides whether the call is sent at all; the server's question confirms details only the server knows. `delete_file` defaults to `deny` in the client, so set it to `ask` or `allow` to try this.
- **Validate on both sides.** The client checks the answer against the schema before sending it; the server checks it again, because it can't trust the client, and also checks what a schema can't express (`confirm_name` must match the file path).
- **Fail closed.** A client that can't be asked (no elicitation support) can't delete; a confirmation is valid for 2 minutes (the sealed state's expiry), and a later answer is refused.

The **Permissions** tab sets this client's policy for each tool; its tool list loads on page load.

The **Audit** tab shows both audit logs, the client's above the server's, each in a scrolling table. Each side keeps its own record: the client writes `client/client_audit.log` (its decisions and outcomes, grouped by request ID) and the server writes `server/logs/server_audit.log` (every tool call it received). Both are JSON lines. A request the client denies never reaches the server, so it appears only in the client log. Each client record has a `source`: `user` for the Tools tab, `model` for the Chat tab. A model's call the client couldn't send (an unknown tool, arguments that aren't a JSON object) is recorded as `NOT SENT`, and resources and prompts the user adds to a chat as `(resource)` and `(prompt)`.

**Demo only:** a real MCP client can't see the server's log. The app shows it only because client and server run on one machine; it reads the file from disk, not through MCP. The tab marks the server panel accordingly.
