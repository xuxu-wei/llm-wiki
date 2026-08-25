# LLM Wiki Agent Contract

This directory is an LLM Wiki. It is designed for Claude Code style agent
collaboration over plain Markdown files.

## Purpose

Describe the domain here.

## Agent Contract

- Orient before changing the wiki: read this agent config file, `README.md`,
  `index.md`, and recent `log.md`.
- Preserve raw sources under `raw/`; do not rewrite them as interpretation
  changes.
- Prefer updating existing durable pages over creating duplicates.
- Add new durable pages to `index.md`.
- Append every ingest, query filing, lint, archive, schema change, or major
  maintenance action to `log.md`.
- Use `wiki_tools.py health` before updating pages because of source drift.
- Confirm before schema changes, deletion, mass archival, or any operation
  expected to touch more than 10 wiki pages.

## Directory Contract

See `README.md` for the human-facing description. Required directories:

- `raw/inbox/`
- `raw/articles/`
- `raw/papers/`
- `raw/transcripts/`
- `raw/data/`
- `raw/media/`
- `raw/derived/`
- `sources/`
- `entities/`
- `concepts/`
- `syntheses/`
- `comparisons/`
- `queries/`
- `_meta/`
- `_archive/`

## Page Rules

- Use lowercase hyphenated slugs.
- Use Obsidian-style `[[wikilinks]]` for internal links.
- Use frontmatter on all wiki pages.
- Write all list-valued frontmatter fields with inline brackets, for example
  `tags: [concept]`, `sources: [sources/paper.md]`, and
  `authors: ["Smith, John", "Doe, Jane"]`. Do not use indented YAML lists.
- Keep frontmatter fields in canonical order. Use `wiki_tools.py health` to
  diagnose order/schema problems and `wiki_tools.py fix` to normalize safe
  placeholders and field order.
- Keep source summaries separate from synthesis pages.
- Keep original raw files; put extraction/OCR/transcription Markdown in
  `raw/derived/` with provenance metadata.
- Ensure each source summary can trace to its `raw_source`; when it has a
  `derived_source`, that derived file must point back with `derived_from`.
- Mark low-confidence or contested claims explicitly.
- Split pages over roughly 200 lines.

## Tag Taxonomy

Add tags here before using them.

- source
- entity
- concept
- synthesis
- comparison
- query
- contested
- archived

## Scientific Literature Rules

For papers, books, chapters, reports, theses, datasets, and other scientific
sources, capture citation metadata when available:

- authors
- title
- year
- venue
- publisher
- DOI
- ISBN
- URL
- source kind

If a bibliographic field is unknown, write `unknown`; do not invent it.
