# MCP security blog series: plan

Spec: docs/superpowers/specs/2026-10-06-blog-series-design.md
Blog repo: `../dominicfarr.github.io`. No commits in either repo.

Pinned commit for code links: `03a9398833959d25549665bbd053ddff54430393` (`SHA` below).
Link form: `https://github.com/dominicfarr/mcp-secuirty-working-notes-example/blob/SHA/<path>#L<a>-L<b>`.

## Task 1: build baseline

1. `BUNDLE_PATH=<scratchpad>/bundle bundle install` in the blog repo (gems go to the scratchpad,
   nothing is written to the repo; `Gemfile.lock` must be unchanged afterwards: check
   `git status`).
2. `bundle exec jekyll build -d <scratchpad>/site` succeeds before any change. Note any existing
   warnings so they aren't blamed on the series.

Caveat to report: the site uses GitHub Pages' legacy build (the `github-pages` gem, Jekyll 3.x),
not this Gemfile. The local Jekyll 4 build approximates it; nothing in the series relies on a
Jekyll 4 feature.

## Task 2: Mermaid support

1. Write a throwaway test page in the scratchpad build (not the repo) with a ` ```mermaid ` block;
   build; confirm the HTML has the block wrapped as `div.language-mermaid` and no Mermaid script
   (fails first: the script isn't there yet).
2. Add to `_includes/footer.html`, after the wrapper div, inside `{% if page.mermaid %}`:
   an ES module script importing `mermaid@11` from `cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs`,
   replacing each `div.language-mermaid` (or `pre > code.language-mermaid`, as the local build
   emits) with `<pre class="mermaid">` holding its text,
   `mermaid.initialize({startOnLoad: false, theme: "default"})`, then `mermaid.run()`.
   (Done differently at first: a dark theme from `prefers-color-scheme`. The browser check
   showed the site never goes dark, since minima 2.5 ignores `skin: auto`, so it was dropped.)
3. Rebuild: a `mermaid: true` page includes the script; a page without the flag (e.g. `about`)
   doesn't.

## Task 3: glossary entries

Create in `_glossary/`, matching `api.md` (front matter `title`, 1–3 plain sentences):
`mcp`, `mcp-client`, `mcp-server`, `tool-call`, `human-in-the-loop`, `roots`, `elicitation`,
`path-traversal`, `toctou`, `fail-closed`, `audit-log`, `prompt-injection`, `stdio-transport`.
Definitions are general, not repo-specific; where useful, one sentence says how the series uses
the term. Build: each appears at `/glossary/<name>/index.html`.

## Task 4: hub (rewrite `_posts/2026-10-01-mcp-security.md`)

Keep front matter date, title and the SVG. Add: what the series is, who it's for, the threat model
in one paragraph (Part 1 = deterministic controls, no LLM), the repo link, how to run it (three
commands from the README), and the numbered series index linking the five parts. Add
`tags: [agentic, tooling, security]`. No Mermaid needed (`mermaid` flag omitted).

## Tasks 5–9: the five parts

One task per part, in order, each the same steps:

1. Pull the snippets from the repo at `SHA` (`git show SHA:<path>`), record exact line ranges.
2. Draft to the spec's six sections (question, threat, spec position, implementation, gotchas,
   proof), front matter `layout: post`, `title`, `date`, `tags: [agentic, tooling, security]`,
   `mermaid: true`. Spec links go to modelcontextprotocol.io (version 2025-06-18 pages for
   roots, elicitation, tools, security best practices).
3. Glossary link on first use of each term; prev / hub / next footer.
4. Run the `humanizer` skill over the prose (not the code blocks).
5. Check 600–900 words of prose (code and Mermaid excluded).

| Task | File | Dates | Core snippets (paths at SHA) |
|---|---|---|---|
| 5 | `2026-10-02-mcp-security-client-permissions.md` | 2026-10-02 | `client/permissions.py` defaults; `client/client.py` `ToolRequest`, `check_permission`, `request_tool`, `approve` |
| 6 | `2026-10-03-mcp-security-roots.md` | 2026-10-03 | `server/server.py` `allowed_areas`, `client_roots`, `resolve_in_roots`; `file_uri_path` |
| 7 | `2026-10-04-mcp-security-elicitation.md` | 2026-10-04 | `DeleteConfirmation`, `confirm_deletion`; client `_on_elicitation`, `answer_input` validation |
| 8 | `2026-10-05-mcp-security-delete-end-to-end.md` | 2026-10-05 | `delete_file`; `file_identity`; audit records from both sides |
| 9 | `2026-10-06-mcp-security-proving-it.md` | 2026-10-06 | `tests/conftest.py` isolation; test names by file; `.githooks/pre-push`; CI workflow; S2083 false positive |

## Task 10: this repo's README

One line under the title: "Write-up: [MCP Security series](https://www.domfarr.com/…)" with the
hub's real URL from the build output.

## Task 11: verification (scratchpad scripts only)

1. `bundle exec jekyll build` clean (no new warnings vs Task 1).
2. Extract every Mermaid block from the five parts; render each with
   `npx -y @mermaid-js/mermaid-cli -i <block>.mmd -o <block>.svg`; all succeed.
3. For every code block followed by a "full source" link: fetch `git show SHA:<path>`, take the
   linked lines, and check every non-`# …` snippet line appears in them in order.
4. Every `/glossary/<name>/` link has a built page; every internal post link resolves in the
   build; every repo link names a file present at `SHA`.
5. Word counts 600–900 per part.
6. `git status` in both repos: only the expected files changed, nothing committed.

## Task 12: review

One fresh reviewer subagent (strong model) reads the spec, the six posts and the code at `SHA`,
checking technical accuracy of every claim, snippet fidelity, and spec conformance. Re-grade
findings by effect on a reader; fix the important ones and re-run Task 11; list deferred minors.

## Final message

Changed files in each repo; verification results with output; reviewer findings and what was
fixed; the legacy-build caveat; the glossary typo list; every decision made for the user.
