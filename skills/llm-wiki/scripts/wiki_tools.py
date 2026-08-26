#!/usr/bin/env python3
"""Cross-platform helper tools for Karpathy-style LLM Wikis.

The script intentionally uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit


PAGE_DIRS = ["sources", "entities", "concepts", "syntheses", "comparisons", "queries"]
PAGE_TYPE_BY_DIR = {
    "sources": "source",
    "entities": "entity",
    "concepts": "concept",
    "syntheses": "synthesis",
    "comparisons": "comparison",
    "queries": "query",
}
RAW_DIRS = [
    "raw/inbox",
    "raw/articles",
    "raw/papers",
    "raw/transcripts",
    "raw/data",
    "raw/media",
    "raw/derived",
]
META_DIRS = ["_meta", "_archive"]
AGENT_CONFIG_FILES = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "generic": "AGENTS.md",
}
AGENT_CONFIG_MARKER = "<!-- llm-wiki-agent-contract -->"
AGENT_CONTRACT_TEMPLATE = "wiki-agent-contract.md"
ROOT_FILES = ["README.md", "index.md", "log.md"]
FORCE_REFRESHABLE_ROOT_FILES = {"README.md", "index.md"}
VALID_TYPES = {"source", "entity", "concept", "synthesis", "comparison", "query"}
VALID_CONFIDENCE = {"high", "medium", "low", "unknown"}
VALID_STATUS = {"active", "contested", "superseded", "archived", "unknown"}
SCIENTIFIC_KINDS = {"paper", "preprint", "book", "chapter", "report", "thesis", "dataset"}
PAPER_KINDS = {"paper", "preprint"}
COMMON_PAGE_FIELD_ORDER = ["title", "created", "updated", "type", "tags", "sources", "summary", "confidence", "status"]
SOURCE_EXTRA_FIELD_ORDER = [
    "source_kind",
    "authors",
    "year",
    "venue",
    "publisher",
    "doi",
    "isbn",
    "url",
    "raw_source",
    "derived_source",
    "raw_hash_scheme",
    "raw_sha256",
    "raw_hashed_at",
]
SOURCE_FIELD_ORDER = COMMON_PAGE_FIELD_ORDER + SOURCE_EXTRA_FIELD_ORDER
RAW_DERIVED_FIELD_ORDER = [
    "derived_from",
    "derivation_method",
    "derived_at",
    "source_hash_at_derivation",
    "source_hash_scheme_at_derivation",
]
RAW_TEXT_FIELD_ORDER = ["source_url", "ingested", "source_kind", "sha256", "hash_scheme", "hashed_at"]
REQUIRED_PAGE_FIELDS = COMMON_PAGE_FIELD_ORDER
REQUIRED_SOURCE_BASE_FIELDS = COMMON_PAGE_FIELD_ORDER + [
    "source_kind",
    "raw_source",
    "raw_hash_scheme",
    "raw_sha256",
    "raw_hashed_at",
]
REQUIRED_CITATION_FIELDS = ["authors", "year", "venue", "publisher", "doi", "isbn", "url", "source_kind"]
REQUIRED_SOURCE_HASH_FIELDS = ["raw_source", "raw_hash_scheme", "raw_sha256", "raw_hashed_at"]
NONCANONICAL_FIELD_ALIASES = {
    "author": "authors",
    "journal": "venue",
    "journal_name": "venue",
    "publication": "venue",
    "publication_year": "year",
    "raw_file": "raw_source",
    "original_source": "raw_source",
    "source_file": "raw_source",
}
INLINE_LIST_FIELDS = {
    "aliases",
    "authors",
    "claims",
    "datasets",
    "derived_sources",
    "evidence",
    "keywords",
    "related",
    "related_pages",
    "sources",
    "tags",
}
HASH_SCHEME_TEXT = "sha256_body_v1"
HASH_SCHEME_BYTES = "sha256_bytes_v1"
VALID_HASH_SCHEMES = {HASH_SCHEME_TEXT, HASH_SCHEME_BYTES}
TEXT_HASH_EXTS = {
    ".bib",
    ".csv",
    ".htm",
    ".html",
    ".json",
    ".jsonl",
    ".md",
    ".ris",
    ".srt",
    ".tsv",
    ".txt",
    ".vtt",
    ".xml",
    ".yaml",
    ".yml",
}
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp3", ".wav", ".mp4", ".mov", ".m4a"}
PAPER_EXTS = {".pdf", ".epub", ".mobi"}
DATA_EXTS = {".csv", ".tsv", ".json", ".jsonl", ".xlsx", ".xls", ".parquet", ".sav", ".dta"}
TRANSCRIPT_EXTS = {".vtt", ".srt"}
ARTICLE_TEXT_EXTS = {".md", ".txt", ".html", ".htm"}
KNOWN_SOURCE_EXTS = MEDIA_EXTS | PAPER_EXTS | DATA_EXTS | TRANSCRIPT_EXTS | ARTICLE_TEXT_EXTS
DERIVED_REQUIRED_FIELDS = ["derived_from", "derivation_method", "derived_at"]
SCHEMA_VERSION = 2


def today() -> str:
    return dt.date.today().isoformat()


def schema_profile(wiki: Path) -> str:
    path = wiki / "_meta" / "schema.json"
    if not path.is_file() or not path_stays_within_wiki(wiki, path):
        return "core"
    try:
        value = json.loads(read_text(path))
        if not isinstance(value, dict):
            return "core"
        profile = str(value.get("profile") or "core").lower()
    except (OSError, ValueError, TypeError):
        return "core"
    return profile if profile in {"core", "research"} else "core"


def schema_config_issues(wiki: Path) -> list[str]:
    path = wiki / "_meta" / "schema.json"
    if not path.exists():
        return []
    if not path.is_file() or not path_stays_within_wiki(wiki, path):
        return ["_meta/schema.json: unsafe or non-file schema configuration"]
    try:
        value = json.loads(read_text(path))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"_meta/schema.json: invalid JSON: {exc}"]
    if not isinstance(value, dict):
        return ["_meta/schema.json: root must be a JSON object"]
    issues: list[str] = []
    profile = value.get("profile")
    if profile not in {"core", "research"}:
        issues.append(f"_meta/schema.json: invalid profile: {profile!r}")
    version = value.get("schema_version")
    if not isinstance(version, int) or version < 1:
        issues.append(f"_meta/schema.json: invalid schema_version: {version!r}")
    return issues


def research_citation_requirements(source_kind: str) -> tuple[list[str], list[str]]:
    required_by_kind = {
        "paper": ["authors", "year"],
        "preprint": ["authors", "year"],
        "book": ["authors", "year", "publisher"],
        "chapter": ["authors", "year", "publisher"],
        "report": ["authors", "year", "publisher"],
        "thesis": ["authors", "year", "publisher"],
        "dataset": ["authors", "year"],
    }
    identifiers_by_kind = {
        "paper": ["doi", "url"],
        "preprint": ["doi", "url"],
        "book": ["isbn", "url"],
        "chapter": ["doi", "isbn", "url"],
        "report": ["doi", "url"],
        "thesis": ["doi", "url"],
        "dataset": ["doi", "url"],
    }
    return required_by_kind.get(source_kind, []), identifiers_by_kind.get(source_kind, [])


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def template_path(name: str) -> Path:
    return skill_root() / "templates" / name


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def path_content_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return bytes_hash(path)


def write_text(path: Path, text: str, expected_hash: str | None = None) -> None:
    """Atomically replace a UTF-8 text file and detect concurrent edits when possible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    initial_hash = path_content_hash(path)
    if expected_hash is not None and initial_hash != expected_hash:
        raise RuntimeError(f"concurrent modification detected before writing {path}")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.llm-wiki-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        if path_content_hash(path) != initial_hash:
            raise RuntimeError(f"concurrent modification detected while writing {path}")
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def write_texts_transactional(
    updates: dict[Path, str | None], expected_hashes: dict[Path, str | None] | None = None
) -> None:
    """Apply multiple text replacements/deletions with best-effort rollback."""
    if not updates:
        return
    originals: dict[Path, tuple[str | None, bytes | None]] = {}
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    token = uuid.uuid4().hex
    try:
        for path, content in updates.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            original_hash = path_content_hash(path)
            if expected_hashes is not None and path in expected_hashes and original_hash != expected_hashes[path]:
                raise RuntimeError(f"concurrent modification detected before transaction staging: {path}")
            original_bytes = path.read_bytes() if path.exists() else None
            originals[path] = (original_hash, original_bytes)
            if content is None:
                continue
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.llm-wiki-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                staged[path] = Path(handle.name)
        for path, (expected, _original_bytes) in originals.items():
            if path_content_hash(path) != expected:
                raise RuntimeError(f"concurrent modification detected before transaction commit: {path}")
        committed: list[Path] = []
        try:
            for path, content in updates.items():
                if path.exists():
                    backup = path.with_name(f".{path.name}.llm-wiki-backup-{token}")
                    os.replace(path, backup)
                    backups[path] = backup
                if content is not None:
                    os.replace(staged[path], path)
                    staged.pop(path, None)
                committed.append(path)
        except BaseException:
            for path in reversed(committed + [item for item in backups if item not in committed]):
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
                backup = backups.get(path)
                if backup is not None and backup.exists():
                    os.replace(backup, path)
            raise
        for backup in backups.values():
            try:
                backup.unlink()
            except OSError:
                pass
    finally:
        for temp_path in staged.values():
            try:
                temp_path.unlink()
            except OSError:
                pass
        for path, backup in backups.items():
            if backup.exists() and not path.exists():
                try:
                    os.replace(backup, path)
                except OSError:
                    pass


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_utf8_text_for_hash(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: invalid UTF-8 text for {HASH_SCHEME_TEXT}") from exc
    return normalize_newlines(text)


def render_template(name: str, values: dict[str, str]) -> str:
    text = read_text(template_path(name))
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text.replace("{{date}}", today())


def detect_agent_platform(wiki: Path, requested: str = "auto") -> str:
    if requested != "auto":
        return requested
    if (wiki / "CLAUDE.md").exists():
        return "claude"
    if (wiki / "AGENTS.md").exists():
        return "codex"
    env_names = {name.upper() for name in os.environ}
    if any(name.startswith("CLAUDE") or name.startswith("ANTHROPIC") for name in env_names):
        return "claude"
    if any(name.startswith("CODEX") or name.startswith("OPENAI") for name in env_names):
        return "codex"
    return "generic"


def resolve_agent_config_name(wiki: Path, requested_platform: str, requested_file: str | None) -> str:
    if requested_file:
        rel = normalize_wiki_rel(requested_file)
        windows_path = PureWindowsPath(rel)
        posix_path = PurePosixPath(rel)
        if (
            not rel
            or rel.endswith("/")
            or "/" in rel
            or ":" in rel
            or windows_path.drive
            or windows_path.root
            or posix_path.is_absolute()
        ):
            raise ValueError("--agent-file must be a root-level Markdown filename")
        if not rel.lower().endswith(".md"):
            raise ValueError("--agent-file must end with .md")
        return rel
    platform = detect_agent_platform(wiki, requested_platform)
    return AGENT_CONFIG_FILES[platform]


def render_agent_contract(values: dict[str, str]) -> str:
    return AGENT_CONFIG_MARKER + "\n" + render_template(AGENT_CONTRACT_TEMPLATE, values).rstrip() + "\n"


def write_or_append_agent_config(path: Path, text: str) -> str:
    if not path.exists():
        write_text(path, text)
        return "written"
    current = read_text(path)
    if AGENT_CONFIG_MARKER in current or "This directory is an LLM Wiki" in current:
        return "unchanged"
    addition = "\n\n## LLM Wiki Agent Contract\n\n" + text.rstrip() + "\n"
    write_text(path, current.rstrip() + addition, expected_hash=bytes_hash(path))
    return "appended"


def frontmatter_block(text: str) -> tuple[dict[str, Any], str, bool]:
    if not text.startswith("---"):
        return {}, text, False
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not match:
        return {}, text, False
    raw_fm, body = match.group(1), match.group(2)
    return parse_simple_yaml(raw_fm), body, True


def parse_simple_yaml(raw: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        data[key] = parse_value(value)
    return data


def parse_value(value: str) -> Any:
    if value == "":
        return ""
    if value.startswith("[") and value.endswith("]"):
        return parse_inline_list(value)
    return clean_scalar(value)


def parse_inline_list(value: str) -> list[str]:
    inner = value[1:-1].strip()
    if not inner:
        return []
    reader = csv.reader(io.StringIO(inner), skipinitialspace=True, strict=True)
    return [clean_scalar(part.strip()) for part in next(reader)]


def clean_scalar(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def frontmatter_format_issues(text: str, rel: str) -> list[str]:
    if not text.startswith("---"):
        return []
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not match:
        return [f"{rel}: malformed frontmatter fence"]
    raw = match.group(1)
    issues: list[str] = []
    seen: set[str] = set()
    current_key: str | None = None
    for lineno, line in enumerate(raw.splitlines(), start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if stripped.startswith("- "):
                key = current_key or "<unknown>"
                issues.append(f"{rel}:{lineno}: {key} uses multiline list; use inline bracket syntax")
            else:
                issues.append(f"{rel}:{lineno}: unsupported indented frontmatter line")
            continue
        if ":" not in line:
            issues.append(f"{rel}:{lineno}: frontmatter line has no ':'")
            current_key = None
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if not key:
            issues.append(f"{rel}:{lineno}: empty frontmatter key")
            continue
        if key in seen:
            issues.append(f"{rel}:{lineno}: duplicate frontmatter key: {key}")
        seen.add(key)
        if key in INLINE_LIST_FIELDS and not (value.startswith("[") and value.endswith("]")):
            issues.append(f"{rel}:{lineno}: {key} must use inline bracket list syntax")
        if (value.startswith("[") or value.endswith("]")) and not (value.startswith("[") and value.endswith("]")):
            issues.append(f"{rel}:{lineno}: malformed inline list for {key}")
        if value.startswith("[") and value.endswith("]"):
            try:
                parse_inline_list(value)
            except csv.Error as exc:
                issues.append(f"{rel}:{lineno}: malformed inline list for {key}: {exc}")
    return issues


def frontmatter_rewrite_safety_issues(text: str, rel: str) -> list[str]:
    """Return constructs the minimal YAML writer cannot round-trip safely."""
    issues = list(frontmatter_format_issues(text, rel))
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not match:
        return issues
    for lineno, line in enumerate(match.group(1).splitlines(), start=2):
        stripped = line.strip()
        if stripped.startswith("#"):
            issues.append(f"{rel}:{lineno}: frontmatter comment requires manual preservation")
            continue
        if "#" in line:
            issues.append(f"{rel}:{lineno}: possible inline frontmatter comment requires manual preservation")
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        _key, value = line.split(":", 1)
        value = value.strip()
        if value in {"|", ">", "|-", ">-", "|+", ">+"}:
            issues.append(f"{rel}:{lineno}: block scalar requires manual preservation")
        if value.startswith(("{", "&", "*", "!")):
            issues.append(f"{rel}:{lineno}: advanced YAML value requires manual preservation")
    return sorted(set(issues))


def dump_simple_yaml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                rendered = ", ".join(render_inline_list_item(item) for item in value)
                lines.append(f"{key}: [{rendered}]")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def render_markdown_with_frontmatter(fm: dict[str, Any], body: str) -> str:
    # Templates use one blank line between the frontmatter fence and Markdown
    # body. Keep the writer on the same canonical shape so a template-authored
    # page does not produce a noisy first fix.
    return "---\n" + dump_simple_yaml(fm) + "---\n\n" + body.lstrip("\n")


def render_inline_list_item(value: Any) -> str:
    text = str(value)
    if not text or any(char in text for char in [",", "#", "[", "]", "{", "}", ":", '"', "'"]) or text != text.strip():
        return json.dumps(text, ensure_ascii=False)
    return text


def page_type_for_path(path: Path, wiki: Path, fm: dict[str, Any]) -> str:
    page_type = str(fm.get("type") or "").strip()
    if page_type:
        return page_type
    try:
        parent = path.relative_to(wiki).parts[0]
    except ValueError:
        parent = path.parent.name
    return PAGE_TYPE_BY_DIR.get(parent, "")


def canonical_field_order(path: Path, wiki: Path, fm: dict[str, Any]) -> list[str]:
    rel = path.relative_to(wiki).as_posix()
    if rel.startswith("raw/derived/"):
        return RAW_DERIVED_FIELD_ORDER
    if rel.startswith("raw/"):
        return RAW_TEXT_FIELD_ORDER
    if page_type_for_path(path, wiki, fm) == "source" or path.parent.name == "sources":
        return SOURCE_FIELD_ORDER
    return COMMON_PAGE_FIELD_ORDER


def required_fields_for_path(path: Path, wiki: Path, fm: dict[str, Any]) -> list[str]:
    rel = path.relative_to(wiki).as_posix()
    if rel.startswith("raw/derived/"):
        return DERIVED_REQUIRED_FIELDS
    if rel.startswith("raw/"):
        return []
    if page_type_for_path(path, wiki, fm) == "source" or path.parent.name == "sources":
        required = list(REQUIRED_SOURCE_BASE_FIELDS)
        source_kind = str(fm.get("source_kind") or "").strip().lower()
        if schema_profile(wiki) == "research":
            citation_required, _identifiers = research_citation_requirements(source_kind)
            required.extend(field for field in citation_required if field not in required)
        return required
    return REQUIRED_PAGE_FIELDS


def infer_placeholder(field: str, path: Path, wiki: Path, fm: dict[str, Any]) -> Any:
    page_type = page_type_for_path(path, wiki, fm)
    if field == "title":
        return path.stem.replace("-", " ").title()
    if field in {"created", "updated"}:
        return today()
    if field == "type":
        return page_type or "concept"
    if field == "tags":
        inferred = page_type or PAGE_TYPE_BY_DIR.get(path.parent.name, "")
        return [inferred] if inferred else []
    if field in {"sources", "authors"}:
        return []
    if field == "summary":
        return "unknown"
    if field == "confidence":
        return "unknown"
    if field == "status":
        return "unknown"
    return "unknown"


def reorder_frontmatter(fm: dict[str, Any], order: list[str]) -> dict[str, Any]:
    reordered: dict[str, Any] = {}
    for key in order:
        if key in fm:
            reordered[key] = fm[key]
    for key, value in fm.items():
        if key not in reordered:
            reordered[key] = value
    return reordered


def expected_frontmatter_order(fm: dict[str, Any], order: list[str]) -> list[str]:
    present_ordered = [key for key in order if key in fm]
    custom = [key for key in fm if key not in order]
    return present_ordered + custom


def missing_required_fields(path: Path, wiki: Path, fm: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    page_type = page_type_for_path(path, wiki, fm)
    for field in required_fields_for_path(path, wiki, fm):
        value = fm.get(field)
        empty_source_list = field == "sources" and page_type == "source" and value == []
        if field not in fm or value == "" or value is None or (
            field in INLINE_LIST_FIELDS and value == [] and not empty_source_list
        ):
            missing.append(field)
    return missing


def placeholder_fields(path: Path, wiki: Path, fm: dict[str, Any]) -> list[str]:
    placeholders: list[str] = []
    source_kind = str(fm.get("source_kind") or "").strip().lower()
    for field in required_fields_for_path(path, wiki, fm):
        value = fm.get(field)
        if field in REQUIRED_CITATION_FIELDS and source_kind not in SCIENTIFIC_KINDS:
            continue
        if field == "sources":
            continue
        if isinstance(value, str) and value.strip().lower() == "unknown":
            placeholders.append(field)
        if field in INLINE_LIST_FIELDS and value == [] and field not in {"sources"}:
            placeholders.append(field)
    return placeholders


def body_hash_for_text(path: Path) -> str:
    text = read_utf8_text_for_hash(path)
    _fm, body, has_fm = frontmatter_block(text)
    content = body if has_fm else text
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def bytes_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_hash_scheme(path: Path) -> str:
    return HASH_SCHEME_TEXT if path.suffix.lower() in TEXT_HASH_EXTS else HASH_SCHEME_BYTES


def compute_source_hash(path: Path, scheme: str | None = None) -> tuple[str, str]:
    selected = scheme or default_hash_scheme(path)
    if selected == HASH_SCHEME_TEXT:
        return body_hash_for_text(path), selected
    if selected == HASH_SCHEME_BYTES:
        return bytes_hash(path), selected
    raise ValueError(f"{path}: unsupported hash_scheme {selected!r}")


def normalize_wiki_rel(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "unknown", "none", "null"}:
        return ""
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def resolve_wiki_reference(wiki: Path, value: Any, field: str) -> tuple[str, Path | None]:
    """Resolve a metadata path while keeping it inside the wiki root."""
    rel = normalize_wiki_rel(value)
    if not rel:
        return "", None
    windows_path = PureWindowsPath(rel)
    posix_path = PurePosixPath(rel)
    parts = posix_path.parts
    if (
        windows_path.is_absolute()
        or windows_path.drive
        or posix_path.is_absolute()
        or ".." in parts
        or any(":" in part for part in parts)
    ):
        raise ValueError(f"{field} must be a relative path inside the wiki: {value}")
    parts = [part for part in parts if part not in {"", "."}]
    if not parts:
        return "", None
    try:
        root = wiki.resolve()
        candidate = (root / Path(*parts)).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"{field} is not a valid wiki-relative path: {value}") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field} must stay inside the wiki: {value}") from exc
    return Path(*parts).as_posix(), candidate


def path_stays_within_wiki(wiki: Path, path: Path) -> bool:
    try:
        root = wiki.resolve()
        path.resolve().relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def collect_unsafe_symlinks(wiki: Path) -> list[str]:
    issues: list[str] = []
    if not wiki.exists():
        return issues
    for directory, dirnames, filenames in os.walk(wiki, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(directory) / name
            if not path.is_symlink():
                continue
            rel = path.relative_to(wiki).as_posix()
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError):
                issues.append(f"{rel}: broken or unresolvable symlink")
                continue
            if not path_stays_within_wiki(wiki, resolved):
                issues.append(f"{rel}: symlink target escapes wiki root")
    return sorted(set(issues))


def list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "untitled"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def is_derived_text_file(path: Path) -> bool:
    if path.suffix.lower() not in ARTICLE_TEXT_EXTS:
        return False
    try:
        fm, body, has_fm = frontmatter_block(read_text(path))
    except OSError:
        return False
    if has_fm and any(fm.get(field) for field in ["derived_from", "derivation_method", "source_hash_at_derivation"]):
        return True
    head = body[:2000].lower() if has_fm else read_text(path)[:2000].lower()
    return any(token in head for token in ["derived_from:", "derivation_method:", "ocr text", "transcribed from"])


def validate_custom_raw_dir(value: str | None) -> str:
    rel = normalize_wiki_rel(value)
    if not rel or not rel.startswith("raw/") or rel in {"raw", "raw/"}:
        raise ValueError("--custom-raw-dir must be a raw/<category> path")
    if ".." in Path(rel).parts:
        raise ValueError("--custom-raw-dir must not contain '..'")
    return rel.rstrip("/")


def classify_file(path: Path) -> str | None:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in MEDIA_EXTS:
        return "raw/media"
    if suffix in PAPER_EXTS:
        return "raw/papers"
    if suffix in DATA_EXTS:
        return "raw/data"
    if suffix in TRANSCRIPT_EXTS or any(token in name for token in ["transcript", "interview", "meeting", "lecture"]):
        return "raw/transcripts"
    if suffix in ARTICLE_TEXT_EXTS:
        if is_derived_text_file(path):
            return "raw/derived"
        try:
            text = read_text(path)[:4000].lower()
        except OSError:
            text = ""
        if any(token in text for token in ["doi:", "abstract", "journal", "isbn", "publisher"]):
            return "raw/papers"
        if any(token in text for token in ["speaker:", "transcript", "interview"]):
            return "raw/transcripts"
        return "raw/articles"
    return None


def classification_for_file(path: Path, unknown_policy: str, custom_raw_dir: str | None) -> dict[str, str]:
    target_rel = classify_file(path)
    if target_rel:
        return {"status": "classified", "target_dir": target_rel, "reason": "matched_known_type"}
    if unknown_policy == "articles":
        return {"status": "classified_fallback", "target_dir": "raw/articles", "reason": "unknown_type_fallback_articles"}
    if unknown_policy == "custom":
        target = validate_custom_raw_dir(custom_raw_dir)
        return {"status": "classified_custom", "target_dir": target, "reason": "unknown_type_custom_category"}
    return {"status": "needs_user_classification", "target_dir": "raw/inbox", "reason": "unknown_type"}


def iter_page_files(wiki: Path) -> list[Path]:
    files: list[Path] = []
    for folder in PAGE_DIRS:
        root = wiki / folder
        if root.exists() and path_stays_within_wiki(wiki, root):
            files.extend(
                sorted(
                    path
                    for path in root.rglob("*.md")
                    if path.is_file() and path_stays_within_wiki(wiki, path)
                )
            )
    return sorted(files)


def iter_raw_text_files(wiki: Path) -> list[Path]:
    raw_root = wiki / "raw"
    if not raw_root.exists() or not path_stays_within_wiki(wiki, raw_root):
        return []
    return sorted(
        path
        for path in raw_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in TEXT_HASH_EXTS
        and path_stays_within_wiki(wiki, path)
    )


def iter_raw_files(wiki: Path, include_inbox: bool = True, include_derived: bool = True) -> list[Path]:
    raw_root = wiki / "raw"
    if not raw_root.exists() or not path_stays_within_wiki(wiki, raw_root):
        return []
    files: list[Path] = []
    for path in raw_root.rglob("*"):
        if not path.is_file() or not path_stays_within_wiki(wiki, path):
            continue
        rel = path.relative_to(wiki).as_posix()
        if not include_inbox and rel.startswith("raw/inbox/"):
            continue
        if not include_derived and rel.startswith("raw/derived/"):
            continue
        files.append(path)
    return sorted(files)


def raw_hash_index(
    wiki: Path,
    *,
    include_inbox: bool = False,
    include_derived: bool = False,
    exclude: set[Path] | None = None,
) -> dict[tuple[str, str], list[Path]]:
    excluded = {path.resolve() for path in (exclude or set())}
    index: dict[tuple[str, str], list[Path]] = {}
    for path in iter_raw_files(wiki, include_inbox=include_inbox, include_derived=include_derived):
        if path.resolve() in excluded:
            continue
        try:
            digest, scheme = compute_source_hash(path)
        except (OSError, ValueError):
            continue
        index.setdefault((scheme, digest), []).append(path)
    return index


def iter_metadata_files(wiki: Path) -> list[Path]:
    return sorted({*iter_page_files(wiki), *iter_raw_text_files(wiki)})


def wiki_link_target(link: str) -> str:
    link = link.split("|", 1)[0].split("#", 1)[0].strip()
    if link.endswith(".md"):
        link = link[:-3]
    return link.replace("\\", "/")


def page_key(path: Path, wiki: Path) -> str:
    rel = path.relative_to(wiki).as_posix()
    return rel[:-3] if rel.endswith(".md") else rel


def extract_wikilinks(text: str) -> list[str]:
    return [wiki_link_target(match.group(1)) for match in re.finditer(r"\[\[([^\]]+)\]\]", text)]


def first_summary(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(">") or stripped.startswith("- "):
            continue
        return stripped[:180]
    return ""


def tags_from_agents(wiki: Path) -> set[str]:
    path = agent_config_path_for_read(wiki)
    if not path.exists():
        return set()
    text = read_text(path)
    tags: set[str] = set()
    in_taxonomy = False
    for line in text.splitlines():
        if re.match(r"^##+\s+(Tag Taxonomy|Evidence Tags)\b", line, re.I):
            in_taxonomy = True
            continue
        if in_taxonomy and line.startswith("#"):
            in_taxonomy = False
            continue
        if in_taxonomy:
            match = re.match(r"\s*-\s+`?([A-Za-z0-9_/-]+)`?", line)
            if match:
                tags.add(match.group(1))
    return tags


def agent_config_path_for_read(wiki: Path) -> Path:
    for name in ["AGENTS.md", "CLAUDE.md"]:
        path = wiki / name
        if path.exists() and path_stays_within_wiki(wiki, path):
            return path
    for path in sorted(wiki.glob("*.md")):
        if not path_stays_within_wiki(wiki, path):
            continue
        try:
            if AGENT_CONFIG_MARKER in read_text(path):
                return path
        except OSError:
            continue
    return wiki / "AGENTS.md"


def has_agent_config(wiki: Path) -> bool:
    if any(
        (wiki / name).exists() and path_stays_within_wiki(wiki, wiki / name)
        for name in AGENT_CONFIG_FILES.values()
    ):
        return True
    for path in sorted(wiki.glob("*.md")):
        if not path_stays_within_wiki(wiki, path):
            continue
        try:
            if AGENT_CONFIG_MARKER in read_text(path):
                return True
        except OSError:
            continue
    return False


def command_init(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    values = {"domain": args.domain}
    try:
        directories: list[Path] = []
        for rel in RAW_DIRS + PAGE_DIRS + META_DIRS:
            _safe_rel, directory = resolve_wiki_reference(wiki, rel, "init directory")
            if directory is None:
                raise ValueError(f"invalid init directory: {rel}")
            directories.append(directory)
        agent_config_name = resolve_agent_config_name(wiki, args.agent_platform, args.agent_file)
        _agent_rel, agent_config_path = resolve_wiki_reference(wiki, agent_config_name, "agent file")
        if agent_config_path is None:
            raise ValueError("agent file path is empty")
        root_targets: dict[str, Path] = {}
        for name in ROOT_FILES:
            _root_rel, target = resolve_wiki_reference(wiki, name, name)
            if target is None:
                raise ValueError(f"invalid root file: {name}")
            root_targets[name] = target
        _schema_rel, schema_path = resolve_wiki_reference(wiki, "_meta/schema.json", "schema file")
        if schema_path is None:
            raise ValueError("invalid schema file path")
    except (OSError, ValueError) as exc:
        print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
        return 2
    try:
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
        return 2
    agent_status = write_or_append_agent_config(agent_config_path, render_agent_contract(values))
    for name, target in root_targets.items():
        if target.exists():
            if not args.force or name not in FORCE_REFRESHABLE_ROOT_FILES:
                continue
        write_text(target, render_template(name, values))
    schema_warning = ""
    if not schema_path.exists():
        schema = {
            "schema_version": SCHEMA_VERSION,
            "profile": "research" if args.research else "core",
            "markdown_is_source_of_truth": True,
        }
        write_text(schema_path, json.dumps(schema, indent=2) + "\n")
    elif args.research:
        schema_expected = bytes_hash(schema_path)
        try:
            schema = json.loads(read_text(schema_path))
            if not isinstance(schema, dict):
                raise ValueError("schema root must be an object")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            schema_warning = f"existing schema is invalid and was not changed: {exc}"
        else:
            if schema.get("profile") != "research":
                schema["profile"] = "research"
                schema.setdefault("schema_version", SCHEMA_VERSION)
                schema.setdefault("markdown_is_source_of_truth", True)
                write_text(
                    schema_path,
                    json.dumps(schema, indent=2) + "\n",
                    expected_hash=schema_expected,
                )
    if args.research:
        add_on = render_template("research-schema.md", values)
        agents = agent_config_path
        current = read_text(agents)
        if "Research Schema Add-on" not in current:
            write_text(agents, current.rstrip() + "\n\n" + add_on, expected_hash=bytes_hash(agents))
    payload = {
        "wiki": str(wiki),
        "created": True,
        "agent_config": agent_config_name,
        "agent_config_status": agent_status,
        "schema_profile": schema_profile(wiki),
    }
    if schema_warning:
        payload["schema_warning"] = schema_warning
    print(json.dumps(payload, indent=2))
    return 0


def command_classify(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    results: list[dict[str, Any]] = []
    try:
        _inbox_rel, inbox = resolve_wiki_reference(wiki, "raw/inbox", "raw inbox")
        if inbox is None:
            raise ValueError("raw inbox path is empty")
        if args.unknown_policy == "custom":
            validate_custom_raw_dir(args.custom_raw_dir)
    except ValueError as exc:
        print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
        return 2
    if not inbox.exists():
        print(json.dumps({"wiki": str(wiki), "classified": []}, indent=2))
        return 0
    inbox_items = sorted(inbox.iterdir())
    unsafe_items = [item for item in inbox_items if not path_stays_within_wiki(wiki, item)]
    if unsafe_items:
        unsafe = ", ".join(item.name for item in unsafe_items)
        print(json.dumps({"wiki": str(wiki), "error": f"unsafe inbox path(s): {unsafe}"}, indent=2))
        return 2
    existing_hashes = raw_hash_index(wiki, include_inbox=False, include_derived=False)
    planned_hashes: dict[tuple[str, str], Path] = {}
    for path in (item for item in inbox_items if item.is_file()):
        try:
            digest, scheme = compute_source_hash(path)
        except (OSError, ValueError) as exc:
            results.append(
                {
                    "source": str(path),
                    "target": "",
                    "status": "error",
                    "reason": str(exc),
                    "moved": False,
                }
            )
            continue
        duplicates = existing_hashes.get((scheme, digest), [])
        if duplicates:
            results.append(
                {
                    "source": str(path),
                    "target": str(duplicates[0]),
                    "status": "reused",
                    "reason": "identical_content_already_classified",
                    "hash_scheme": scheme,
                    "sha256": digest,
                    "moved": False,
                }
            )
            continue
        if (scheme, digest) in planned_hashes:
            results.append(
                {
                    "source": str(path),
                    "target": str(planned_hashes[(scheme, digest)]),
                    "status": "reused",
                    "reason": "identical_content_in_batch",
                    "hash_scheme": scheme,
                    "sha256": digest,
                    "moved": False,
                }
            )
            continue
        classification = classification_for_file(path, args.unknown_policy, args.custom_raw_dir)
        try:
            target_rel = f"{classification['target_dir']}/{path.name}"
            _safe_target_rel, target = resolve_wiki_reference(wiki, target_rel, "classification target")
        except ValueError as exc:
            print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
            return 2
        if target is None:
            print(json.dumps({"wiki": str(wiki), "error": "classification target path is empty"}, indent=2))
            return 2
        target = unique_path(target)
        target_dir = target.parent
        moved = False
        if args.move and classification["status"] != "needs_user_classification":
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            moved = True
            existing_hashes.setdefault((scheme, digest), []).append(target)
        planned_hashes[(scheme, digest)] = target
        results.append(
            {
                "source": str(path),
                "target": str(target),
                "status": classification["status"],
                "reason": classification["reason"],
                "hash_scheme": scheme,
                "sha256": digest,
                "moved": moved,
            }
        )
    print(json.dumps({"wiki": str(wiki), "classified": results}, indent=2))
    return 0


def command_hash_source(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    deprecation = (
        "--write is deprecated because raw sources should remain byte-immutable; "
        "store the hash on the source summary instead"
    )
    try:
        digest, scheme = compute_source_hash(path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"path": str(path), "error": str(exc)}, indent=2))
        return 2
    if args.write and any(part.lower() == "raw" for part in path.parts):
        print(
            json.dumps(
                {
                    "path": str(path),
                    "hash_scheme": scheme,
                    "sha256": digest,
                    "written": False,
                    "deprecation_warning": deprecation,
                    "error": "refusing --write for a path inside raw; raw sources are byte-immutable",
                },
                indent=2,
            )
        )
        return 2
    written = False
    if args.write:
        if scheme != HASH_SCHEME_TEXT:
            print(
                json.dumps(
                    {
                        "path": str(path),
                        "hash_scheme": scheme,
                        "sha256": digest,
                        "deprecation_warning": deprecation,
                        "error": "--write is only supported for UTF-8 text sources",
                    },
                    indent=2,
                )
            )
            return 2
        text = read_utf8_text_for_hash(path)
        expected_hash = bytes_hash(path)
        fm, body, has_fm = frontmatter_block(text)
        fm["sha256"] = digest
        fm["hash_scheme"] = scheme
        fm["hashed_at"] = today()
        fm.setdefault("ingested", today())
        new_text = "---\n" + dump_simple_yaml(fm) + "---\n" + (body if has_fm else text)
        write_text(path, new_text, expected_hash=expected_hash)
        written = True
    payload: dict[str, Any] = {
        "path": str(path),
        "hash_scheme": scheme,
        "sha256": digest,
        "written": written,
    }
    if args.write:
        payload["deprecation_warning"] = deprecation
    print(json.dumps(payload, indent=2))
    return 0


def build_index_text(wiki: Path, exclude: set[Path] | None = None) -> tuple[str, int]:
    excluded = {path.resolve() for path in (exclude or set())}
    sections = {
        "source": "Sources",
        "entity": "Entities",
        "concept": "Concepts",
        "synthesis": "Syntheses",
        "comparison": "Comparisons",
        "query": "Queries",
    }
    entries: dict[str, list[str]] = {section: [] for section in sections.values()}
    for path in iter_page_files(wiki):
        if path.resolve() in excluded:
            continue
        text = read_text(path)
        fm, body, _has_fm = frontmatter_block(text)
        page_type = str(fm.get("type") or PAGE_TYPE_BY_DIR.get(path.parent.name, "concept"))
        section = sections.get(page_type, "Concepts")
        title = str(fm.get("title") or path.stem.replace("-", " ").title())
        summary = str(fm.get("summary") or first_summary(body) or "No summary.")
        target = page_key(path, wiki)
        entries.setdefault(section, []).append(f"- [[{target}|{title}]] - {summary}")
    lines = [
        "# Wiki Index",
        "",
        "> Catalog of wiki pages. Keep one concise line per durable page.",
        f"> Last updated: {today()} | Total pages: {sum(len(v) for v in entries.values())}",
        "",
    ]
    for section in ["Sources", "Entities", "Concepts", "Syntheses", "Comparisons", "Queries"]:
        lines.append(f"## {section}")
        lines.append("")
        lines.extend(sorted(entries.get(section, [])))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", sum(len(v) for v in entries.values())


def command_update_index(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    try:
        _index_rel, index_path = resolve_wiki_reference(wiki, "index.md", "index.md")
        if index_path is None:
            raise ValueError("invalid index.md path")
        index_text, pages = build_index_text(wiki)
        write_text(index_path, index_text)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
        return 2
    print(json.dumps({"wiki": str(wiki), "pages": pages}, indent=2))
    return 0


def canonical_source_identifier(field: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "unknown":
        return ""
    if field == "doi":
        text = re.sub(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", "", text, flags=re.I)
        return text.strip().lower()
    if field == "isbn":
        return re.sub(r"[^0-9Xx]", "", text).upper()
    if field == "url":
        try:
            parts = urlsplit(text)
        except ValueError:
            return text.rstrip("/").lower()
        if not parts.scheme or not parts.netloc:
            return text.rstrip("/").lower()
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
    return text.lower()


def page_lookup_tables(
    wiki: Path, page_files: list[Path]
) -> tuple[dict[str, Path], dict[str, set[Path]], dict[str, list[Path]]]:
    exact: dict[str, Path] = {}
    aliases: dict[str, set[Path]] = {}
    stems: dict[str, list[Path]] = {}
    for path in page_files:
        key = page_key(path, wiki)
        exact[key] = path
        stems.setdefault(path.stem, []).append(path)
        text = read_text(path)
        fm, _body, _has_fm = frontmatter_block(text)
        names = {path.stem}
        names.update(str(item).strip() for item in list_value(fm.get("aliases")) if str(item).strip())
        title = str(fm.get("title") or "").strip()
        if title:
            names.add(title)
        for name in names:
            aliases.setdefault(name, set()).add(path)
            aliases.setdefault(name.lower(), set()).add(path)
    return exact, aliases, stems


def resolve_page_link(
    link: str,
    exact: dict[str, Path],
    aliases: dict[str, set[Path]],
) -> tuple[Path | None, list[Path]]:
    if link in exact:
        return exact[link], [exact[link]]
    candidates = aliases.get(link, set()) | aliases.get(link.lower(), set())
    ordered = sorted(candidates)
    return (ordered[0] if len(ordered) == 1 else None), ordered


def duplicate_raw_hash_issues(wiki: Path) -> list[str]:
    issues: list[str] = []
    index = raw_hash_index(wiki, include_inbox=False, include_derived=False)
    for (scheme, digest), paths in sorted(index.items()):
        if len(paths) < 2:
            continue
        rels = ", ".join(path.relative_to(wiki).as_posix() for path in paths)
        issues.append(f"{scheme}:{digest}: {rels}")
    return issues


def duplicate_source_identifier_issues(wiki: Path, page_files: list[Path]) -> list[str]:
    values: dict[tuple[str, str], list[str]] = {}
    for path in page_files:
        fm, _body, _has_fm = frontmatter_block(read_text(path))
        if page_type_for_path(path, wiki, fm) != "source" and path.parent.name != "sources":
            continue
        rel = path.relative_to(wiki).as_posix()
        for field in ["url", "doi", "isbn"]:
            canonical = canonical_source_identifier(field, fm.get(field))
            if canonical:
                values.setdefault((field, canonical), []).append(rel)
    issues: list[str] = []
    for (field, value), rels in sorted(values.items()):
        if len(rels) > 1:
            issues.append(f"{field}:{value}: {', '.join(rels)}")
    return issues


def lint_wiki(wiki: Path) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {
        "unsafe_paths": collect_unsafe_symlinks(wiki),
        "schema_config": schema_config_issues(wiki),
        "missing_root_files": [],
        "missing_agent_config": [],
        "inbox_files": [],
        "missing_frontmatter": [],
        "frontmatter_format": [],
        "missing_fields": [],
        "invalid_types": [],
        "missing_citation_metadata": [],
        "source_provenance_issues": [],
        "broken_links": [],
        "orphan_pages": [],
        "missing_index_entries": [],
        "tag_drift": [],
        "source_hash_drift": [],
        "derived_metadata_gaps": [],
        "derived_hash_drift": [],
        "duplicate_raw_hashes": [],
        "duplicate_source_identifiers": [],
        "duplicate_stems": [],
        "ambiguous_links": [],
        "index_membership_issues": [],
        "oversized_pages": [],
        "log_rotation": [],
    }
    for name in ROOT_FILES:
        if not (wiki / name).exists():
            issues["missing_root_files"].append(name)
    if not has_agent_config(wiki):
        issues["missing_agent_config"].append("CLAUDE.md, AGENTS.md, or marked custom root Markdown config")
    inbox = wiki / "raw" / "inbox"
    if inbox.exists():
        issues["inbox_files"].extend(str(path.relative_to(wiki)) for path in sorted(inbox.iterdir()) if path.is_file())
    page_files = iter_page_files(wiki)
    known_pages, page_aliases, stem_groups = page_lookup_tables(wiki, page_files)
    inbound: dict[str, int] = {key: 0 for key in known_pages}
    for stem, paths in sorted(stem_groups.items()):
        if len(paths) > 1:
            issues["duplicate_stems"].append(
                f"{stem}: {', '.join(path.relative_to(wiki).as_posix() for path in paths)}"
            )
    issues["duplicate_raw_hashes"].extend(duplicate_raw_hash_issues(wiki))
    issues["duplicate_source_identifiers"].extend(duplicate_source_identifier_issues(wiki, page_files))
    index_path = wiki / "index.md"
    indexed = read_text(index_path) if index_path.exists() and path_stays_within_wiki(wiki, index_path) else ""
    index_targets = [normalized_page_ref(item) for item in extract_wikilinks(indexed)]
    index_counts: dict[str, int] = {}
    for target in index_targets:
        index_counts[target] = index_counts.get(target, 0) + 1
    allowed_tags = tags_from_agents(wiki)
    for path in page_files:
        rel = path.relative_to(wiki).as_posix()
        text = read_text(path)
        issues["frontmatter_format"].extend(frontmatter_format_issues(text, rel))
        fm, body, has_fm = frontmatter_block(text)
        if not has_fm:
            issues["missing_frontmatter"].append(rel)
            fm = {}
            body = text
        missing = missing_required_fields(path, wiki, fm)
        if missing:
            issues["missing_fields"].append(f"{rel}: {', '.join(missing)}")
        page_type = str(fm.get("type", ""))
        if page_type and page_type not in VALID_TYPES:
            issues["invalid_types"].append(f"{rel}: {page_type}")
        if page_type == "source" or path.parent.name == "sources":
            raw_invalid = False
            try:
                raw_source, raw_path = resolve_wiki_reference(wiki, fm.get("raw_source"), "raw_source")
            except ValueError as exc:
                issues["source_provenance_issues"].append(f"{rel}: {exc}")
                raw_source, raw_path, raw_invalid = "", None, True
            try:
                derived_source, derived_path = resolve_wiki_reference(wiki, fm.get("derived_source"), "derived_source")
            except ValueError as exc:
                issues["source_provenance_issues"].append(f"{rel}: {exc}")
                derived_source, derived_path = "", None
            if derived_source and not raw_source and not raw_invalid:
                issues["source_provenance_issues"].append(f"{rel}: derived_source without raw_source")
            if raw_source and raw_path is not None and not raw_path.is_file():
                issues["source_provenance_issues"].append(f"{rel}: raw_source not found: {raw_source}")
            if derived_source and derived_path is not None and not derived_path.is_file():
                issues["source_provenance_issues"].append(f"{rel}: derived_source not found: {derived_source}")
            kind = str(fm.get("source_kind", "")).lower()
            if kind in SCIENTIFIC_KINDS and schema_profile(wiki) == "research":
                required_citation, identifier_options = research_citation_requirements(kind)
                missing_citation = [
                    field for field in required_citation if field not in fm or fm.get(field) in ("", [], None)
                ]
                if identifier_options and not any(
                    canonical_source_identifier(field, fm.get(field)) for field in identifier_options
                ):
                    missing_citation.append("one of " + "/".join(identifier_options))
                if missing_citation:
                    issues["missing_citation_metadata"].append(f"{rel}: {', '.join(missing_citation)}")
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        if allowed_tags:
            for tag in tags:
                if tag not in allowed_tags:
                    issues["tag_drift"].append(f"{rel}: {tag}")
        for link in extract_wikilinks(text):
            if not link:
                continue
            resolved, candidates = resolve_page_link(link, known_pages, page_aliases)
            if len(candidates) > 1:
                candidate_rels = ", ".join(page_key(item, wiki) for item in candidates)
                issues["ambiguous_links"].append(f"{rel}: [[{link}]] -> {candidate_rels}")
            elif resolved is not None:
                inbound[page_key(resolved, wiki)] += 1
            else:
                issues["broken_links"].append(f"{rel}: [[{link}]]")
        key = page_key(path, wiki)
        membership_count = index_counts.get(key, 0)
        if membership_count == 0:
            issues["missing_index_entries"].append(rel)
            issues["index_membership_issues"].append(f"{rel}: missing exact index entry [[{key}]]")
        elif membership_count > 1:
            issues["index_membership_issues"].append(f"{rel}: duplicate exact index entries ({membership_count})")
        if len(text.splitlines()) > 200:
            issues["oversized_pages"].append(rel)
    for key, count in sorted(inbound.items()):
        if count == 0 and not key.startswith("sources/"):
            issues["orphan_pages"].append(key + ".md")
    for target, count in sorted(index_counts.items()):
        if target not in known_pages:
            issues["index_membership_issues"].append(f"index.md: stale entry [[{target}]]")
    raw_root = wiki / "raw"
    if raw_root.exists() and path_stays_within_wiki(wiki, raw_root):
        checked_raw_frontmatter: set[Path] = set()
        derived_root = raw_root / "derived"
        if derived_root.exists() and path_stays_within_wiki(wiki, derived_root):
            for path in sorted(derived_root.rglob("*")):
                if (
                    not path.is_file()
                    or path.suffix.lower() not in TEXT_HASH_EXTS
                    or not path_stays_within_wiki(wiki, path)
                ):
                    continue
                rel = path.relative_to(wiki).as_posix()
                raw_text = read_text(path)
                checked_raw_frontmatter.add(path)
                issues["frontmatter_format"].extend(frontmatter_format_issues(raw_text, rel))
                fm, _body, has_fm = frontmatter_block(raw_text)
                missing = [field for field in DERIVED_REQUIRED_FIELDS if not has_fm or fm.get(field) in ("", [], None)]
                if missing:
                    issues["derived_metadata_gaps"].append(f"{rel}: {', '.join(missing)}")
                if has_fm and fm.get("derived_from") not in ("", [], None):
                    try:
                        derived_from, derived_from_path = resolve_wiki_reference(
                            wiki, fm.get("derived_from"), "derived_from"
                        )
                    except ValueError as exc:
                        issues["derived_metadata_gaps"].append(f"{rel}: {exc}")
                    else:
                        if derived_from and derived_from_path is not None and not derived_from_path.is_file():
                            issues["derived_metadata_gaps"].append(
                                f"{rel}: derived_from not found: {derived_from}"
                            )
                        elif derived_from and derived_from_path is not None and derived_from_path.is_file():
                            expected_derivation_hash = str(fm.get("source_hash_at_derivation") or "").strip()
                            derivation_scheme = str(
                                fm.get("source_hash_scheme_at_derivation") or default_hash_scheme(derived_from_path)
                            ).strip()
                            if not expected_derivation_hash:
                                issues["derived_metadata_gaps"].append(
                                    f"{rel}: source_hash_at_derivation"
                                )
                            elif derivation_scheme not in VALID_HASH_SCHEMES:
                                issues["derived_hash_drift"].append(
                                    f"{rel}: unsupported source_hash_scheme_at_derivation {derivation_scheme!r}"
                                )
                            else:
                                try:
                                    actual_derivation_hash, _used = compute_source_hash(
                                        derived_from_path, derivation_scheme
                                    )
                                except (OSError, ValueError) as exc:
                                    issues["derived_hash_drift"].append(f"{rel}: {exc}")
                                else:
                                    if expected_derivation_hash != actual_derivation_hash:
                                        issues["derived_hash_drift"].append(
                                            f"{rel}: derived_stale ({derived_from})"
                                        )
        for path in sorted(raw_root.rglob("*")):
            if (
                not path.is_file()
                or path.suffix.lower() not in TEXT_HASH_EXTS
                or not path_stays_within_wiki(wiki, path)
            ):
                continue
            raw_text = read_text(path)
            rel = path.relative_to(wiki).as_posix()
            if path not in checked_raw_frontmatter:
                issues["frontmatter_format"].extend(frontmatter_format_issues(raw_text, rel))
            fm, _body, has_fm = frontmatter_block(raw_text)
            expected = fm.get("sha256") if has_fm else None
            if expected:
                scheme = str(fm.get("hash_scheme") or HASH_SCHEME_TEXT)
                try:
                    actual, _used_scheme = compute_source_hash(path, scheme)
                except (OSError, ValueError) as exc:
                    issues["source_hash_drift"].append(f"{rel}: {exc}")
                    continue
                if str(expected) != actual:
                    issues["source_hash_drift"].append(f"{rel}: {scheme}")
    log_path = wiki / "log.md"
    if (
        log_path.exists()
        and path_stays_within_wiki(wiki, log_path)
        and read_text(log_path).count("\n## [") > 500
    ):
        issues["log_rotation"].append("log.md has more than 500 entries")
    return issues


def filter_issue_map(issues: dict[str, list[str]], source: str | None) -> dict[str, list[str]]:
    if not source:
        return {name: list(items) for name, items in issues.items()}
    needle = normalize_wiki_rel(source).lower()
    stem = Path(needle).stem.lower()
    tokens = {needle, normalized_page_ref(needle), stem}
    return {
        name: [item for item in items if any(token and token in item.lower() for token in tokens)]
        for name, items in issues.items()
    }


def limit_issue_map(
    issues: dict[str, list[str]], limit: int | None
) -> tuple[dict[str, list[str]], dict[str, int]]:
    if limit is None:
        return {name: list(items) for name, items in issues.items()}, {}
    effective = max(0, limit)
    limited = {name: items[:effective] for name, items in issues.items()}
    truncated = {name: len(items) - effective for name, items in issues.items() if len(items) > effective}
    return limited, truncated


def command_lint(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    issues = filter_issue_map(lint_wiki(wiki), args.source)
    total = sum(len(items) for items in issues.values())
    output_limit = args.limit if args.limit is not None else (3 if args.summary else None)
    displayed, truncated = limit_issue_map(issues, output_limit)
    if args.json:
        payload: dict[str, Any] = {
            "wiki": str(wiki),
            "source": args.source,
            "total_issues": total,
            "issues": displayed,
        }
        if args.summary:
            payload["summary"] = True
            payload["issue_counts"] = {name: len(items) for name, items in issues.items() if items}
        if truncated:
            payload["truncated"] = truncated
        print(json.dumps(payload, indent=2))
    else:
        print(f"Wiki: {wiki}")
        print(f"Total issues: {total}")
        if args.source:
            print(f"Source scope: {args.source}")
        for name, items in displayed.items():
            if not items:
                continue
            count_suffix = f" ({len(issues[name])})" if args.summary else ""
            print(f"\n{name}{count_suffix}:")
            for item in items:
                print(f"- {item}")
            if name in truncated:
                print(f"- ... {truncated[name]} more")
    return 1 if total and args.fail_on_issues else 0


def source_page_records(wiki: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in iter_page_files(wiki):
        text = read_text(path)
        fm, _body, _has_fm = frontmatter_block(text)
        page_type = str(fm.get("type", ""))
        if page_type == "source" or path.parent.name == "sources":
            records.append({"path": path, "rel": path.relative_to(wiki).as_posix(), "fm": fm, "text": text})
    return records


def source_page_ref_map(wiki: Path, records: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for record in records:
        path = record["path"]
        rel = record["rel"]
        key = page_key(path, wiki)
        mapping[key] = rel
        mapping[rel] = rel
        mapping[path.stem] = rel
    return mapping


def normalized_page_ref(value: Any) -> str:
    rel = normalize_wiki_rel(value)
    return rel[:-3] if rel.endswith(".md") else rel


def source_aliases(path: Path, wiki: Path) -> set[str]:
    key = page_key(path, wiki)
    return {key, path.stem}


def source_pages_for_raw(raw_rel: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = normalize_wiki_rel(raw_rel)
    return [record for record in records if normalize_wiki_rel(record["fm"].get("raw_source")) == normalized]


def affected_pages_for_source(wiki: Path, source_path: Path) -> list[str]:
    pages = iter_page_files(wiki)
    exact, aliases, _stems = page_lookup_tables(wiki, pages)
    reverse_edges: dict[Path, set[Path]] = {path: set() for path in pages}
    for path in pages:
        text = read_text(path)
        fm, _body, _has_fm = frontmatter_block(text)
        refs = [normalized_page_ref(item) for item in list_value(fm.get("sources"))]
        refs.extend(extract_wikilinks(text))
        for ref in refs:
            resolved, candidates = resolve_page_link(ref, exact, aliases)
            if resolved is not None and len(candidates) == 1 and resolved != path:
                reverse_edges.setdefault(resolved, set()).add(path)
    start = source_path.resolve()
    canonical_start = next((path for path in pages if path.resolve() == start), source_path)
    seen: set[Path] = {canonical_start}
    queue = [canonical_start]
    affected: list[Path] = []
    while queue:
        current = queue.pop(0)
        for dependent in sorted(reverse_edges.get(current, set())):
            if dependent in seen:
                continue
            seen.add(dependent)
            affected.append(dependent)
            queue.append(dependent)
    return [path.relative_to(wiki).as_posix() for path in sorted(affected)]


def collect_relationship_issues(wiki: Path, records: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    source_refs = source_page_ref_map(wiki, records)
    raw_refs: set[str] = set()
    derived_refs: set[str] = set()
    for record in records:
        for field, bucket in [("raw_source", raw_refs), ("derived_source", derived_refs)]:
            try:
                resolved_rel, _path = resolve_wiki_reference(wiki, record["fm"].get(field), field)
            except ValueError:
                continue
            if resolved_rel:
                bucket.add(resolved_rel)
    for record in records:
        fm = record["fm"]
        source_rel = record["rel"]
        raw_invalid = False
        derived_invalid = False
        try:
            raw_rel, raw_path = resolve_wiki_reference(wiki, fm.get("raw_source"), "raw_source")
        except ValueError as exc:
            issues.append(f"{source_rel}: {exc}")
            raw_rel, raw_path, raw_invalid = "", None, True
        try:
            derived_rel, derived_path = resolve_wiki_reference(wiki, fm.get("derived_source"), "derived_source")
        except ValueError as exc:
            issues.append(f"{source_rel}: {exc}")
            derived_rel, derived_path, derived_invalid = "", None, True
        if not raw_rel and not raw_invalid:
            issues.append(f"{source_rel}: missing raw_source")
        elif raw_path is not None and not raw_path.is_file():
            issues.append(f"{source_rel}: raw_source not found: {raw_rel}")
        if derived_rel and not derived_invalid and derived_path is not None:
            if not derived_rel.startswith("raw/derived/"):
                issues.append(f"{source_rel}: derived_source must be under raw/derived/: {derived_rel}")
            if not derived_path.is_file():
                issues.append(f"{source_rel}: derived_source not found: {derived_rel}")
            elif derived_path.suffix.lower() in TEXT_HASH_EXTS:
                derived_fm, _body, has_fm = frontmatter_block(read_text(derived_path))
                derived_from_invalid = False
                try:
                    derived_from, _derived_from_path = resolve_wiki_reference(
                        wiki, derived_fm.get("derived_from") if has_fm else "", "derived_from"
                    )
                except ValueError as exc:
                    issues.append(f"{source_rel}: {exc}")
                    derived_from, derived_from_invalid = "", True
                if not derived_from and not derived_from_invalid:
                    issues.append(f"{source_rel}: derived_source missing derived_from: {derived_rel}")
                elif raw_rel and derived_from != raw_rel:
                    issues.append(f"{source_rel}: derived_source derived_from mismatch: {derived_rel} -> {derived_from}, expected {raw_rel}")
    for path in iter_page_files(wiki):
        fm, _body, has_fm = frontmatter_block(read_text(path))
        page_type = page_type_for_path(path, wiki, fm)
        if page_type == "source" or path.parent.name == "sources":
            continue
        rel = path.relative_to(wiki).as_posix()
        for ref in list_value(fm.get("sources") if has_fm else []):
            normalized = normalized_page_ref(ref)
            if normalized and normalized not in source_refs:
                issues.append(f"{rel}: source reference not found: {ref}")
    raw_root = wiki / "raw"
    if raw_root.exists() and path_stays_within_wiki(wiki, raw_root):
        for path in sorted(raw_root.rglob("*")):
            if not path.is_file() or not path_stays_within_wiki(wiki, path):
                continue
            rel = path.relative_to(wiki).as_posix()
            if rel.startswith("raw/inbox/"):
                continue
            if rel.startswith("raw/derived/"):
                if path.suffix.lower() in TEXT_HASH_EXTS:
                    fm, _body, has_fm = frontmatter_block(read_text(path))
                    derived_from_invalid = False
                    try:
                        derived_from, derived_from_path = resolve_wiki_reference(
                            wiki, fm.get("derived_from") if has_fm else "", "derived_from"
                        )
                    except ValueError as exc:
                        issues.append(f"{rel}: {exc}")
                        derived_from, derived_from_path, derived_from_invalid = "", None, True
                    if not derived_from and not derived_from_invalid:
                        issues.append(f"{rel}: missing derived_from")
                    elif derived_from_path is not None and not derived_from_path.is_file():
                        issues.append(f"{rel}: derived_from not found: {derived_from}")
                    if rel not in derived_refs:
                        issues.append(f"{rel}: unlinked_derived_source")
                continue
            if rel not in raw_refs:
                issues.append(f"{rel}: unlinked_raw_source")
    return issues


def collect_source_hash_health(wiki: Path, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    drifted: list[dict[str, Any]] = []
    issues: list[str] = []
    blocking: list[str] = []
    for record in records:
        fm = record["fm"]
        source_rel = record["rel"]
        try:
            raw_rel, raw_path = resolve_wiki_reference(wiki, fm.get("raw_source"), "raw_source")
        except ValueError as exc:
            message = f"{source_rel}: {exc}"
            issues.append(message)
            blocking.append(message)
            continue
        if not raw_rel or raw_path is None or not raw_path.is_file():
            continue
        for field in REQUIRED_SOURCE_HASH_FIELDS:
            value = str(fm.get(field) or "").strip()
            if not value or value.lower() == "unknown":
                message = f"{source_rel}: missing {field}"
                issues.append(message)
                blocking.append(message)
        expected = str(fm.get("raw_sha256") or "").strip()
        scheme = str(fm.get("raw_hash_scheme") or "").strip()
        if not expected or expected.lower() == "unknown" or not scheme or scheme.lower() == "unknown":
            continue
        if scheme not in VALID_HASH_SCHEMES:
            message = f"{source_rel}: unsupported raw_hash_scheme {scheme!r}"
            issues.append(message)
            blocking.append(message)
            continue
        default_scheme = default_hash_scheme(raw_path)
        if scheme != default_scheme:
            issues.append(f"{source_rel}: raw_hash_scheme {scheme} differs from default {default_scheme}")
        try:
            actual, used_scheme = compute_source_hash(raw_path, scheme)
        except (OSError, ValueError) as exc:
            message = f"{source_rel}: {exc}"
            issues.append(message)
            blocking.append(message)
            continue
        if expected != actual:
            message = f"{source_rel}: source_summary_raw_hash_drift"
            issues.append(message)
            drifted.append(
                {
                    "raw_source": raw_rel,
                    "source_page": source_rel,
                    "hash_scheme": used_scheme,
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "reason": "source_summary_raw_hash_drift",
                }
            )
    return drifted, issues, blocking


def collect_metadata_schema_issues(wiki: Path) -> list[str]:
    issues: list[str] = []
    for path in iter_metadata_files(wiki):
        rel = path.relative_to(wiki).as_posix()
        text = read_text(path)
        fm, _body, has_fm = frontmatter_block(text)
        if not has_fm:
            if not rel.startswith("raw/") or rel.startswith("raw/derived/"):
                issues.append(f"{rel}: missing frontmatter")
            continue
        missing = missing_required_fields(path, wiki, fm)
        if missing:
            issues.append(f"{rel}: missing required fields: {', '.join(missing)}")
        placeholders = placeholder_fields(path, wiki, fm)
        if placeholders:
            issues.append(f"{rel}: placeholder fields need review: {', '.join(placeholders)}")
        page_type = page_type_for_path(path, wiki, fm)
        if page_type and not rel.startswith("raw/") and page_type not in VALID_TYPES:
            issues.append(f"{rel}: invalid type: {page_type}")
        confidence = str(fm.get("confidence") or "").strip().lower()
        if confidence and confidence not in VALID_CONFIDENCE:
            issues.append(f"{rel}: invalid confidence: {confidence}")
        status = str(fm.get("status") or "").strip().lower()
        if status and status not in VALID_STATUS:
            issues.append(f"{rel}: invalid status: {status}")
        source_kind = str(fm.get("source_kind") or "").strip().lower()
        if source_kind and source_kind != "unknown" and source_kind not in SCIENTIFIC_KINDS | {"article", "transcript", "media"}:
            issues.append(f"{rel}: invalid source_kind: {source_kind}")
        if source_kind in SCIENTIFIC_KINDS and schema_profile(wiki) == "research":
            required_citation, identifier_options = research_citation_requirements(source_kind)
            citation_missing = [
                field
                for field in required_citation
                if fm.get(field) in ("", [], None) or str(fm.get(field)).strip().lower() == "unknown"
            ]
            if identifier_options and not any(
                canonical_source_identifier(field, fm.get(field)) for field in identifier_options
            ):
                citation_missing.append("one of " + "/".join(identifier_options))
            if citation_missing:
                issues.append(f"{rel}: research citation fields need review: {', '.join(citation_missing)}")
    return issues


def collect_field_order_issues(wiki: Path) -> list[str]:
    issues: list[str] = []
    for path in iter_metadata_files(wiki):
        rel = path.relative_to(wiki).as_posix()
        text = read_text(path)
        fm, _body, has_fm = frontmatter_block(text)
        if not has_fm:
            continue
        order = canonical_field_order(path, wiki, fm)
        if not order:
            continue
        expected = expected_frontmatter_order(fm, order)
        actual = list(fm.keys())
        if actual != expected:
            issues.append(f"{rel}: expected order {', '.join(expected)}")
    return issues


def collect_noncanonical_fields(wiki: Path) -> list[str]:
    issues: list[str] = []
    for path in iter_metadata_files(wiki):
        text = read_text(path)
        fm, _body, has_fm = frontmatter_block(text)
        if not has_fm:
            continue
        rel = path.relative_to(wiki).as_posix()
        for key in fm:
            canonical = NONCANONICAL_FIELD_ALIASES.get(key)
            if canonical:
                issues.append(f"{rel}: {key} -> {canonical}")
    return issues


def collect_metadata_inventory(wiki: Path, limit: int) -> dict[str, dict[str, Any]]:
    values: dict[str, set[str]] = {}
    for path in iter_metadata_files(wiki):
        text = read_text(path)
        fm, _body, has_fm = frontmatter_block(text)
        if not has_fm:
            continue
        for key, value in fm.items():
            bucket = values.setdefault(key, set())
            if isinstance(value, list):
                if not value:
                    bucket.add("[]")
                else:
                    bucket.update(str(item) for item in value)
            else:
                bucket.add(str(value))
    inventory: dict[str, dict[str, Any]] = {}
    for key in sorted(values):
        sorted_values = sorted(values[key])
        inventory[key] = {
            "count": len(sorted_values),
            "values": sorted_values[:limit],
            "truncated": len(sorted_values) > limit,
        }
    return inventory


def collect_raw_frontmatter_drift(wiki: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drifted: list[dict[str, Any]] = []
    raw_root = wiki / "raw"
    if not raw_root.exists() or not path_stays_within_wiki(wiki, raw_root):
        return drifted
    for path in sorted(raw_root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in TEXT_HASH_EXTS
            or not path_stays_within_wiki(wiki, path)
        ):
            continue
        fm, _body, has_fm = frontmatter_block(read_text(path))
        expected = str(fm.get("sha256") or "").strip() if has_fm else ""
        if not expected:
            continue
        rel = path.relative_to(wiki).as_posix()
        scheme = str(fm.get("hash_scheme") or HASH_SCHEME_TEXT)
        try:
            actual, used_scheme = compute_source_hash(path, scheme)
        except (OSError, ValueError) as exc:
            drifted.append(
                {
                    "raw_source": rel,
                    "source_page": "",
                    "hash_scheme": scheme,
                    "expected_sha256": expected,
                    "actual_sha256": "",
                    "reason": str(exc),
                }
            )
            continue
        if expected == actual:
            continue
        linked_sources = source_pages_for_raw(rel, records)
        if not linked_sources:
            drifted.append(
                {
                    "raw_source": rel,
                    "source_page": "",
                    "hash_scheme": used_scheme,
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "reason": "raw_frontmatter_hash_drift",
                }
            )
            continue
        for source_page in linked_sources:
            drifted.append(
                {
                    "raw_source": rel,
                    "source_page": source_page["rel"],
                    "hash_scheme": used_scheme,
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "reason": "raw_frontmatter_hash_drift",
                }
            )
    return drifted


def collect_derived_hash_health(wiki: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drifted: list[dict[str, Any]] = []
    derived_root = wiki / "raw" / "derived"
    if not derived_root.exists() or not path_stays_within_wiki(wiki, derived_root):
        return drifted
    by_derived = {
        normalize_wiki_rel(record["fm"].get("derived_source")): record
        for record in records
        if normalize_wiki_rel(record["fm"].get("derived_source"))
    }
    for path in sorted(derived_root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in TEXT_HASH_EXTS
            or not path_stays_within_wiki(wiki, path)
        ):
            continue
        rel = path.relative_to(wiki).as_posix()
        fm, _body, has_fm = frontmatter_block(read_text(path))
        if not has_fm:
            continue
        expected = str(fm.get("source_hash_at_derivation") or "").strip()
        if not expected:
            continue
        try:
            raw_rel, raw_path = resolve_wiki_reference(wiki, fm.get("derived_from"), "derived_from")
        except ValueError:
            continue
        if not raw_rel or raw_path is None or not raw_path.is_file():
            continue
        scheme = str(fm.get("source_hash_scheme_at_derivation") or default_hash_scheme(raw_path))
        try:
            actual, used_scheme = compute_source_hash(raw_path, scheme)
        except (OSError, ValueError):
            continue
        if expected == actual:
            continue
        record = by_derived.get(rel)
        drifted.append(
            {
                "raw_source": raw_rel,
                "derived_source": rel,
                "source_page": record["rel"] if record else "",
                "hash_scheme": used_scheme,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "reason": "derived_stale",
            }
        )
    return drifted


def health_report(wiki: Path, inventory_limit: int = 50, include_inventory: bool = True) -> dict[str, Any]:
    issues = lint_wiki(wiki)
    lint_total = sum(len(items) for items in issues.values())
    records = source_page_records(wiki)
    raw_drift = collect_raw_frontmatter_drift(wiki, records)
    summary_drift, source_hash_issues, source_blocking = collect_source_hash_health(wiki, records)
    derived_drift = collect_derived_hash_health(wiki, records)
    drifted_sources = raw_drift + summary_drift + derived_drift
    relationship_issues = collect_relationship_issues(wiki, records)
    metadata_schema_issues = collect_metadata_schema_issues(wiki)
    field_order_issues = collect_field_order_issues(wiki)
    noncanonical_fields = collect_noncanonical_fields(wiki)
    metadata_inventory = collect_metadata_inventory(wiki, max(1, inventory_limit)) if include_inventory else {}
    blocking_issues: list[str] = []
    for category in [
        "unsafe_paths",
        "schema_config",
        "missing_root_files",
        "broken_links",
        "missing_frontmatter",
        "frontmatter_format",
        "invalid_types",
        "ambiguous_links",
        "duplicate_stems",
        "derived_hash_drift",
    ]:
        for item in issues.get(category, []):
            blocking_issues.append(f"{category}: {item}")
    blocking_issues.extend(source_blocking)
    blocking_issues.extend(f"relationship_issues: {item}" for item in relationship_issues)
    affected_pages: list[dict[str, Any]] = []
    seen_source_pages: set[str] = set()
    for item in drifted_sources:
        source_page = item.get("source_page")
        if not source_page or source_page in seen_source_pages:
            continue
        seen_source_pages.add(source_page)
        source_path = wiki / str(source_page)
        dependents = affected_pages_for_source(wiki, source_path) if source_path.is_file() else []
        affected_pages.append(
            {
                "source_page": source_page,
                "dependent_pages": dependents,
                "all_pages": [source_page] + dependents,
            }
        )
    health_issue_total = (
        len(relationship_issues)
        + len(source_hash_issues)
        + len(metadata_schema_issues)
        + len(field_order_issues)
        + len(noncanonical_fields)
    )
    return {
        "wiki": str(wiki),
        "update_required": bool(drifted_sources),
        "blocking_issues": blocking_issues,
        "maintenance_recommended": bool(lint_total or drifted_sources or health_issue_total),
        "drifted_sources": drifted_sources,
        "affected_pages": affected_pages,
        "relationship_issues": relationship_issues,
        "source_hash_issues": source_hash_issues,
        "metadata_schema_issues": metadata_schema_issues,
        "field_order_issues": field_order_issues,
        "metadata_inventory": metadata_inventory,
        "noncanonical_fields": noncanonical_fields,
        "source_reference_issues": relationship_issues + source_hash_issues,
        "lint": {"total_issues": lint_total, "issues": issues},
    }


def command_health(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    report = health_report(wiki, args.inventory_limit, include_inventory=not args.no_inventory)
    if args.source:
        needle = normalize_wiki_rel(args.source).lower()
        stem = Path(needle).stem.lower()

        def relevant(value: Any) -> bool:
            rendered = json.dumps(value, ensure_ascii=False).lower()
            return any(token and token in rendered for token in {needle, normalized_page_ref(needle), stem})

        for key in [
            "blocking_issues",
            "drifted_sources",
            "affected_pages",
            "relationship_issues",
            "source_hash_issues",
            "metadata_schema_issues",
            "field_order_issues",
            "noncanonical_fields",
            "source_reference_issues",
        ]:
            report[key] = [item for item in report[key] if relevant(item)]
        report["lint"]["issues"] = filter_issue_map(report["lint"]["issues"], args.source)
        report["lint"]["total_issues"] = sum(len(items) for items in report["lint"]["issues"].values())
        report["metadata_inventory"] = {}
        report["update_required"] = bool(report["drifted_sources"])
        report["maintenance_recommended"] = bool(
            report["update_required"]
            or report["blocking_issues"]
            or report["relationship_issues"]
            or report["source_hash_issues"]
            or report["metadata_schema_issues"]
            or report["lint"]["total_issues"]
        )
    full_counts = {
        key: len(report[key])
        for key in [
            "blocking_issues",
            "drifted_sources",
            "affected_pages",
            "relationship_issues",
            "source_hash_issues",
            "metadata_schema_issues",
            "field_order_issues",
        ]
    }
    output_limit = args.limit if args.limit is not None else (3 if args.summary else None)
    truncated: dict[str, int] = {}
    if output_limit is not None:
        effective = max(0, output_limit)
        for key in [
            "blocking_issues",
            "drifted_sources",
            "affected_pages",
            "relationship_issues",
            "source_hash_issues",
            "metadata_schema_issues",
            "field_order_issues",
            "noncanonical_fields",
            "source_reference_issues",
        ]:
            items = report[key]
            if len(items) > effective:
                truncated[key] = len(items) - effective
                report[key] = items[:effective]
        report["lint"]["issues"], lint_truncated = limit_issue_map(report["lint"]["issues"], effective)
        truncated.update({f"lint.{key}": value for key, value in lint_truncated.items()})
    if args.json:
        if args.summary:
            payload: dict[str, Any] = {
                "wiki": report["wiki"],
                "source": args.source,
                "summary": True,
                "update_required": report["update_required"],
                "maintenance_recommended": report["maintenance_recommended"],
                "blocking_issue_count": len(report["blocking_issues"]) + truncated.get("blocking_issues", 0),
                "drifted_source_count": len(report["drifted_sources"]) + truncated.get("drifted_sources", 0),
                "affected_page_count": len(report["affected_pages"]) + truncated.get("affected_pages", 0),
                "lint_issue_count": report["lint"]["total_issues"],
                "drifted_sources": report["drifted_sources"],
                "affected_pages": report["affected_pages"],
                "issue_counts": {
                    "relationship": len(report["relationship_issues"]) + truncated.get("relationship_issues", 0),
                    "source_hash": len(report["source_hash_issues"]) + truncated.get("source_hash_issues", 0),
                    "metadata_schema": len(report["metadata_schema_issues"]) + truncated.get("metadata_schema_issues", 0),
                },
            }
            if truncated:
                payload["truncated"] = truncated
            print(json.dumps(payload, indent=2))
        else:
            if truncated:
                report["truncated"] = truncated
            print(json.dumps(report, indent=2))
    else:
        print(f"Wiki: {wiki}")
        print(f"Update required: {str(report['update_required']).lower()}")
        print(f"Maintenance recommended: {str(report['maintenance_recommended']).lower()}")
        print(f"Blocking issues: {full_counts['blocking_issues']}")
        print(f"Drifted sources: {full_counts['drifted_sources']}")
        print(f"Affected source pages: {full_counts['affected_pages']}")
        print(f"Relationship issues: {full_counts['relationship_issues']}")
        print(f"Source hash issues: {full_counts['source_hash_issues']}")
        print(f"Metadata schema issues: {full_counts['metadata_schema_issues']}")
        print(f"Field order issues: {full_counts['field_order_issues']}")
        if report["drifted_sources"]:
            print("\ndrifted_sources:")
            for item in report["drifted_sources"]:
                source_page = item.get("source_page") or "unlinked"
                print(f"- {item['raw_source']} -> {source_page}: {item['reason']}")
        if report["relationship_issues"]:
            print("\nrelationship_issues:")
            for item in report["relationship_issues"]:
                print(f"- {item}")
        if report["source_hash_issues"]:
            print("\nsource_hash_issues:")
            for item in report["source_hash_issues"]:
                print(f"- {item}")
    if args.fail_on_update and report["update_required"]:
        return 1
    if args.fail_on_issues and report["maintenance_recommended"]:
        return 1
    return 0


def fix_frontmatter_file(path: Path, wiki: Path, dry_run: bool) -> dict[str, Any] | None:
    rel = path.relative_to(wiki).as_posix()
    text = read_text(path)
    if rel.startswith("raw/"):
        return None
    format_issues = frontmatter_rewrite_safety_issues(text, rel)
    if format_issues:
        return {
            "path": rel,
            "changed": False,
            "manual_required": True,
            "issues": format_issues,
        }
    fm, body, has_fm = frontmatter_block(text)
    order = canonical_field_order(path, wiki, fm)
    if not order:
        return None
    before_keys = list(fm.keys())
    missing = missing_required_fields(path, wiki, fm)
    mechanically_inferable = [field for field in missing if field in {"title", "type"}]
    semantic_missing = [field for field in missing if field not in {"title", "type"}]
    if semantic_missing:
        return {
            "path": rel,
            "changed": False,
            "manual_required": True,
            "issues": [f"semantic fields require review: {', '.join(semantic_missing)}"],
        }
    for field in mechanically_inferable:
        fm[field] = infer_placeholder(field, path, wiki, fm)
    fixed = reorder_frontmatter(fm, order)
    new_text = render_markdown_with_frontmatter(fixed, body)
    changed = new_text != text
    if changed and not dry_run:
        write_text(path, new_text, expected_hash=bytes_hash(path))
    if changed or missing or before_keys != list(fixed.keys()):
        return {
            "path": rel,
            "changed": changed,
            "missing_fields_added": mechanically_inferable,
            "field_order_before": before_keys,
            "field_order_after": list(fixed.keys()),
        }
    return None


def command_fix(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    results: list[dict[str, Any]] = []
    manual_required: list[dict[str, Any]] = []
    for path in iter_page_files(wiki):
        result = fix_frontmatter_file(path, wiki, args.dry_run)
        if result:
            if result.get("manual_required"):
                manual_required.append(result)
            else:
                results.append(result)
    print(
        json.dumps(
            {
                "wiki": str(wiki),
                "dry_run": args.dry_run,
                "changed_files": results,
                "manual_required": manual_required,
            },
            indent=2,
        )
    )
    return 0


def parse_log_entries(text: str) -> list[str]:
    matches = list(re.finditer(r"(?m)^## \[", text))
    entries: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        entries.append(text[match.start():end].strip())
    return entries


def bound_context_payload(payload: dict[str, Any], char_budget: int) -> tuple[dict[str, Any], str]:
    budget = max(512, min(char_budget, 12000))
    payload["char_budget"] = budget
    payload["wiki"] = str(payload["wiki"])[-240:]
    payload["query"] = str(payload["query"])[:240]
    for item in payload["results"]:
        item["title"] = str(item["title"])[:100]
        item["summary"] = str(item["summary"])[:120]
        item["snippet"] = str(item["snippet"])[:120]
        item["aliases"] = [str(value)[:48] for value in item["aliases"][:4]]
        item["tags"] = [str(value)[:32] for value in item["tags"][:6]]
    payload["recent_log"] = [entry[:180] for entry in payload["recent_log"]]

    def render() -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    rendered = render()
    while len(rendered) > budget and len(payload["results"]) > 1:
        payload["results"].pop()
        payload["truncated"] = True
        rendered = render()
    while len(rendered) > budget and payload["recent_log"]:
        payload["recent_log"].pop()
        rendered = render()
    if len(rendered) > budget:
        for item in payload["results"]:
            item["summary"] = str(item["summary"])[:48]
            item["snippet"] = str(item["snippet"])[:48]
            item["aliases"] = []
            item["tags"] = []
        rendered = render()
    if len(rendered) > budget:
        payload["results"] = []
        payload["recent_log"] = []
        payload["truncated"] = True
        payload["query"] = str(payload["query"])[:80]
        payload["wiki"] = str(payload["wiki"])[-120:]
        rendered = render()
    if len(rendered) > budget:
        # Keep JSON valid even for an unusually small budget or very long path.
        payload = {
            "query": str(payload.get("query") or "")[:32],
            "truncated": True,
            "results": [],
            "recent_log": [],
            "char_budget": budget,
        }
        rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return payload, rendered


def command_context(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    query = args.query.strip()
    query_terms = [term for term in re.findall(r"[\w-]+", query.lower()) if term]
    limit = max(1, min(args.limit, 100))
    recent_limit = max(0, min(args.recent_log, 50))
    matches: list[dict[str, Any]] = []
    for path in iter_page_files(wiki):
        text = read_text(path)
        fm, body, _has_fm = frontmatter_block(text)
        page_type = page_type_for_path(path, wiki, fm)
        if args.type and page_type != args.type:
            continue
        title = str(fm.get("title") or path.stem.replace("-", " ").title())
        aliases = list_value(fm.get("aliases"))
        tags = list_value(fm.get("tags"))
        summary = str(fm.get("summary") or first_summary(body))[:160]
        searchable = {
            "title": title.lower(),
            "aliases": " ".join(aliases).lower(),
            "tags": " ".join(tags).lower(),
            "summary": summary.lower(),
            "body": body[:4000].lower(),
        }
        score = 0
        for term in query_terms:
            score += 8 if term in searchable["title"] else 0
            score += 6 if term in searchable["aliases"] else 0
            score += 4 if term in searchable["tags"] else 0
            score += 3 if term in searchable["summary"] else 0
            score += 1 if term in searchable["body"] else 0
        if query_terms and score == 0:
            continue
        snippet = first_summary(body) or summary
        matches.append(
            {
                "path": path.relative_to(wiki).as_posix(),
                "type": page_type,
                "title": title,
                "aliases": aliases[:12],
                "tags": tags[:12],
                "summary": summary,
                "snippet": snippet[:160],
                "score": score,
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["path"]))
    log_entries: list[str] = []
    log_path = wiki / "log.md"
    if recent_limit and log_path.is_file() and path_stays_within_wiki(wiki, log_path):
        entries = parse_log_entries(read_text(log_path))
        related = [entry for entry in entries if not query_terms or all(term in entry.lower() for term in query_terms)]
        log_entries = [entry[:180] for entry in related[-recent_limit:]][::-1]
    payload = {
        "wiki": str(wiki),
        "query": query,
        "type": args.type,
        "limit": limit,
        "recent_log_limit": recent_limit,
        "total_matches": len(matches),
        "truncated": len(matches) > limit,
        "results": matches[:limit],
        "recent_log": log_entries,
    }
    payload, compact_json = bound_context_payload(payload, args.char_budget)
    if args.json:
        print(compact_json)
    else:
        lines = [f"Wiki: {wiki}", f"Query: {query}", f"Matches: {len(matches)}"]
        for item in payload["results"]:
            lines.append(f"- [{item['type']}] {item['path']} | {item['title']} | {item['summary']}")
        if payload["recent_log"]:
            lines.append("Recent related log:")
            lines.extend(entry.splitlines()[0] for entry in payload["recent_log"])
        rendered = "\n".join(lines)
        print(rendered[: payload["char_budget"]])
    return 0


def resolve_preflight_inputs(wiki: Path, values: list[str], recursive: bool) -> list[Path]:
    candidates: list[Path] = []
    requested = values or ["raw/inbox"]
    for value in requested:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            candidate = candidate.resolve()
            if not path_stays_within_wiki(wiki, candidate):
                raise ValueError(f"preflight path must stay inside wiki: {value}")
        else:
            _rel, resolved = resolve_wiki_reference(wiki, value, "preflight path")
            if resolved is None:
                raise ValueError(f"invalid preflight path: {value}")
            candidate = resolved
        if candidate.is_dir():
            iterator = candidate.rglob("*") if recursive else candidate.iterdir()
            for path in iterator:
                if not path_stays_within_wiki(wiki, path):
                    raise ValueError(f"unsafe preflight path escapes wiki: {path}")
                if path.is_file():
                    candidates.append(path)
        elif candidate.is_file():
            if not path_stays_within_wiki(wiki, candidate):
                raise ValueError(f"unsafe preflight path escapes wiki: {candidate}")
            candidates.append(candidate)
        else:
            raise ValueError(f"preflight path not found: {value}")
    return sorted(set(candidates))


def preflight_identity_metadata(path: Path) -> tuple[dict[str, Any], dict[str, str], set[str]]:
    fm: dict[str, Any] = {}
    if path.suffix.lower() in TEXT_HASH_EXTS:
        try:
            fm, _body, _has_fm = frontmatter_block(read_text(path))
        except OSError:
            fm = {}
    identities: dict[str, str] = {}
    for field in ["url", "doi", "isbn"]:
        raw_value = fm.get(field)
        if field == "url" and not raw_value:
            raw_value = fm.get("source_url")
        canonical = canonical_source_identifier(field, raw_value)
        if canonical:
            identities[field] = canonical
    names = {slugify(path.stem), path.stem.lower()}
    title = str(fm.get("title") or "").strip()
    if title:
        names.update({title.lower(), slugify(title)})
    for alias in list_value(fm.get("aliases")):
        names.update({alias.strip().lower(), slugify(alias)})
    return fm, identities, {name for name in names if name}


def existing_source_identity_index(wiki: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in source_page_records(wiki):
        for field in ["url", "doi", "isbn"]:
            canonical = canonical_source_identifier(field, record["fm"].get(field))
            if canonical:
                index.setdefault((field, canonical), []).append(record)
    return index


def command_ingest_preflight(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    try:
        paths = resolve_preflight_inputs(wiki, args.paths, args.recursive)
    except (OSError, ValueError) as exc:
        print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
        return 2
    existing = raw_hash_index(wiki, include_inbox=False, include_derived=False, exclude=set(paths))
    identity_index = existing_source_identity_index(wiki)
    _exact_pages, alias_index, _stem_groups = page_lookup_tables(wiki, iter_page_files(wiki))
    batch: dict[tuple[str, str], Path] = {}
    batch_identities: dict[tuple[str, str], tuple[str, Path]] = {}
    items: list[dict[str, Any]] = []
    blocking = False
    for path in paths:
        rel = path.relative_to(wiki).as_posix()
        try:
            digest, scheme = compute_source_hash(path)
        except (OSError, ValueError) as exc:
            items.append({"source": rel, "status": "error", "reason": str(exc)})
            blocking = True
            continue
        _raw_fm, identities, candidate_names = preflight_identity_metadata(path)
        identity_checks: dict[str, Any] = {
            "canonical": identities,
            "available": sorted(identities),
            "scope": "URL/DOI/ISBN checks require those fields in raw frontmatter or an existing source summary",
        }
        identity_conflicts: list[str] = []
        identity_reuse: list[dict[str, Any]] = []
        for field, identity in identities.items():
            for record in identity_index.get((field, identity), []):
                existing_digest = str(record["fm"].get("raw_sha256") or "").strip()
                if existing_digest and existing_digest != digest:
                    identity_conflicts.append(f"{field}:{identity} -> {record['rel']}")
                elif existing_digest == digest:
                    identity_reuse.append(record)
            batch_match = batch_identities.get((field, identity))
            if batch_match and batch_match[0] != digest:
                identity_conflicts.append(
                    f"{field}:{identity} -> {batch_match[1].relative_to(wiki).as_posix()}"
                )
        alias_conflicts: list[str] = []
        source_alias_conflicts: list[str] = []
        for name in candidate_names:
            for owner in alias_index.get(name, set()) | alias_index.get(name.lower(), set()):
                owner_fm, _body, _has_fm = frontmatter_block(read_text(owner))
                if str(owner_fm.get("raw_sha256") or "").strip() == digest:
                    continue
                conflict = f"{name} -> {owner.relative_to(wiki).as_posix()}"
                alias_conflicts.append(conflict)
                if page_type_for_path(owner, wiki, owner_fm) == "source" or owner.parent.name == "sources":
                    source_alias_conflicts.append(conflict)
        if identity_conflicts:
            items.append(
                {
                    "source": rel,
                    "hash_scheme": scheme,
                    "sha256": digest,
                    "classification": "canonical_identity",
                    "target": "",
                    "status": "new_version",
                    "reason": "canonical_identity_has_different_content",
                    "identity_checks": identity_checks,
                    "conflicts": sorted(set(identity_conflicts)),
                    "reuse": "",
                }
            )
            blocking = True
            continue
        if alias_conflicts:
            source_identity_conflict = bool(source_alias_conflicts)
            items.append(
                {
                    "source": rel,
                    "hash_scheme": scheme,
                    "sha256": digest,
                    "classification": "slug_alias",
                    "target": "",
                    "status": "new_version" if source_identity_conflict else "conflict",
                    "reason": (
                        "source_summary_identity_has_different_content"
                        if source_identity_conflict
                        else "source_summary_slug_or_alias_conflict"
                    ),
                    "identity_checks": identity_checks,
                    "conflicts": sorted(set(alias_conflicts)),
                    "reuse": "",
                }
            )
            blocking = True
            continue
        key = (scheme, digest)
        if identity_reuse:
            record = identity_reuse[0]
            reuse = normalize_wiki_rel(record["fm"].get("raw_source")) or record["rel"]
            items.append(
                {
                    "source": rel,
                    "hash_scheme": scheme,
                    "sha256": digest,
                    "classification": "reused_identity",
                    "target": reuse,
                    "status": "reused",
                    "reason": "canonical_identity_and_content_match",
                    "identity_checks": identity_checks,
                    "reuse": reuse,
                }
            )
            continue
        if key in existing:
            reuse = existing[key][0].relative_to(wiki).as_posix()
            items.append(
                {
                    "source": rel,
                    "hash_scheme": scheme,
                    "sha256": digest,
                    "classification": "reused",
                    "target": reuse,
                    "status": "reused",
                    "reason": "identical_content_already_classified",
                    "identity_checks": identity_checks,
                    "reuse": reuse,
                }
            )
            continue
        if key in batch:
            reuse = batch[key].relative_to(wiki).as_posix()
            items.append(
                {
                    "source": rel,
                    "hash_scheme": scheme,
                    "sha256": digest,
                    "classification": "duplicate_batch",
                    "target": reuse,
                    "status": "reused",
                    "reason": "identical_content_in_batch",
                    "identity_checks": identity_checks,
                    "reuse": reuse,
                }
            )
            continue
        if args.category:
            target_dir = f"raw/{args.category}"
            classification = "category_override"
        else:
            decision = classification_for_file(path, "inbox", None)
            target_dir = decision["target_dir"]
            classification = decision["status"]
        _target_rel, target = resolve_wiki_reference(wiki, f"{target_dir}/{path.name}", "preflight target")
        if target is None:
            raise ValueError("empty preflight target")
        status = "ready"
        reason = "classified"
        if classification == "needs_user_classification":
            status = "blocked"
            reason = "needs_user_classification"
            blocking = True
        elif target.exists() and target.resolve() != path.resolve():
            target_digest, target_scheme = compute_source_hash(target)
            if (target_scheme, target_digest) == key:
                status = "reused"
                reason = "identical_target_exists"
            else:
                status = "conflict"
                reason = "target_name_exists_with_different_content"
                blocking = True
        batch[key] = target
        for field, identity in identities.items():
            batch_identities[(field, identity)] = (digest, path)
        items.append(
            {
                "source": rel,
                "hash_scheme": scheme,
                "sha256": digest,
                "classification": classification,
                "target": target.relative_to(wiki).as_posix(),
                "status": status,
                "reason": reason,
                "identity_checks": identity_checks,
                "reuse": target.relative_to(wiki).as_posix() if status == "reused" else "",
            }
        )
    print(
        json.dumps(
            {
                "wiki": str(wiki),
                "recursive": args.recursive,
                "category": args.category,
                "identity_scope": "Content hash, target name, source-summary slug/alias, and canonical URL/DOI/ISBN when metadata is available; metadata-free raw files cannot be identity-matched beyond content/name.",
                "items": items,
                "blocking": blocking,
            },
            indent=2,
        )
    )
    return 1 if blocking else 0


def resolve_page_argument(wiki: Path, value: str) -> Path:
    rel = normalize_wiki_rel(value)
    if not rel:
        raise ValueError("page path is empty")
    candidates: list[Path] = []
    if "/" in rel:
        with_suffix = rel if rel.endswith(".md") else rel + ".md"
        _safe, path = resolve_wiki_reference(wiki, with_suffix, "page")
        if (
            path is not None
            and path.is_file()
            and path.suffix.lower() == ".md"
            and path.relative_to(wiki).parts[0] in PAGE_DIRS
        ):
            candidates = [path]
    else:
        stem = Path(rel).stem
        candidates = [path for path in iter_page_files(wiki) if path.stem == stem]
    if not candidates:
        raise ValueError(f"page not found: {value}")
    if len(candidates) > 1:
        raise ValueError(f"ambiguous page: {value}: {', '.join(path.relative_to(wiki).as_posix() for path in candidates)}")
    return candidates[0]


def validate_source_finalize(wiki: Path, page: Path) -> list[str]:
    rel = page.relative_to(wiki).as_posix()
    text = read_text(page)
    issues = frontmatter_format_issues(text, rel)
    fm, _body, has_fm = frontmatter_block(text)
    if not has_fm or (page_type_for_path(page, wiki, fm) != "source" and page.parent.name != "sources"):
        issues.append(f"{rel}: expected a source page")
        return issues
    try:
        raw_rel, raw_path = resolve_wiki_reference(wiki, fm.get("raw_source"), "raw_source")
    except ValueError as exc:
        issues.append(f"{rel}: {exc}")
        return issues
    if not raw_rel or raw_path is None or not raw_path.is_file():
        issues.append(f"{rel}: raw_source not found: {raw_rel}")
        return issues
    expected = str(fm.get("raw_sha256") or "").strip()
    scheme = str(fm.get("raw_hash_scheme") or "").strip()
    if not expected or scheme not in VALID_HASH_SCHEMES:
        issues.append(f"{rel}: valid raw_sha256 and raw_hash_scheme are required")
    else:
        actual, _used = compute_source_hash(raw_path, scheme)
        if actual != expected:
            issues.append(f"{rel}: source_summary_raw_hash_drift")
    derived_value = fm.get("derived_source")
    if normalize_wiki_rel(derived_value):
        try:
            derived_rel, derived_path = resolve_wiki_reference(wiki, derived_value, "derived_source")
        except ValueError as exc:
            issues.append(f"{rel}: {exc}")
            return issues
        if derived_path is None or not derived_path.is_file():
            issues.append(f"{rel}: derived_source not found: {derived_rel}")
        elif derived_path.suffix.lower() in TEXT_HASH_EXTS:
            derived_text = read_text(derived_path)
            derived_issues = frontmatter_format_issues(derived_text, derived_rel)
            issues.extend(derived_issues)
            derived_fm, _body, derived_has_fm = frontmatter_block(derived_text)
            if not derived_has_fm or normalize_wiki_rel(derived_fm.get("derived_from")) != raw_rel:
                issues.append(f"{rel}: derived_from mismatch")
            expected_at_derivation = str(derived_fm.get("source_hash_at_derivation") or "").strip()
            derivation_scheme = str(derived_fm.get("source_hash_scheme_at_derivation") or scheme)
            if not expected_at_derivation:
                issues.append(f"{rel}: derived source missing source_hash_at_derivation")
            elif derivation_scheme not in VALID_HASH_SCHEMES:
                issues.append(f"{rel}: invalid source_hash_scheme_at_derivation")
            else:
                actual_at_finalize, _used = compute_source_hash(raw_path, derivation_scheme)
                if actual_at_finalize != expected_at_derivation:
                    issues.append(f"{rel}: derived_stale")
    missing = missing_required_fields(page, wiki, fm)
    if missing:
        issues.append(f"{rel}: missing required fields: {', '.join(missing)}")
    records = source_page_records(wiki)
    active_for_raw = [
        record
        for record in source_pages_for_raw(raw_rel, records)
        if str(record["fm"].get("status") or "").strip().lower() == "active"
    ]
    if len(active_for_raw) != 1:
        issues.append(
            f"{rel}: raw_source must have exactly one active source summary; found {len(active_for_raw)}"
        )
    for field in ["url", "doi", "isbn"]:
        canonical = canonical_source_identifier(field, fm.get(field))
        if not canonical:
            continue
        owners = [
            record["rel"]
            for record in records
            if canonical_source_identifier(field, record["fm"].get(field)) == canonical
        ]
        if len(owners) > 1:
            issues.append(f"{rel}: duplicate {field}:{canonical}: {', '.join(owners)}")
    pages = iter_page_files(wiki)
    exact, aliases, stem_groups = page_lookup_tables(wiki, pages)
    if len(stem_groups.get(page.stem, [])) > 1:
        issues.append(f"{rel}: duplicate stem: {page.stem}")
    own_names = {page.stem, str(fm.get("title") or "").strip()}
    own_names.update(str(value).strip() for value in list_value(fm.get("aliases")))
    for name in sorted(value for value in own_names if value):
        owners = aliases.get(name, set()) | aliases.get(name.lower(), set())
        if len(owners) > 1:
            issues.append(
                f"{rel}: duplicate alias/title {name!r}: "
                + ", ".join(owner.relative_to(wiki).as_posix() for owner in sorted(owners))
            )
    for dependent in pages:
        if dependent == page:
            continue
        dependent_fm, _body, dependent_has_fm = frontmatter_block(read_text(dependent))
        if not dependent_has_fm:
            continue
        for ref in list_value(dependent_fm.get("sources")):
            normalized = normalized_page_ref(ref)
            resolved, candidates = resolve_page_link(normalized, exact, aliases)
            if page in candidates and (resolved != page or len(candidates) != 1):
                issues.append(
                    f"{dependent.relative_to(wiki).as_posix()}: ambiguous dependent source reference {ref!r}"
                )
            elif resolved == page and normalized != page_key(page, wiki):
                issues.append(
                    f"{dependent.relative_to(wiki).as_posix()}: noncanonical dependent source reference "
                    f"{ref!r}; use {page_key(page, wiki)!r}"
                )
    return issues


def build_log_text(current: str, action: str, subject: str, files: list[str], notes: str | None) -> str:
    lines = ["", f"## [{today()}] {action} | {subject}"]
    if files:
        lines.append(f"- Files: {', '.join(files)}")
    if notes:
        lines.append(f"- Notes: {notes}")
    return current.rstrip() + "\n" + "\n".join(lines) + "\n"


def command_ingest_finalize(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    try:
        pages = [resolve_page_argument(wiki, value) for value in args.source_pages]
        issues = [issue for page in pages for issue in validate_source_finalize(wiki, page)]
    except (OSError, ValueError) as exc:
        print(json.dumps({"wiki": str(wiki), "finalized": False, "error": str(exc)}, indent=2))
        return 2
    if issues:
        print(json.dumps({"wiki": str(wiki), "finalized": False, "issues": issues}, indent=2))
        return 1
    index_text, _page_count = build_index_text(wiki)
    index_path = wiki / "index.md"
    log_path = wiki / "log.md"
    index_expected = path_content_hash(index_path)
    log_expected = path_content_hash(log_path)
    current_log = read_text(log_path) if log_path.exists() else "# Wiki Log\n"
    rels = [page.relative_to(wiki).as_posix() for page in pages]
    log_text = build_log_text(current_log, args.log_action, ", ".join(rels), rels, "ingest finalized")
    try:
        write_texts_transactional(
            {index_path: index_text, log_path: log_text},
            expected_hashes={index_path: index_expected, log_path: log_expected},
        )
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"wiki": str(wiki), "finalized": False, "error": str(exc)}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "wiki": str(wiki),
                "finalized": True,
                "source_pages": rels,
                "index_updated": True,
                "logged": True,
            },
            indent=2,
        )
    )
    return 0


def backlinks_to_page(wiki: Path, target: Path) -> list[Path]:
    target_key = page_key(target, wiki)
    pages = iter_page_files(wiki)
    exact, aliases, _stems = page_lookup_tables(wiki, pages)
    backlinks: list[Path] = []
    for path in pages:
        if path == target:
            continue
        text = read_text(path)
        fm, _body, _has_fm = frontmatter_block(text)
        refs = [normalized_page_ref(item) for item in list_value(fm.get("sources"))]
        refs.extend(extract_wikilinks(text))
        for ref in refs:
            resolved, candidates = resolve_page_link(ref, exact, aliases)
            if resolved == target and len(candidates) == 1:
                backlinks.append(path)
                break
            if ref == target_key:
                backlinks.append(path)
                break
    return sorted(set(backlinks))


def rewrite_page_reference(text: str, old_key: str, replacement_key: str) -> str:
    def replace_link(match: re.Match[str]) -> str:
        content = match.group(1)
        target = wiki_link_target(content)
        # Rewrite only the canonical path. A stem-only link can become
        # ambiguous as the wiki grows, so guessing here could retarget a
        # different page that happens to share the archived page's stem.
        if target != old_key:
            return match.group(0)
        delimiter_positions = [position for position in [content.find("|"), content.find("#")] if position >= 0]
        suffix = content[min(delimiter_positions):] if delimiter_positions else ""
        return f"[[{replacement_key}{suffix}]]"

    rewritten = re.sub(r"\[\[([^\]]+)\]\]", replace_link, text)
    fm, _body, has_fm = frontmatter_block(rewritten)
    if has_fm and isinstance(fm.get("sources"), list):
        sources = [replacement_key if normalized_page_ref(item) == old_key else item for item in fm["sources"]]
        if sources != fm["sources"]:
            rendered = "sources: " + dump_simple_yaml({"sources": sources}).split(": ", 1)[1].rstrip("\n")
            rewritten = re.sub(r"(?m)^sources:\s*.*$", rendered, rewritten, count=1)
    return rewritten


def command_archive(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    try:
        requested_rel = normalize_wiki_rel(args.page)
        if "/" in requested_rel and PurePosixPath(requested_rel).parts[0] not in PAGE_DIRS:
            raise ValueError(f"archive only accepts durable pages under: {', '.join(PAGE_DIRS)}")
        page = resolve_page_argument(wiki, args.page)
        rel = page.relative_to(wiki).as_posix()
        page_expected_hash = bytes_hash(page)
        text = read_text(page)
        if bytes_hash(page) != page_expected_hash:
            raise RuntimeError(f"concurrent modification detected while reading {rel}")
        if page.relative_to(wiki).parts[0] not in PAGE_DIRS:
            raise ValueError(f"archive only accepts durable pages under: {', '.join(PAGE_DIRS)}")
        format_issues = frontmatter_rewrite_safety_issues(text, rel)
        if format_issues:
            raise ValueError("; ".join(format_issues))
        replacement = resolve_page_argument(wiki, args.replaced_by) if args.replaced_by else None
        if replacement == page:
            raise ValueError("--replaced-by must name a different page")
        backlinks = backlinks_to_page(wiki, page)
        archive_rel = f"_archive/{rel}"
        _safe, archive_path = resolve_wiki_reference(wiki, archive_rel, "archive target")
        if archive_path is None:
            raise ValueError("invalid archive target")
        if archive_path.exists():
            raise ValueError(f"archive target already exists: {archive_rel}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"wiki": str(wiki), "applied": False, "error": str(exc)}, indent=2))
        return 2
    backlink_rels = [path.relative_to(wiki).as_posix() for path in backlinks]
    payload: dict[str, Any] = {
        "wiki": str(wiki),
        "page": rel,
        "archive_target": archive_rel,
        "reason": args.reason,
        "replaced_by": replacement.relative_to(wiki).as_posix() if replacement else "",
        "backlinks": backlink_rels,
        "index_change": f"remove [[{page_key(page, wiki)}]]",
        "mode": "apply" if args.apply else "dry-run",
        "applied": False,
    }
    if not args.apply:
        print(json.dumps(payload, indent=2))
        return 0
    if backlinks and replacement is None:
        payload["error"] = "backlinks exist; provide --replaced-by or update them before archiving"
        print(json.dumps(payload, indent=2))
        return 1
    fm, body, has_fm = frontmatter_block(text)
    if not has_fm:
        payload["error"] = "archive requires valid frontmatter"
        print(json.dumps(payload, indent=2))
        return 1
    fm["updated"] = today()
    fm["status"] = "archived"
    fm["archived_at"] = today()
    fm["archive_reason"] = args.reason
    if replacement is not None:
        fm["replaced_by"] = page_key(replacement, wiki)
    archived_text = render_markdown_with_frontmatter(fm, body)
    updates: dict[Path, str | None] = {archive_path: archived_text, page: None}
    expected_hashes: dict[Path, str | None] = {archive_path: None, page: page_expected_hash}
    if replacement is not None:
        replacement_key = page_key(replacement, wiki)
        old_key = page_key(page, wiki)
        lookup_pages = iter_page_files(wiki)
        exact, aliases, _stems = page_lookup_tables(wiki, lookup_pages)
        for backlink in backlinks:
            backlink_expected = bytes_hash(backlink)
            backlink_text = read_text(backlink)
            if bytes_hash(backlink) != backlink_expected:
                payload["error"] = f"concurrent modification detected while reading {backlink}"
                print(json.dumps(payload, indent=2))
                return 2
            backlink_issues = frontmatter_rewrite_safety_issues(
                backlink_text, backlink.relative_to(wiki).as_posix()
            )
            if backlink_issues:
                payload["error"] = "; ".join(backlink_issues)
                print(json.dumps(payload, indent=2))
                return 1
            rewritten = rewrite_page_reference(backlink_text, old_key, replacement_key)
            rewritten_fm, _rewritten_body, _has_fm = frontmatter_block(rewritten)
            remaining_refs = [normalized_page_ref(item) for item in list_value(rewritten_fm.get("sources"))]
            remaining_refs.extend(extract_wikilinks(rewritten))
            unresolved = []
            for ref in remaining_refs:
                resolved, candidates = resolve_page_link(ref, exact, aliases)
                if ref == old_key or resolved == page or page in candidates:
                    unresolved.append(ref)
            if unresolved:
                payload["error"] = (
                    f"{backlink.relative_to(wiki).as_posix()}: noncanonical or ambiguous backlinks "
                    f"require manual update before archival: {', '.join(sorted(set(unresolved)))}"
                )
                print(json.dumps(payload, indent=2))
                return 1
            updates[backlink] = rewritten
            expected_hashes[backlink] = backlink_expected
    index_text, _count = build_index_text(wiki, exclude={page})
    log_path = wiki / "log.md"
    expected_hashes[wiki / "index.md"] = path_content_hash(wiki / "index.md")
    expected_hashes[log_path] = path_content_hash(log_path)
    current_log = read_text(log_path) if log_path.exists() else "# Wiki Log\n"
    updates[wiki / "index.md"] = index_text
    updates[log_path] = build_log_text(
        current_log,
        "Archive",
        rel,
        [rel, archive_rel],
        f"reason={args.reason}" + (f"; replaced_by={page_key(replacement, wiki)}" if replacement else ""),
    )
    try:
        write_texts_transactional(updates, expected_hashes=expected_hashes)
    except (OSError, RuntimeError) as exc:
        payload["error"] = str(exc)
        print(json.dumps(payload, indent=2))
        return 2
    payload["applied"] = True
    print(json.dumps(payload, indent=2))
    return 0


def command_append_log(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    try:
        _log_rel, log_path = resolve_wiki_reference(wiki, "log.md", "log.md")
    except ValueError as exc:
        print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
        return 2
    if log_path is None:
        print(json.dumps({"wiki": str(wiki), "error": "invalid log.md path"}, indent=2))
        return 2
    current = read_text(log_path) if log_path.exists() else "# Wiki Log\n"
    expected = bytes_hash(log_path) if log_path.exists() else None
    try:
        write_text(
            log_path,
            build_log_text(current, args.action, args.subject, args.file or [], args.notes),
            expected_hash=expected,
        )
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"wiki": str(wiki), "logged": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps({"wiki": str(wiki), "logged": True}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a Karpathy-style LLM Wiki.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create wiki root files and directories.")
    init.add_argument("wiki")
    init.add_argument("--domain", default="general knowledge")
    init.add_argument("--research", action="store_true", help="Append research schema guidance to the selected agent config file.")
    init.add_argument(
        "--agent-platform",
        choices=["auto", "claude", "codex", "generic"],
        default="codex",
        help="Choose the root agent config file (default: codex): claude=CLAUDE.md, codex/generic=AGENTS.md.",
    )
    init.add_argument("--agent-file", help="Override the root agent config Markdown filename.")
    init.add_argument(
        "--force",
        action="store_true",
        help="Refresh generated README.md and index.md; never overwrite agent config files or append-only log.md.",
    )
    init.set_defaults(func=command_init)

    classify = sub.add_parser("classify", help="Classify files from raw/inbox.")
    classify.add_argument("wiki")
    classify.add_argument("--move", action="store_true", help="Move files instead of dry-run classification.")
    classify.add_argument(
        "--unknown-policy",
        choices=["inbox", "articles", "custom"],
        default="inbox",
        help="How to handle unknown file types. Default keeps them in raw/inbox for user classification.",
    )
    classify.add_argument("--custom-raw-dir", help="Explicit raw/<category> destination for --unknown-policy custom.")
    classify.set_defaults(func=command_classify)

    hash_source = sub.add_parser("hash-source", help="Compute a source sha256; raw originals are never rewritten.")
    hash_source.add_argument("path")
    hash_source.add_argument("--write", action="store_true", help="Deprecated: write sha256 into text frontmatter.")
    hash_source.set_defaults(func=command_hash_source)

    update_index = sub.add_parser("update-index", help="Regenerate index.md from wiki pages.")
    update_index.add_argument("wiki")
    update_index.set_defaults(func=command_update_index)

    lint = sub.add_parser("lint", help="Audit wiki structure, links, metadata, and raw source drift.")
    lint.add_argument("wiki")
    lint.add_argument("--json", action="store_true")
    lint.add_argument("--summary", action="store_true", help="Return compact issue counts and bounded samples.")
    lint.add_argument("--limit", type=int, help="Maximum displayed issues per category.")
    lint.add_argument("--source", help="Limit output to issues mentioning this wiki-relative source/page.")
    lint.add_argument("--fail-on-issues", action="store_true")
    lint.set_defaults(func=command_lint)

    health = sub.add_parser("health", help="Diagnose wiki health, source drift, and update impact.")
    health.add_argument("wiki")
    health.add_argument("--json", action="store_true")
    health.add_argument("--summary", action="store_true", help="Return compact health counts and bounded samples.")
    health.add_argument("--limit", type=int, help="Maximum displayed items per detail category.")
    health.add_argument("--source", help="Limit output to this wiki-relative source/page.")
    health.add_argument("--no-inventory", action="store_true", help="Skip metadata inventory collection.")
    health.add_argument("--inventory-limit", type=int, default=50, help="Maximum unique values returned per frontmatter field.")
    health.add_argument("--fail-on-update", action="store_true")
    health.add_argument("--fail-on-issues", action="store_true")
    health.set_defaults(func=command_health)

    fix = sub.add_parser("fix", help="Normalize safe page frontmatter; raw files are never rewritten.")
    fix.add_argument("wiki")
    fix.add_argument("--dry-run", action="store_true")
    fix.set_defaults(func=command_fix)

    append_log = sub.add_parser("append-log", help="Append a log.md entry.")
    append_log.add_argument("wiki")
    append_log.add_argument("--action", required=True)
    append_log.add_argument("--subject", required=True)
    append_log.add_argument("--file", action="append")
    append_log.add_argument("--notes")
    append_log.set_defaults(func=command_append_log)

    context = sub.add_parser("context", help="Return bounded page and log context for a query.")
    context.add_argument("wiki")
    context.add_argument("query")
    context.add_argument("--type", choices=sorted(VALID_TYPES))
    context.add_argument("--limit", type=int, default=12)
    context.add_argument("--recent-log", type=int, default=5)
    context.add_argument(
        "--char-budget",
        type=int,
        default=3200,
        help="Strict output-character budget, clamped to 512..12000 (default: 3200).",
    )
    context.add_argument("--json", action="store_true")
    context.set_defaults(func=command_context)

    preflight = sub.add_parser("ingest-preflight", help="Read-only classification, hash, and duplicate preflight.")
    preflight.add_argument("wiki")
    preflight.add_argument("paths", nargs="*")
    preflight.add_argument("--recursive", action="store_true")
    preflight.add_argument(
        "--category",
        choices=["articles", "papers", "transcripts", "data", "media", "derived"],
        help="Override automatic classification with a raw category.",
    )
    preflight.set_defaults(func=command_ingest_preflight)

    finalize = sub.add_parser(
        "ingest-finalize",
        help="Validate source provenance, then update index and log with pre-write checks and rollback.",
    )
    finalize.add_argument("wiki")
    finalize.add_argument("source_pages", nargs="+")
    finalize.add_argument("--log-action", default="Ingest")
    finalize.set_defaults(func=command_ingest_finalize)

    archive = sub.add_parser("archive", help="Preview or apply a provenance-preserving page archive.")
    archive.add_argument("wiki")
    archive.add_argument("page")
    archive.add_argument("--reason", required=True)
    archive.add_argument("--replaced-by")
    mode = archive.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default).")
    mode.add_argument("--apply", action="store_true", help="Apply archive, index, backlink, and log changes.")
    archive.set_defaults(func=command_archive)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
