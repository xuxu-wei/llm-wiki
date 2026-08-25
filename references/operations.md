# Operations

## Create

1. Determine the wiki path.
2. Create the directory contract from the selected root agent config file,
   `templates/README.md`, `templates/index.md`, and `templates/log.md`.
   Use `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex/OpenAI agents, and
   `--agent-file` for another root Markdown config file. If that file already
   exists, append the LLM Wiki contract instead of overwriting it.
3. Customize domain, tag taxonomy, page thresholds, and source rules.
4. If the wiki is research-heavy, merge the relevant parts of
   `templates/research-schema.md` into `AGENTS.md`.
5. Run `python scripts/wiki_tools.py lint <wiki-path>` and fix structural
   warnings before ingesting sources.

## Orient

Always orient before changing an existing wiki:

1. Read the configured agent file: `CLAUDE.md`, `AGENTS.md`, or the custom root
   Markdown file chosen for the wiki.
2. Read `README.md`.
3. Read `index.md`.
4. Read the recent end of `log.md`.
5. Search existing pages for topic terms, aliases, and source titles.

## Ingest

1. Save original material under `raw/inbox/`.
2. Classify it with `wiki_tools.py classify <wiki-path> --move`, or classify
   manually using the same folder contract. Unknown types stay in `raw/inbox/`
   by default; ask whether to use an existing category or create a new
   `raw/<category>/`.
3. If extraction is needed, keep the original raw file and save derived
   Markdown/text under `raw/derived/` with `derived_from`,
   `derivation_method`, and `derived_at`.
4. Hash raw text files with `wiki_tools.py hash-source <path> --write` when
   frontmatter hashing is useful. Text hashes use `sha256_body_v1`: UTF-8
   text, frontmatter excluded, newlines normalized to LF. Binary sources use
   `sha256_bytes_v1` and are recorded from the source summary page.
5. Create or update a `sources/` summary page, recording `raw_source`,
   optional `derived_source`, `raw_hash_scheme`, `raw_sha256`, and
   `raw_hashed_at` when available.
6. Extract durable entities, concepts, claims, methods, dates, and open
   questions.
7. Update existing pages before creating new ones.
8. Create only pages that meet the wiki's page threshold.
9. Add internal links, source references, and confidence/status fields.
10. Run `update-index`, append `log.md`, and report changed files.

## Bulk Ingest

For multiple sources:

- Read or summarize all sources first.
- Build one list of candidate entities/concepts.
- Search once for existing pages.
- Batch page updates.
- Update `index.md` once.
- Write one log entry with all source files and pages touched.

Confirm first if the batch will touch more than 10 wiki pages.

## Query

1. Orient.
2. Read relevant pages.
3. Search all Markdown files if the wiki is large or the index is stale.
4. Answer from wiki pages and cite page names.
5. File the answer only if it is a durable synthesis, comparison, or deep-dive
   query result.
6. Log whether the answer was filed.

## Maintain

Run `wiki_tools.py lint <wiki-path>` to locate structural problems and
`wiki_tools.py health <wiki-path>` when source drift or update scope matters.
Use `wiki_tools.py fix <wiki-path> --dry-run` to preview automatic frontmatter
normalization, then run `wiki_tools.py fix <wiki-path>` only when the changes
are acceptable.

Address:

- Missing root files.
- Unclassified `raw/inbox/` files.
- Broken wikilinks.
- Missing required frontmatter.
- Malformed frontmatter, especially indented YAML lists instead of inline
  bracket lists for `tags`, `sources`, `authors`, and other list fields.
- Missing citation metadata for scientific sources.
- Source provenance gaps such as `derived_source` without `raw_source`.
- Broken relationships among raw originals, `raw/derived/` files, source
  summaries, and page `sources` fields.
- Missing or `unknown` `raw_sha256`, `raw_hash_scheme`, or `raw_hashed_at` on
  source summaries.
- Derived files missing `derived_from`, `derivation_method`, or `derived_at`.
- Frontmatter fields that are present but not in canonical order.
- Noncanonical metadata names such as `journal` instead of `venue`.
- Pages absent from `index.md`.
- Orphan pages.
- Raw source hash drift.
- Pages over the line threshold.
- Log files needing rotation.

When `health` reports `update_required: true`, review the affected `sources/`
page first, then update dependent wiki pages, links, properties, tags,
`index.md`, and `log.md`. Hash drift means the source version changed; it does
not automatically decide the correct interpretation.

Use the `metadata_inventory` section from `health --json` before bulk cleanup or
ingest. It gives the agent a capped list of existing field names and values so
new metadata does not introduce avoidable synonyms.

## Archive

1. Move the page into `_archive/` preserving the old relative path.
2. Remove it from `index.md`.
3. Update incoming links.
4. Mark related pages with the replacement page or reason.
5. Append an `archive` log entry.

Delete only with explicit user approval.

## Log Actions

Use these actions consistently:

- `create`
- `ingest`
- `update`
- `query`
- `lint`
- `health`
- `maintain`
- `archive`
- `delete`
- `schema`

Format:

```markdown
## [YYYY-MM-DD] action | subject
- Files: path-a, path-b
- Notes: concise reason
```
