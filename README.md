# LLM Wiki Codex Skill

`llm-wiki` is an OpenAI-compatible Codex skill for building and maintaining a
source-grounded Markdown knowledge base. It preserves raw sources, maintains
linked wiki pages and navigation, and includes a standard-library Python CLI
for bounded retrieval, staged ingest, indexing, linting, source-drift diagnosis,
safe metadata normalization, and archival.

The installable skill is [`skills/llm-wiki`](skills/llm-wiki). Repository
documentation, tests, and CI remain outside that directory so Codex installs
only runtime content.

## Inspiration and Design Alignment

This independent project is inspired by Andrej Karpathy's
[`LLM Wiki` idea file](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
which describes a persistent, compounding Markdown wiki maintained by an LLM
between a user and immutable raw sources. It is not affiliated with, endorsed
by, or maintained by Andrej Karpathy.

The skill follows the original pattern at the architecture and workflow level:

- Raw sources remain the source of truth while the agent maintains a durable,
  cross-linked knowledge layer.
- A local agent contract such as `AGENTS.md` or `CLAUDE.md` defines the schema
  and operating rules.
- Ingest, query, and lint workflows keep knowledge cumulative; `index.md`
  supports navigation and `log.md` records an append-only history.
- The human curates sources, directs inquiry, and reviews judgment calls while
  the agent handles synthesis, cross-linking, and maintenance.
- Plain Markdown is the foundation; Obsidian, search engines, and other tools
  remain optional.

Karpathy's document intentionally leaves directory layout, schema, page
formats, and tooling to each user and agent. This repository is an opinionated,
Codex-first implementation that adds its own directory and frontmatter
defaults, source summaries, provenance and drift checks, structural linting,
Python CLI, safety rules, tests, and install/update workflow. These are project
extensions, not parts of an official Karpathy specification or implementation.

Two boundaries are worth making explicit:

- `hash-source --write` is retained only as a deprecated compatibility flag and
  is refused for files under `raw/`. Record the reported hash on the matching
  `sources/` page or in generated machine state instead.
- The CLI lint focuses on structure, links, metadata, provenance, and drift.
  Semantic review for contradictions, stale claims, missing concepts, and new
  research questions remains an agent-led task.

## Progressive Disclosure

The skill keeps its always-loaded instructions deliberately small. `SKILL.md`
contains task routing and safety invariants; Create, Ingest, Query, and Maintain
each have one short first-level reference. Schema semantics live only in the
wiki contract, while templates contain file shapes without repeating workflow
rules. A normal query therefore loads the skill, the Query reference, the local
agent contract, and bounded CLI context—not the full contract, README, index,
log, ingest rules, or research guidance.

This division is intentional: templates are the formatting source of truth,
references are the workflow and semantic source of truth, and the CLI enforces
deterministic checks. It reduces routine context cost without weakening raw
immutability, provenance, path containment, prompt-injection resistance, or
high-impact stop conditions.

The current measured rule budgets are below; unit tests lock their ceilings.
English word counts include `SKILL.md` and the selected operation references,
but exclude on-demand templates, the local wiki contract, and bounded runtime
results:

| Route | Skill files loaded | Rule words |
| --- | ---: | ---: |
| Query | 2 | 1,117 |
| Ingest | 2 | 1,190 |
| Research ingest | 3 | 1,574 |
| Maintain | 2 | 1,143 |

The generated base `AGENTS.md` is 213 words, and `context` defaults to at most
3,200 characters (roughly 800 English tokens). This keeps the normal Query
path near the planned 2–2.5k-token fixed-context envelope while allowing
targeted expansion when provenance or drift requires it.

The bundled source template is named `templates/wiki-agent-contract.md` to
avoid confusing it with an active Codex instruction file. During wiki
initialization, the CLI renders that template as `<wiki>/AGENTS.md` by default,
or as `CLAUDE.md` only when Claude compatibility is explicitly selected.

## Install with Codex

Ask Codex:

```text
Use $skill-installer to install https://github.com/xuxu-wei/llm-wiki/tree/main/skills/llm-wiki
```

The skill installer places the package at
`$CODEX_HOME/skills/llm-wiki` (normally `~/.codex/skills/llm-wiki`). The newly
installed skill is available on the next Codex turn.

`main` is the latest development release. For a reproducible installation,
replace `main` with a SemVer tag after one is published:

```text
Use $skill-installer to install https://github.com/xuxu-wei/llm-wiki/tree/vMAJOR.MINOR.PATCH/skills/llm-wiki
```

## Update Safely

The standard skill installer intentionally refuses to overwrite an existing
destination. Ask Codex to use a staged replacement instead of deleting the
installed skill first:

```text
Use $skill-installer to update llm-wiki from https://github.com/xuxu-wei/llm-wiki/tree/main/skills/llm-wiki. Install the candidate into a unique staging directory under $CODEX_HOME/.skill-staging, validate it, back up the current skill under $CODEX_HOME/.skill-backups, replace the installed directory, and restore the backup if any post-install check fails. Keep staging and backups outside $CODEX_HOME/skills.
```

The update procedure is:

1. Install the candidate with the skill installer's `--dest` option into
   `$CODEX_HOME/.skill-staging/<unique-id>/llm-wiki`.
2. Run OpenAI's `quick_validate.py` against the candidate and run
   `python <candidate>/scripts/wiki_tools.py --help`.
3. Move the current `$CODEX_HOME/skills/llm-wiki` to
   `$CODEX_HOME/.skill-backups/llm-wiki-<timestamp>`.
4. Move the validated candidate into `$CODEX_HOME/skills/llm-wiki` on the same
   filesystem. Once the old directory has moved to backup, restore it after any
   replacement or validation failure; never leave the installed path missing
   or partially replaced.
5. Repeat validation at the installed path and restore the backup immediately
   if validation fails.
6. Confirm the new skill on the next Codex turn, then remove stale staging and
   backup directories when rollback is no longer needed.

Never place a backup inside `$CODEX_HOME/skills`; Codex could discover it as a
second skill. To pin an update, use a tag URL instead of `main`.

## Runtime

- Python 3.10 or newer
- Python standard library only
- Plain Markdown; Obsidian integrations are optional
- Codex creates `AGENTS.md` by default; explicit Claude compatibility remains
  available through `--agent-platform claude`

The default profile targets a personal, medium-sized wiki. SQLite FTS,
persistent cross-process locks or crash journals, source-version/multi-artifact
models, and partitioned thousand-page indexes are optional scale extensions,
not hidden runtime dependencies. Add them only as explicit, versioned schema
decisions; Markdown must remain the source of truth and any search database must
be rebuildable.

Resolve the installed skill directory and run:

```text
python <skill-dir>/scripts/wiki_tools.py --help
```

## Develop and Validate

Run the repository tests:

```text
python -m unittest discover -s tests -v
```

Validate the installable directory with the current OpenAI skill validator:

```text
python <skill-creator-dir>/scripts/quick_validate.py skills/llm-wiki
```

CI runs the tests on Python 3.10 and 3.13 across Windows, Ubuntu, and macOS,
then fetches the current validator from `openai/skills` for a separate package
check.

## Versioning

- `main`: latest version
- `vMAJOR.MINOR.PATCH`: immutable, reproducible release reference
- Breaking CLI or wiki-contract changes require a major version increment.
- Backward-compatible features require a minor increment; fixes require a
  patch increment.

No release tag is created by this repository conversion.
