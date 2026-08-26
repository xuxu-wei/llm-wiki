# Karpathy LLM Wiki Pattern

Karpathy's LLM Wiki pattern treats a wiki as compiled memory for agents. The
agent should not rediscover the same knowledge from raw sources for every query.
Instead, it should preserve source material, update durable pages, maintain
links, and use the wiki as a compounding substrate for later work.

Source: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

## Core Idea

- Raw sources are preserved.
- The wiki is the interpreted, cross-linked layer.
- A schema or agent contract tells future agents how to operate.
- Ingest, query, and lint are the core operations in the original pattern.
- This skill adds a separate read-only health and source-drift diagnosis.
- Navigation files matter: `index.md` helps find knowledge; `log.md` explains
  what changed and when.

## Difference From RAG

RAG usually retrieves snippets at answer time. A wiki compiles useful findings
into named pages ahead of time. That makes later work faster, but only if agents
maintain the wiki carefully: no duplicate pages, no unlinked pages, no silent
contradictions, and no source material rewritten as interpretation changes.

## Three Layers

1. Raw sources: immutable files under `raw/`, with derived Markdown/text under
   `raw/derived/` linked back to preserved originals.
2. Wiki pages: source summaries, entities, concepts, syntheses, comparisons,
   and filed queries.
3. Contract and navigation: the selected root agent config (`AGENTS.md` by
   default), generated `index.md`, append-only `log.md`, and `_meta/` files.
   `README.md` is the human-facing map rather than mandatory task context.

## Core Operations and Skill Extension

- Ingest: classify sources, summarize, update durable pages, update navigation,
  and log the action.
- Query: retrieve relevant pages, answer from compiled knowledge, and file only
  durable syntheses worth reusing. Read-only answers do not require a log entry.
- Lint: review contradictions, stale claims, orphan pages, missing concepts or
  cross-references, and data gaps. The agent leads this semantic review while
  the CLI supplies structural checks.
- Health (this skill's extension): decide whether source drift requires a
  knowledge update, then identify affected source summaries and dependent
  pages.

Karpathy's source is an abstract idea file rather than a software
specification. This skill's fixed directory layout, frontmatter rules, source
summaries, hashes, health/fix commands, safe-path checks, and packaging are
opinionated implementation choices.

## Agent Discipline

The agent should optimize for future reuse. A good ingest may touch several
pages, but every change must be explainable from sources and discoverable from
the index. Raw files remain stable; wiki pages carry interpretation and can be
revised.
