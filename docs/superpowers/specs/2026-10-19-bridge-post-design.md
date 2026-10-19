# Bridge post: Part 1 on MCP 2026-07-28

Date: 2026-10-19
Status: implemented, not committed

## Purpose

Part 1 of the blog series (hub `2026-10-01` to Part 5 `2026-10-06`) describes the handshake-era
reference: the server pauses a `delete_file` call on `elicitation/create` and reads the client's
roots. Commit `096d5ac` moved the repo to MCP 2026-07-28, where neither exists: a server asks by
returning `input_required` and the client re-sends the call (multi round-trip requests, MRTR),
and roots are deprecated. One standalone post explains what changed, why the security guarantees
still hold, and how a reader moves their own server. It sits between Part 1 and the future Part 2,
so Part 2 can build on 2026-07-28 without re-teaching it.

Readers: engineers who read Part 1 (or know handshake-era MCP) and now face 2026-07-28.

Success: one post of 1,000–1,300 words of prose (code, diagrams and front matter excluded), in the
series' format, every claim backed by a pinned snippet or a linked test; the two dated notes in
Part 1 point at it; the site builds with the diagrams rendering. Nothing is committed in either
repo.

Out of scope: rewriting Part 1 posts (they pin `03a9398` and stay correct for their era); Part 2
content (the LLM host); code changes in this repo.

## Design

### 1. The post

File `../dominicfarr.github.io/_posts/2026-10-19-mcp-security-mrtr.md`, dated 2026-10-19 (not
future-dated, so it builds). Working title: **"MCP stopped waiting: roots and elicitation on
2026-07-28"**. Front matter: `tags: [agentic, tooling, security]`, `mermaid: true`.

The series' six sections, in order:

1. **The question.** Part 1's server asked mid-call and waited for the answer. 2026-07-28 removed
   that wait. What happens to the guarantees Part 1 proved?
2. **The threat.** State that lived in the server's memory between question and answer now
   travels through the client, and the answer arrives on a new, separate request. The four risks:
   tampered state; replayed state; the file swapped between rounds; approval drifting from what
   is re-sent.
3. **What the spec says.** From the 2026-07-28 MRTR page, quoted verbatim and linked: servers MUST
   treat `requestState` as attacker-controlled and protect its integrity when it affects access;
   SHOULD bind it to the principal, an expiry and the originating request; single-use is the
   server's job; clients echo it unchanged on a new request. Plus: roots deprecated (SEP-2577),
   server configuration the recommended replacement. Exact wording is checked against the spec
   page while writing, not taken from this summary.
4. **The reference implementation.**
   - Two Mermaid sequence diagrams: before (`tools/call` → `elicitation/create` → answer → result,
     one request held open) and after (round 1 `tools/call` → `input_required` with sealed
     `requestState`; round 2 `tools/call` with `inputResponses` + echoed state → result).
   - Snippets (≤ 20 lines each, verbatim, trimmed only with `# …`):
     `delete_file` before (`7c1324c`) and after (`096d5ac`); the server construction with
     `RequestStateSecurity.ephemeral(ttl=…)` and `ModernProtocolOnly`; the client's round loop
     in `_call_server`; `allowed_area` / `resolve_allowed` replacing roots.
5. **Gotchas.**
   - One tool can't ask in both eras with one code path (fastmcp rejects `InputRequiredResult`
     on a handshake connection), so the server serves 2026-07-28 only and refuses `initialize`.
   - The seal binds to the request and expiry but is not single-use; the file-identity check is
     what makes a replayed delete do nothing.
   - The client drives the rounds itself, re-sending the approved arguments, so the human's
     approval stays bound to what is executed.
   - Changing the folder restarts the server, so open questions must be closed (the bug review
     caught; `test_folder_change_closes_an_open_question`).
   - The old elicitation timeout becomes an expiry on the sealed state.
   - `serverInfo` is no longer a handshake field; don't rely on it.
6. **Proof.** Linked tests at `096d5ac`: `tests/test_request_state.py` (tampered, reused for
   another file, answer without state); `test_retry_sends_the_approved_arguments`;
   `test_expired_confirmation_deletes_nothing`; `test_file_changed_between_rounds_is_not_deleted`;
   `test_symlink_swapped_between_rounds_is_not_followed`; `test_handshake_client_is_refused`;
   `test_client_speaks_2026_07_28`; `tests/test_folder.py`.

Then a short **migration checklist** (5–7 bullets) for readers moving their own servers, and a
footer linking the Part 1 hub and Parts 2–4 (roots, elicitation, delete end to end).

Voice matches the series: short paragraphs, plain statements, no hype. The draft gets a pass with
the `humanizer` skill.

### 2. Code links

"Before" snippets link to `7c1324c7d00fa524b1340236894cc83992f720dc` (the README's "last
handshake-era commit"; `b4aa249` after it changed only a spec, so the code is identical). "After"
snippets and tests link to `096d5accedffc2c407adb3f450bf6e32d947b840`. Each snippet is followed by
a "full source" link to its exact `#L` range.

### 3. Glossary (`_glossary/`, same format as existing entries)

- New `mrtr.md`: multi round-trip requests; the server returns `input_required`, the client
  re-sends the call with answers and the echoed state; no request is held open.
- New `request-state.md`: the opaque state a server hands the client between rounds; must be
  treated as attacker-controlled, so it is sealed and bound to the request and an expiry.
- `roots.md`: one added sentence: deprecated as of MCP 2026-07-28 (SEP-2577); servers take
  folders from configuration instead.
- `elicitation.md`: one added sentence: on 2026-07-28 the question travels inside an
  `input_required` result rather than as a mid-call `elicitation/create` request.

The post links each term on first use.

### 4. Part 1 notes

The dated notes in `2026-10-01-mcp-security.md` (hub) and `2026-10-03-mcp-security-roots.md`
change their link from the README's Versions section to this post (`{% post_url
2026-10-19-mcp-security-mrtr %}`). Wording otherwise unchanged; they stay after the first
paragraph.

### 5. This repo

README: one line in the Versions section linking the bridge post. Migration spec section 7 gets a
line noting the notes now link the post. No code changes.

## Verification

- `bundle exec jekyll build` succeeds (gems under the scratchpad's `BUNDLE_PATH`).
- Both Mermaid blocks render (`@mermaid-js/mermaid-cli` to SVG in the scratchpad), and render in
  the built page in a headless browser.
- Every snippet matches its pinned commit and its `#L` range covers it (snippet checker run against
  `git show <sha>:<path>`).
- Every glossary link has a file; every `post_url` resolves (the build fails otherwise); every
  repo link points at a file and test name that exist at its commit.
- Spec quotes match the 2026-07-28 MRTR and roots pages.
- Prose word count 1,000–1,300.
- One fresh reviewer subagent (opus) reads the post, glossary changes and notes against this spec
  and the code.

## Decisions made for the user

- "Before" pins `7c1324c` rather than `b4aa249`: same code, and it matches the README's wording.
- File slug `mcp-security-mrtr`, so the URL groups with the series.
- The post is not added to the hub's Part 1 index; the hub reaches it through its dated note. The
  Part 2 hub will link it as its prerequisite.
- The "in-place fastmcp upgrade clobbers its package" gotcha was cut from the post while writing:
  it's an install problem, not a protocol one, and the README already covers the repair.
- Review fixes: the seal binds to the tool and arguments (not a per-request id); replay is
  narrowed, not closed, by the identity check; the client still times out the question.
