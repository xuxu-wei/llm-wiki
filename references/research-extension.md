# Research Extension

Use this reference when the wiki is used for papers, books, scientific
literature, evidence maps, research ideas, proposals, articles, or perspectives.

## Default Orientation

The wiki remains a general LLM Wiki, but research sources need stronger
provenance. Do not reduce scientific sources to loose notes. Preserve citation
metadata, study type, methods, population or dataset context, and limitations.

## Citation Metadata

For each substantive paper, preprint, book, chapter, report, thesis, or dataset,
create a `sources/` page with:

- `source_kind`
- `authors`
- `title`
- `year`
- `venue` or `publisher`
- `doi`, `isbn`, or stable URL when available
- source file path under `raw/`
- abstract or source summary
- key claims
- methods or design
- evidence limits
- related wiki pages

If DOI or bibliographic metadata is missing, mark it `unknown` and avoid
inventing it. If the user needs bibliographic completeness, stop and ask whether
to search authoritative metadata sources.

## Research Page Types

- `sources/`: one page per source.
- `concepts/`: theories, mechanisms, methods, measures, constructs.
- `entities/`: authors, labs, cohorts, datasets, software, institutions.
- `syntheses/`: state-of-knowledge, evidence map summaries, literature maps.
- `comparisons/`: method comparisons, guideline comparisons, model comparisons.
- `queries/`: durable answers to research questions.

## Evidence Discipline

- Separate source claims from agent interpretation.
- Mark single-source findings as `confidence: medium` or `low` unless they are
  simple bibliographic facts.
- Mark contradictions with `status: contested` and explain the disagreement.
- Prefer dates, study design, sample, method, and measurement context over vague
  statements like "shown to improve".
- Do not treat preprints, blogs, and opinion pieces as equivalent to peer
  reviewed evidence.

## Optional Hermes Research Handoff

When a user is running repository research workflows, the wiki can supply:

- background context for `research-context-builder`;
- evidence packets for `research-opportunity-mapper`;
- source summaries for `proposal-context-brief-builder`;
- literature notes for article or perspective workflows.

Preserve artifact lineage when creating research workflow artifacts from wiki
content: source pages, wiki page names, dates, and unresolved limitations should
be listed in the handoff. The LLM Wiki should not evaluate ideas, write
proposals, or substitute for an independent evaluator.

## Literature Ingest Checklist

For each paper or book:

1. Save original raw material in `raw/papers/`.
2. If text extraction is needed, save derived Markdown/text in `raw/derived/`
   with `derived_from`, `derivation_method`, and `derived_at`.
3. Create a `sources/` page from `templates/source-summary.md`.
4. Fill citation metadata.
5. Extract concepts and entities only when durable.
6. Link to related sources and pages.
7. Update `index.md` and `log.md`.
