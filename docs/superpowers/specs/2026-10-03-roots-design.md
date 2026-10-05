# Roots: the client declares a boundary, the server enforces it

Date: 2026-10-03
Status: implemented (plan: docs/superpowers/plans/2026-10-03-roots.md)

## Purpose

Part 1 of the project (`client/app.py`, no LLM) teaches the deterministic controls of the MCP
security model. This feature adds **roots**: the client tells the server which folders it may work
in, and the server confines its file tools to them.

The demo teaches two lessons:

1. **The client narrows the server.** The user picks a root (e.g. `workspace/projects`) and the
   server's file tools only work inside it.
2. **The server keeps its own limit.** Roots can only narrow the server's configured workspace,
   never widen it. Declaring `/` or a folder outside the workspace gives the server nothing extra.

Not in scope (considered and set aside): a misbehaving server that ignores roots, and the
`roots/list_changed` notification.

Success: someone running the demo can set a root, see calls inside it succeed and calls outside it
refused by the server, and see that a wide root gains the server nothing.

## Concepts

- **Direction.** Roots flow from client to server ("these are the folders you may work in"). This is
  the reverse of resources (server to client).
- **Advisory.** The client declares roots; only the server's own code can enforce them. A local
  stdio server is an ordinary process with the user's file permissions.
- **Roots restrict; they don't relocate.** File paths stay relative to the server's workspace. A
  root limits where they may land; it is not a current folder (with several roots it couldn't be).
- **The root isn't in the tool call.** `tools/call` carries only the name and arguments; the server
  asks the client for roots (`roots/list`) during the call. The timeline shows that round trip.
- **Server-side information stays server-side.** The client shows only what it declares. It never
  shows the server's limit or the resulting allowed area; the server's answers (success or
  refusal) show that.

## Design

### 1. Server: roots narrow the workspace (`server/server.py`)

- The server's own limit is `WORKSPACE_DIR` (the project's `workspace/`), set in server
  configuration as today.
- On every file tool call (`read_file`, `write_file`, `delete_file`) the server asks the client
  for its current roots with `ctx.list_roots()`. For each `file://` root, the allowed area is its
  overlap with the workspace:

  | Client root | Allowed area | Effect |
  |---|---|---|
  | `workspace/projects` (inside the workspace) | `workspace/projects` | Narrowed |
  | `workspace/` | `workspace/` | Unchanged |
  | `/`, or the project folder (contains the workspace) | `workspace/` | Nothing extra |
  | `/Users/dom/Documents` (no overlap) | none | All file tools refused |

  With several roots, the allowed area is the union of the overlaps.
- File paths stay relative to `workspace/`, as the tool descriptions say. With root
  `workspace/projects`, `write_file("projects/a.txt")` is allowed and `write_file("a.txt")` is
  refused: "Access denied: a.txt is outside the client's roots".
- The existing checks run first and are unchanged: outside the workspace, blank path, folder path
  (`resolve_in_workspace`).
- A root may be a single file (the spec allows it); then only that file is usable.
- Paths and roots are fully resolved (symlinks included) before comparing. The file path is
  resolved again after the server has the client's roots, so a folder swapped for a symlink
  while the client answers can't redirect the operation (the client controls that wait).
- Only `file://` URIs with no host (or `localhost`) name local folders (RFC 8089); other hosts,
  other schemes and paths containing NUL are not usable roots.
- `execute_command` does not touch files and is unaffected.
- Refusals are `ToolError`s, so `@audited` records them in `server/logs/server_audit.log` as
  `error` entries and the Audit tab's server panel shows them.

Edge cases:

| Situation | Behaviour |
|---|---|
| Client doesn't support roots (`roots/list` returns "List roots not supported") | Fall back to `workspace/`. |
| Client sends an empty roots list | Refuse all file tools: "the client declared no roots". |
| No root is a `file://` URI | Refuse all file tools (same as empty). |
| No root overlaps the workspace | Refuse: "x is outside the client's roots". |
| The roots request fails otherwise (error, timeout, or a malformed reply such as a non-`file://` root) | Refuse: "could not get the client's roots". The MCP library validates the whole reply, so one bad root refuses the call rather than being skipped. |

Implementation notes:

- The file tools become `async` (to await `ctx.list_roots()`) and take a FastMCP `Context`
  parameter. `@audited` must support async functions and must leave the `Context` argument out of
  the audit record.

### 2. Client: declaring roots (`client/client.py`)

- `self.roots: list[Path]`, defaulting to the project's `workspace/` folder (the user's files; a
  real host would know this as the folder the user opened).
- `connect()` passes `list_roots_callback` to `ClientSession`, which declares the roots capability.
  The callback answers each `roots/list` request with the current roots as `file://` URIs
  (`Root(uri=path.as_uri(), name=...)`).
- `set_roots(paths)` replaces the list. Because the server asks on every file tool call, the change
  applies to the next call without reconnecting or sending `roots/list_changed`.
- Relative paths (e.g. `workspace/projects`) resolve against the project folder, not the current
  working directory. Absolute paths are accepted as given, including ones outside the workspace.
- Each change is written to the client audit log: tool `(roots)`, outcome `ROOTS DECLARED`,
  arguments `{"roots": [...]}` with the URIs sent.

### 3. Tools tab (`client/app.py`)

Above the tool table:

```
Client root (sent to the server as an MCP root): [ workspace/projects          ▾ ]
Declared: file:///Users/dom/.../workspace/projects
```

- `gr.Dropdown` with `allow_custom_value=True`. Presets: `workspace/`, each existing subfolder of
  `workspace/` (refreshed on page load), and `/ (whole disk)`.
- Changing it calls `set_roots()` and updates the "Declared" line with the exact URI sent.
- An empty value shows "Enter a folder path" and keeps the previous root.
- The GUI never shows the server's limit or the allowed area. A refused call ends with
  `✗ 4 Error` and the server's message.
- The root lasts for the session (resets to `workspace/` on restart) and is shared by all browser
  tabs, like pending requests.

## Checks

Covered by the Part 1 test suite (`tests/`, see `docs/superpowers/specs/2026-10-05-part1-tests-design.md`). (The cases below were first verified by check scripts during implementation.)

- **Server** (real server, client with configurable roots): narrowed root allows inside and refuses
  outside; `/` behaves like `workspace/`; a root outside the workspace refuses everything; an empty
  list refuses everything; a client without roots support falls back to `workspace/`; refusals
  appear in the server log; `ctx` never appears in audit records.
- **Client**: callback returns `file://` URIs of the current roots; relative paths resolve against
  the project folder; a change writes `ROOTS DECLARED` to the client log.
- **GUI**: presets include `workspace/` subfolders and `/`; the "Declared" line shows the URI; a
  root change affects the next tool call; an empty value keeps the previous root.
- All existing checks pass; pylint 10/10 on `client/` and `server/`.

### 4. Making the round trip visible (added 2026-10-04)

- `ToolOutcome.roots_answered: list[str] | None`: the roots the client sent if the server asked
  during that call (`None` if it didn't, e.g. `execute_command`). The client records each answer in
  its `roots/list` callback; `_send()` attaches it to the call in progress.
- The timeline's step 3 reads "Sent to server (tools/call: name + arguments only, no root)" and,
  when the server asked, an unnumbered line follows: "↩ Server asked the client for its roots
  (`roots/list`); client answered: `file:///…`".
- Selecting a tool fills `filepath` with the root's prefix relative to the workspace (`ROOT/`);
  empty for the workspace itself, `/`, or a root outside the workspace.
- The Tools tab and README carry a "Where things run" note (local stdio vs remote servers).

## Known limitations

- One root at a time in the GUI (client and server support several).
- The root is not persisted across restarts.
- A roots request is matched to a tool call by timing; with two calls in flight at once it could
  be attributed to the wrong one. The GUI allows one request at a time.
- Roots typed with different letter case (e.g. `Workspace/`) are refused on case-insensitive
  filesystems: paths are compared as resolved, not case-folded (fails closed).
- Deferred minor issues from the final review: typed roots like `~nosuchuser` show Gradio's error
  popup instead of a warning; a single-file root is displayed with a trailing slash; server audit
  refusals don't record which roots the client declared.

## Docs to update with the implementation

- README: Part 1 lists roots as done; Tools tab paragraph describes the root control; a short
  explanation of roots (direction, advisory, server keeps its own limit).
- Tools-tab spec: Change history entry for the root control.

## Change history

- 2026-10-03: implemented; final-review fixes (re-resolve after the roots wait, local-only
  `file://` URIs, malformed replies refused with the spec message).
- 2026-10-04: round trip made visible (section 4); README explains paths, message flow,
  where things run and what roots protect.
