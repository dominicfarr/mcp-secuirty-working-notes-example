# MCP security blog series

Date: 2026-10-06
Status: implemented, not committed (plan: docs/superpowers/plans/2026-10-06-blog-series.md)

## Purpose

Turn Part 1 of this repo into a short series on domfarr.com (`../dominicfarr.github.io`, Jekyll,
minima theme). The repo is the reference implementation: every claim in a post points at the code
or the test that backs it. Readers are engineers who know roughly what MCP is and want to know
where the security actually lives.

Success: a hub post and five parts, each 600–900 words, each with at least one Mermaid diagram and
real snippets linked to this repo at a fixed commit; glossary entries for the terms the posts use;
the site builds locally with diagrams readable whether the reader's OS is in light or dark mode. Nothing is committed in
either repo.

Out of scope: Part 2 (LLM in the loop) and Part 3 (remote auth) of the repo, which get their own
posts later; restyling the site; fixing typos in the existing glossary entries (listed for the
user instead).

## Design

### 1. Posts

The existing `_posts/2026-10-01-mcp-security.md` ("Enough said" + `mcp-security.svg`) becomes the
hub, keeping its URL. The five parts are new files.

| # | File (`_posts/`) | Title | Diagram | Repo material |
|---|---|---|---|---|
| 0 | `2026-10-01-mcp-security.md` (rewrite) | MCP Security | the existing SVG + series index | README "Project structure" |
| 1 | `2026-10-02-mcp-security-client-permissions.md` | Allow, ask, deny: the client decides | state diagram of a request (ALLOWED / DENIED / ASK → approved / rejected / policy changed) | `client/permissions.py`, `request_tool`, `approve`, frozen `ToolRequest` |
| 2 | `2026-10-03-mcp-security-roots.md` | Roots are a request, not a wall | flowchart of `resolve_in_roots` + sequence of `roots/list` inside `tools/call` | `resolve_in_workspace`, `allowed_areas`, `client_roots`, `file_uri_path` |
| 3 | `2026-10-04-mcp-security-elicitation.md` | Ask the human, not the caller | sequence: tool call paused on `elicitation/create` | `DeleteConfirmation`, `confirm_deletion`, client `_on_elicitation`, `schema_errors` |
| 4 | `2026-10-05-mcp-security-delete-end-to-end.md` | One delete, every check | end-to-end sequence of `delete_file` with each control marked | `delete_file`, `file_identity`, `backup_path`, both audit logs |
| 5 | `2026-10-06-mcp-security-proving-it.md` | Proving it: tests as the security spec | flowchart: pre-push hook → CI matrix → coverage → SonarQube gate | `tests/` names, `conftest.py` isolation, `.githooks/pre-push`, the S2083 false positive |

The hub keeps its date (2026-10-01); each part is one day after the one before, so Part 5 lands
on 2026-10-06. None is future-dated, so all build (Jekyll hides future-dated posts).

Each part uses the same sections, in this order:

1. **The question**: one or two sentences on what the reader would ask.
2. **The threat**: what goes wrong without the control.
3. **What the spec says**: the MCP specification's position, linked.
4. **The reference implementation**: the diagram, then 2–3 short snippets (≤ 20 lines each),
   each followed by a "full source" link to the exact lines.
5. **Gotchas**: what was learned building it (e.g. re-resolve after an await; a schema can't say
   "must match the file name"; roots are advisory).
6. **Proof**: the test names that guarantee it, linked.

Footer of every part: previous / hub / next links. Front matter:
`tags: [agentic, tooling, security]`, `mermaid: true`.

Voice matches the existing posts: short paragraphs, plain statements, no hype. Each draft gets a
pass with the `humanizer` skill.

### 2. Code links

Snippets link to `https://github.com/dominicfarr/mcp-secuirty-working-notes-example/blob/03a9398833959d25549665bbd053ddff54430393/<path>#L<a>-L<b>`.
A commit SHA never moves, so the line numbers stay right as the repo changes; no tag is needed.
Snippets are copied verbatim from that commit (trimmed only with a `# …` line).

### 3. Mermaid

`_includes/footer.html` (already overriding minima's footer) gets a block that runs only when
`page.mermaid` is true: it loads Mermaid 11 as an ES module from `cdn.jsdelivr.net`, turns
kramdown's ` ```mermaid ` code blocks into `<pre class="mermaid">`, and renders with the `default`
theme. The site is always light: `_config.yml` sets `skin: auto`, but that is a minima 3 option and
the site runs minima 2.5, which ignores it. (Following `prefers-color-scheme` was tried and put
light-on-light diagrams on dark-mode machines.) Pages without the flag load nothing.

### 4. Glossary

Posts link a term on first use to `/glossary/<name>/`. New entries in `_glossary/` (same format as
`api.md`: `title` front matter, short plain definition, no headings unless needed):

`mcp`, `mcp-client`, `mcp-server`, `tool-call`, `human-in-the-loop`, `roots`, `elicitation`,
`path-traversal`, `toctou`, `fail-closed`, `audit-log`, `prompt-injection`, `stdio-transport`.

### 5. This repo

The README gets one line under the title linking the series hub. No code changes.

## Verification

- `bundle exec jekyll build` succeeds in the blog repo (if Ruby/bundler aren't installed, say so
  and fall back to the checks below).
- Every Mermaid block parses (`npx -y @mermaid-js/mermaid-cli` renders each to SVG in the
  scratchpad).
- Every snippet matches the source at the pinned commit, and every `#L` range covers it (a
  script compares them).
- Every `/glossary/<name>/` link has a file; every repo link resolves to a file at the commit.
- Word count per part is 600–900.
- One fresh reviewer subagent reads all six posts against this spec and the code.

## Decisions made for the user

- The hub reuses the existing post and URL rather than adding a new one.
- Code links pin the commit SHA instead of creating a tag (a tag would need pushing).
- Existing glossary typos (`retreive`, `remove service`, `Phenonmenon`, `Postional`, `usin`,
  `receieve`, `simialar`) are reported, not fixed.
