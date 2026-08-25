# Obsidian Integration

The LLM Wiki is plain Markdown and does not require Obsidian. Use Obsidian
features only when they improve the user's requested workflow.

## Required Related Skill

`obsidian-markdown` is the only hard skill dependency. Use it for:

- wikilinks;
- embeds;
- callouts;
- YAML properties;
- Obsidian tag syntax;
- note formatting that must render cleanly in Obsidian.

## Optional Skills

- `defuddle`: use for cleaning normal web pages into Markdown before saving
  source text into `raw/articles/`. Do not require it; use available extraction
  tools when it is absent.
- `json-canvas`: use for optional concept maps, research evidence maps, or
  workflow diagrams stored as `.canvas`.
- `obsidian-bases`: use for optional dashboards over source metadata, status,
  confidence, tags, and update dates.
- `obsidian-cli`: use only when the user wants operations against a running
  Obsidian vault.

## Vault Settings

Recommended user-facing settings:

- Keep wikilinks enabled.
- Set attachments to `raw/media/`.
- Use properties for frontmatter fields.
- Consider Bases or Dataview-style dashboards for large source collections.

## Platform Boundary

Do not require systemd, Linux services, a GUI, Node.js, Obsidian Sync, or shell
syntax. Sync and UI choices are outside the core skill; the wiki must remain
usable from Windows paths and normal file editors.
