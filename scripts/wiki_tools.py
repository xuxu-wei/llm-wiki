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
from pathlib import Path
from typing import Any


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
ROOT_FILES = ["README.md", "index.md", "log.md"]
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


def today() -> str:
    return dt.date.today().isoformat()


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def template_path(name: str) -> Path:
    return skill_root() / "templates" / name


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


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
        if not rel or rel.endswith("/") or "/" in rel:
            raise ValueError("--agent-file must be a root-level Markdown filename")
        if not rel.lower().endswith(".md"):
            raise ValueError("--agent-file must end with .md")
        return rel
    platform = detect_agent_platform(wiki, requested_platform)
    return AGENT_CONFIG_FILES[platform]


def render_agent_contract(values: dict[str, str]) -> str:
    return AGENT_CONFIG_MARKER + "\n" + render_template("AGENTS.md", values).rstrip() + "\n"


def write_or_append_agent_config(path: Path, text: str, force: bool = False) -> str:
    if not path.exists() or force:
        write_text(path, text)
        return "written"
    current = read_text(path)
    if AGENT_CONFIG_MARKER in current or "This directory is an LLM Wiki" in current:
        return "unchanged"
    addition = "\n\n## LLM Wiki Agent Contract\n\n" + text.rstrip() + "\n"
    write_text(path, current.rstrip() + addition)
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
    return "---\n" + dump_simple_yaml(fm) + "---\n" + body.lstrip("\n")


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
        if str(fm.get("source_kind") or "").strip().lower() in PAPER_KINDS:
            required.extend(field for field in REQUIRED_CITATION_FIELDS if field not in required)
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
        return "medium"
    if field == "status":
        return "active"
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
    for field in required_fields_for_path(path, wiki, fm):
        value = fm.get(field)
        if field not in fm or value == "" or value is None or (field in INLINE_LIST_FIELDS and value == []):
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
    return text.replace("\\", "/").lstrip("./")


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
        if root.exists():
            files.extend(sorted(path for path in root.rglob("*.md") if path.is_file()))
    return sorted(files)


def iter_raw_text_files(wiki: Path) -> list[Path]:
    raw_root = wiki / "raw"
    if not raw_root.exists():
        return []
    return sorted(path for path in raw_root.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_HASH_EXTS)


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
    for name in ["CLAUDE.md", "AGENTS.md"]:
        path = wiki / name
        if path.exists():
            return path
    for path in sorted(wiki.glob("*.md")):
        try:
            if AGENT_CONFIG_MARKER in read_text(path):
                return path
        except OSError:
            continue
    return wiki / "AGENTS.md"


def has_agent_config(wiki: Path) -> bool:
    if any((wiki / name).exists() for name in AGENT_CONFIG_FILES.values()):
        return True
    for path in sorted(wiki.glob("*.md")):
        try:
            if AGENT_CONFIG_MARKER in read_text(path):
                return True
        except OSError:
            continue
    return False


def command_init(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    values = {"domain": args.domain}
    for rel in RAW_DIRS + PAGE_DIRS + META_DIRS:
        (wiki / rel).mkdir(parents=True, exist_ok=True)
    try:
        agent_config_name = resolve_agent_config_name(wiki, args.agent_platform, args.agent_file)
    except ValueError as exc:
        print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
        return 2
    agent_status = write_or_append_agent_config(wiki / agent_config_name, render_agent_contract(values), args.force)
    for name in ROOT_FILES:
        target = wiki / name
        if target.exists() and not args.force:
            continue
        write_text(target, render_template(name, values))
    if args.research:
        add_on = render_template("research-schema.md", values)
        agents = wiki / agent_config_name
        current = read_text(agents)
        if "Research Schema Add-on" not in current:
            write_text(agents, current.rstrip() + "\n\n" + add_on)
    print(json.dumps({"wiki": str(wiki), "created": True, "agent_config": agent_config_name, "agent_config_status": agent_status}, indent=2))
    return 0


def command_classify(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    inbox = wiki / "raw" / "inbox"
    results: list[dict[str, str]] = []
    if args.unknown_policy == "custom":
        try:
            validate_custom_raw_dir(args.custom_raw_dir)
        except ValueError as exc:
            print(json.dumps({"wiki": str(wiki), "error": str(exc)}, indent=2))
            return 2
    if not inbox.exists():
        print(json.dumps({"wiki": str(wiki), "classified": []}, indent=2))
        return 0
    for path in sorted(item for item in inbox.iterdir() if item.is_file()):
        classification = classification_for_file(path, args.unknown_policy, args.custom_raw_dir)
        target_dir = wiki / classification["target_dir"]
        target = unique_path(target_dir / path.name)
        moved = False
        if args.move and classification["status"] != "needs_user_classification":
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            moved = True
        results.append(
            {
                "source": str(path),
                "target": str(target),
                "status": classification["status"],
                "reason": classification["reason"],
                "moved": str(moved).lower(),
            }
        )
    print(json.dumps({"wiki": str(wiki), "classified": results}, indent=2))
    return 0


def command_hash_source(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    try:
        digest, scheme = compute_source_hash(path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"path": str(path), "error": str(exc)}, indent=2))
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
                        "error": "--write is only supported for UTF-8 text sources",
                    },
                    indent=2,
                )
            )
            return 2
        text = read_utf8_text_for_hash(path)
        fm, body, has_fm = frontmatter_block(text)
        fm["sha256"] = digest
        fm["hash_scheme"] = scheme
        fm["hashed_at"] = today()
        fm.setdefault("ingested", today())
        new_text = "---\n" + dump_simple_yaml(fm) + "---\n" + (body if has_fm else text)
        write_text(path, new_text)
        written = True
    print(json.dumps({"path": str(path), "hash_scheme": scheme, "sha256": digest, "written": written}, indent=2))
    return 0


def command_update_index(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
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
    write_text(wiki / "index.md", "\n".join(lines).rstrip() + "\n")
    print(json.dumps({"wiki": str(wiki), "pages": sum(len(v) for v in entries.values())}, indent=2))
    return 0


def lint_wiki(wiki: Path) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {
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
    known_pages = {page_key(path, wiki): path for path in page_files}
    known_stems = {path.stem: path for path in page_files}
    inbound: dict[str, int] = {key: 0 for key in known_pages}
    indexed = read_text(wiki / "index.md") if (wiki / "index.md").exists() else ""
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
            raw_source = normalize_wiki_rel(fm.get("raw_source"))
            derived_source = normalize_wiki_rel(fm.get("derived_source"))
            if derived_source and not raw_source:
                issues["source_provenance_issues"].append(f"{rel}: derived_source without raw_source")
            if raw_source and not (wiki / raw_source).is_file():
                issues["source_provenance_issues"].append(f"{rel}: raw_source not found: {raw_source}")
            if derived_source and not (wiki / derived_source).is_file():
                issues["source_provenance_issues"].append(f"{rel}: derived_source not found: {derived_source}")
            kind = str(fm.get("source_kind", "")).lower()
            if kind in SCIENTIFIC_KINDS:
                missing_citation = [field for field in REQUIRED_CITATION_FIELDS if field not in fm or fm.get(field) in ("", [])]
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
            if link in known_pages:
                inbound[link] += 1
            elif link in known_stems:
                inbound[page_key(known_stems[link], wiki)] += 1
            else:
                issues["broken_links"].append(f"{rel}: [[{link}]]")
        if page_key(path, wiki) not in indexed and path.stem not in indexed:
            issues["missing_index_entries"].append(rel)
        if len(text.splitlines()) > 200:
            issues["oversized_pages"].append(rel)
    for key, count in sorted(inbound.items()):
        if count == 0 and not key.startswith("sources/"):
            issues["orphan_pages"].append(key + ".md")
    raw_root = wiki / "raw"
    if raw_root.exists():
        checked_raw_frontmatter: set[Path] = set()
        derived_root = raw_root / "derived"
        if derived_root.exists():
            for path in sorted(derived_root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in TEXT_HASH_EXTS:
                    continue
                rel = path.relative_to(wiki).as_posix()
                raw_text = read_text(path)
                checked_raw_frontmatter.add(path)
                issues["frontmatter_format"].extend(frontmatter_format_issues(raw_text, rel))
                fm, _body, has_fm = frontmatter_block(raw_text)
                missing = [field for field in DERIVED_REQUIRED_FIELDS if not has_fm or fm.get(field) in ("", [], None)]
                if missing:
                    issues["derived_metadata_gaps"].append(f"{rel}: {', '.join(missing)}")
        for path in sorted(raw_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_HASH_EXTS:
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
    if log_path.exists() and read_text(log_path).count("\n## [") > 500:
        issues["log_rotation"].append("log.md has more than 500 entries")
    return issues


def command_lint(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    issues = lint_wiki(wiki)
    total = sum(len(items) for items in issues.values())
    if args.json:
        print(json.dumps({"wiki": str(wiki), "total_issues": total, "issues": issues}, indent=2))
    else:
        print(f"Wiki: {wiki}")
        print(f"Total issues: {total}")
        for name, items in issues.items():
            if not items:
                continue
            print(f"\n{name}:")
            for item in items:
                print(f"- {item}")
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
    aliases = source_aliases(source_path, wiki)
    affected: list[str] = []
    for path in iter_page_files(wiki):
        if path == source_path:
            continue
        text = read_text(path)
        fm, _body, _has_fm = frontmatter_block(text)
        source_refs = {normalized_page_ref(item) for item in list_value(fm.get("sources"))}
        wikilinks = set(extract_wikilinks(text))
        if aliases & source_refs or aliases & wikilinks:
            affected.append(path.relative_to(wiki).as_posix())
    return sorted(affected)


def collect_relationship_issues(wiki: Path, records: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    source_refs = source_page_ref_map(wiki, records)
    raw_refs = {normalize_wiki_rel(record["fm"].get("raw_source")) for record in records}
    raw_refs.discard("")
    derived_refs = {normalize_wiki_rel(record["fm"].get("derived_source")) for record in records}
    derived_refs.discard("")
    for record in records:
        fm = record["fm"]
        source_rel = record["rel"]
        raw_rel = normalize_wiki_rel(fm.get("raw_source"))
        derived_rel = normalize_wiki_rel(fm.get("derived_source"))
        if not raw_rel:
            issues.append(f"{source_rel}: missing raw_source")
        elif not (wiki / raw_rel).is_file():
            issues.append(f"{source_rel}: raw_source not found: {raw_rel}")
        if derived_rel:
            derived_path = wiki / derived_rel
            if not derived_rel.startswith("raw/derived/"):
                issues.append(f"{source_rel}: derived_source must be under raw/derived/: {derived_rel}")
            if not derived_path.is_file():
                issues.append(f"{source_rel}: derived_source not found: {derived_rel}")
            elif derived_path.suffix.lower() in TEXT_HASH_EXTS:
                derived_fm, _body, has_fm = frontmatter_block(read_text(derived_path))
                derived_from = normalize_wiki_rel(derived_fm.get("derived_from")) if has_fm else ""
                if not derived_from:
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
    if raw_root.exists():
        for path in sorted(raw_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(wiki).as_posix()
            if rel.startswith("raw/inbox/"):
                continue
            if rel.startswith("raw/derived/"):
                if path.suffix.lower() in TEXT_HASH_EXTS:
                    fm, _body, has_fm = frontmatter_block(read_text(path))
                    derived_from = normalize_wiki_rel(fm.get("derived_from")) if has_fm else ""
                    if not derived_from:
                        issues.append(f"{rel}: missing derived_from")
                    elif not (wiki / derived_from).is_file():
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
        raw_rel = normalize_wiki_rel(fm.get("raw_source"))
        if not raw_rel or not (wiki / raw_rel).is_file():
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
        raw_path = wiki / raw_rel
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
        if source_kind in PAPER_KINDS:
            citation_missing = [
                field
                for field in REQUIRED_CITATION_FIELDS
                if fm.get(field) in ("", [], None) or str(fm.get(field)).strip().lower() == "unknown"
            ]
            if citation_missing:
                issues.append(f"{rel}: paper citation fields need review: {', '.join(citation_missing)}")
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
    if not raw_root.exists():
        return drifted
    for path in sorted(raw_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_HASH_EXTS:
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

def health_report(wiki: Path, inventory_limit: int = 50) -> dict[str, Any]:
    issues = lint_wiki(wiki)
    lint_total = sum(len(items) for items in issues.values())
    records = source_page_records(wiki)
    raw_drift = collect_raw_frontmatter_drift(wiki, records)
    summary_drift, source_hash_issues, source_blocking = collect_source_hash_health(wiki, records)
    drifted_sources = raw_drift + summary_drift
    relationship_issues = collect_relationship_issues(wiki, records)
    metadata_schema_issues = collect_metadata_schema_issues(wiki)
    field_order_issues = collect_field_order_issues(wiki)
    noncanonical_fields = collect_noncanonical_fields(wiki)
    metadata_inventory = collect_metadata_inventory(wiki, max(1, inventory_limit))
    blocking_issues: list[str] = []
    for category in ["missing_root_files", "broken_links", "missing_frontmatter", "frontmatter_format", "invalid_types"]:
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
    report = health_report(wiki, args.inventory_limit)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Wiki: {wiki}")
        print(f"Update required: {str(report['update_required']).lower()}")
        print(f"Maintenance recommended: {str(report['maintenance_recommended']).lower()}")
        print(f"Blocking issues: {len(report['blocking_issues'])}")
        print(f"Drifted sources: {len(report['drifted_sources'])}")
        print(f"Affected source pages: {len(report['affected_pages'])}")
        print(f"Relationship issues: {len(report['relationship_issues'])}")
        print(f"Source hash issues: {len(report['source_hash_issues'])}")
        print(f"Metadata schema issues: {len(report['metadata_schema_issues'])}")
        print(f"Field order issues: {len(report['field_order_issues'])}")
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
    fm, body, has_fm = frontmatter_block(text)
    is_raw = rel.startswith("raw/")
    is_derived = rel.startswith("raw/derived/")
    is_page = not is_raw
    if is_raw and not is_derived and not has_fm:
        return None
    if not has_fm and not (is_page or is_derived):
        return None
    order = canonical_field_order(path, wiki, fm)
    if not order:
        return None
    before_keys = list(fm.keys())
    missing = missing_required_fields(path, wiki, fm)
    for field in missing:
        fm[field] = infer_placeholder(field, path, wiki, fm)
    fixed = reorder_frontmatter(fm, order)
    new_text = render_markdown_with_frontmatter(fixed, body)
    changed = new_text != text
    if changed and not dry_run:
        write_text(path, new_text)
    if changed or missing or before_keys != list(fixed.keys()):
        return {
            "path": rel,
            "changed": changed,
            "missing_fields_added": missing,
            "field_order_before": before_keys,
            "field_order_after": list(fixed.keys()),
        }
    return None


def command_fix(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    results: list[dict[str, Any]] = []
    for path in iter_metadata_files(wiki):
        result = fix_frontmatter_file(path, wiki, args.dry_run)
        if result:
            results.append(result)
    print(json.dumps({"wiki": str(wiki), "dry_run": args.dry_run, "changed_files": results}, indent=2))
    return 0


def command_append_log(args: argparse.Namespace) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    log_path = wiki / "log.md"
    files = ", ".join(args.file or [])
    lines = ["", f"## [{today()}] {args.action} | {args.subject}"]
    if files:
        lines.append(f"- Files: {files}")
    if args.notes:
        lines.append(f"- Notes: {args.notes}")
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
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
        default="auto",
        help="Choose the root agent config file: claude=CLAUDE.md, codex/generic=AGENTS.md.",
    )
    init.add_argument("--agent-file", help="Override the root agent config Markdown filename.")
    init.add_argument("--force", action="store_true", help="Overwrite root template files if they already exist.")
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

    hash_source = sub.add_parser("hash-source", help="Compute or write a source sha256.")
    hash_source.add_argument("path")
    hash_source.add_argument("--write", action="store_true", help="Write sha256 into text frontmatter.")
    hash_source.set_defaults(func=command_hash_source)

    update_index = sub.add_parser("update-index", help="Regenerate index.md from wiki pages.")
    update_index.add_argument("wiki")
    update_index.set_defaults(func=command_update_index)

    lint = sub.add_parser("lint", help="Audit wiki structure, links, metadata, and raw source drift.")
    lint.add_argument("wiki")
    lint.add_argument("--json", action="store_true")
    lint.add_argument("--fail-on-issues", action="store_true")
    lint.set_defaults(func=command_lint)

    health = sub.add_parser("health", help="Diagnose wiki health, source drift, and update impact.")
    health.add_argument("wiki")
    health.add_argument("--json", action="store_true")
    health.add_argument("--inventory-limit", type=int, default=50, help="Maximum unique values returned per frontmatter field.")
    health.add_argument("--fail-on-update", action="store_true")
    health.add_argument("--fail-on-issues", action="store_true")
    health.set_defaults(func=command_health)

    fix = sub.add_parser("fix", help="Normalize frontmatter field order and add safe placeholder fields.")
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
