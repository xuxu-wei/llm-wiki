# Repository Agent Guide

This repository publishes the `llm-wiki` Codex skill. Treat
`skills/llm-wiki/` as the only installable skill directory.

## Repository Contract

- Keep `SKILL.md`, runtime scripts, references, templates, and
  `agents/openai.yaml` under `skills/llm-wiki/`.
- Keep repository documentation, CI, and tests outside the installable skill.
- Keep `SKILL.md` frontmatter limited to `name` and `description`.
- Keep `agents/openai.yaml` synchronized with `SKILL.md`; do not add icons,
  brand colors, tools, or policy fields unless explicitly requested.
- Preserve a Python 3.10+ standard-library-only runtime.
- Make Codex and `AGENTS.md` the defaults. Preserve Claude support only as an
  explicit compatibility mode.
- Treat Obsidian and other skills or integrations as optional.

## Safety and Compatibility

- Never let metadata paths escape the target wiki root.
- Never overwrite an existing agent config or append-only `log.md` during
  initialization.
- Preview bulk wiki normalization with `fix --dry-run`.
- Preserve existing CLI flags unless a versioned breaking change is intended.
- Keep install and staged-update instructions in the root README aligned with
  the behavior of OpenAI's `skill-installer`.

## Required Validation

Before handing off a change:

1. Run OpenAI `quick_validate.py` against `skills/llm-wiki`.
2. Run `python -m unittest discover -s tests -v`.
3. Run `python skills/llm-wiki/scripts/wiki_tools.py --help`.
4. Confirm tests did not modify tracked files or add caches to version control.

Do not commit, push, create a release, or create a Git tag unless the user
explicitly requests it.
