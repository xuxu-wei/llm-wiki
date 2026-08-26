# Create

Use this procedure to initialize a new wiki or deliberately migrate an existing
one. Read the wiki contract immediately after this file; it is the authority for
directory layout, profiles, metadata, and lifecycle behavior.

## Prepare

Resolve the target from the user's explicit path, then `WIKI_PATH`, then a
clearly named local directory. Infer the domain from the request and ask only
when it would materially change the taxonomy or research profile. Inspect an
existing target before initialization. Do not replace an existing agent file,
append-only log, or unrelated project instructions.

Use the installed skill directory, not the current working directory:

```bash
python "<skill-dir>/scripts/wiki_tools.py" init <wiki-path> --domain "<domain>"
```

The default platform is Codex and the default contract is `AGENTS.md`. Use
`--agent-platform claude` only for explicit Claude compatibility, or
`--agent-file <root-markdown-name>` for a user-selected compatible agent file.
Use `--research` only when the wiki will manage scientific literature or other
research evidence. Initialization may refresh generated `README.md` and
`index.md` with `--force`, but it must never overwrite an existing agent config
or `log.md`.

## Establish the contract

Keep the generated `AGENTS.md` short and operational. Put the human description
in `README.md`; agents do not need to read that file on every task. Customize
the domain and a small tag taxonomy without duplicating the full schema. Use
`_meta/` for any larger controlled vocabulary or administrative note.

Do not add a new page type, raw category, required field, database, embedding
index, Obsidian dependency, or external runtime during ordinary initialization.
Treat each of those as a schema decision and obtain approval when it changes the
published contract.

## Verify

Run bounded structural checks before the first ingest:

```bash
python "<skill-dir>/scripts/wiki_tools.py" lint <wiki-path> --summary --limit 20
python "<skill-dir>/scripts/wiki_tools.py" health <wiki-path> --summary --limit 20 --no-inventory
```

Confirm that the agent file, root navigation files, raw categories, durable-page
directories, `_meta/schema.json`, and `_archive/` exist. Confirm schema version
2 and the selected `core` or `research` profile. Report the domain, platform,
profile, created files, preserved files, and warnings. Stop rather than
partially migrating an existing wiki when its current layout or metadata cannot
be reconciled safely with the contract.
