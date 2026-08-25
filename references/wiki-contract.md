# Wiki Contract

Use this contract when creating or changing an LLM Wiki.

## Root Files

- `CLAUDE.md`, `AGENTS.md`, or another root Markdown config file: platform
  specific instructions for agents operating in the wiki.
- `README.md`: human-facing guide to structure, workflow, commands, tips, and
  maintenance.
- `index.md`: generated or maintained catalog of wiki pages.
- `log.md`: append-only timeline of actions.

## Directory Layout

```text
raw/inbox/        New materials before classification.
raw/articles/     Web articles, blog posts, documentation, clippings.
raw/papers/       Papers, books, chapters, reports, preprints, PDFs, EPUBs.
raw/transcripts/  Meetings, interviews, lectures, captions, chats.
raw/data/         CSV, TSV, JSON, spreadsheets, datasets.
raw/media/        Images, audio, video, diagrams, attachments.
raw/derived/      Derived Markdown or text from preserved raw originals.
sources/          One source summary per substantive source.
entities/         People, organizations, products, datasets, tools, projects.
concepts/         Concepts, methods, phenomena, definitions, topic notes.
syntheses/        Cross-source synthesis and state-of-knowledge pages.
comparisons/      Side-by-side analyses.
queries/          Durable filed answers worth reusing.
_meta/            Taxonomies, maps, dashboards, lint reports, admin notes.
_archive/         Superseded or out-of-scope pages.
```

## Naming

- Use lowercase slugs with hyphens and `.md`.
- Keep raw filenames descriptive and stable.
- Prefer one page per durable entity or concept.
- Do not create pages for passing mentions.
- Split pages over roughly 200 lines.

## Raw Originals And Derived Text

Keep the most original available file. A PDF, image, audio file, video, EPUB,
or dataset should remain in the appropriate `raw/` category even if the agent
also creates Markdown, OCR text, a transcript, or a cleaned export.

Store derived text in `raw/derived/` and include:

```yaml
---
derived_from: raw/papers/source-file.pdf
derivation_method: pdf-text-extraction | ocr | transcription | cleanup | export
derived_at: YYYY-MM-DD
source_hash_at_derivation: optional raw source hash
source_hash_scheme_at_derivation: optional hash scheme
---
```

If the incoming document is already the original `.txt`, `.md`, or `.html`
source, store it directly in the appropriate raw category instead of
`raw/derived/`.

## Agent Config Selection

Initialize the root agent contract according to the active ecosystem:

- Claude Code: `CLAUDE.md`.
- Codex/OpenAI agents: `AGENTS.md`.
- Other agents: use `AGENTS.md` by default, or pass an explicit root Markdown
  filename when initializing.

If the selected config file already exists, append the LLM Wiki contract under
a marked section instead of overwriting unrelated project instructions.

## Classification Fallback

Do not silently scatter unknown document types into guessed directories.
Unknown file extensions should remain in `raw/inbox/` until the user chooses an
existing category or explicitly creates a new `raw/<category>/`.

## Page Frontmatter

Wiki pages should use this common field order:

```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: source | entity | concept | synthesis | comparison | query
tags: [tag]
sources: [sources/source-slug.md]
summary: One-line summary for index.md
confidence: high | medium | low
status: active | contested | superseded | archived
---
```

`wiki_tools.py health` reports `field_order_issues` when present fields are out
of canonical order. `wiki_tools.py fix` can reorder fields and insert missing
safe placeholders without inventing semantic metadata.

`confidence` and `status` are especially important for fast-moving topics,
single-source claims, or unresolved contradictions.

Use inline bracket syntax for every list-valued field. The helper script uses a
minimal frontmatter parser and intentionally rejects indented YAML lists.

```yaml
# Correct
tags: [ai-ml, methodology]
sources: [sources/paper.md]
authors: ["Smith, John", "Doe, Jane"]

# Incorrect
tags:
  - ai-ml
  - methodology
```

Quote any list item that contains a comma, especially author names in
`Family, Given` form. `wiki_tools.py lint` and `wiki_tools.py health` report
these problems under `frontmatter_format`.

## Scientific Source Metadata

For papers, books, chapters, reports, and other scientific literature, source
summary pages should include these fields when available:

```yaml
title: Source Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: source
tags: [source]
sources: []
summary: One-line source summary for index.md
confidence: medium
status: active
source_kind: paper | preprint | book | chapter | report | thesis | dataset
authors: ["Family Name, Given Name"]
year: 2026
venue: Journal or conference name
publisher: Publisher name
doi: 10.xxxx/yyyy
isbn: 000-0-00-000000-0
url: https://example.org/source
raw_source: raw/papers/source-file.pdf
derived_source: raw/derived/source-file.md
raw_hash_scheme: sha256_bytes_v1
raw_sha256: hex-digest
raw_hashed_at: YYYY-MM-DD
```

Use `venue` for journals, conferences, repositories, and proceedings. Use
`publisher` for books and formal reports. If a required bibliographic field is
unknown, write `unknown` rather than inventing it.

For `source_kind: paper` and `source_kind: preprint`, missing or `unknown`
citation fields are maintenance issues. Missing or `unknown` source hash fields
are source integrity issues.

## Source Summary Raw Provenance

Every substantive `sources/` page should point back to its raw source and, when
available, record the raw source hash:

```yaml
raw_source: raw/papers/source-file.pdf
derived_source: raw/derived/source-file.md
raw_hash_scheme: sha256_bytes_v1
raw_sha256: hex-digest
raw_hashed_at: YYYY-MM-DD
```

Use stable slug/path identifiers for raw files and source summaries. Do not
introduce sequential integer IDs unless a separate user workflow explicitly
requires them.

`raw_source`, `raw_hash_scheme`, `raw_sha256`, and `raw_hashed_at` should be
present on each source summary. If a source has `derived_source`, it must point
to `raw/derived/`, and the derived file's `derived_from` must point back to the
same `raw_source`.

## Raw Source Metadata

Text raw sources may use:

```yaml
---
source_url: https://example.org/source
ingested: YYYY-MM-DD
sha256: hex-digest
hash_scheme: sha256_body_v1
hashed_at: YYYY-MM-DD
source_kind: article | paper | book | transcript | dataset | media
---
```

`sha256_body_v1` is computed over UTF-8 or UTF-8 BOM text after frontmatter is
excluded and newlines are normalized to LF. Raw binary files use
`sha256_bytes_v1`, computed over raw bytes, and are tracked from their source
summary page.

Hash drift means the source version changed; it does not by itself prove the
wiki interpretation is wrong. Use `wiki_tools.py health <wiki-path>` to locate
affected source summaries and dependent pages before updating knowledge pages.

## Health And Fix Rules

Run `wiki_tools.py health <wiki-path>` for read-only diagnosis. It reports:

- `relationship_issues`: broken or missing raw/source/derived/page references.
- `source_hash_issues`: missing, unknown, unsupported, mismatched, or drifted
  source hashes.
- `metadata_schema_issues`: missing fields, placeholder values, invalid types,
  and invalid controlled values.
- `field_order_issues`: frontmatter fields not in canonical order.
- `metadata_inventory`: capped unique values for all frontmatter fields.
- `noncanonical_fields`: known alias fields such as `journal -> venue`.

Run `wiki_tools.py fix <wiki-path>` only when automatic frontmatter
normalization is desired. It reorders fields, inserts safe placeholders, and
preserves custom fields after canonical fields. It does not resolve hash drift,
invent citation metadata, or choose between contradictory sources.

## Link Rules

- Use `[[wikilinks]]` for internal wiki links.
- Every non-source page `sources` entry should resolve to an existing
  `sources/*.md` page.
- New durable pages should link to at least two related pages when such pages
  exist.
- When a page is archived, remove it from `index.md` and update incoming links.
- Broken links are higher priority than orphan pages.

## Tag Rules

- Define tag taxonomy in `AGENTS.md` or `_meta/tags.md`.
- Add a tag to the taxonomy before using it.
- Prefer a small stable vocabulary over many near-duplicates.

## Lifecycle

- `raw/inbox`: unprocessed.
- classified raw source: preserved original.
- `raw/derived`: optional derived text linked back to a preserved raw original.
- `sources/`: source interpreted and bibliographically described.
- durable page: linked and indexed.
- synthesis/comparison/query: filed only when reuse value is high.
- `_archive`: superseded or out-of-scope material.
