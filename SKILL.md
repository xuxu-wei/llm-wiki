---
name: llm-wiki
description: "Build, query, and maintain a Karpathy-style LLM Wiki: an interlinked Markdown knowledge base where raw sources are immutable, agents maintain wiki pages, and AGENTS.md defines the Claude Code collaboration contract. Use when the user asks to create a wiki, ingest sources, query or synthesize wiki knowledge, classify materials, audit links/indexes/metadata, or maintain an Obsidian-compatible research or general knowledge vault."
metadata:
  hermes:
    version: 3.5.0
    tags: [wiki, knowledge-base, markdown, obsidian, claude-code, research, citation-metadata]
    category: research
    related_skills:
      - obsidian-markdown
---

# LLM Wiki

Use this skill to create and operate a compounding Markdown wiki based on Andrej
Karpathy's LLM Wiki pattern. The runtime model is agent-config-file based:
`CLAUDE.md`, `AGENTS.md`, or another root Markdown config file is the local
contract, the human directs scope and review, and the agent maintains source
summaries, wiki pages, links, indexes, logs, and lint reports.

This skill does not create a database, embedding index, or forced Obsidian
dependency. Obsidian is optional; the wiki is plain files and must work on
Windows, macOS, and Linux.

## Load Order

1. Read `references/karpathy-pattern.md` when explaining the design or deciding
   whether a task belongs in the wiki.
2. Read `references/wiki-contract.md` before creating or changing a wiki schema,
   directory layout, page type, or metadata rule.
3. Read `references/operations.md` before ingesting, querying, linting,
   archiving, or maintaining a wiki.
4. Read `references/research-extension.md` for papers, books, scientific
   literature, citation metadata, evidence maps, and research workflow handoff.
5. Read `references/obsidian-integration.md` only when the user mentions
   Obsidian, wikilinks, Bases, Canvas, CLI, or web-page cleanup.

Use `scripts/wiki_tools.py` for repeatable local operations. It uses only the
Python standard library:

```bash
python scripts/wiki_tools.py init <wiki-path> --domain "AI research"
python scripts/wiki_tools.py init <wiki-path> --agent-platform claude
python scripts/wiki_tools.py init <wiki-path> --agent-file CUSTOM_AGENT.md
python scripts/wiki_tools.py classify <wiki-path> --move
python scripts/wiki_tools.py classify <wiki-path> --unknown-policy custom --custom-raw-dir raw/protocols --move
python scripts/wiki_tools.py hash-source <raw-source-path> --write
python scripts/wiki_tools.py update-index <wiki-path>
python scripts/wiki_tools.py lint <wiki-path>
python scripts/wiki_tools.py health <wiki-path> --inventory-limit 50
python scripts/wiki_tools.py fix <wiki-path> --dry-run
python scripts/wiki_tools.py append-log <wiki-path> --action ingest --subject "new source"
```

## Wiki Contract

A wiki root contains:

```text
CLAUDE.md or AGENTS.md
README.md
index.md
log.md
raw/inbox/
raw/articles/
raw/papers/
raw/transcripts/
raw/data/
raw/media/
raw/derived/
sources/
entities/
concepts/
syntheses/
comparisons/
queries/
_meta/
_archive/
```

- `raw/` contains immutable source material. Do not rewrite source files to fix
  interpretation errors; write corrections in wiki pages.
- `raw/derived/` contains derived text or Markdown created from raw originals,
  such as PDF extraction, OCR, transcription, or cleaned exports. Keep the
  original raw file and link derived files back to it.
- `sources/` contains source summaries and citation metadata.
- `entities/`, `concepts`, `syntheses`, `comparisons`, and `queries` contain
  agent-maintained wiki pages.
- `CLAUDE.md`, `AGENTS.md`, or another selected root Markdown file is the
  agent contract for the wiki.
- `README.md` is the human-facing guide to structure, workflow, commands,
  maintenance, and tips.
- `index.md` is the content catalog.
- `log.md` is append-only operational history.

Initialize the selected agent config from `templates/AGENTS.md`; initialize
root docs from `templates/README.md`, `templates/index.md`, and
`templates/log.md`. Use `templates/page.md`,
`templates/source-summary.md`, `templates/lint-report.md`, and
`templates/research-schema.md` as needed.

## Core Workflow

### Orient

Before changing an existing wiki:

1. Read the configured agent file: `CLAUDE.md`, `AGENTS.md`, or the custom root
   Markdown file chosen for the wiki.
2. Read `README.md`.
3. Read `index.md`.
4. Scan the recent end of `log.md`.
5. Search existing wiki pages for the current topic before creating new pages.

This prevents duplicate pages, missed links, schema drift, and repeated work.

### Create

When creating a wiki:

1. Determine the wiki path. Prefer the user's explicit path; otherwise use
   `WIKI_PATH`; otherwise use a clearly named local folder.
2. Ask for the domain only if it cannot be inferred from the user's request.
3. Run `wiki_tools.py init` or create the same contract manually from templates.
4. Let `init` choose the agent config for the current platform, or specify
   `--agent-platform claude`, `--agent-platform codex`, or `--agent-file`.
   If the selected config already exists, append the LLM Wiki contract instead
   of overwriting unrelated project instructions.
5. If the wiki is research-oriented, include `templates/research-schema.md`
   guidance in the selected agent config.
6. Confirm the root files and raw/wiki directories exist.

### Ingest

When ingesting a URL, file, pasted text, paper, book, or dataset:

1. Put the original material in `raw/inbox/` first.
2. Run or emulate `wiki_tools.py classify` to move it to the correct raw folder.
   Unknown types stay in `raw/inbox/` by default and require user
   classification or an explicit `--custom-raw-dir`.
3. If a PDF, image, audio, video, or other source needs extraction, preserve
   the original raw file and place the derived Markdown/text in `raw/derived/`
   with `derived_from`, `derivation_method`, and `derived_at` metadata.
4. Hash raw sources when version consistency matters. Text sources use
   `sha256_body_v1`: UTF-8 or UTF-8 BOM text, frontmatter excluded, newlines
   normalized to LF, hashed with Python `hashlib.sha256`. Binary sources use
   `sha256_bytes_v1`.
5. Create or update one `sources/` summary page for each substantive source,
   including `raw_source`, optional `derived_source`, `raw_hash_scheme`,
   `raw_sha256`, and `raw_hashed_at` when available.
6. For papers, books, reports, chapters, and scientific literature, capture
   citation metadata: authors, title, year, journal or publisher, DOI, ISBN,
   URL, and source kind when available.
7. Search `index.md` and the wiki before creating entity or concept pages.
8. Update relevant pages with sourced claims and `[[wikilinks]]`.
9. Run `update-index`, append `log.md`, and report changed files.

### Query

When answering from the wiki:

1. Orient first.
2. Read the relevant page set; for large wikis also search all Markdown files.
3. Synthesize from wiki pages and cite the wiki page names used.
4. File only durable, non-trivial answers into `queries/`, `syntheses/`, or
   `comparisons/`.
5. Append the query decision to `log.md`.

### Maintain

When auditing or maintaining:

1. Run `wiki_tools.py lint <wiki-path>`.
2. Run `wiki_tools.py health <wiki-path>` when drift or update scope matters.
   `health` reports `update_required`, `drifted_sources`, affected pages,
   relationship issues, source hash issues, metadata schema issues, field order
   issues, and a capped metadata inventory; it does not rewrite wiki knowledge.
3. Fix broken wikilinks, missing index entries, missing frontmatter,
   malformed frontmatter, unclassified inbox items, citation metadata gaps,
   source/derived relationship gaps, source hash drift, orphan pages, and pages
   over the size threshold.
   Frontmatter list fields must use inline bracket syntax such as
   `tags: [ai-ml, methodology]`, `sources: [sources/paper.md]`, and
   `authors: ["Smith, John", "Doe, Jane"]`.
4. Run `wiki_tools.py fix <wiki-path>` only when safe placeholder insertion and
   frontmatter field reordering are desired. It does not invent semantic
   metadata or resolve hash drift.
5. When drift occurs, review the affected `sources/` page first, then update
   related wiki pages, links, properties, tags, `index.md`, and `log.md`.
6. Use `templates/lint-report.md` when the report needs to be filed.
7. Append a `lint` or `maintain` entry to `log.md`.

### Archive

Archive instead of deleting when material is superseded or out of scope:

1. Move the page under `_archive/` preserving its old folder path.
2. Remove it from `index.md`.
3. Replace incoming links with plain text plus an archive note, or link to the
   archived path if the wiki contract allows it.
4. Append an `archive` entry to `log.md`.

Confirm with the user before deletion, schema changes, mass archival, or any
operation expected to touch more than 10 wiki pages. Also confirm before
creating a new `raw/<category>/` for a document type that cannot be confidently
classified into existing raw categories.

## Obsidian Boundary

Use `obsidian-markdown` for Obsidian-flavored Markdown details such as
wikilinks, embeds, properties, callouts, and tags. Treat these as optional:

- `defuddle`: optional web-page cleanup before saving raw articles.
- `json-canvas`: optional visual maps of wiki concepts or research evidence.
- `obsidian-bases`: optional dashboard views over source/page metadata.
- `obsidian-cli`: optional operations against a running Obsidian vault.

Do not require Obsidian, Node.js, systemd, shell-specific syntax, or a GUI for
the core wiki workflow.

## Stop Rules

Stop and ask before proceeding when:

- The target wiki path is ambiguous and a wrong path could modify unrelated
  notes.
- A schema or directory contract change would invalidate existing pages.
- A source contradicts existing content and the correct resolution is a domain
  judgment rather than a date/provenance issue.
- An ingest or cleanup would touch more than 10 wiki pages.
- Required citation metadata for a scientific source is unavailable and the user
  needs bibliographic completeness.
