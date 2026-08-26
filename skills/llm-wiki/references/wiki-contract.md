# Wiki Contract

Use this contract as the single semantic authority for wiki structure,
metadata, profiles, provenance, and lifecycle. Templates provide file shapes;
the CLI validates deterministic rules.

## Root and directories

- `AGENTS.md`: default operating contract for Codex. Use `CLAUDE.md` only for
  explicit Claude compatibility, or another root Markdown file when selected.
- `README.md`: human-facing domain and layout guide; it is not required context
  for every agent task.
- `index.md`: generated catalog of durable pages.
- `log.md`: append-only history of successful mutations and requested audits.
- `_meta/schema.json`: generated machine state with `schema_version: 2`, the
  selected `core` or `research` profile, and Markdown as the source of truth.

```text
raw/inbox/        Unprocessed originals.
raw/articles/     Web pages, documentation, blogs, and clippings.
raw/papers/       Papers, books, chapters, reports, preprints, PDFs, and EPUBs.
raw/transcripts/  Meetings, interviews, lectures, captions, and chats.
raw/data/         Tables, structured files, spreadsheets, and datasets.
raw/media/        Images, audio, video, diagrams, and attachments.
raw/derived/      Extracted or transformed text linked to an original.
sources/          One summary per substantive source or source version.
entities/         People, organizations, products, datasets, tools, projects.
concepts/         Concepts, methods, phenomena, definitions, and topic notes.
syntheses/        Cross-source state-of-knowledge pages.
comparisons/      Side-by-side analyses.
queries/          Durable filed answers with future reuse value.
_meta/            Taxonomies, maps, reports, and administrative state.
_archive/         Superseded or out-of-scope pages preserved by old path.
```

Use lowercase hyphenated `.md` slugs. Keep raw filenames descriptive and stable.
Prefer one page per durable entity or concept, do not create pages for passing
mentions, and consider splitting a page near 200 lines. Unknown file types stay
in `raw/inbox/` until a category is explicitly selected; do not silently create
new raw categories.

## Raw and derived integrity

The most original available artifact is immutable after intake. Never insert
frontmatter, normalize newlines, re-encode, format, or otherwise change a file
under `raw/`. Corrections and interpretation belong in source summaries and
durable pages.

Store extraction, OCR, transcription, cleanup, and export results separately in
`raw/derived/` with this frontmatter:

```yaml
---
derived_from: raw/papers/source-file.pdf
derivation_method: pdf-text-extraction
derived_at: YYYY-MM-DD
source_hash_at_derivation: <sha256>
source_hash_scheme_at_derivation: sha256_bytes_v1
---
```

`derived_from` must resolve to a non-derived raw original. Record the two hash
fields whenever the original has been hashed. If a later raw hash differs,
health reports `derived_stale`; regenerate or review the derived artifact before
using it as current evidence. An original text, Markdown, or HTML file belongs
in its classified raw directory, not `raw/derived/`.

Text hashes use `sha256_body_v1`: decode UTF-8 or UTF-8 BOM, exclude existing
frontmatter only for the calculation, normalize line endings to LF, and hash the
body with SHA-256. Binary hashes use `sha256_bytes_v1` over exact bytes. Store
both schemes and digests on source summaries or in machine state, never by
writing into the raw original.

## Common page schema

Use this order for every durable page:

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

Source pages may use `sources: []`; every non-source page needs at least one
resolving `sources/*.md` entry. Use inline bracket lists. Quote list items that
contain commas, including names in `Family, Given` form. The supported parser
does not accept indented YAML lists. Keep custom fields after canonical fields.

`confidence` describes evidentiary support, not writing quality. Use `status:
contested` for unresolved disagreement, `superseded` when a current replacement
exists, and `archived` only after archival. Do not let automated repair invent
either field.

## Source profiles

All `sources/` pages use the **core profile**. Select the **research profile**
for a wiki centered on scientific literature, formal research reports, theses,
or datasets. The selected wiki-wide profile lives in `_meta/schema.json` and
controls validation behavior; it is not repeated as page frontmatter. A core
wiki may still hold an individual research source with its applicable metadata.

Core source fields extend the common schema:

```yaml
source_kind: article | transcript | dataset | media | paper | preprint | book | chapter | report | thesis
raw_source: raw/articles/source-file.ext
raw_hash_scheme: sha256_body_v1 | sha256_bytes_v1
raw_sha256: <sha256>
raw_hashed_at: YYYY-MM-DD
```

Add `derived_source` only when a matching file exists under `raw/derived/`; its
`derived_from` must point to the same `raw_source`. Add `url` only when it is a
stable source identity or access location. Once ingestion is finalized, the raw
path and three hash fields are required. An older incomplete source page may be
diagnosed without being silently filled.

Research source pages add only applicable bibliographic fields:

| Field | Apply when |
| --- | --- |
| `authors` | Named creators are available. |
| `year` | A publication, release, or dataset year is known. |
| `venue` | A paper, preprint, or chapter has a journal, conference, repository, or proceedings venue. |
| `publisher` | A book, report, thesis, or dataset has a responsible publisher or institution. |
| `doi` | A DOI was issued for a paper, preprint, chapter, report, or dataset. |
| `isbn` | A book or chapter belongs to an ISBN-bearing publication. |
| `url` | A stable landing page or canonical web identity exists. |

Omit optional fields that are unavailable or inapplicable. Do not write
`unknown`, empty placeholder values, or invented metadata. Missing applicable
metadata is a warning; it becomes blocking only when the user requested
bibliographic completeness or the missing identity prevents safe deduplication.
Use `venue`, not aliases such as `journal`.

## Links, evidence, and taxonomy

Use `[[wikilinks]]` for internal pages. New durable pages should link to at
least two related pages when those pages exist. Broken links outrank orphan-page
style issues. Define the tag vocabulary in the root agent contract or
`_meta/tags.md`, add terms before use, and prefer a small stable vocabulary.

Keep source claims distinct from agent interpretation. Page-level `sources`
references are sufficient for ordinary claims. Add a compact locator—page,
section, timestamp, table, figure, or row range—to quantitative,
time-sensitive, high-impact, or contested claims. Never imply that a locator
resolves a semantic conflict; preserve both positions and their provenance.

## Lifecycle and mutation rules

The lifecycle is:

```text
raw/inbox -> classified immutable raw -> optional derived artifact
          -> source summary -> linked durable pages -> reusable synthesis
          -> superseded page -> _archive
```

Content identity hash is the primary duplicate check; canonical URL, DOI, and
ISBN are stable identity checks. Identical content reuses the current raw file
and source summary. The same identity with different content is a candidate new
version and requires confirmation. Duplicate slugs, aliases, and ambiguous
wikilinks are blocking until resolved.

File a query only when it is a durable, non-trivial result. Read-only queries do
not modify `log.md`. Log successful ingestion, durable query filing, repair,
schema change, and archival only after all related writes succeed. Keep the log
append-only.

Archive rather than delete: preserve the old relative path under `_archive/`,
record `archived_at`, `archive_reason`, and optional `replaced_by`, remove the
page from the active index, and repair incoming links. Deletion requires
explicit approval.

Managed rewrites use a same-directory temporary file and atomic replacement.
Compare the pre-write content hash immediately before replacement; abort on
concurrent change. Batch finalization validates all inputs first and uses
in-process rollback if a later write fails; it is not a persistent transaction
journal for recovery from abrupt process or machine failure.

`lint` and `health` diagnose without rewriting. `fix` skips `raw/`, operates only
after `--dry-run`, and fails closed when frontmatter contains comments, nested
structures, duplicate keys, unsupported YAML, or content it cannot round-trip.
It may perform mechanically safe normalization but never invent semantic or
bibliographic values.
