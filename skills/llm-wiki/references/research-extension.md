# Research Extension

Apply this extension to papers, preprints, books, chapters, formal reports,
theses, datasets, evidence maps, and research-workflow handoffs. It adds evidence
discipline to the normal ingest procedure; it does not change the wiki's plain
Markdown architecture.

## Interpret research sources

Preserve the work's source kind, named creators, date, study or artifact type,
population or dataset context, methods, measurements, and limitations when the
source provides them. Use only bibliographic fields applicable to that source
kind. Omit an unavailable DOI, ISBN, venue, publisher, URL, or other optional
field instead of inserting `unknown`, an empty placeholder, or a guess. Stop to
obtain authoritative metadata only when the user requires bibliographic
completeness or identity cannot otherwise be established.

Separate these layers explicitly:

- what the authors or artifact report;
- what the design and measurements actually support;
- limitations, access gaps, and unresolved questions;
- the agent's cross-source interpretation.

Do not treat a preprint, blog, opinion piece, observational study, controlled
experiment, systematic review, and benchmark dataset as equivalent evidence.
Keep dates, sample or corpus, method, comparator, outcome definition, and
measurement context near any claim whose meaning depends on them. Carry a page,
section, figure, table, timestamp, or row locator for important numerical or
contested findings.

Use `confidence: medium` or `low` for substantive single-source findings unless
confidence is justified by the evidence, not merely by publication venue. Use
`status: contested` when credible sources disagree. Preserve each side and its
provenance; do not manufacture consensus.

## Produce durable research knowledge

Create the source summary first. Update durable concept or entity pages only
for reusable methods, constructs, datasets, tools, cohorts, organizations, or
findings. Use synthesis pages for state-of-knowledge conclusions and comparison
pages for explicit method, model, or guideline comparisons. File research
queries only when their answer will be useful again.

For a literature batch, produce one short fact card per source before merging
candidate concepts, identities, findings, and conflicts. This bounded
map/reduce pattern avoids loading all originals simultaneously and makes source
attribution easier to audit.

## Hand off to other research workflows

When another compatible workflow is available, provide a compact packet of
source-page paths, durable page names, dates, locators, confidence, contested
points, and unresolved limitations. Preserve lineage into any proposal,
article, perspective, evidence map, or idea artifact. The wiki supplies compiled
context; it does not replace independent evaluation, peer review, statistical
review, or methodology review.
