# Obsidian Integration

The LLM Wiki is plain Markdown and does not require Obsidian. Use Obsidian
features only when they improve the user's requested workflow.

## Optional Markdown Assistance

If an Obsidian-specific Markdown skill is available, use it for:

- wikilinks;
- embeds;
- callouts;
- YAML properties;
- Obsidian tag syntax;
- note formatting that must render cleanly in Obsidian.

If it is unavailable, follow this skill's plain Markdown, frontmatter, and
wikilink rules. Do not block the core workflow on another skill.

## Optional Integrations

- A web-page cleanup tool: use it before saving source text into
  `raw/articles/`. Do not require it; use available extraction
  tools when it is absent.
- A JSON Canvas tool: use it for optional concept maps, research evidence maps, or
  workflow diagrams stored as `.canvas`.
- An Obsidian Bases tool: use it for optional dashboards over source metadata, status,
  confidence, tags, and update dates.
- An Obsidian CLI integration: use it only when the user wants operations against a running
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
