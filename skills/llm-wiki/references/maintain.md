# Maintain

Use this procedure for structural audit, source drift, bounded repair,
normalization, or archival. Start with compact read-only output and expand only
the affected scope.

## Diagnose

Run structural lint first. Scope by a source path when investigating one ingest
or drift chain:

```bash
python "<skill-dir>/scripts/wiki_tools.py" lint <wiki-path> --summary --limit 20
python "<skill-dir>/scripts/wiki_tools.py" health <wiki-path> --summary --limit 20 --no-inventory
python "<skill-dir>/scripts/wiki_tools.py" health <wiki-path> --source <path> --summary --limit 20
```

Remove `--summary`, raise `--limit`, or allow the inventory only after the
compact result identifies a relevant category. Treat containment failures,
missing raw originals, broken provenance, invalid hashes, duplicate identities,
and ambiguous source references as blocking. Treat style, field order, page
length, and optional metadata gaps as warnings unless they conceal provenance.

Hash drift means the source bytes changed; it does not prove an interpretation
is wrong. Review the matching source summary, any derived artifact and its
derivation-time hash, then dependent durable pages. Mark derived material stale
when its recorded source hash no longer matches.

## Repair safely

Preview deterministic normalization:

```bash
python "<skill-dir>/scripts/wiki_tools.py" fix <wiki-path> --dry-run
```

Apply only changes shown in the preview. `fix` must skip raw originals and fail
closed on comments, nested or otherwise unsupported YAML, duplicate fields, or
any frontmatter it cannot round-trip safely. It may normalize canonical field
order or mechanically derivable values; it must not invent confidence, status,
bibliographic facts, interpretations, or conflict resolutions. Re-run the same
scoped lint and health checks after applying changes. Expect the result to
converge with no repeated rewrite.

## Archive without deleting

Preview archival with a reason and optional replacement:

```bash
python "<skill-dir>/scripts/wiki_tools.py" archive <wiki-path> <page> --reason "<reason>" --dry-run
```

Review backlinks, index changes, the preserved `_archive/` destination, and
`replaced_by` before rerunning with `--apply`. Archive must preserve the old
relative path, record `archived_at` and the reason, update incoming links, and
append one log entry only after the operation succeeds. Delete only with
explicit approval.

Use atomic same-directory replacement and a pre-write content check for every
managed rewrite. If any input changed since diagnosis, abort and re-run rather
than overwriting concurrent work. Log successful mutations or an explicitly
requested audit, not routine read-only diagnostics. Stop before schema changes,
mass archival, or repairs expected to change more than 10 pages, and present
the exact scope and recovery path.
