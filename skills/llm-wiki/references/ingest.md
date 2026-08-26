# Ingest

Use this procedure for a URL, local file, pasted text, dataset, media item, or a
small source batch. Apply the global raw-integrity, containment, provenance, and
untrusted-source invariants throughout. Load the research extension only for
scientific or research evidence.

## Preflight

Place new originals in `raw/inbox/` without rewriting them. Preview identity,
content hashes, classification, duplicate content, source-name conflicts, and
unsafe paths before moving or authoring anything:

```bash
python "<skill-dir>/scripts/wiki_tools.py" ingest-preflight <wiki-path> [paths ...]
```

Omit paths to inspect `raw/inbox/`. Add `--recursive` only for an intentional
tree, or `--category {articles,papers,transcripts,data,media,derived}` when the
classification is known. Preflight is read-only JSON. Reuse an existing raw file
and source summary for an identical content hash. If a stable identity such as a
canonical URL, DOI, or ISBN matches but the content differs, treat it as a new
version and stop for confirmation. Leave unknown types in the inbox unless the
user approves a category.

## Produce knowledge

Classify the original without changing its bytes. If extraction, OCR,
transcription, cleanup, or export is needed, write a separate file under
`raw/derived/` and record its original, method, date, and the raw hash observed
at derivation time. Never treat a derived artifact as the original.

Create or update one matching `sources/` page. Put hashes and source identity
there, omit optional fields that do not apply, and use the research template
only for a compatible research kind. Separate source claims from agent
interpretation. Add a page, section, timestamp, row range, or similarly compact
locator for important quantitative, time-sensitive, high-impact, or contested
claims; ordinary claims may remain page-level sourced.

Search bounded context before creating durable pages. Merge into existing
entities or concepts when possible, and create only reusable knowledge. Keep
each claim traceable to a source page. For a batch, process sources into short
fact cards first, then consolidate names, concepts, conflicts, and page updates
instead of loading every original at once.

## Finalize

Validate all affected source pages together, then refresh navigation with
per-file atomic replacement and append one ingest record only if the batch is
complete:

```bash
python "<skill-dir>/scripts/wiki_tools.py" ingest-finalize <wiki-path> <source-pages...> --log-action "ingest batch"
```

Finalization validates before writing and rolls navigation and log back on a
detected command failure. It is not a crash-recovery journal, so after abrupt
process or machine failure, inspect both files and recover from version control
before retrying. Any source/derived relationship, hash, duplicate identity,
source reference, or frontmatter failure blocks the batch. Report reused items,
new versions, created and updated pages, unresolved conflicts, and the final log
entry. Stop before applying a batch expected to change more than 10 wiki pages.
