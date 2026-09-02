#!/usr/bin/env python3
"""Deterministic runtime for an LLM Wiki vault.

The runtime intentionally owns mechanical work only: filesystem operations,
frontmatter validation, the generated CSV index, structured retrieval, and Git
checkpoints.  Semantic metadata and page prose remain agent/user work.
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


EXIT_OK = 0
EXIT_INPUT = 2
EXIT_CONFLICT = 3
EXIT_AUDIT = 4
EXIT_REVIEW = 5

INDEX_HEADER = ("path", "kind", "summary", "aliases", "tags")
TAG_PLAN_HEADER = ("tag", "page_count", "action", "target")
TAG_POLICY_PATH = "tags-review.csv"
SPREADSHEET_FORMULA_PREFIXES = frozenset("=+-@\t\r\n")
INDEX_DIRS = ("sources", "notes", "inbox")
VAULT_DIRS = ("inbox", "raw", "sources", "notes", "assets")
KEEP_FILE = ".gitkeep"
KEEP_PATHS = frozenset(f"{directory}/{KEEP_FILE}" for directory in VAULT_DIRS)
CORE_HEAD_FILES = {
    "AGENTS.md",
    "index.csv",
    ".gitattributes",
    ".gitignore",
    *KEEP_PATHS,
}
RAW_CONTROL_NAMES = frozenset({".gitignore", ".gitattributes", ".gitmodules", "agents.md"})
ROOT_REPO_DOC_NAMES = {
    "agents.md",
    "readme.md",
    "changelog.md",
    "contributing.md",
    "security.md",
    "code_of_conduct.md",
    "code-of-conduct.md",
    "license.md",
}
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
WINDOWS_INVALID_CHARS = set('<>:"|?*')
VALID_KINDS = {"source", "note", "moc", "inbox"}
LIST_FIELDS = ("aliases", "tags", "sources", "raw")
KNOWN_FRONTMATTER_FIELDS = {"kind", "summary", *LIST_FIELDS}
STRICT_METADATA_KINDS = {"source", "note", "moc"}
HIGH_RISK_OPERATIONS = {
    "human",
    "human-edit",
    "human-checkpoint",
    "rename",
    "delete",
    "merge",
    "split",
    "rewrite",
    "source-binding",
    "conflict",
    "control",
    "tag-maintenance",
    "tag-policy",
    "raw-update",
}
GLOBAL_AUDIT_CODES = {
    "E_VAULT_HEAD",
    "E_VAULT_TRACKING",
    "E_HOME_HEAD_COUNT",
    "E_VAULT_DIR",
    "E_VAULT_FILE",
    "E_HOME_COUNT",
    "E_RAW_ATTRIBUTES",
    "E_TAG_POLICY",
}
_GIT_HOOKS_PATH_OVERRIDE: str | None = None


class WikiError(Exception):
    """A public, actionable CLI failure."""

    def __init__(
        self,
        message: str,
        *,
        code: int = EXIT_INPUT,
        next_step: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.next_step = next_step
        self.details = details or {}


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        emit(
            {
                "ok": False,
                "command": "parse",
                "error": message,
                "next": "Run with --help and correct the command arguments.",
            },
        )
        raise SystemExit(EXIT_INPUT)


@dataclass(frozen=True)
class PageRecord:
    path: str
    kind: str
    summary: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    sources: tuple[str, ...] = ()
    raw: tuple[str, ...] = ()

    def index_row(self) -> tuple[str, str, str, str, str]:
        return (
            self.path,
            self.kind,
            self.summary,
            json.dumps(list(self.aliases), ensure_ascii=False, separators=(",", ":")),
            json.dumps(list(self.tags), ensure_ascii=False, separators=(",", ":")),
        )

    def public(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "summary": self.summary,
            "aliases": list(self.aliases),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class TagDecision:
    tag: str
    page_count: int
    action: str
    target: str = ""


def emit(payload: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream)


def configure_stdio() -> None:
    """Keep the JSON wire format UTF-8 on every supported platform."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def run_git(
    vault: Path | None,
    args: Sequence[str],
    *,
    check: bool = True,
    binary: bool = False,
    commit_identity: bool = False,
) -> subprocess.CompletedProcess[Any]:
    command = ["git"]
    if _GIT_HOOKS_PATH_OVERRIDE is not None:
        command.extend(["-c", f"core.hooksPath={_GIT_HOOKS_PATH_OVERRIDE}"])
    if vault is not None:
        command.extend(["--literal-pathspecs", "-C", str(vault)])
    command.extend(args)
    environment = os.environ.copy()
    environment.setdefault("GIT_TERMINAL_PROMPT", "0")
    if commit_identity:
        environment.setdefault("GIT_AUTHOR_NAME", "LLM Wiki")
        environment.setdefault("GIT_AUTHOR_EMAIL", "llm-wiki@local")
        environment.setdefault("GIT_COMMITTER_NAME", environment["GIT_AUTHOR_NAME"])
        environment.setdefault("GIT_COMMITTER_EMAIL", environment["GIT_AUTHOR_EMAIL"])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
            env=environment,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WikiError(
            "Git is required but was not found.",
            next_step="Install Git and retry the command.",
        ) from exc
    if check and result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")
        raise WikiError(
            f"Git command failed: {' '.join(args)}: {stderr.strip() or 'unknown error'}",
            code=EXIT_CONFLICT,
            next_step="Inspect the repository state and retry without discarding the visible changes.",
        )
    return result


def ensure_git_available() -> None:
    run_git(None, ["--version"])


def vault_from_cwd() -> Path:
    return Path.cwd().resolve()


def ensure_dedicated_worktree(vault: Path) -> None:
    result = run_git(vault, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        raise WikiError(
            f"Not a Git worktree: {vault}",
            next_step="Run init for a new wiki, or change directory to the wiki worktree root.",
        )
    root = Path(result.stdout.strip()).resolve()
    if root != vault.resolve():
        raise WikiError(
            f"The wiki must be the Git worktree root; repository root is {root}.",
            next_step="Use a dedicated repository or linked worktree whose root is the vault.",
        )


def ensure_wiki_contract(vault: Path) -> None:
    ensure_dedicated_worktree(vault)
    findings = wiki_contract_findings(vault)
    if findings:
        raise WikiError(
            "The repository does not satisfy the wiki structure contract.",
            next_step="Run audit --scope all, correct the reported structure findings, and retry.",
            details={"findings": findings},
        )


def head_oid(vault: Path) -> str:
    result = run_git(vault, ["rev-parse", "--verify", "HEAD"], check=False)
    if result.returncode != 0:
        raise WikiError("The wiki has no Git checkpoint.", code=EXIT_CONFLICT)
    return result.stdout.strip()


def verify_base(vault: Path, base: str) -> str:
    if not re.fullmatch(r"[0-9A-Fa-f]{7,64}", base):
        raise WikiError("--base must be a Git commit OID returned by begin.")
    result = run_git(vault, ["rev-parse", "--verify", f"{base}^{{commit}}"], check=False)
    if result.returncode != 0:
        raise WikiError("--base does not resolve to a commit in this wiki.", code=EXIT_CONFLICT)
    resolved = result.stdout.strip()
    current = head_oid(vault)
    if resolved != current:
        raise WikiError(
            "The wiki HEAD changed since begin.",
            code=EXIT_CONFLICT,
            next_step="Run begin again and review changes before continuing.",
            details={"base": resolved, "head": current},
        )
    return current


def decode_nul(data: bytes) -> list[str]:
    return [item.decode("utf-8", "surrogateescape") for item in data.split(b"\0") if item]


def git_path_set(vault: Path, args: Sequence[str]) -> set[str]:
    result = run_git(vault, args, binary=True)
    return {exact_rel_text(item) for item in decode_nul(result.stdout)}


def dirty_path_sets(vault: Path) -> tuple[set[str], set[str], set[str]]:
    staged = git_path_set(vault, ["diff", "--cached", "--name-only", "-z", "HEAD", "--"])
    unstaged = git_path_set(vault, ["diff", "--name-only", "-z", "--"])
    untracked = git_path_set(vault, ["ls-files", "--others", "--exclude-standard", "-z", "--"])
    return unstaged | staged | untracked, staged, untracked


def unstaged_paths(vault: Path) -> set[str]:
    return git_path_set(vault, ["diff", "--name-only", "-z", "--"])


def exact_rel_text(value: str) -> str:
    """Return a separator-normalized path without changing its Unicode identity."""

    return value.replace("\\", "/")


def canonical_rel_text(value: str) -> str:
    return unicodedata.normalize("NFC", exact_rel_text(value))


def portable_path_key(value: str) -> str:
    return canonical_rel_text(value).casefold()


def is_keep_path(rel: str) -> bool:
    return exact_rel_text(rel) in KEEP_PATHS


def managed_control_issue(rel: str) -> str | None:
    exact = exact_rel_text(rel)
    pure = PurePosixPath(exact)
    folded_parts = tuple(part.casefold() for part in pure.parts)
    if ".git" in folded_parts:
        return ".git path components are reserved"
    if pure.name.casefold() == KEEP_FILE and exact not in KEEP_PATHS:
        return f"{KEEP_FILE} is only allowed at the five vault directory roots"
    if folded_parts and folded_parts[0] == "raw" and pure.name.casefold() in RAW_CONTROL_NAMES:
        return f"{pure.name} is reserved and cannot be stored as raw material"
    return None


def portable_path_findings(paths: Iterable[str]) -> list[dict[str, Any]]:
    """Report normalization and cross-platform collisions without changing path identity."""

    findings: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    exact_paths = {exact_rel_text(path) for path in paths}
    for exact in sorted(exact_paths, key=lambda item: (portable_path_key(item), item)):
        normalized = canonical_rel_text(exact)
        if exact != normalized:
            findings.append(
                finding("E_PATH_NORMALIZATION", exact, f"managed path is not Unicode NFC: {normalized}")
            )
        key = portable_path_key(exact)
        previous = seen.get(key)
        if previous is not None and previous != exact:
            findings.append(
                finding(
                    "E_PATH_COLLISION",
                    exact,
                    f"managed path conflicts with {previous} after NFC/casefold normalization",
                )
            )
        else:
            seen[key] = exact
    return findings


def is_managed_rel(rel: str) -> bool:
    exact = exact_rel_text(rel)
    if portable_path_key(exact) == portable_path_key(TAG_POLICY_PATH):
        return True
    if any(portable_path_key(exact) == portable_path_key(core) for core in CORE_HEAD_FILES):
        return True
    pure = PurePosixPath(exact)
    if len(pure.parts) == 1:
        return pure.suffix.casefold() == ".md" and pure.name.casefold() not in ROOT_REPO_DOC_NAMES
    return pure.parts[0].casefold() in {*INDEX_DIRS, "raw", "assets"}


def unique_findings(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    for item in findings:
        key = (item["code"], item["path"], item["message"], item.get("field"))
        unique[key] = item
    return sorted(unique.values(), key=lambda item: (item["path"], item["code"], item["message"]))


def portable_component_issue(component: str) -> str | None:
    if not component or component in {".", ".."}:
        return "empty or traversal component"
    if any(ord(character) < 32 or ord(character) == 127 for character in component):
        return "control character"
    if any(character in WINDOWS_INVALID_CHARS for character in component):
        return "Windows-invalid character"
    if component.endswith((" ", ".")):
        return "trailing space or dot"
    basename = component.split(".", 1)[0].casefold()
    if basename in WINDOWS_RESERVED_NAMES:
        return "Windows reserved name"
    return None


def validate_portable_rel(rel: str, *, label: str) -> None:
    pure = PurePosixPath(rel)
    for component in pure.parts:
        issue = portable_component_issue(component)
        if issue:
            raise WikiError(f"Unsafe {label} component {component!r}: {issue}")
    control_issue = managed_control_issue(rel)
    if control_issue:
        raise WikiError(f"Unsafe {label}: {control_issue}")


def validate_path_chain(vault: Path, path: Path, *, label: str) -> None:
    root = vault.resolve()
    lexical_root = Path(os.path.abspath(vault))
    lexical_path = Path(os.path.abspath(path))
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError as exc:
        raise WikiError(f"{label} escapes the vault: {path}") from exc
    current = lexical_root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise WikiError(f"Unsafe {label}: symbolic links are not allowed: {current}")
        if os.path.lexists(current):
            resolved = current.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise WikiError(f"{label} resolves outside the vault: {current}") from exc
            if os.path.normcase(str(resolved)) != os.path.normcase(str(Path(os.path.abspath(current)))):
                raise WikiError(f"Unsafe {label}: reparse or linked path is not allowed: {current}")


def validate_discovered_path(vault: Path, path: Path, *, label: str) -> str:
    lexical = Path(os.path.abspath(path))
    try:
        disk_rel = lexical.relative_to(Path(os.path.abspath(vault))).as_posix()
    except ValueError as exc:
        raise WikiError(f"{label} is outside the vault: {path}") from exc
    rel = exact_rel_text(disk_rel)
    validate_portable_rel(rel, label=label)
    validate_path_chain(vault, lexical, label=label)
    if not os.path.lexists(lexical):
        raise WikiError(f"Missing {label}: {rel}")
    return rel


def safe_rel(vault: Path, value: str, *, label: str = "path") -> tuple[str, Path]:
    if not isinstance(value, str) or not value:
        raise WikiError(f"{label} must be a non-empty vault-relative path.")
    text = canonical_rel_text(value)
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise WikiError(f"Unsafe {label}: {value!r}")
    if re.match(r"^[A-Za-z]:", text) or text.startswith("//") or "\x00" in text:
        raise WikiError(f"Unsafe {label}: {value!r}")
    validate_portable_rel(pure.as_posix(), label=label)
    candidate = vault / Path(*pure.parts)
    validate_path_chain(vault, candidate, label=label)
    return pure.as_posix(), candidate


def validate_page_name(value: str, *, label: str = "page name") -> str:
    if value != value.strip():
        raise WikiError(f"Invalid {label}: leading or trailing whitespace is not portable")
    name = unicodedata.normalize("NFC", value)
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise WikiError(f"Invalid {label}: {value!r}")
    if name.lower().endswith(".md"):
        name = name[:-3]
    if not name or name in {".", ".."}:
        raise WikiError(f"Invalid {label}: {value!r}")
    validate_portable_rel(name, label=label)
    return name


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WikiError(f"Markdown is not valid UTF-8: {path}") from exc


def atomic_write(path: Path, data: bytes, *, expected: bytes | None = None) -> bool:
    current = path.read_bytes() if path.exists() else None
    if expected is not None and current != expected:
        raise WikiError(
            f"Refusing to overwrite a file that changed during the operation: {path}",
            code=EXIT_CONFLICT,
        )
    if current == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        if expected is not None:
            latest = path.read_bytes() if path.exists() else None
            if latest != expected:
                raise WikiError(
                    f"Refusing to overwrite a file that changed during the operation: {path}",
                    code=EXIT_CONFLICT,
                )
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def atomic_create(path: Path, data: bytes) -> None:
    """Create one complete file without replacing a concurrently created path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise WikiError(
                f"Refusing to overwrite a file created during the operation: {path}",
                code=EXIT_CONFLICT,
            ) from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stable_strings(values: Iterable[str]) -> tuple[str, ...]:
    cleaned = {unicodedata.normalize("NFC", value.strip()) for value in values if value.strip()}
    return tuple(sorted(cleaned, key=lambda item: (item.casefold(), item)))


def ordered_strings(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = unicodedata.normalize("NFC", value.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return tuple(result)


def parse_scalar(value: str) -> str | None:
    value = value.strip()
    if not value:
        return ""
    if has_unquoted_inline_comment(value):
        raise ValueError("unquoted inline comments are not supported")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid quoted scalar: {value}") from exc
        if not isinstance(parsed, str):
            raise ValueError("expected a string")
        return parsed
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value.casefold() in {"null", "~"}:
        return None
    if value in {"|", ">", "|-", ">-", "|+", ">+"}:
        raise ValueError("multiline values are not supported for indexed fields")
    return value


def parse_inline_list(value: str) -> list[str]:
    value = value.strip()
    if value == "[]":
        return []
    if not (value.startswith("[") and value.endswith("]")):
        raise ValueError("expected a list")
    if has_unquoted_inline_comment(value[1:-1]):
        raise ValueError("unquoted inline comments are not supported in list values")
    if has_unquoted_mapping_marker(value[1:-1]):
        raise ValueError("list values must be strings, not mappings")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            inner = value[1:-1]
            parsed = next(csv.reader([inner], skipinitialspace=True)) if inner.strip() else []
    if not isinstance(parsed, (list, tuple)):
        raise ValueError("expected a list")
    result: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            raise ValueError("list values must be strings")
        result.append(item)
    return result


def has_unquoted_mapping_marker(value: str) -> bool:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote == '"':
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if quote == "'":
            if escaped:
                escaped = False
            elif character == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    escaped = True
                    continue
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
            continue
        if character == ":" and (index + 1 == len(value) or value[index + 1].isspace()):
            return True
    return False


def has_unquoted_inline_comment(value: str) -> bool:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote == '"':
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if quote == "'":
            if escaped:
                escaped = False
            elif character == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    escaped = True
                    continue
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
            continue
        if character == "#" and (index == 0 or value[index - 1].isspace()):
            return True
    return False


def parse_frontmatter_text(text: str) -> tuple[dict[str, Any], str, list[str]]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return {}, text, ["missing frontmatter"]
    closing: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        return {}, text, ["unterminated frontmatter"]
    header = [line.rstrip("\r\n") for line in lines[1:closing]]
    body = "".join(lines[closing + 1 :])
    values: dict[str, Any] = {}
    errors: list[str] = []
    index = 0
    top_level_key: str | None = None
    while index < len(header):
        line = header[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$", line)
        if not match:
            indented_known = re.match(
                r"^[ \t]+([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$",
                line,
            )
            if indented_known and top_level_key in {"kind", "summary"}:
                nested_key = indented_known.group(1)
                if nested_key in KNOWN_FRONTMATTER_FIELDS:
                    errors.append(
                        f"indented field {nested_key} under scalar field {top_level_key}"
                    )
            malformed = None
            if line[:1] not in {" ", "\t"}:
                malformed = re.match(r"^([^:#]+?)[ \t]*:", line)
                if malformed is None:
                    malformed = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)(?:[ \t]+|=)", line)
            malformed_key = malformed.group(1).strip() if malformed else None
            if malformed_key in KNOWN_FRONTMATTER_FIELDS:
                errors.append(f"malformed field {malformed_key}")
            index += 1
            continue
        key, raw_value = match.group(1), match.group(2) or ""
        top_level_key = key
        if key not in KNOWN_FRONTMATTER_FIELDS:
            index += 1
            continue
        duplicate = key in values
        if duplicate:
            errors.append(f"duplicate field {key}")
        block_items: list[str] = []
        cursor = index + 1
        while cursor < len(header):
            continuation = header[cursor]
            if not continuation.strip() or continuation.lstrip().startswith("#"):
                cursor += 1
                continue
            item_match = re.match(r"^[ \t]+-[ \t]*(.*)$", continuation)
            if item_match:
                block_items.append(item_match.group(1))
                cursor += 1
                continue
            if continuation[:1] in {" ", "\t"}:
                errors.append(f"{key}: unsupported indented continuation")
                cursor += 1
                continue
            break
        try:
            if key in LIST_FIELDS:
                if block_items:
                    if raw_value.strip():
                        raise ValueError("cannot mix an inline list with block list items")
                    parsed_items = []
                    for item in block_items:
                        if has_unquoted_inline_comment(item):
                            raise ValueError("unquoted inline comments are not supported in list values")
                        if has_unquoted_mapping_marker(item):
                            raise ValueError("list values must be strings, not mappings")
                        parsed = parse_scalar(item)
                        if parsed is None:
                            raise ValueError("list values must be strings")
                        parsed_items.append(parsed)
                    if not duplicate:
                        values[key] = parsed_items
                    index = cursor
                    continue
                parsed_value = [] if not raw_value.strip() else parse_inline_list(raw_value)
                if not duplicate:
                    values[key] = parsed_value
            elif key in {"kind", "summary"}:
                if block_items:
                    raise ValueError("must be a scalar, not a block list")
                stripped = raw_value.strip()
                if stripped.startswith(("[", "{")) and stripped.endswith(("]", "}")):
                    raise ValueError("must be a scalar, not a collection")
                if not duplicate:
                    values[key] = parse_scalar(raw_value)
        except ValueError as exc:
            errors.append(f"{key}: {exc}")
            if block_items:
                index = cursor
                continue
        index += 1
    return values, body, errors


def read_frontmatter_only(path: Path) -> tuple[dict[str, Any], list[str]]:
    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            first = handle.readline()
            if first.lstrip("\ufeff").strip() != "---":
                return {}, ["missing frontmatter"]
            lines.append(first)
            for line in handle:
                lines.append(line)
                if line.strip() == "---":
                    break
            else:
                return {}, ["unterminated frontmatter"]
    except UnicodeDecodeError as exc:
        raise WikiError(f"Markdown is not valid UTF-8: {path}") from exc
    values, _body, errors = parse_frontmatter_text("".join(lines))
    return values, errors


def is_indexable_rel(rel: str) -> bool:
    pure = PurePosixPath(canonical_rel_text(rel))
    if pure.suffix.casefold() != ".md":
        return False
    if len(pure.parts) == 1:
        return pure.name.casefold() not in ROOT_REPO_DOC_NAMES
    return pure.parts[0] in INDEX_DIRS


def iter_indexable_pages(vault: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    pages: list[Path] = []
    findings: list[dict[str, Any]] = []
    head_homes = set(head_home_pages(vault, head_tree_paths(vault)))
    for path in vault.iterdir():
        if path.suffix.casefold() != ".md" or path.name.casefold() in ROOT_REPO_DOC_NAMES:
            continue
        try:
            rel = validate_discovered_path(vault, path, label="root Markdown page")
        except WikiError as exc:
            rel = exact_rel_text(path.name)
            findings.append(finding("E_PATH_UNSAFE", rel, str(exc)))
            continue
        if not path.is_file():
            continue
        if path.suffix != ".md":
            findings.append(finding("E_PATH_EXTENSION", rel, "Markdown page extension must be lowercase .md"))
        include = rel in head_homes
        if not include:
            values, _errors = read_frontmatter_only(path)
            include = values.get("kind") == "moc"
        if include:
            pages.append(path)
    for directory in INDEX_DIRS:
        root = vault / directory
        if root.is_dir():
            for path in root.rglob("*"):
                try:
                    rel = validate_discovered_path(vault, path, label="wiki page path")
                except WikiError as exc:
                    rel = exact_rel_text(path.relative_to(vault).as_posix())
                    findings.append(finding("E_PATH_UNSAFE", rel, str(exc)))
                    continue
                if not path.is_file() or path.suffix.casefold() != ".md":
                    continue
                if path.suffix != ".md":
                    findings.append(
                        finding("E_PATH_EXTENSION", rel, "Markdown page extension must be lowercase .md")
                    )
                pages.append(path)
    return (
        sorted(
            pages,
            key=lambda path: (
                portable_path_key(path.relative_to(vault).as_posix()),
                exact_rel_text(path.relative_to(vault).as_posix()),
            ),
        ),
        findings,
    )


def discover_files_under(
    vault: Path, directory: str, *, label: str
) -> tuple[list[tuple[str, Path]], list[dict[str, Any]]]:
    root = vault / directory
    files: list[tuple[str, Path]] = []
    findings: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        try:
            rel = validate_discovered_path(vault, path, label=label)
        except WikiError as exc:
            rel = exact_rel_text(path.relative_to(vault).as_posix())
            findings.append(finding("E_PATH_UNSAFE", rel, str(exc)))
            continue
        if path.is_file():
            files.append((rel, path))
    files.sort(key=lambda item: (portable_path_key(item[0]), item[0]))
    return files, findings


def page_record(
    vault: Path,
    path: Path,
    *,
    strict: bool,
    include_body: bool = False,
) -> tuple[PageRecord | None, list[dict[str, Any]]]:
    rel = exact_rel_text(path.relative_to(vault).as_posix())
    if include_body:
        values, body, parse_errors = parse_frontmatter_text(read_text(path))
    else:
        values, parse_errors = read_frontmatter_only(path)
        body = ""
    findings = [finding("E_FRONTMATTER", rel, message) for message in parse_errors]
    kind = values.get("kind")
    if not isinstance(kind, str) or kind not in VALID_KINDS:
        findings.append(finding("E_KIND", rel, "kind must be source, note, moc, or inbox", "kind"))
        return None, findings
    expected: set[str]
    parts = PurePosixPath(rel).parts
    if len(parts) == 1:
        expected = {"moc"}
    elif parts[0] == "sources":
        expected = {"source"}
    elif parts[0] == "notes":
        expected = {"note", "moc"}
    elif parts[0] == "inbox":
        expected = {"inbox"}
    else:
        expected = set()
    if kind not in expected:
        findings.append(finding("E_KIND_PATH", rel, f"kind {kind!r} does not match its directory", "kind"))
    summary_value = values.get("summary", "")
    if summary_value is None:
        summary_value = ""
    if not isinstance(summary_value, str):
        findings.append(finding("E_FIELD_TYPE", rel, "summary must be a string", "summary"))
        summary_value = ""
    summary = unicodedata.normalize("NFC", summary_value.strip())
    if strict and kind in STRICT_METADATA_KINDS and not summary:
        findings.append(finding("E_SUMMARY_REQUIRED", rel, "summary is required before save", "summary"))
    lists: dict[str, tuple[str, ...]] = {}
    for field in LIST_FIELDS:
        raw_value = values.get(field, [])
        if not isinstance(raw_value, list) or any(not isinstance(item, str) for item in raw_value):
            findings.append(finding("E_FIELD_TYPE", rel, f"{field} must be a list of strings", field))
            raw_value = []
        lists[field] = stable_strings(raw_value)
    if strict and kind in STRICT_METADATA_KINDS and "tags" not in values:
        findings.append(finding("E_TAGS_REQUIRED", rel, "tags must be present; use [] when empty", "tags"))
    if kind != "source" and lists["raw"]:
        findings.append(finding("E_RAW_FIELD", rel, "only source pages may declare raw files", "raw"))
    if include_body:
        heading = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), None)
        if heading != path.stem:
            findings.append(finding("E_TITLE", rel, "the first H1 must equal the page filename"))
    record = PageRecord(
        path=rel,
        kind=kind,
        summary=summary,
        aliases=lists["aliases"],
        tags=lists["tags"],
        sources=lists["sources"],
        raw=lists["raw"],
    )
    return record, findings


def collect_records(
    vault: Path, *, strict: bool, include_body: bool = False
) -> tuple[list[PageRecord], list[dict[str, Any]]]:
    records: list[PageRecord] = []
    pages, findings = iter_indexable_pages(vault)
    seen_paths: set[str] = set()
    portable_paths: dict[str, str] = {}
    findings.extend(
        portable_path_findings(path.relative_to(vault).as_posix() for path in pages)
    )
    for path in pages:
        rel = exact_rel_text(path.relative_to(vault).as_posix())
        if rel in seen_paths:
            continue
        seen_paths.add(rel)
        portable_key = portable_path_key(rel)
        previous = portable_paths.get(portable_key)
        if previous is not None and previous != rel:
            continue
        portable_paths[portable_key] = rel
        record, page_findings = page_record(vault, path, strict=strict, include_body=include_body)
        findings.extend(page_findings)
        if record is not None:
            records.append(record)
    records.sort(key=lambda item: item.path)
    return records, findings


def build_index_bytes(records: Iterable[PageRecord]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(INDEX_HEADER)
    for record in sorted(records, key=lambda item: item.path):
        writer.writerow(record.index_row())
    return output.getvalue().encode("utf-8")


def read_index(vault: Path) -> list[PageRecord]:
    path = vault / "index.csv"
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except FileNotFoundError as exc:
        raise ValueError("index.csv is missing") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("index.csv is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != INDEX_HEADER:
        raise ValueError("index.csv has the wrong header")
    records: list[PageRecord] = []
    for number, row in enumerate(reader, start=2):
        try:
            aliases = json.loads(row["aliases"])
            tags = json.loads(row["tags"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"index.csv row {number} has invalid JSON lists") from exc
        if not isinstance(aliases, list) or not isinstance(tags, list):
            raise ValueError(f"index.csv row {number} lists are invalid")
        records.append(
            PageRecord(
                path=row["path"],
                kind=row["kind"],
                summary=row["summary"],
                aliases=tuple(str(item) for item in aliases),
                tags=tuple(str(item) for item in tags),
            )
        )
    return records


def finding(code: str, path: str, message: str, field: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "path": path, "message": message}
    if field:
        item["field"] = field
    return item


def wiki_contract_findings(vault: Path) -> list[dict[str, Any]]:
    """Return read-only findings for the Git-backed wiki identity and directories."""

    findings: list[dict[str, Any]] = []
    head_files = readable_head_tree_paths(vault)
    if head_files is None:
        findings.append(
            finding(
                "E_VAULT_HEAD",
                ".",
                "the repository has no readable Git HEAD checkpoint",
            )
        )
    else:
        for required in sorted(CORE_HEAD_FILES - head_files):
            findings.append(
                finding(
                    "E_VAULT_TRACKING",
                    required,
                    f"required core path is not tracked by HEAD: {required}",
                )
            )
        head_homes = head_home_pages(vault, head_files)
        if len(head_homes) != 1:
            findings.append(
                finding(
                    "E_HOME_HEAD_COUNT",
                    ".",
                    "HEAD must contain exactly one root home MOC; "
                    f"found: {', '.join(head_homes) or 'none'}",
                )
            )

    for name in VAULT_DIRS:
        path = vault / name
        try:
            validate_discovered_path(vault, path, label="vault directory")
            if not path.is_dir():
                raise WikiError(f"required vault directory is not a directory: {name}")
        except WikiError as exc:
            findings.append(finding("E_VAULT_DIR", name, str(exc)))
    return sorted(findings, key=lambda item: (item["path"], item["code"], item["message"]))


def link_target(value: str) -> str:
    target = value.strip()
    if target.startswith("!"):
        target = target[1:].lstrip()
    if target.startswith("[[") and target.endswith("]]" ):
        target = target[2:-2]
    target = target.split("|", 1)[0].split("#", 1)[0].strip()
    return canonical_rel_text(target)


def page_link_rel(vault: Path, value: str) -> tuple[str, Path]:
    target = link_target(value)
    if not target.casefold().endswith(".md"):
        target += ".md"
    return safe_rel(vault, target, label="page reference")


def raw_link_rel(vault: Path, value: str) -> tuple[str, Path]:
    return safe_rel(vault, link_target(value), label="raw reference")


def git_blob_oid(path: Path, vault: Path | None = None) -> str:
    result = run_git(vault, ["hash-object", "--no-filters", "--", str(path)])
    return result.stdout.strip()


def head_raw_blobs(vault: Path) -> dict[str, str]:
    result = run_git(
        vault,
        ["ls-tree", "-r", "-z", "HEAD", "--", "raw"],
        check=False,
        binary=True,
    )
    if result.returncode != 0:
        return {}
    blobs: dict[str, str] = {}
    for entry in result.stdout.split(b"\0"):
        if not entry or b"\t" not in entry:
            continue
        header, raw_path = entry.split(b"\t", 1)
        fields = header.split()
        if len(fields) != 3 or fields[1] != b"blob":
            continue
        rel = exact_rel_text(raw_path.decode("utf-8", "surrogateescape"))
        if rel == "raw/.gitkeep":
            continue
        blobs[rel] = fields[2].decode("ascii")
    return blobs


def head_raw_paths(vault: Path) -> list[str]:
    return sorted(head_raw_blobs(vault), key=lambda item: (portable_path_key(item), item))


def readable_head_tree_paths(vault: Path) -> set[str] | None:
    result = run_git(vault, ["ls-tree", "-r", "--name-only", "-z", "HEAD"], check=False, binary=True)
    if result.returncode != 0:
        return None
    return {exact_rel_text(item) for item in decode_nul(result.stdout)}


def head_tree_paths(vault: Path) -> set[str]:
    return readable_head_tree_paths(vault) or set()


def head_home_pages(vault: Path, head_files: set[str] | None = None) -> list[str]:
    files = head_files if head_files is not None else head_tree_paths(vault)
    homes: list[str] = []
    for rel in sorted(files):
        pure = PurePosixPath(rel)
        if (
            len(pure.parts) != 1
            or pure.suffix != ".md"
            or pure.name.casefold() in ROOT_REPO_DOC_NAMES
        ):
            continue
        text = revision_text(vault, "HEAD", rel)
        if text is None:
            continue
        values, _body, errors = parse_frontmatter_text(text)
        if not errors and values.get("kind") == "moc":
            homes.append(rel)
    return homes


def head_blob_oid(vault: Path, rel: str) -> str | None:
    result = run_git(vault, ["rev-parse", "--verify", f"HEAD:{rel}"], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def raw_attribute_findings(
    vault: Path,
    raw_files: Sequence[tuple[str, Path]],
    *,
    attributes_git_dir: Path | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    targets = [rel for rel, _path in raw_files if not is_keep_path(rel)]
    targets.append("raw/.llm-wiki-attribute-probe")
    for rel in sorted(set(targets)):
        if is_keep_path(rel):
            continue
        repository_options = (
            [f"--git-dir={attributes_git_dir}", f"--work-tree={vault}"]
            if attributes_git_dir is not None
            else []
        )
        result = run_git(
            vault,
            [
                *repository_options,
                "check-attr",
                "-z",
                "text",
                "diff",
                "filter",
                "working-tree-encoding",
                "eol",
                "--",
                rel,
            ],
            binary=True,
        )
        fields = decode_nul(result.stdout)
        attributes = {
            fields[index + 1]: fields[index + 2]
            for index in range(0, len(fields) - 2, 3)
        }
        unsafe: list[str] = []
        if attributes.get("text") != "unset":
            unsafe.append(f"text={attributes.get('text', 'unspecified')}")
        if attributes.get("diff") != "unset":
            unsafe.append(f"diff={attributes.get('diff', 'unspecified')}")
        for attribute in ("filter", "working-tree-encoding", "eol"):
            value = attributes.get(attribute, "unspecified")
            if value not in {"unspecified", "unset"}:
                unsafe.append(f"{attribute}={value}")
        if unsafe:
            findings.append(
                finding(
                    "E_RAW_ATTRIBUTES",
                    rel,
                    "unsafe effective Git attributes: " + ", ".join(unsafe),
                )
            )
    return findings


def mask_non_prose_markdown(text: str) -> str:
    """Mask Markdown regions where wikilink-looking text is only an example."""

    characters = list(text)

    def mask(start: int, end: int) -> None:
        for index in range(start, end):
            if characters[index] not in {"\r", "\n"}:
                characters[index] = " "

    for match in re.finditer(r"<!--.*?-->", text, flags=re.DOTALL):
        mask(match.start(), match.end())

    offset = 0
    fence_character: str | None = None
    fence_length = 0
    for line in "".join(characters).splitlines(keepends=True):
        line_end = offset + len(line)
        if fence_character is None:
            opening = re.match(r"^[ ]{0,3}(`{3,}|~{3,})", line)
            if opening:
                fence_character = opening.group(1)[0]
                fence_length = len(opening.group(1))
                mask(offset, line_end)
        else:
            closing = re.match(
                rf"^[ ]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*(?:\r?\n)?$",
                line,
            )
            mask(offset, line_end)
            if closing:
                fence_character = None
                fence_length = 0
        offset = line_end

    masked = "".join(characters)
    index = 0
    while index < len(masked):
        if masked[index] != "`":
            index += 1
            continue
        run_end = index + 1
        while run_end < len(masked) and masked[run_end] == "`":
            run_end += 1
        delimiter_length = run_end - index
        cursor = run_end
        closing_end: int | None = None
        while cursor < len(masked):
            candidate = masked.find("`", cursor)
            if candidate < 0:
                break
            candidate_end = candidate + 1
            while candidate_end < len(masked) and masked[candidate_end] == "`":
                candidate_end += 1
            if candidate_end - candidate == delimiter_length:
                closing_end = candidate_end
                break
            cursor = candidate_end
        if closing_end is None:
            index = run_end
            continue
        mask(index, closing_end)
        masked = "".join(characters)
        index = closing_end
    return "".join(characters)


def iter_prose_wikilinks(body: str) -> Iterable[re.Match[str]]:
    prose = mask_non_prose_markdown(body)
    for match in re.finditer(r"!?\[\[([^\]]+)\]\]", prose):
        slash_index = match.start() - 1
        slash_count = 0
        while slash_index >= 0 and prose[slash_index] == "\\":
            slash_count += 1
            slash_index -= 1
        if slash_count % 2:
            continue
        yield match


def body_link_findings(
    vault: Path,
    records: Sequence[PageRecord],
    attachments: Sequence[tuple[str, Path]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    page_paths = {record.path for record in records}
    names: dict[str, set[str]] = {}
    for record in records:
        names.setdefault(Path(record.path).stem.casefold(), set()).add(record.path)
        for alias in record.aliases:
            names.setdefault(alias.casefold(), set()).add(record.path)
    attachment_names: dict[str, set[str]] = {}
    attachment_paths = {rel for rel, _path in attachments if not is_keep_path(rel)}
    for rel in attachment_paths:
        attachment_names.setdefault(PurePosixPath(rel).name.casefold(), set()).add(rel)

    for record in records:
        _rel, path = safe_rel(vault, record.path, label="page path")
        try:
            validate_discovered_path(vault, path, label="page path")
        except WikiError as exc:
            findings.append(finding("E_PATH_UNSAFE", record.path, str(exc)))
            continue
        _values, body, _errors = parse_frontmatter_text(read_text(path))
        for match in iter_prose_wikilinks(body):
            original = match.group(1)
            target = link_target(original)
            if not target or target.startswith("^") or "://" in target:
                continue
            pure = PurePosixPath(target)
            if len(pure.parts) > 1:
                first = pure.parts[0]
                if first in INDEX_DIRS:
                    rel = target if target.casefold().endswith(".md") else target + ".md"
                    try:
                        canonical, linked = safe_rel(vault, rel, label="wikilink")
                        if canonical not in page_paths:
                            raise WikiError(f"missing page {canonical}")
                        validate_discovered_path(vault, linked, label="wikilink")
                    except WikiError as exc:
                        findings.append(finding("E_LINK_BROKEN", record.path, f"{original!r}: {exc}"))
                elif first in {"raw", "assets"}:
                    try:
                        canonical, linked = safe_rel(vault, target, label="embed")
                        if canonical not in attachment_paths:
                            raise WikiError(f"missing attachment {canonical}")
                        validate_discovered_path(vault, linked, label="embed")
                    except WikiError as exc:
                        findings.append(finding("E_LINK_BROKEN", record.path, f"{original!r}: {exc}"))
                else:
                    findings.append(
                        finding("E_LINK_BROKEN", record.path, f"unsupported or missing qualified link: {original!r}")
                    )
                continue

            page_name = pure.stem if pure.suffix.casefold() == ".md" else pure.name
            candidates = set(names.get(page_name.casefold(), set()))
            candidates.update(names.get(target.casefold(), set()))
            if len(candidates) == 1:
                continue
            if len(candidates) > 1:
                findings.append(
                    finding(
                        "E_LINK_AMBIGUOUS",
                        record.path,
                        f"{original!r} matches: {', '.join(sorted(candidates))}",
                    )
                )
                continue
            attachment_candidates = attachment_names.get(pure.name.casefold(), set())
            if len(attachment_candidates) == 1:
                continue
            code = "E_LINK_AMBIGUOUS" if len(attachment_candidates) > 1 else "E_LINK_BROKEN"
            detail = (
                f"{original!r} matches: {', '.join(sorted(attachment_candidates))}"
                if attachment_candidates
                else f"missing wikilink target: {original!r}"
            )
            findings.append(finding(code, record.path, detail))
    return findings


def audit_findings(
    vault: Path,
    *,
    check_index: bool = True,
    attributes_git_dir: Path | None = None,
) -> list[dict[str, Any]]:
    records, findings = collect_records(vault, strict=True, include_body=True)
    findings.extend(wiki_contract_findings(vault))
    findings.extend(tag_policy_findings(vault))
    required_files = CORE_HEAD_FILES if check_index else CORE_HEAD_FILES - {"index.csv"}
    invalid_required_files: set[str] = set()
    for required in required_files:
        path = vault / Path(*PurePosixPath(required).parts)
        try:
            validate_discovered_path(vault, path, label="required vault path")
        except (WikiError, OSError) as exc:
            findings.append(finding("E_VAULT_FILE", required, str(exc)))
            invalid_required_files.add(required)
            continue
        if not path.is_file():
            findings.append(finding("E_VAULT_FILE", required, f"required vault path is not a file: {required}"))
            invalid_required_files.add(required)

    root_homes = [record.path for record in records if len(PurePosixPath(record.path).parts) == 1]
    if len(root_homes) != 1:
        findings.append(
            finding(
                "E_HOME_COUNT",
                ".",
                f"the worktree must contain exactly one root home MOC; found: {', '.join(root_homes) or 'none'}",
            )
        )

    attributes_path = vault / ".gitattributes"
    if os.path.lexists(attributes_path):
        try:
            validate_discovered_path(vault, attributes_path, label="Git attributes file")
            if attributes_path.is_file() and not re.search(
                r"(?m)^raw/\*\*\s+-text\s+-diff\s+-eol\s*$", read_text(attributes_path)
            ):
                findings.append(
                    finding(
                        "E_RAW_ATTRIBUTES",
                        ".gitattributes",
                        "raw/** must disable text conversion, diff drivers, and inherited eol",
                    )
                )
        except WikiError as exc:
            findings.append(finding("E_PATH_UNSAFE", ".gitattributes", str(exc)))

    index_path = vault / "index.csv"
    index_bytes: bytes | None = None
    if os.path.lexists(index_path) and "index.csv" not in invalid_required_files:
        try:
            validate_discovered_path(vault, index_path, label="index.csv")
            if not index_path.is_file():
                raise WikiError("index.csv is not a regular file")
            index_bytes = index_path.read_bytes()
        except (WikiError, OSError) as exc:
            findings.append(finding("E_VAULT_FILE", "index.csv", f"index.csv is not readable: {exc}"))
            invalid_required_files.add("index.csv")

    if check_index:
        if not os.path.lexists(index_path):
            findings.append(finding("E_INDEX_MISSING", "index.csv", "index.csv is missing"))
        elif index_bytes is not None and index_bytes != build_index_bytes(records):
            findings.append(
                finding("E_INDEX_DRIFT", "index.csv", "index.csv does not match page frontmatter")
            )

    page_paths = {record.path for record in records}
    aliases: dict[str, list[str]] = {}
    raw_owners: dict[str, list[str]] = {}
    for record in records:
        for alias in record.aliases:
            aliases.setdefault(alias.casefold(), []).append(record.path)
        for value in record.sources:
            try:
                rel, target = page_link_rel(vault, value)
                validate_discovered_path(vault, target, label="source reference")
            except WikiError as exc:
                findings.append(finding("E_SOURCE_REF", record.path, str(exc), "sources"))
                continue
            if not rel.startswith("sources/") or rel not in page_paths or not target.is_file():
                findings.append(finding("E_SOURCE_REF", record.path, f"missing source page: {rel}", "sources"))
        for value in record.raw:
            try:
                rel, target = raw_link_rel(vault, value)
                validate_discovered_path(vault, target, label="raw reference")
            except WikiError as exc:
                findings.append(finding("E_RAW_REF", record.path, str(exc), "raw"))
                continue
            if not rel.startswith("raw/") or not target.is_file():
                findings.append(finding("E_RAW_REF", record.path, f"missing raw file: {rel}", "raw"))
                continue
            raw_owners.setdefault(rel, []).append(record.path)
    for alias, owners in sorted(aliases.items()):
        unique = sorted(set(owners))
        if len(unique) > 1:
            findings.append(finding("E_ALIAS_COLLISION", unique[0], f"alias {alias!r} is shared by: {', '.join(unique)}"))

    discovered_raw, raw_discovery_findings = discover_files_under(vault, "raw", label="raw path")
    discovered_assets, asset_discovery_findings = discover_files_under(vault, "assets", label="asset path")
    findings.extend(raw_discovery_findings)
    findings.extend(asset_discovery_findings)
    head_managed_paths = {rel for rel in head_tree_paths(vault) if is_managed_rel(rel)}
    for rel in head_managed_paths:
        try:
            validate_portable_rel(rel, label="HEAD managed path")
        except WikiError as exc:
            findings.append(finding("E_PATH_UNSAFE", rel, str(exc)))
    managed_paths = {
        *head_managed_paths,
        *(record.path for record in records),
        *(rel for rel, _path in discovered_raw),
        *(rel for rel, _path in discovered_assets),
        *(required for required in CORE_HEAD_FILES if os.path.lexists(vault / Path(*PurePosixPath(required).parts))),
        *(path for path in (TAG_POLICY_PATH,) if os.path.lexists(vault / path)),
    }
    findings.extend(portable_path_findings(managed_paths))
    raw_files = [(rel, path) for rel, path in discovered_raw if rel != "raw/.gitkeep"]
    for rel, _path in raw_files:
        owners = raw_owners.get(rel, [])
        if not owners:
            findings.append(finding("E_RAW_UNCLAIMED", rel, "raw file is not declared by a source page"))
        elif len(set(owners)) > 1:
            findings.append(finding("E_RAW_MULTIPLE_OWNERS", rel, f"raw file is declared by: {', '.join(sorted(set(owners)))}"))

    blobs: dict[str, list[str]] = {}
    for rel, path in raw_files:
        try:
            oid = git_blob_oid(path, vault)
        except WikiError:
            continue
        blobs.setdefault(oid, []).append(rel)
    for paths in blobs.values():
        if len(paths) > 1:
            findings.append(finding("E_RAW_DUPLICATE", paths[0], f"exact duplicate raw files: {', '.join(paths)}"))

    findings.extend(
        raw_attribute_findings(
            vault,
            raw_files,
            attributes_git_dir=attributes_git_dir,
        )
    )
    findings.extend(body_link_findings(vault, records, [*raw_files, *discovered_assets]))

    return unique_findings(findings)


def render_frontmatter(properties: Sequence[tuple[str, Any]]) -> str:
    lines = ["---"]
    for key, value in properties:
        if isinstance(value, list):
            rendered = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
        else:
            rendered = json.dumps(str(value), ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def load_template(name: str) -> str:
    path = Path(__file__).resolve().parent.parent / "templates" / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WikiError(
            f"Required wiki template is unavailable: {path}",
            next_step="Reinstall or repair the llm-wiki skill package.",
        ) from exc


def render_template(name: str, replacements: dict[str, str]) -> str:
    text = load_template(name)
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    unresolved = sorted(set(re.findall(r"\{\{([A-Za-z0-9_-]+)\}\}", text)))
    if unresolved:
        raise WikiError(f"Template {name} has unresolved fields: {', '.join(unresolved)}")
    return text.replace("\r\n", "\n")


def quoted_template_value(value: str) -> str:
    """Return content safe inside an existing JSON-compatible YAML quote."""

    return json.dumps(value, ensure_ascii=False)[1:-1]


def replace_frontmatter_list(text: str, field: str, values: Sequence[str]) -> str:
    """Replace one list property without touching any semantic field or body."""

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        raise WikiError("Existing source page has no valid frontmatter.", code=EXIT_CONFLICT)
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        raise WikiError("Existing source page has unterminated frontmatter.", code=EXIT_CONFLICT)
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines[: closing + 1]) else "\n"
    replacement = (
        f"{field}: "
        + json.dumps(list(values), ensure_ascii=False, separators=(", ", ": "))
        + newline
    )
    start: int | None = None
    end: int | None = None
    for index in range(1, closing):
        if re.match(rf"^{re.escape(field)}\s*:", lines[index]):
            start = index
            end = index + 1
            preserved: list[str] = []
            while end < closing:
                continuation = lines[end]
                if not continuation.strip() or continuation.lstrip().startswith("#"):
                    preserved.append(continuation)
                    end += 1
                    continue
                if re.match(r"^[ \t]+-[ \t]*", continuation):
                    end += 1
                    continue
                break
            break
    if start is None or end is None:
        lines.insert(closing, replacement)
    else:
        lines[start:end] = [replacement, *preserved]
    return "".join(lines)


def wiki_link(rel: str) -> str:
    target = rel[:-3] if rel.casefold().endswith(".md") else rel
    return f"[[{target}]]"


def command_init(args: argparse.Namespace) -> int:
    ensure_git_available()
    vault = Path(args.vault).expanduser().resolve()
    name = validate_page_name(args.name, label="home page name")
    home_rel = f"{name}.md"
    if home_rel.casefold() in ROOT_REPO_DOC_NAMES:
        raise WikiError(f"The home page name {name!r} is reserved by the vault contract.")
    summary = args.home_summary.strip()
    if not summary:
        raise WikiError("--home-summary must be non-empty.")
    # Resolve every package asset before creating the target, so an incomplete
    # installation cannot leave a partial vault.
    agents = render_template("AGENTS-for-wiki.md", {"wiki_name": name})
    home = render_template(
        "home.md",
        {
            "home_name": name,
            "home_summary": quoted_template_value(summary),
        },
    )
    attributes = load_template(".gitattributes").replace("\r\n", "\n")
    ignore = load_template(".gitignore").replace("\r\n", "\n")
    if vault.exists() and any(vault.iterdir()):
        raise WikiError(
            f"Refusing to initialize a non-empty directory: {vault}",
            next_step="Choose an empty directory so existing files and Git configuration are not overwritten.",
        )
    vault.mkdir(parents=True, exist_ok=True)
    run_git(vault, ["init", "--quiet"])
    ensure_dedicated_worktree(vault)
    for directory in VAULT_DIRS:
        (vault / directory).mkdir(exist_ok=True)
    files = {
        vault / "AGENTS.md": agents.encode("utf-8"),
        vault / home_rel: home.encode("utf-8"),
        vault / ".gitattributes": attributes.encode("utf-8"),
        vault / ".gitignore": ignore.encode("utf-8"),
        vault / TAG_POLICY_PATH: build_tag_policy_bytes(()),
        **{vault / directory / KEEP_FILE: b"" for directory in VAULT_DIRS},
    }
    for path, data in files.items():
        if path.exists():
            raise WikiError(f"Refusing to overwrite existing file: {path}")
        atomic_write(path, data)
    records, findings = collect_records(vault, strict=True, include_body=True)
    if findings:
        raise WikiError("Generated home page failed validation.", details={"findings": findings})
    atomic_write(vault / "index.csv", build_index_bytes(records))
    paths = [
        "AGENTS.md",
        home_rel,
        "index.csv",
        TAG_POLICY_PATH,
        ".gitattributes",
        ".gitignore",
        *(f"{directory}/{KEEP_FILE}" for directory in VAULT_DIRS),
    ]
    run_git(vault, ["add", "--", *paths])
    run_git(
        vault,
        ["commit", "--quiet", "--no-gpg-sign", "-m", f"wiki: initialize {name}"],
        commit_identity=True,
    )
    commit = head_oid(vault)
    emit(
        {
            "ok": True,
            "command": "init",
            "vault": str(vault),
            "home": home_rel,
            "index": "index.csv",
            "tag_policy": TAG_POLICY_PATH,
            "commit": commit,
            "clean": not dirty_path_sets(vault)[0],
        }
    )
    return EXIT_OK


def change_inventory(vault: Path) -> list[dict[str, Any]]:
    dirty, staged, untracked = dirty_path_sets(vault)
    unstaged = unstaged_paths(vault)
    changes = []
    for path in sorted(dirty):
        states = []
        if path in staged:
            states.append("staged")
        if path in untracked:
            states.append("untracked")
        if path in unstaged:
            states.append("unstaged")
        changes.append({"path": path, "states": states})
    return changes


def command_begin(_args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    ensure_wiki_contract(vault)
    changes = change_inventory(vault)
    base = head_oid(vault)
    raw_impact = raw_impact_from_worktree(vault, base)
    emit(
        {
            "ok": True,
            "command": "begin",
            "vault": str(vault),
            "base": base,
            "clean": not changes,
            "changes": changes,
            "raw_impact": raw_impact,
            "next": (
                "Start the requested operation using this base."
                if not changes
                else (
                    "Review raw_impact one active distance layer at a time before saving."
                    if raw_impact is not None
                    else "Review and checkpoint the existing changes before starting unrelated work."
                )
            ),
        }
    )
    return EXIT_OK


def tag_sort_key(value: str) -> tuple[str, str]:
    return (unicodedata.normalize("NFC", value).casefold(), value)


def normalized_tag_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def validate_plain_tag(value: str, *, label: str) -> None:
    if not value:
        raise WikiError(f"{label} is empty.")
    if value != value.strip():
        raise WikiError(f"{label} has leading or trailing whitespace.")
    if unicodedata.normalize("NFC", value) != value:
        raise WikiError(f"{label} is not Unicode NFC-normalized.")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise WikiError(f"{label} contains a control character.")


def root_tag_plan_rel(vault: Path, plan: Path) -> str | None:
    if plan.parent != vault or not re.fullmatch(r"tags-review-[A-Za-z0-9_-]+\.csv", plan.name):
        return None
    return plan.name


def resolve_tag_review_path(vault: Path, value: str, *, option: str) -> tuple[Path, str | None]:
    reject_unsafe_external_path(value, option=option)
    path = Path(value).expanduser().resolve()
    if not path_is_within(path, vault):
        return path, None
    rel = root_tag_plan_rel(vault, path)
    if rel is None:
        raise WikiError(
            f"{option} inside the vault must name a root temporary review CSV created by tags collect."
        )
    return path, rel


def clean_tag_base(
    vault: Path,
    value: str,
    *,
    allowed_plans: Sequence[Path] = (),
) -> str:
    ensure_wiki_contract(vault)
    base = verify_base(vault, value)
    dirty, staged, untracked = dirty_path_sets(vault)
    allowed: set[str] = set()
    invalid_plan_state = False
    for plan in allowed_plans:
        rel = root_tag_plan_rel(vault, plan)
        tracked = git_path_set(vault, ["ls-files", "-z", "--", rel]) if rel is not None else set()
        if rel is not None and os.path.lexists(plan) and not tracked:
            allowed.add(rel)
        elif path_is_within(plan, vault):
            invalid_plan_state = True
    if invalid_plan_state or staged or dirty - allowed or untracked - allowed:
        raise WikiError(
            "Tag maintenance requires a clean wiki checkpoint.",
            code=EXIT_CONFLICT,
            next_step="Review and save the existing changes, then run begin and start tag maintenance again.",
            details={"changes": change_inventory(vault)},
        )
    return base


def tag_records(vault: Path) -> list[PageRecord]:
    pages, _discovery_findings = iter_indexable_pages(vault)
    head_paths = head_tree_paths(vault)
    untracked_pages = sorted(
        exact_rel_text(path.relative_to(vault).as_posix())
        for path in pages
        if exact_rel_text(path.relative_to(vault).as_posix()) not in head_paths
    )
    if untracked_pages:
        raise WikiError(
            "Tag maintenance requires every indexable page to belong to the current HEAD checkpoint.",
            code=EXIT_CONFLICT,
            next_step="Review and save or remove the untracked pages, then collect a fresh tag plan.",
            details={"untracked_pages": untracked_pages},
        )
    records, findings = collect_records(vault, strict=True)
    if findings:
        raise WikiError(
            "Tag maintenance requires valid page metadata.",
            code=EXIT_AUDIT,
            next_step="Correct the reported page metadata findings and retry.",
            details={"findings": unique_findings(findings)},
        )
    return records


def tag_inventory(records: Sequence[PageRecord]) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    tagged_pages = 0
    for record in records:
        if record.tags:
            tagged_pages += 1
        for tag in record.tags:
            counts[tag] = counts.get(tag, 0) + 1
    return counts, tagged_pages


def encode_tag_plan_cell(value: str) -> str:
    if value.startswith("'") or value[:1] in SPREADSHEET_FORMULA_PREFIXES:
        return "'" + value
    return value


def decode_tag_plan_cell(value: str) -> str:
    if value.startswith("''") or (
        value.startswith("'") and value[1:2] in SPREADSHEET_FORMULA_PREFIXES
    ):
        decoded = value[1:]
    elif value[:1] in SPREADSHEET_FORMULA_PREFIXES:
        raise ValueError("spreadsheet formula-like text must use the generated apostrophe escape")
    else:
        decoded = value
    decoded = unicodedata.normalize("NFC", decoded)
    if encode_tag_plan_cell(decoded) != value:
        raise ValueError("tag text does not use the canonical spreadsheet-safe encoding")
    return decoded


def build_tag_policy_bytes(rows: Sequence[TagDecision]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(TAG_PLAN_HEADER)
    for row in sorted(rows, key=lambda item: tag_sort_key(item.tag)):
        writer.writerow(
            (
                encode_tag_plan_cell(row.tag),
                row.page_count,
                row.action,
                encode_tag_plan_cell(row.target) if row.target else "",
            )
        )
    return output.getvalue().encode("utf-8-sig")


def canonical_tag_policy_worktree_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def read_tag_table(
    path: Path,
    *,
    allow_unresolved: bool,
) -> tuple[tuple[TagDecision, ...], bytes]:
    if not path.is_file():
        raise WikiError(f"Tag review CSV is not a regular file: {path}")
    try:
        table_bytes = capture_file(path, rel=str(path))
        text = table_bytes.decode("utf-8-sig")
    except FileNotFoundError as exc:
        raise WikiError(f"Tag review CSV does not exist: {path}") from exc
    except UnicodeDecodeError as exc:
        raise WikiError(f"Tag review CSV is not valid UTF-8: {path}") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fieldnames = tuple(reader.fieldnames or ())
        raw_rows = list(reader)
    except csv.Error as exc:
        raise WikiError(f"Tag review CSV contains malformed CSV: {exc}") from exc
    if fieldnames != TAG_PLAN_HEADER:
        raise WikiError(
            "Tag review CSV has the wrong header; expected: " + ",".join(TAG_PLAN_HEADER)
        )

    rows: list[TagDecision] = []
    exact_tags: set[str] = set()
    normalized_tags: dict[str, str] = {}
    for number, raw_row in enumerate(raw_rows, start=2):
        if None in raw_row or any(raw_row.get(field) is None for field in TAG_PLAN_HEADER):
            raise WikiError(f"Tag review CSV row {number} has the wrong number of fields.")
        raw_tag = raw_row["tag"]
        if not raw_tag:
            raise WikiError(f"Tag review CSV row {number} has an empty tag.")
        try:
            tag = decode_tag_plan_cell(raw_tag)
        except ValueError as exc:
            raise WikiError(f"Tag review CSV row {number} has an unsafe tag cell: {exc}.") from exc
        validate_plain_tag(tag, label=f"Tag review CSV row {number} tag")
        if tag in exact_tags:
            raise WikiError(f"Tag review CSV contains duplicate tag {tag!r}.")
        key = normalized_tag_key(tag)
        previous = normalized_tags.get(key)
        if previous is not None and previous != tag:
            raise WikiError(
                "Tag review CSV contains tags that collide after NFC/casefold normalization.",
                details={"first": previous, "second": tag},
            )
        exact_tags.add(tag)
        normalized_tags[key] = tag

        raw_count = raw_row["page_count"]
        if not re.fullmatch(r"[1-9][0-9]*", raw_count):
            raise WikiError(f"Tag review CSV row {number} has an invalid page_count.")
        action = raw_row["action"]
        raw_target = raw_row["target"]
        try:
            target = decode_tag_plan_cell(raw_target) if raw_target else ""
        except ValueError as exc:
            raise WikiError(f"Tag review CSV row {number} has an unsafe target cell: {exc}.") from exc
        if target:
            validate_plain_tag(target, label=f"Tag review CSV row {number} target")
        if action == "" and allow_unresolved:
            if target:
                raise WikiError(f"Tag review CSV row {number} has a target but no action.")
        elif action not in {"keep", "rename", "delete"}:
            raise WikiError(
                f"Tag review CSV row {number} has an invalid action {action!r}."
            )
        elif action == "rename":
            if not target:
                raise WikiError(f"Tag review CSV row {number} must provide a rename target.")
            if target == tag:
                raise WikiError(f"Tag review CSV row {number} cannot rename a tag to itself.")
        elif target:
            raise WikiError(
                f"Tag review CSV row {number} action {action!r} requires an empty target."
            )
        rows.append(TagDecision(tag, int(raw_count), action, target))
    return tuple(rows), table_bytes


def find_tag_rename_cycles(rename_map: dict[str, str]) -> list[list[str]]:
    state: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()

    def visit(tag: str) -> None:
        state[tag] = 1
        stack.append(tag)
        target = rename_map[tag]
        if target in rename_map:
            if state.get(target, 0) == 0:
                visit(target)
            elif state.get(target) == 1:
                start = stack.index(target)
                cycle = stack[start:] + [target]
                body = cycle[:-1]
                identity = min(tuple(body[index:] + body[:index]) for index in range(len(body)))
                if identity not in seen_cycles:
                    seen_cycles.add(identity)
                    cycles.append(cycle)
        stack.pop()
        state[tag] = 2

    for tag in sorted(rename_map, key=tag_sort_key):
        if state.get(tag, 0) == 0:
            visit(tag)
    return cycles


def validate_tag_policy(rows: Sequence[TagDecision], *, label: str) -> None:
    unresolved = sorted((row.tag for row in rows if not row.action), key=tag_sort_key)
    if unresolved:
        raise WikiError(
            f"{label} contains unresolved tag decisions.",
            details={"tags": unresolved},
        )
    by_tag = {row.tag: row for row in rows}
    name_registry: dict[str, tuple[str, str]] = {}
    name_conflicts: list[dict[str, str]] = []

    def register_name(name: str, role: str) -> None:
        key = normalized_tag_key(name)
        previous = name_registry.get(key)
        if previous is not None and previous[0] != name:
            name_conflicts.append(
                {
                    "first": previous[0],
                    "first_role": previous[1],
                    "second": name,
                    "second_role": role,
                }
            )
        else:
            name_registry[key] = (name, role)

    for row in rows:
        register_name(row.tag, f"{row.action}_tag")
    for row in rows:
        if row.action == "rename":
            register_name(row.target, f"rename_target_of:{row.tag}")
    if name_conflicts:
        raise WikiError(
            f"{label} has ambiguous tag names after NFC/casefold normalization.",
            details={"conflicts": name_conflicts},
        )

    rename_map = {row.tag: row.target for row in rows if row.action == "rename"}
    conflicts: list[dict[str, Any]] = []
    for cycle in find_tag_rename_cycles(rename_map):
        conflicts.append({"type": "rename_cycle", "path": cycle})
    for source, target in sorted(rename_map.items(), key=lambda item: tag_sort_key(item[0])):
        target_row = by_tag.get(target)
        if target_row is None:
            continue
        if target_row.action == "delete":
            conflicts.append({"type": "rename_target_deleted", "source": source, "target": target})
        elif target_row.action == "rename":
            conflicts.append(
                {
                    "type": "rename_chain",
                    "source": source,
                    "target": target,
                    "next_target": target_row.target,
                }
            )
    if conflicts:
        raise WikiError(
            f"{label} contains conflicting tag decisions.",
            details={"conflicts": conflicts},
        )


def load_tag_policy(vault: Path) -> tuple[tuple[TagDecision, ...], bytes | None]:
    path = vault / TAG_POLICY_PATH
    head_variants = sorted(
        rel
        for rel in head_tree_paths(vault)
        if rel != TAG_POLICY_PATH
        and portable_path_key(rel) == portable_path_key(TAG_POLICY_PATH)
    )
    if head_variants:
        raise WikiError(
            "Tracked tag policy path conflicts after NFC/casefold normalization.",
            details={"paths": [TAG_POLICY_PATH, *head_variants]},
        )
    collisions = sorted(
        candidate.name
        for candidate in vault.iterdir()
        if candidate.name != TAG_POLICY_PATH
        and portable_path_key(candidate.name) == portable_path_key(TAG_POLICY_PATH)
    )
    if collisions:
        raise WikiError(
            "Persistent tag policy path collides after NFC/casefold normalization.",
            details={"paths": [TAG_POLICY_PATH, *collisions]},
        )
    if not os.path.lexists(path):
        if TAG_POLICY_PATH in head_tree_paths(vault):
            raise WikiError("Tracked persistent tag policy is missing from the worktree.")
        return (), None
    validate_discovered_path(vault, path, label="tag policy")
    if not path.is_file():
        raise WikiError("Persistent tag policy is not a regular file.")
    rows, policy_bytes = read_tag_table(path, allow_unresolved=False)
    validate_tag_policy(rows, label="Persistent tag policy")
    canonical = build_tag_policy_bytes(rows)
    crlf_worktree = canonical.replace(b"\n", b"\r\n")
    if policy_bytes not in {canonical, crlf_worktree}:
        raise WikiError(
            "Persistent tag policy is not in canonical UTF-8 BOM, sorted CSV form."
        )
    return rows, policy_bytes


def build_tag_vocabulary(rows: Sequence[TagDecision]) -> dict[str, Any]:
    validate_tag_policy(rows, label="Persistent tag policy")
    preferred = {row.tag for row in rows if row.action == "keep"}
    preferred.update(row.target for row in rows if row.action == "rename")
    forbidden = {row.tag for row in rows if row.action in {"rename", "delete"}}
    rename_items = sorted(
        ((row.tag, row.target) for row in rows if row.action == "rename"),
        key=lambda item: tag_sort_key(item[0]),
    )
    return {
        "preferred_tags": sorted(preferred, key=tag_sort_key),
        "forbidden_tags": sorted(forbidden, key=tag_sort_key),
        "rename_map": {source: target for source, target in rename_items},
    }


def tag_policy_findings(vault: Path) -> list[dict[str, Any]]:
    path = vault / TAG_POLICY_PATH
    head_paths = head_tree_paths(vault)
    temporary_paths = sorted(
        rel
        for rel in head_paths
        if len(PurePosixPath(rel).parts) == 1
        and re.fullmatch(r"tags-review-[A-Za-z0-9_-]+\.csv", rel, flags=re.IGNORECASE)
    )
    findings = [
        finding(
            "E_TAG_POLICY",
            rel,
            "temporary tag review CSV must not be tracked by Git",
        )
        for rel in temporary_paths
    ]
    for rel in sorted(
        rel
        for rel in head_paths
        if rel != TAG_POLICY_PATH
        and portable_path_key(rel) == portable_path_key(TAG_POLICY_PATH)
    ):
        findings.append(
            finding(
                "E_TAG_POLICY",
                rel,
                f"tag policy path conflicts with {TAG_POLICY_PATH} after NFC/casefold normalization",
            )
        )
    tracked = TAG_POLICY_PATH in head_paths
    if not os.path.lexists(path):
        if tracked:
            findings.append(
                finding("E_TAG_POLICY", TAG_POLICY_PATH, "tracked tag policy is missing from the worktree")
            )
        return findings
    try:
        validate_discovered_path(vault, path, label="tag policy")
        if not path.is_file():
            raise WikiError("tag policy is not a regular file")
        load_tag_policy(vault)
    except (WikiError, OSError) as exc:
        findings.append(finding("E_TAG_POLICY", TAG_POLICY_PATH, str(exc)))
    return findings


def prepared_tag_plan(
    counts: dict[str, int],
    policy_rows: Sequence[TagDecision],
) -> tuple[tuple[TagDecision, ...], dict[str, Any]]:
    policy_by_tag = {row.tag: row for row in policy_rows}
    rename_targets = {row.target for row in policy_rows if row.action == "rename"}
    rows: list[TagDecision] = []
    inherited: list[str] = []
    implicit_keeps: list[str] = []
    unresolved: list[str] = []
    for tag in sorted(counts, key=tag_sort_key):
        validate_plain_tag(tag, label="Current page tag")
        historical = policy_by_tag.get(tag)
        if historical is not None:
            rows.append(TagDecision(tag, counts[tag], historical.action, historical.target))
            inherited.append(tag)
        elif tag in rename_targets:
            rows.append(TagDecision(tag, counts[tag], "keep", ""))
            implicit_keeps.append(tag)
        else:
            rows.append(TagDecision(tag, counts[tag], "", ""))
            unresolved.append(tag)
    # Parsing the generated bytes applies the same exact and portable name rules
    # used for user-edited plans without changing the current page tags.
    normalized: dict[str, str] = {}
    for row in rows:
        key = normalized_tag_key(row.tag)
        previous = normalized.get(key)
        if previous is not None and previous != row.tag:
            raise WikiError(
                "Current page tags collide after NFC/casefold normalization.",
                details={"first": previous, "second": row.tag},
            )
        normalized[key] = row.tag
    return tuple(rows), {
        "inherited_count": len(inherited),
        "inherited_tags": sorted(inherited, key=tag_sort_key),
        "implicit_target_keep_count": len(implicit_keeps),
        "implicit_target_keep_tags": sorted(implicit_keeps, key=tag_sort_key),
        "unresolved_count": len(unresolved),
        "unresolved_tags": sorted(unresolved, key=tag_sort_key),
    }


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def reject_unsafe_external_path(value: str, *, option: str) -> None:
    windows_form = value.replace("/", "\\")
    if windows_form.startswith(("\\\\?\\", "\\\\.\\")):
        raise WikiError(f"{option} does not accept Windows device or extended paths.")
    if os.name == "nt":
        _drive, tail = os.path.splitdrive(value)
        if ":" in tail:
            raise WikiError(f"{option} does not accept Windows alternate data streams.")


def finish_tag_plan_file(descriptor: int, path: Path, data: bytes) -> None:
    try:
        handle = os.fdopen(descriptor, "wb")
    except BaseException:
        os.close(descriptor)
        if os.path.lexists(path):
            os.unlink(path)
        raise
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if os.path.lexists(path):
            os.unlink(path)
        raise


def write_tag_plan(vault: Path, requested: str | None, data: bytes) -> Path:
    if requested is None:
        descriptor, name = tempfile.mkstemp(
            prefix="tags-review-",
            suffix=".csv",
            dir=str(vault),
        )
        output = Path(name).resolve()
        finish_tag_plan_file(descriptor, output, data)
        return output

    reject_unsafe_external_path(requested, option="--output")
    output = Path(requested).expanduser().resolve()
    if path_is_within(output, vault):
        raise WikiError("--output must be outside the wiki vault.")
    if os.path.lexists(output):
        raise WikiError(f"Refusing to overwrite an existing tag plan: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise WikiError(f"Refusing to overwrite an existing tag plan: {output}") from exc
    finish_tag_plan_file(descriptor, output, data)
    return output


def command_tags_collect(args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    base = clean_tag_base(vault, args.base)
    records = tag_records(vault)
    counts, page_count = tag_inventory(records)
    policy_rows, _policy_bytes = load_tag_policy(vault)
    plan_rows, preparation = prepared_tag_plan(counts, policy_rows)
    clean_tag_base(vault, base)
    plan = write_tag_plan(vault, args.output, build_tag_policy_bytes(plan_rows))
    emit(
        {
            "ok": True,
            "command": "tags",
            "action": "collect",
            "vault": str(vault),
            "base": base,
            "plan": str(plan),
            "tag_count": len(counts),
            "page_count": page_count,
            **preparation,
            **build_tag_vocabulary(policy_rows),
        }
    )
    return EXIT_OK


def read_tag_plan(
    path: Path,
    counts: dict[str, int],
) -> tuple[tuple[TagDecision, ...], dict[str, tuple[str, str | None]], bytes]:
    rows, plan_bytes = read_tag_table(path, allow_unresolved=False)
    validate_tag_policy(rows, label="Reviewed tag plan")
    stated_counts = {row.tag: row.page_count for row in rows}
    if set(stated_counts) != set(counts):
        raise WikiError(
            "Tag plan must contain exactly one row for every current tag.",
            details={
                "plan_tags": sorted(stated_counts, key=tag_sort_key),
                "current_tags": sorted(counts, key=tag_sort_key),
            },
        )
    if stated_counts != counts:
        raise WikiError(
            "The tag plan no longer matches the current wiki tag inventory.",
            code=EXIT_CONFLICT,
            next_step="Run tags collect again and review a fresh plan.",
            details={
                "plan_tags": sorted(stated_counts, key=tag_sort_key),
                "current_tags": sorted(counts, key=tag_sort_key),
            },
        )
    mapping = {
        row.tag: (
            row.action,
            row.target if row.action == "rename" else row.tag if row.action == "keep" else None,
        )
        for row in rows
    }
    return rows, mapping, plan_bytes


def apply_tag_policy_amendments(
    policy_rows: Sequence[TagDecision],
    amendment_rows: Sequence[TagDecision],
    *,
    plan_tags: set[str],
) -> tuple[tuple[TagDecision, ...], dict[str, Any]]:
    validate_tag_policy(policy_rows, label="Existing tag policy")
    validate_tag_policy(amendment_rows, label="Reviewed policy amendments")
    policy_by_tag = {row.tag: row for row in policy_rows}
    amendment_by_tag = {row.tag: row for row in amendment_rows}
    overlap = sorted(plan_tags & set(amendment_by_tag), key=tag_sort_key)
    if overlap:
        raise WikiError(
            "Policy amendments may only update historical tags absent from the current plan.",
            details={"tags": overlap},
        )
    unknown = sorted(set(amendment_by_tag) - set(policy_by_tag), key=tag_sort_key)
    if unknown:
        raise WikiError(
            "Policy amendments may only update tags already present in the persistent policy.",
            details={"tags": unknown},
        )
    mismatches = [
        {
            "tag": tag,
            "policy_page_count": policy_by_tag[tag].page_count,
            "amendment_page_count": row.page_count,
        }
        for tag, row in sorted(amendment_by_tag.items(), key=lambda item: tag_sort_key(item[0]))
        if policy_by_tag[tag].page_count != row.page_count
    ]
    if mismatches:
        raise WikiError(
            "Policy amendments must preserve each historical tag's last-reviewed page_count.",
            details={"mismatches": mismatches},
        )
    overrides = sorted(
        (
            tag
            for tag, row in amendment_by_tag.items()
            if policy_by_tag[tag].action != row.action
            or policy_by_tag[tag].target != row.target
        ),
        key=tag_sort_key,
    )
    amended = dict(policy_by_tag)
    amended.update(amendment_by_tag)
    result = tuple(sorted(amended.values(), key=lambda row: tag_sort_key(row.tag)))
    validate_tag_policy(result, label="Tag policy after reviewed amendments")
    return result, {
        "amendment_rows": len(amendment_rows),
        "decision_override_count": len(overrides),
        "decision_overrides": overrides,
    }


def merge_tag_decisions(
    policy_rows: Sequence[TagDecision],
    plan_rows: Sequence[TagDecision],
) -> tuple[tuple[TagDecision, ...], dict[str, Any]]:
    validate_tag_policy(policy_rows, label="Existing tag policy")
    validate_tag_policy(plan_rows, label="Reviewed tag plan")
    policy_by_tag = {row.tag: row for row in policy_rows}
    plan_by_tag = {row.tag: row for row in plan_rows}
    decision_overrides = sorted(
        (
            tag
            for tag, row in plan_by_tag.items()
            if tag in policy_by_tag
            and (
                policy_by_tag[tag].action != row.action
                or policy_by_tag[tag].target != row.target
            )
        ),
        key=tag_sort_key,
    )
    count_updates = sorted(
        (
            tag
            for tag, row in plan_by_tag.items()
            if tag in policy_by_tag and policy_by_tag[tag].page_count != row.page_count
        ),
        key=tag_sort_key,
    )
    new_tags = sorted(set(plan_by_tag) - set(policy_by_tag), key=tag_sort_key)
    retained = sorted(set(policy_by_tag) - set(plan_by_tag), key=tag_sort_key)
    merged = dict(policy_by_tag)
    merged.update(plan_by_tag)
    result = tuple(sorted(merged.values(), key=lambda row: tag_sort_key(row.tag)))
    validate_tag_policy(result, label="Prospective merged tag policy")
    return result, {
        "policy_rows_before": len(policy_rows),
        "plan_rows": len(plan_rows),
        "merged_rows": len(result),
        "decision_override_count": len(decision_overrides),
        "decision_overrides": decision_overrides,
        "page_count_update_count": len(count_updates),
        "new_tag_count": len(new_tags),
        "new_tags": new_tags,
        "retained_history_count": len(retained),
        "retained_history_tags": retained,
    }


def reviewed_policy_bundle(
    vault: Path,
    plan: Path,
    *,
    counts: dict[str, int] | None,
    amendments: Path | None,
) -> tuple[
    tuple[TagDecision, ...],
    dict[str, tuple[str, str | None]],
    tuple[TagDecision, ...],
    dict[str, Any],
    dict[str, Any],
    bytes,
    bytes | None,
    bytes | None,
]:
    policy_rows, policy_bytes = load_tag_policy(vault)
    if counts is None:
        plan_rows, plan_bytes = read_tag_table(plan, allow_unresolved=False)
        validate_tag_policy(plan_rows, label="Reviewed tag plan")
        mapping = {
            row.tag: (
                row.action,
                row.target if row.action == "rename" else row.tag if row.action == "keep" else None,
            )
            for row in plan_rows
        }
    else:
        plan_rows, mapping, plan_bytes = read_tag_plan(plan, counts)
    amendment_bytes: bytes | None = None
    effective_policy: Sequence[TagDecision] = policy_rows
    amendment_stats: dict[str, Any] = {
        "amendment_rows": 0,
        "decision_override_count": 0,
        "decision_overrides": [],
    }
    if amendments is not None:
        amendment_rows, amendment_bytes = read_tag_table(amendments, allow_unresolved=False)
        effective_policy, amendment_stats = apply_tag_policy_amendments(
            policy_rows,
            amendment_rows,
            plan_tags={row.tag for row in plan_rows},
        )
    merged, merge_stats = merge_tag_decisions(effective_policy, plan_rows)
    return (
        plan_rows,
        mapping,
        merged,
        merge_stats,
        amendment_stats,
        plan_bytes,
        amendment_bytes,
        policy_bytes,
    )


def planned_tag_updates(
    vault: Path,
    records: Sequence[PageRecord],
    mapping: dict[str, tuple[str, str | None]],
) -> list[tuple[str, Path, bytes, bytes]]:
    updates: list[tuple[str, Path, bytes, bytes]] = []
    for record in records:
        rel, path = safe_rel(vault, record.path, label="tag maintenance page")
        original = capture_file(path, rel=rel)
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WikiError(f"Markdown is not valid UTF-8: {rel}") from exc
        values, _body, errors = parse_frontmatter_text(text)
        raw_tags = values.get("tags", [])
        if errors or not isinstance(raw_tags, list) or any(
            not isinstance(item, str) for item in raw_tags
        ):
            raise WikiError(
                f"Page metadata changed while the tag plan was being prepared: {rel}",
                code=EXIT_CONFLICT,
                next_step="Run tags collect again and review a fresh plan.",
            )
        current = ordered_strings(raw_tags)
        revised: list[str] = []
        seen: set[str] = set()
        for tag in current:
            decision = mapping.get(tag)
            if decision is None:
                raise WikiError(
                    f"Page tags changed while the tag plan was being prepared: {rel}",
                    code=EXIT_CONFLICT,
                    next_step="Run tags collect again and review a fresh plan.",
                )
            target = decision[1]
            if target is not None and target not in seen:
                seen.add(target)
                revised.append(target)
        if tuple(revised) == current:
            continue
        updated = replace_frontmatter_list(text, "tags", revised).encode("utf-8")
        updates.append((rel, path, original, updated))
    return updates


def command_tags_apply(args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    plan, plan_rel = resolve_tag_review_path(vault, args.plan, option="--plan")
    amendments: Path | None = None
    amendments_rel: str | None = None
    if getattr(args, "amendments", None):
        amendments, amendments_rel = resolve_tag_review_path(
            vault, args.amendments, option="--amendments"
        )
        if amendments == plan:
            raise WikiError("--amendments must not name the reviewed tag plan itself.")
    allowed_plans = tuple(
        path for path, rel in ((plan, plan_rel), (amendments, amendments_rel)) if path is not None and rel
    )
    base = clean_tag_base(vault, args.base, allowed_plans=allowed_plans)
    records = tag_records(vault)
    counts, _page_count = tag_inventory(records)
    (
        _plan_rows,
        mapping,
        _merged,
        merge_stats,
        amendment_stats,
        plan_bytes,
        amendment_bytes,
        policy_bytes,
    ) = reviewed_policy_bundle(
        vault,
        plan,
        counts=counts,
        amendments=amendments,
    )
    updates = planned_tag_updates(vault, records, mapping)
    planned_paths = sorted(item[0] for item in updates)
    summary = {
        action: sum(1 for item in mapping.values() if item[0] == action)
        for action in ("keep", "rename", "delete")
    }
    clean_tag_base(vault, base, allowed_plans=allowed_plans)
    if capture_file(plan, rel=str(plan)) != plan_bytes:
        raise WikiError(
            "The tag plan changed while it was being reviewed.",
            code=EXIT_CONFLICT,
            next_step="Review the current tag plan and retry.",
        )
    if amendments is not None and capture_file(amendments, rel=str(amendments)) != amendment_bytes:
        raise WikiError(
            "The tag policy amendments changed while they were being reviewed.",
            code=EXIT_CONFLICT,
            next_step="Review the current amendments and retry.",
        )
    for rel, path, original, _updated in updates:
        if capture_file(path, rel=rel) != original:
            raise WikiError(
                f"Page changed while the tag plan was being reviewed: {rel}",
                code=EXIT_CONFLICT,
                next_step="Run tags collect again and review a fresh plan.",
            )

    if not args.approved:
        emit(
            {
                "ok": False,
                "command": "tags",
                "action": "apply",
                "vault": str(vault),
                "base": base,
                "plan": str(plan),
                "amendments": str(amendments) if amendments is not None else None,
                "approved": False,
                "changed": False,
                "changed_paths": planned_paths,
                "mapping": summary,
                "policy_merge": merge_stats,
                "policy_amendments": amendment_stats,
                "review_required": True,
                "next": "Review and confirm the tag plan, then repeat with --approved.",
            }
        )
        return EXIT_REVIEW

    # The semantic inputs must still be the exact bytes validated above when
    # the first page write begins.
    clean_tag_base(vault, base, allowed_plans=allowed_plans)
    if capture_file(plan, rel=str(plan)) != plan_bytes:
        raise WikiError("The tag plan changed before the first page write.", code=EXIT_CONFLICT)
    if amendments is not None and capture_file(amendments, rel=str(amendments)) != amendment_bytes:
        raise WikiError(
            "The tag policy amendments changed before the first page write.",
            code=EXIT_CONFLICT,
        )
    policy_path = vault / TAG_POLICY_PATH
    latest_policy = current_tag_policy_bytes(policy_path)
    if latest_policy != policy_bytes:
        raise WikiError(
            "The persistent tag policy changed before the first page write.",
            code=EXIT_CONFLICT,
        )

    changed_paths: list[str] = []
    for rel, path, original, updated in updates:
        if capture_file(path, rel=rel) != original:
            raise WikiError(
                f"Page changed while tags were being applied: {rel}",
                code=EXIT_CONFLICT,
                next_step="Inspect the visible changes, then collect and review a fresh tag plan.",
                details={"changed_paths": changed_paths},
            )
        try:
            atomic_write(path, updated, expected=original)
        except WikiError as exc:
            raise WikiError(
                str(exc),
                code=exc.code,
                next_step="Inspect the visible changes, then collect and review a fresh tag plan.",
                details={"changed_paths": changed_paths},
            ) from exc
        except OSError as exc:
            raise WikiError(
                f"Tag application stopped while writing {rel}: {exc}",
                code=EXIT_CONFLICT,
                next_step="Inspect the visible changes before retrying.",
                details={"changed_paths": changed_paths},
            ) from exc
        changed_paths.append(rel)

    if head_oid(vault) != base:
        raise WikiError(
            "The wiki HEAD changed while tags were being applied.",
            code=EXIT_CONFLICT,
            next_step="Inspect the visible changes, then run begin again.",
            details={"changed_paths": changed_paths},
        )
    emit(
        {
            "ok": True,
            "command": "tags",
            "action": "apply",
            "vault": str(vault),
            "base": base,
            "plan": str(plan),
            "amendments": str(amendments) if amendments is not None else None,
            "approved": True,
            "changed": bool(changed_paths),
            "changed_paths": sorted(changed_paths),
            "mapping": summary,
            "policy_merge": merge_stats,
            "policy_amendments": amendment_stats,
            "review_required": False,
            "next": (
                "Run save with operation tag-maintenance and the returned changed_paths."
                if changed_paths
                else "No page tags changed; no save is needed."
            ),
        }
    )
    return EXIT_OK


def command_tags_vocabulary(_args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    ensure_wiki_contract(vault)
    rows, _policy_bytes = load_tag_policy(vault)
    emit(
        {
            "ok": True,
            "command": "tags",
            "action": "vocabulary",
            "vault": str(vault),
            "policy": TAG_POLICY_PATH,
            "policy_rows": len(rows),
            **build_tag_vocabulary(rows),
        }
    )
    return EXIT_OK


def parse_proposed_tags(value: str) -> list[str]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise WikiError(f"--tags-json is not valid JSON: {exc}.") from exc
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise WikiError("--tags-json must be a JSON array of strings.")
    exact: set[str] = set()
    normalized: dict[str, str] = {}
    result: list[str] = []
    for index, tag in enumerate(decoded):
        validate_plain_tag(tag, label=f"--tags-json item {index}")
        if tag in exact:
            raise WikiError(f"--tags-json contains duplicate tag {tag!r}.")
        key = normalized_tag_key(tag)
        previous = normalized.get(key)
        if previous is not None and previous != tag:
            raise WikiError(
                "--tags-json contains tags that collide after NFC/casefold normalization.",
                details={"first": previous, "second": tag},
            )
        exact.add(tag)
        normalized[key] = tag
        result.append(tag)
    return result


def command_tags_check(args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    ensure_wiki_contract(vault)
    rows, _policy_bytes = load_tag_policy(vault)
    vocabulary = build_tag_vocabulary(rows)
    proposed = parse_proposed_tags(args.tags_json)
    preferred = set(vocabulary["preferred_tags"])
    forbidden = set(vocabulary["forbidden_tags"])
    rename_map = vocabulary["rename_map"]
    deleted = {row.tag for row in rows if row.action == "delete"}
    known_by_key = {normalized_tag_key(tag): tag for tag in preferred | forbidden}
    accepted: list[str] = []
    new_tags: list[str] = []
    rejected: list[dict[str, str]] = []
    for tag in proposed:
        if tag in rename_map:
            rejected.append(
                {"tag": tag, "reason": "rename_source", "replacement": rename_map[tag]}
            )
        elif tag in deleted:
            rejected.append({"tag": tag, "reason": "deleted_tag"})
        elif tag in preferred:
            accepted.append(tag)
        else:
            known = known_by_key.get(normalized_tag_key(tag))
            if known is not None:
                if known in rename_map:
                    rejected.append(
                        {
                            "tag": tag,
                            "reason": "noncanonical_rename_source",
                            "replacement": rename_map[known],
                        }
                    )
                elif known in deleted:
                    rejected.append({"tag": tag, "reason": "deleted_tag"})
                else:
                    rejected.append(
                        {"tag": tag, "reason": "noncanonical_spelling", "replacement": known}
                    )
            else:
                new_tags.append(tag)
    emit(
        {
            "ok": not rejected,
            "command": "tags",
            "action": "check",
            "vault": str(vault),
            "policy": TAG_POLICY_PATH,
            "accepted_tags": accepted,
            "new_tags": new_tags,
            "rejected_tags": rejected,
            "guidance": (
                "Use preferred tags first. New tags are allowed only when the preferred vocabulary "
                "cannot adequately describe the material."
            ),
        }
    )
    return EXIT_OK if not rejected else EXIT_CONFLICT


def revision_blob_bytes(vault: Path, revision: str, rel: str) -> bytes | None:
    result = run_git(vault, ["show", f"{revision}:{rel}"], check=False, binary=True)
    return result.stdout if result.returncode == 0 else None


def revision_tag_snapshot(
    vault: Path,
    revision: str,
) -> dict[str, tuple[tuple[str, ...], bytes]]:
    snapshot: dict[str, tuple[tuple[str, ...], bytes]] = {}
    for rel, entry in sorted(exact_tree_entries(vault, revision).items()):
        if entry.object_type != "blob" or not is_indexable_rel(rel):
            continue
        data = revision_blob_bytes(vault, revision, rel)
        if data is None:
            raise WikiError(
                f"Cannot read page {rel} from checkpoint {revision}.",
                code=EXIT_CONFLICT,
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WikiError(
                f"Markdown is not valid UTF-8 in checkpoint {revision}: {rel}",
                code=EXIT_CONFLICT,
            ) from exc
        values, _body, errors = parse_frontmatter_text(text)
        raw_tags = values.get("tags", [])
        if errors or not isinstance(raw_tags, list) or any(
            not isinstance(item, str) for item in raw_tags
        ):
            raise WikiError(
                f"Page metadata is invalid in checkpoint {revision}: {rel}",
                code=EXIT_CONFLICT,
            )
        snapshot[rel] = (ordered_strings(raw_tags), data)
    return snapshot


def snapshot_tag_inventory(
    snapshot: dict[str, tuple[tuple[str, ...], bytes]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tags, _data in snapshot.values():
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def expected_tag_snapshot(
    snapshot: dict[str, tuple[tuple[str, ...], bytes]],
    mapping: dict[str, tuple[str, str | None]],
) -> tuple[dict[str, bytes], set[str]]:
    expected: dict[str, bytes] = {}
    changed: set[str] = set()
    for rel, (current, original) in snapshot.items():
        revised: list[str] = []
        seen: set[str] = set()
        for tag in current:
            decision = mapping.get(tag)
            if decision is None:
                raise WikiError(
                    "The reviewed plan does not match the tag inventory of its source checkpoint.",
                    code=EXIT_CONFLICT,
                    details={"path": rel, "tag": tag},
                )
            target = decision[1]
            if target is not None and target not in seen:
                seen.add(target)
                revised.append(target)
        if tuple(revised) == current:
            expected[rel] = original
            continue
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError as exc:  # already checked by revision_tag_snapshot
            raise WikiError(f"Markdown is not valid UTF-8: {rel}", code=EXIT_CONFLICT) from exc
        expected[rel] = replace_frontmatter_list(text, "tags", revised).encode("utf-8")
        changed.add(rel)
    return expected, changed


def verify_tag_policy_checkpoint(
    vault: Path,
    plan_rows: Sequence[TagDecision],
    mapping: dict[str, tuple[str, str | None]],
) -> dict[str, Any]:
    current = head_oid(vault)
    stated_counts = {row.tag: row.page_count for row in plan_rows}
    current_snapshot = revision_tag_snapshot(vault, current)
    current_counts = snapshot_tag_inventory(current_snapshot)
    if current_counts == stated_counts:
        _expected, changed = expected_tag_snapshot(current_snapshot, mapping)
        if not changed:
            return {
                "mode": "no-page-change",
                "source_checkpoint": current,
                "page_checkpoint": current,
                "changed_paths": [],
            }

    parents = run_git(vault, ["rev-list", "--parents", "-n", "1", current]).stdout.split()
    if len(parents) != 2:
        raise WikiError(
            "The current HEAD is not a single-parent page checkpoint for this tag plan.",
            code=EXIT_CONFLICT,
            next_step="Apply and save the reviewed page changes before merging the tag policy.",
        )
    parent = parents[1]
    parent_snapshot = revision_tag_snapshot(vault, parent)
    parent_counts = snapshot_tag_inventory(parent_snapshot)
    if parent_counts != stated_counts:
        raise WikiError(
            "The reviewed tag plan does not match the parent of the current page checkpoint.",
            code=EXIT_CONFLICT,
            next_step="Use the plan that produced the current page checkpoint, or begin a fresh maintenance round.",
            details={
                "plan_tags": sorted(stated_counts, key=tag_sort_key),
                "checkpoint_tags": sorted(parent_counts, key=tag_sort_key),
            },
        )
    expected, changed = expected_tag_snapshot(parent_snapshot, mapping)
    if not changed:
        raise WikiError(
            "The current HEAD is not the required page checkpoint for this no-op tag plan.",
            code=EXIT_CONFLICT,
        )
    if set(current_snapshot) != set(expected):
        raise WikiError(
            "The page checkpoint added or removed indexable pages outside the reviewed tag plan.",
            code=EXIT_CONFLICT,
        )
    mismatched = sorted(
        rel
        for rel, expected_bytes in expected.items()
        if current_snapshot[rel][1] != expected_bytes
    )
    parent_entries = exact_tree_entries(vault, parent)
    current_entries = exact_tree_entries(vault, current)
    mode_mismatches = sorted(
        rel
        for rel in {*changed, "index.csv"}
        if rel not in parent_entries
        or rel not in current_entries
        or parent_entries[rel].mode != current_entries[rel].mode
        or parent_entries[rel].object_type != "blob"
        or current_entries[rel].object_type != "blob"
    )
    actual_changes = set(exact_tree_changes(vault, parent, current))
    expected_changes = {*changed, "index.csv"}
    if mismatched or mode_mismatches or actual_changes != expected_changes:
        raise WikiError(
            "The current HEAD does not exactly match the page checkpoint produced by this tag plan.",
            code=EXIT_CONFLICT,
            next_step="Inspect the checkpoint and use the matching reviewed plan.",
            details={
                "mismatched_pages": mismatched,
                "mode_mismatches": mode_mismatches,
                "expected_changes": sorted(expected_changes),
                "actual_changes": sorted(actual_changes),
            },
        )
    return {
        "mode": "page-checkpoint",
        "source_checkpoint": parent,
        "page_checkpoint": current,
        "changed_paths": sorted(changed),
    }


def current_tag_policy_bytes(path: Path) -> bytes | None:
    if not os.path.lexists(path):
        return None
    validate_discovered_path(path.parent, path, label="tag policy")
    if not path.is_file():
        raise WikiError(
            "The persistent tag policy path appeared with an invalid file type.",
            code=EXIT_CONFLICT,
        )
    return capture_file(path, rel=TAG_POLICY_PATH)


def command_tags_merge(args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    plan, plan_rel = resolve_tag_review_path(vault, args.plan, option="--plan")
    amendments: Path | None = None
    amendments_rel: str | None = None
    if getattr(args, "amendments", None):
        amendments, amendments_rel = resolve_tag_review_path(
            vault, args.amendments, option="--amendments"
        )
        if amendments == plan:
            raise WikiError("--amendments must not name the reviewed tag plan itself.")
    allowed_plans = tuple(
        path for path, rel in ((plan, plan_rel), (amendments, amendments_rel)) if path is not None and rel
    )
    base = clean_tag_base(vault, args.base, allowed_plans=allowed_plans)
    findings = audit_findings(vault)
    if findings:
        raise WikiError(
            "Tag policy merge requires a healthy page checkpoint.",
            code=EXIT_AUDIT,
            next_step="Correct the audit findings before merging the reviewed policy.",
            details={"findings": findings},
        )
    (
        plan_rows,
        mapping,
        merged,
        merge_stats,
        amendment_stats,
        plan_bytes,
        amendment_bytes,
        policy_bytes,
    ) = reviewed_policy_bundle(
        vault,
        plan,
        counts=None,
        amendments=amendments,
    )
    checkpoint = verify_tag_policy_checkpoint(vault, plan_rows, mapping)
    merged_bytes = build_tag_policy_bytes(merged)
    changed = (
        policy_bytes is None
        or canonical_tag_policy_worktree_bytes(policy_bytes) != merged_bytes
    )
    changed_paths = [TAG_POLICY_PATH] if changed else []
    summary = {
        action: sum(1 for row in plan_rows if row.action == action)
        for action in ("keep", "rename", "delete")
    }

    clean_tag_base(vault, base, allowed_plans=allowed_plans)
    if capture_file(plan, rel=str(plan)) != plan_bytes:
        raise WikiError(
            "The tag plan changed while the policy merge was being prepared.",
            code=EXIT_CONFLICT,
        )
    if amendments is not None and capture_file(amendments, rel=str(amendments)) != amendment_bytes:
        raise WikiError(
            "The tag policy amendments changed while the policy merge was being prepared.",
            code=EXIT_CONFLICT,
        )
    policy_path = vault / TAG_POLICY_PATH
    current_policy_bytes = current_tag_policy_bytes(policy_path)
    if current_policy_bytes != policy_bytes:
        raise WikiError(
            "The persistent tag policy changed while the merge was being prepared.",
            code=EXIT_CONFLICT,
        )

    payload = {
        "ok": bool(args.approved),
        "command": "tags",
        "action": "merge",
        "vault": str(vault),
        "base": base,
        "plan": str(plan),
        "amendments": str(amendments) if amendments is not None else None,
        "policy": TAG_POLICY_PATH,
        "approved": bool(args.approved),
        "changed": changed,
        "changed_paths": changed_paths,
        "mapping": summary,
        "checkpoint": checkpoint,
        "policy_merge": merge_stats,
        "policy_amendments": amendment_stats,
        "vocabulary": build_tag_vocabulary(merged),
    }
    if not args.approved:
        payload.update(
            {
                "review_required": True,
                "next": "Review the prospective policy merge, then repeat with --approved.",
            }
        )
        emit(payload)
        return EXIT_REVIEW

    # Repeat every read-only guard immediately before the single policy write.
    clean_tag_base(vault, base, allowed_plans=allowed_plans)
    if capture_file(plan, rel=str(plan)) != plan_bytes:
        raise WikiError("The tag plan changed before the policy write.", code=EXIT_CONFLICT)
    if amendments is not None and capture_file(amendments, rel=str(amendments)) != amendment_bytes:
        raise WikiError("The tag policy amendments changed before the policy write.", code=EXIT_CONFLICT)
    verify_tag_policy_checkpoint(vault, plan_rows, mapping)
    clean_tag_base(vault, base, allowed_plans=allowed_plans)
    if capture_file(plan, rel=str(plan)) != plan_bytes:
        raise WikiError("The tag plan changed during checkpoint verification.", code=EXIT_CONFLICT)
    if amendments is not None and capture_file(amendments, rel=str(amendments)) != amendment_bytes:
        raise WikiError(
            "The tag policy amendments changed during checkpoint verification.",
            code=EXIT_CONFLICT,
        )
    latest_policy = current_tag_policy_bytes(policy_path)
    if latest_policy != policy_bytes:
        raise WikiError("The persistent tag policy changed before the policy write.", code=EXIT_CONFLICT)
    if head_oid(vault) != base:
        raise WikiError("The wiki HEAD changed before the policy write.", code=EXIT_CONFLICT)
    if changed:
        if policy_bytes is None:
            atomic_create(policy_path, merged_bytes)
        else:
            atomic_write(policy_path, merged_bytes, expected=policy_bytes)
        if policy_path.read_bytes() != merged_bytes:
            raise WikiError(
                "The persistent tag policy failed read-back verification.",
                code=EXIT_CONFLICT,
            )
    try:
        post_write_conflict = head_oid(vault) != base
        post_write_conflict = post_write_conflict or capture_file(plan, rel=str(plan)) != plan_bytes
        if amendments is not None:
            post_write_conflict = post_write_conflict or (
                capture_file(amendments, rel=str(amendments)) != amendment_bytes
            )
        expected_policy_bytes = merged_bytes if changed else policy_bytes
        post_write_conflict = post_write_conflict or (
            current_tag_policy_bytes(policy_path) != expected_policy_bytes
        )
        dirty, staged, untracked = dirty_path_sets(vault)
        allowed_dirty = {
            rel
            for path in allowed_plans
            for rel in (root_tag_plan_rel(vault, path),)
            if rel is not None
        }
        if changed:
            allowed_dirty.add(TAG_POLICY_PATH)
        post_write_conflict = post_write_conflict or bool(
            staged or dirty - allowed_dirty or untracked - allowed_dirty
        )
    except (WikiError, OSError):
        post_write_conflict = True
    if post_write_conflict:
        raise WikiError(
            "The wiki HEAD or reviewed inputs changed while the tag policy was being written.",
            code=EXIT_CONFLICT,
            next_step="Inspect the visible tag policy change and concurrent repository state before retrying.",
            details={"changed_paths": changed_paths},
        )
    payload.update(
        {
            "ok": True,
            "review_required": False,
            "next": (
                "Save tags-review.csv in a separate checkpoint with operation tag-policy."
                if changed
                else "The persistent tag policy already matches the reviewed decisions."
            ),
        }
    )
    emit(payload)
    return EXIT_OK


def raw_owner_map(vault: Path) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = {}
    records, _findings = collect_records(vault, strict=False)
    for record in records:
        if record.kind != "source":
            continue
        for value in record.raw:
            try:
                rel, _path = raw_link_rel(vault, value)
            except WikiError:
                continue
            owners.setdefault(rel, set()).add(record.path)
    return owners


def existing_raw_blobs(vault: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    files, findings = discover_files_under(vault, "raw", label="raw path")
    findings.extend(portable_path_findings(rel for rel, _path in files))
    if findings:
        raise WikiError(
            "Unsafe raw path prevents ingestion.",
            code=EXIT_CONFLICT,
            details={"findings": findings},
        )
    for rel, path in files:
        if rel == "raw/.gitkeep":
            continue
        result.setdefault(git_blob_oid(path, vault), []).append(rel)
    for paths in result.values():
        paths.sort()
    return result


def unique_raw_target(
    vault: Path,
    directory: str,
    filename: str,
    oid: str,
    occupied_keys: set[str],
) -> tuple[str, Path]:
    filename = unicodedata.normalize("NFC", filename)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        candidate_name = filename if counter == 1 else f"{stem}-{counter}{suffix}"
        rel, target = safe_rel(vault, f"raw/{directory}/{candidate_name}", label="raw target")
        if not os.path.lexists(target):
            if portable_path_key(rel) not in occupied_keys:
                return rel, target
            counter += 1
            continue
        validate_discovered_path(vault, target, label="raw target")
        if target.is_file() and git_blob_oid(target, vault) == oid:
            return rel, target
        counter += 1


def parse_identifiers(values: list[str] | None) -> list[str]:
    result = []
    for value in values or []:
        if "=" not in value:
            raise WikiError("--identifier must use KEY=VALUE form.")
        key, content = value.split("=", 1)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key) or not content.strip():
            raise WikiError(f"Invalid identifier: {value!r}")
        result.append(f"{key.casefold()}={content.strip()}")
    return list(stable_strings(result))


def command_add(args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    ensure_wiki_contract(vault)
    base = verify_base(vault, args.base)
    dirty, _staged, _untracked = dirty_path_sets(vault)
    if dirty:
        raise WikiError(
            "add requires the baseline worktree to be clean.",
            code=EXIT_CONFLICT,
            next_step="Checkpoint the existing changes separately, then run begin again.",
            details={"changes": sorted(dirty)},
        )
    name = validate_page_name(args.name)
    if name.casefold() == "agents":
        raise WikiError("The source page name AGENTS is reserved by the vault contract.")
    raw_directory = validate_page_name(args.raw_dir or name, label="raw directory name")
    validate_portable_rel(f"raw/{raw_directory}/probe", label="raw directory")
    source_rel, source_path = safe_rel(vault, f"sources/{name}.md", label="source page")
    source_files, source_findings = discover_files_under(vault, "sources", label="source path")
    if source_findings:
        raise WikiError(
            "Unsafe source path prevents ingestion.",
            code=EXIT_CONFLICT,
            details={"findings": source_findings},
        )
    source_conflict = next(
        (
            rel
            for rel, _path in source_files
            if portable_path_key(rel) == portable_path_key(source_rel) and rel != source_rel
        ),
        None,
    )
    if source_conflict is not None:
        raise WikiError(
            f"Source page path conflicts with an existing portable path: {source_conflict}",
            code=EXIT_CONFLICT,
        )
    inputs: list[Path] = []
    for value in args.inputs:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise WikiError(f"Input is not a file: {value}")
        validate_portable_rel(f"raw/{raw_directory}/{path.name}", label="raw input name")
        inputs.append(path)
    if not inputs:
        raise WikiError("add requires at least one input file.")
    identifiers = parse_identifiers(args.identifier)
    parent_links: list[str] = []
    for parent in args.parent or []:
        rel, path = page_link_rel(vault, parent)
        if not rel.startswith("sources/") or not path.is_file():
            raise WikiError(f"Parent source does not exist: {parent}")
        parent_links.append(wiki_link(rel))

    known_blobs = existing_raw_blobs(vault)
    raw_files, raw_findings = discover_files_under(vault, "raw", label="raw path")
    if raw_findings:
        raise WikiError(
            "Unsafe raw path prevents ingestion.",
            code=EXIT_CONFLICT,
            details={"findings": raw_findings},
        )
    occupied_raw_keys = {portable_path_key(rel) for rel, _path in raw_files}
    owners = raw_owner_map(vault)
    input_rows: list[dict[str, Any]] = []
    planned: dict[str, tuple[Path, str, Path]] = {}
    requested_raw: list[str] = []
    for input_path in inputs:
        oid = git_blob_oid(input_path, vault)
        existing = known_blobs.get(oid, [])
        if existing:
            rel = existing[0]
            target = vault / Path(*PurePosixPath(rel).parts)
            reused = True
        elif oid in planned:
            _first_input, rel, target = planned[oid]
            reused = True
        else:
            rel, target = unique_raw_target(
                vault,
                raw_directory,
                input_path.name,
                oid,
                occupied_raw_keys,
            )
            planned[oid] = (input_path, rel, target)
            occupied_raw_keys.add(portable_path_key(rel))
            reused = False
        requested_raw.append(rel)
        input_rows.append(
            {
                "input": str(input_path),
                "path": rel,
                "blob": oid,
                "reused": reused,
            }
        )

    source_exists = os.path.lexists(source_path)
    source_original: bytes | None = None
    existing_raw_rels: list[str] = []
    if source_exists:
        validate_discovered_path(vault, source_path, label="existing source page")
        if not source_path.is_file():
            raise WikiError(f"Source page is not a regular file: {source_rel}", code=EXIT_CONFLICT)
        source_original = source_path.read_bytes()
        existing_text = source_original.decode("utf-8")
        values, _body, errors = parse_frontmatter_text(existing_text)
        if errors or values.get("kind") != "source":
            raise WikiError(
                f"Existing page is not a valid source page: {source_rel}",
                code=EXIT_CONFLICT,
                details={"frontmatter_errors": errors},
            )
        raw_values = values.get("raw", [])
        if not isinstance(raw_values, list) or any(not isinstance(value, str) for value in raw_values):
            raise WikiError(f"Existing source has an invalid raw property: {source_rel}", code=EXIT_CONFLICT)
        for value in raw_values:
            rel, _target = raw_link_rel(vault, value)
            existing_raw_rels.append(rel)
        if parent_links or identifiers:
            raise WikiError(
                "--parent and --identifier are only accepted when creating a new source page.",
                code=EXIT_CONFLICT,
            )

    existing_owners = {owner for rel in requested_raw for owner in owners.get(rel, set())}
    other_owners = sorted(existing_owners - ({source_rel} if source_exists else set()))
    if other_owners:
        if not source_exists and not planned and len(other_owners) == 1:
            emit(
                {
                    "ok": True,
                    "command": "add",
                    "vault": str(vault),
                    "base": base,
                    "pending": False,
                    "source": other_owners[0],
                    "raw": input_rows,
                    "reused_source": True,
                    "next": "Use the existing source page; no files were changed.",
                }
            )
            return EXIT_OK
        raise WikiError(
            "One or more exact raw files already belong to an existing source page.",
            code=EXIT_CONFLICT,
            next_step="Update the owning source deliberately instead of creating a second owner.",
            details={"owners": other_owners},
        )
    if source_exists and not planned and existing_owners == {source_rel}:
        emit(
            {
                "ok": True,
                "command": "add",
                "vault": str(vault),
                "base": base,
                "pending": False,
                "source": source_rel,
                "raw": input_rows,
                "reused_source": True,
                "next": "Use the existing source page; no files were changed.",
            }
        )
        return EXIT_OK

    copied: list[Path] = []
    try:
        for input_path, rel, target in planned.values():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(input_path, target)
            copied.append(target)
            if git_blob_oid(target, vault) != git_blob_oid(input_path, vault):
                raise OSError(f"byte verification failed for {rel}")
        all_raw_rels = stable_strings([*existing_raw_rels, *requested_raw])
        raw_links = [f"[[{rel}]]" for rel in all_raw_rels]
        if source_exists:
            assert source_original is not None
            source_text = replace_frontmatter_list(source_original.decode("utf-8"), "raw", raw_links)
        else:
            source_text = render_template(
                "source.md",
                {"summary": "", "raw_path": raw_links[0][2:-2], "page_name": name},
            )
            extra_lines: list[str] = []
            if parent_links:
                extra_lines.append(
                    "sources: "
                    + json.dumps(list(stable_strings(parent_links)), ensure_ascii=False, separators=(", ", ": "))
                )
            if identifiers:
                extra_lines.append(
                    "identifiers: " + json.dumps(identifiers, ensure_ascii=False, separators=(", ", ": "))
                )
            raw_line = "raw: " + json.dumps(raw_links, ensure_ascii=False, separators=(", ", ": "))
            source_text = re.sub(
                r"(?m)^raw:.*$",
                raw_line + ("\n" + "\n".join(extra_lines) if extra_lines else ""),
                source_text,
                count=1,
            )
        atomic_write(source_path, source_text.encode("utf-8"))
    except Exception:
        if source_original is not None:
            atomic_write(source_path, source_original)
        elif source_path.exists():
            source_path.unlink()
        for path in copied:
            if path.exists():
                path.unlink()
        raise
    emit(
        {
            "ok": True,
            "command": "add",
            "vault": str(vault),
            "base": base,
            "pending": True,
            "source": source_rel,
            "raw": input_rows,
            "reused_source": source_exists,
            "updated_source": source_exists,
            "next": (
                "Review the appended raw relationship, then save with approval."
                if source_exists
                else "Read the material, complete the source summary and tags, then run save with these paths."
            ),
        }
    )
    return EXIT_OK


QUERY_KEYS = {
    "phrases",
    "terms",
    "kinds",
    "required_tags",
    "boost_tags",
    "path_prefixes",
    "limit",
}


def parse_query_plan(value: str) -> dict[str, Any]:
    try:
        plan = json.loads(value)
    except json.JSONDecodeError as exc:
        raise WikiError(f"--plan is not valid JSON: {exc.msg}") from exc
    if not isinstance(plan, dict):
        raise WikiError("--plan must be a JSON object.")
    unknown = sorted(set(plan) - QUERY_KEYS)
    if unknown:
        raise WikiError(f"Unknown QueryPlan fields: {', '.join(unknown)}")
    for key in QUERY_KEYS - {"limit"}:
        current = plan.get(key, [])
        if not isinstance(current, list) or any(not isinstance(item, str) for item in current):
            raise WikiError(f"QueryPlan {key} must be a list of strings.")
        plan[key] = list(stable_strings(current))
    limit = plan.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise WikiError("QueryPlan limit must be a positive integer when present.")
    kinds = set(plan["kinds"])
    if not kinds <= VALID_KINDS:
        raise WikiError(f"Unknown QueryPlan kinds: {', '.join(sorted(kinds - VALID_KINDS))}")
    plan["limit"] = limit
    return plan


def normalized_search(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def score_record(record: PageRecord, plan: dict[str, Any]) -> tuple[int, list[str]] | None:
    if plan["kinds"] and record.kind not in set(plan["kinds"]):
        return None
    tags = {normalized_search(tag) for tag in record.tags}
    required = {normalized_search(tag) for tag in plan["required_tags"]}
    if not required <= tags:
        return None
    prefixes = [normalized_search(prefix).strip("/") for prefix in plan["path_prefixes"]]
    path_search = normalized_search(record.path)
    if prefixes and not any(path_search.startswith(prefix) for prefix in prefixes):
        return None
    stem = normalized_search(Path(record.path).stem)
    aliases = [normalized_search(item) for item in record.aliases]
    summary = normalized_search(record.summary)
    score = 0
    reasons: list[str] = []
    matched = False
    for original in plan["phrases"]:
        value = normalized_search(original)
        if value == stem or value in aliases:
            score += 100
            matched = True
            reasons.append(f"exact phrase: {original}")
        elif value in stem or any(value in alias for alias in aliases):
            score += 60
            matched = True
            reasons.append(f"name phrase: {original}")
        elif value in path_search:
            score += 45
            matched = True
            reasons.append(f"path phrase: {original}")
        elif value in summary:
            score += 30
            matched = True
            reasons.append(f"summary phrase: {original}")
    for original in plan["terms"]:
        value = normalized_search(original)
        if value == stem or value in aliases:
            score += 50
            matched = True
            reasons.append(f"exact term: {original}")
        elif value in tags:
            score += 35
            matched = True
            reasons.append(f"tag: {original}")
        elif value in stem or value in path_search or any(value in alias for alias in aliases):
            score += 25
            matched = True
            reasons.append(f"name/path term: {original}")
        elif value in summary:
            score += 15
            matched = True
            reasons.append(f"summary term: {original}")
    boost = tags & {normalized_search(tag) for tag in plan["boost_tags"]}
    if boost:
        score += 8 * len(boost)
        reasons.append("boost tag: " + ", ".join(sorted(boost)))
    if (plan["phrases"] or plan["terms"]) and not matched:
        return None
    return score, reasons


def command_context(args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    ensure_wiki_contract(vault)
    plan = parse_query_plan(args.plan)
    dirty, _staged, _untracked = dirty_path_sets(vault)
    records, _findings = collect_records(vault, strict=False)
    expected_index = build_index_bytes(records)
    overlay = any(is_indexable_rel(path) or path == "index.csv" for path in dirty)
    index_error: str | None = None
    try:
        current_index = (vault / "index.csv").read_bytes()
    except FileNotFoundError:
        index_error = "index.csv is missing"
    except OSError as exc:
        index_error = f"index.csv cannot be read: {exc}"
    else:
        if current_index != expected_index:
            try:
                read_index(vault)
            except ValueError as exc:
                index_error = str(exc)
            else:
                index_error = "index.csv differs from Markdown frontmatter"
    if index_error is not None:
        overlay = True
    ranked: list[tuple[int, str, PageRecord, list[str]]] = []
    for record in records:
        result = score_record(record, plan)
        if result is not None:
            score, reasons = result
            ranked.append((score, record.path, record, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    total = len(ranked)
    if plan["limit"] is not None:
        ranked = ranked[: plan["limit"]]
    candidates = []
    for score, _path, record, reasons in ranked:
        candidate = record.public()
        candidate.update({"score": score, "reasons": reasons})
        candidates.append(candidate)
    payload: dict[str, Any] = {
        "ok": True,
        "command": "context",
        "vault": str(vault),
        "plan": plan,
        "overlay": overlay,
        "count": len(candidates),
        "total_matches": total,
        "candidates": candidates,
    }
    if index_error:
        payload["index_warning"] = index_error
    emit(payload)
    return EXIT_OK


def output_audit(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        emit(payload)
        return
    findings = payload["findings"]
    if output_format == "text":
        print("valid" if payload["valid"] else "invalid")
        for item in findings:
            suffix = f" [{item['field']}]" if "field" in item else ""
            print(f"{item['code']} {item['path']}{suffix}: {item['message']}")
        return
    writer = csv.DictWriter(sys.stdout, fieldnames=("code", "path", "field", "message"), lineterminator="\n")
    writer.writeheader()
    for item in findings:
        writer.writerow({key: item.get(key, "") for key in writer.fieldnames})


def changed_scope_findings(
    vault: Path, findings: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    global_findings = [item for item in findings if item["code"] in GLOBAL_AUDIT_CODES]
    try:
        dirty, _staged, _untracked = dirty_path_sets(vault)
    except WikiError:
        return list(findings)
    if not dirty:
        return global_findings
    indexable_changed = any(is_indexable_rel(path) for path in dirty)
    root_page_changed = any(
        len(PurePosixPath(path).parts) == 1
        and PurePosixPath(path).suffix.casefold() == ".md"
        and PurePosixPath(path).name.casefold() not in ROOT_REPO_DOC_NAMES
        for path in dirty
    )
    selected: list[dict[str, Any]] = []
    for item in findings:
        if item["code"] in GLOBAL_AUDIT_CODES:
            selected.append(item)
            continue
        path = item["path"]
        message = item["message"]
        local = path in dirty or any(changed in message for changed in dirty)
        if path == "index.csv" and ("index.csv" in dirty or indexable_changed):
            local = True
        if item["code"] == "E_HOME_COUNT" and root_page_changed:
            local = True
        if item["code"] == "E_RAW_ATTRIBUTES" and ".gitattributes" in dirty:
            local = True
        if local:
            selected.append(item)
    return selected


def command_audit(args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    ensure_dedicated_worktree(vault)
    findings = audit_findings(vault)
    if args.scope == "changed":
        findings = changed_scope_findings(vault, findings)
    counts: dict[str, int] = {}
    for item in findings:
        counts[item["code"]] = counts.get(item["code"], 0) + 1
    payload = {
        "ok": not findings,
        "command": "audit",
        "vault": str(vault),
        "scope": args.scope,
        "valid": not findings,
        "findings": findings,
        "counts": dict(sorted(counts.items())),
    }
    output_audit(payload, args.format)
    return EXIT_OK if not findings else EXIT_AUDIT


def flatten_includes(values: list[list[str]] | None) -> list[str]:
    return [item for group in values or [] for item in group]


def expand_include_scope(vault: Path, requested: list[str], dirty: set[str]) -> set[str]:
    included: set[str] = set()
    for value in requested:
        rel, path = safe_rel(vault, value, label="included path")
        if path.is_dir() or value.endswith(("/", "\\")):
            prefix = rel.rstrip("/") + "/"
            included.update(item for item in dirty if item.startswith(prefix))
            if path.is_dir():
                for discovered in path.rglob("*"):
                    if discovered.is_file():
                        included.add(
                            validate_discovered_path(vault, discovered, label="included path")
                        )
            included.update(
                item for item in exact_tree_entries(vault, "HEAD") if item.startswith(prefix)
            )
        else:
            included.add(rel)
    return included


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    object_type: str
    oid: str


@dataclass(frozen=True)
class ImpactPage:
    path: str
    kind: str
    aliases: tuple[str, ...]
    sources: tuple[str, ...]
    raw: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class CandidateCheckpoint:
    repository: Path
    commit: str
    tree: str
    changes: tuple[str, ...]
    risks: tuple[str, ...]
    index_bytes: bytes
    index_changed: bool
    original_index_bytes: bytes | None
    git_index_bytes: bytes
    findings: tuple[dict[str, Any], ...]
    diff: str
    raw_impact: dict[str, Any] | None


def exact_tree_entries(repository: Path, revision: str) -> dict[str, TreeEntry]:
    """Read Git tree identities without normalizing path spelling."""

    result = run_git(
        repository,
        ["ls-tree", "-r", "-z", revision],
        binary=True,
    )
    entries: dict[str, TreeEntry] = {}
    for encoded in result.stdout.split(b"\0"):
        if not encoded:
            continue
        metadata, separator, encoded_path = encoded.partition(b"\t")
        if not separator:
            raise WikiError("Git returned an invalid tree entry.", code=EXIT_CONFLICT)
        try:
            mode, object_type, oid = metadata.decode("ascii").split()
        except (UnicodeDecodeError, ValueError) as exc:
            raise WikiError("Git returned an invalid tree entry.", code=EXIT_CONFLICT) from exc
        rel = encoded_path.decode("utf-8", "surrogateescape")
        entries[rel] = TreeEntry(mode=mode, object_type=object_type, oid=oid)
    return entries


def exact_tree_changes(repository: Path, base: str, candidate: str) -> tuple[str, ...]:
    result = run_git(
        repository,
        ["diff", "--no-renames", "--name-only", "-z", base, candidate, "--"],
        binary=True,
    )
    return tuple(sorted(decode_nul(result.stdout)))


def raw_snapshot_from_revision(repository: Path, revision: str) -> dict[str, str]:
    return {
        rel: entry.oid
        for rel, entry in exact_tree_entries(repository, revision).items()
        if rel.startswith("raw/")
        and rel != "raw/.gitkeep"
        and entry.object_type == "blob"
    }


def raw_snapshot_from_worktree(vault: Path) -> dict[str, str]:
    files, _findings = discover_files_under(vault, "raw", label="raw impact path")
    return {
        rel: git_blob_oid(path, vault)
        for rel, path in files
        if rel != "raw/.gitkeep"
    }


def impact_page_from_bytes(rel: str, data: bytes) -> ImpactPage | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    values, body, errors = parse_frontmatter_text(text)
    kind = values.get("kind")
    if errors or kind not in {"source", "note"}:
        return None
    parsed: dict[str, tuple[str, ...]] = {}
    for field in ("aliases", "sources", "raw"):
        value = values.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return None
        parsed[field] = stable_strings(value)
    return ImpactPage(
        path=rel,
        kind=kind,
        aliases=parsed["aliases"],
        sources=parsed["sources"],
        raw=parsed["raw"],
        body=body,
    )


def impact_pages_from_revision(repository: Path, revision: str) -> dict[str, ImpactPage]:
    pages: dict[str, ImpactPage] = {}
    for rel, entry in sorted(exact_tree_entries(repository, revision).items()):
        if entry.object_type != "blob" or not is_indexable_rel(rel):
            continue
        data = revision_blob_bytes(repository, revision, rel)
        if data is None:
            continue
        page = impact_page_from_bytes(rel, data)
        if page is not None:
            pages[rel] = page
    return pages


def impact_pages_from_worktree(vault: Path) -> dict[str, ImpactPage]:
    pages: dict[str, ImpactPage] = {}
    paths, _findings = iter_indexable_pages(vault)
    for path in paths:
        rel = exact_rel_text(path.relative_to(vault).as_posix())
        try:
            data = capture_file(path, rel=rel)
        except WikiError:
            continue
        page = impact_page_from_bytes(rel, data)
        if page is not None:
            pages[rel] = page
    return pages


def resolve_impact_wikilink(
    original: str,
    *,
    page_paths: set[str],
    page_names: dict[str, set[str]],
    raw_paths: set[str],
    raw_names: dict[str, set[str]],
) -> tuple[str, str] | None:
    target = link_target(original)
    if not target or target.startswith("^") or "://" in target:
        return None
    pure = PurePosixPath(target)
    if len(pure.parts) > 1:
        first = pure.parts[0]
        if first in {"sources", "notes"}:
            rel = target if target.casefold().endswith(".md") else target + ".md"
            return ("page", rel) if rel in page_paths else None
        if first == "raw":
            return ("raw", target) if target in raw_paths else None
        return None

    page_name = pure.stem if pure.suffix.casefold() == ".md" else pure.name
    candidates = set(page_names.get(page_name.casefold(), set()))
    candidates.update(page_names.get(target.casefold(), set()))
    if len(candidates) == 1:
        return "page", next(iter(candidates))
    raw_candidates = raw_names.get(pure.name.casefold(), set())
    if len(raw_candidates) == 1:
        return "raw", next(iter(raw_candidates))
    return None


def impact_graph_snapshot(
    pages: dict[str, ImpactPage],
    raw_paths: set[str],
) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[str, set[tuple[str, str]]],
]:
    page_paths = set(pages)
    page_names: dict[str, set[str]] = {}
    for page in pages.values():
        page_names.setdefault(PurePosixPath(page.path).stem.casefold(), set()).add(page.path)
        for alias in page.aliases:
            page_names.setdefault(alias.casefold(), set()).add(page.path)
    raw_names: dict[str, set[str]] = {}
    for rel in raw_paths:
        raw_names.setdefault(PurePosixPath(rel).name.casefold(), set()).add(rel)

    edges: dict[tuple[str, str], set[str]] = {}
    raw_roots: dict[str, set[tuple[str, str]]] = {}
    for page in pages.values():
        for value in page.sources:
            target = link_target(value)
            if not target.casefold().endswith(".md"):
                target += ".md"
            if target in page_paths:
                edges.setdefault((target, page.path), set()).add("sources")
        for value in page.raw:
            target = link_target(value)
            if target in raw_paths:
                raw_roots.setdefault(page.path, set()).add((target, "declares_raw"))
        for match in iter_prose_wikilinks(page.body):
            resolved = resolve_impact_wikilink(
                match.group(1),
                page_paths=page_paths,
                page_names=page_names,
                raw_paths=raw_paths,
                raw_names=raw_names,
            )
            if resolved is None:
                continue
            kind, target = resolved
            if kind == "page":
                edges.setdefault((target, page.path), set()).add("wikilink")
            else:
                raw_roots.setdefault(page.path, set()).add((target, "raw_link"))
    return edges, raw_roots


def strongly_connected_components(
    nodes: set[str],
    adjacency: dict[str, set[str]],
) -> list[tuple[str, ...]]:
    visited: set[str] = set()
    order: list[str] = []
    for start in sorted(nodes):
        if start in visited:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                order.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            for successor in sorted(adjacency.get(node, set()), reverse=True):
                if successor in nodes and successor not in visited:
                    stack.append((successor, False))

    reverse: dict[str, set[str]] = {node: set() for node in nodes}
    for source, successors in adjacency.items():
        if source not in nodes:
            continue
        for target in successors:
            if target in nodes:
                reverse[target].add(source)
    assigned: set[str] = set()
    components: list[tuple[str, ...]] = []
    for start in reversed(order):
        if start in assigned:
            continue
        component: set[str] = set()
        stack = [start]
        assigned.add(start)
        while stack:
            node = stack.pop()
            component.add(node)
            for predecessor in sorted(reverse.get(node, set()), reverse=True):
                if predecessor not in assigned:
                    assigned.add(predecessor)
                    stack.append(predecessor)
        components.append(tuple(sorted(component)))
    return components


def build_raw_impact(
    before_raw: dict[str, str],
    after_raw: dict[str, str],
    before_pages: dict[str, ImpactPage],
    after_pages: dict[str, ImpactPage],
) -> dict[str, Any] | None:
    changes: list[dict[str, Any]] = []
    for rel in sorted(set(before_raw) | set(after_raw)):
        before_oid = before_raw.get(rel)
        after_oid = after_raw.get(rel)
        if before_oid == after_oid:
            continue
        status = "added" if before_oid is None else "deleted" if after_oid is None else "modified"
        changes.append(
            {
                "path": rel,
                "status": status,
                "before_path": rel if before_oid is not None else None,
                "after_path": rel if after_oid is not None else None,
                "before_oid": before_oid,
                "after_oid": after_oid,
            }
        )
    if not changes:
        return None

    before_edges, before_roots = impact_graph_snapshot(before_pages, set(before_raw))
    after_edges, after_roots = impact_graph_snapshot(after_pages, set(after_raw))
    pages = dict(before_pages)
    pages.update(after_pages)
    edges: dict[tuple[str, str], set[str]] = {}
    for snapshot_edges in (before_edges, after_edges):
        for edge, relations in snapshot_edges.items():
            edges.setdefault(edge, set()).update(relations)
    changed_raw = {item["path"] for item in changes}
    root_reasons: dict[str, set[tuple[str, str]]] = {}
    for snapshot_roots in (before_roots, after_roots):
        for page, reasons in snapshot_roots.items():
            for raw_rel, relation in reasons:
                if raw_rel in changed_raw:
                    root_reasons.setdefault(page, set()).add((raw_rel, relation))

    adjacency: dict[str, set[str]] = {path: set() for path in pages}
    reverse: dict[str, set[str]] = {path: set() for path in pages}
    for source, target in edges:
        if source in pages and target in pages:
            adjacency[source].add(target)
            reverse[target].add(source)
    roots = set(root_reasons)
    reachable: set[str] = set()
    pending = sorted(roots, reverse=True)
    while pending:
        node = pending.pop()
        if node in reachable:
            continue
        reachable.add(node)
        pending.extend(sorted(adjacency.get(node, set()) - reachable, reverse=True))

    components = strongly_connected_components(reachable, adjacency)
    component_of = {
        page: index
        for index, component in enumerate(components)
        for page in component
    }
    component_edges: dict[int, set[int]] = {index: set() for index in range(len(components))}
    component_reverse: dict[int, set[int]] = {index: set() for index in range(len(components))}
    for source, successors in adjacency.items():
        if source not in reachable:
            continue
        for target in successors:
            if target not in reachable:
                continue
            left, right = component_of[source], component_of[target]
            if left != right:
                component_edges[left].add(right)
                component_reverse[right].add(left)

    distances: dict[int, int] = {}
    queue = sorted({component_of[root] for root in roots}, reverse=True)
    for component in queue:
        distances[component] = 1
    while queue:
        component = queue.pop()
        next_distance = distances[component] + 1
        for successor in sorted(component_edges[component], reverse=True):
            if successor not in distances or next_distance < distances[successor]:
                distances[successor] = next_distance
                queue.append(successor)

    ordered_components = sorted(
        range(len(components)),
        key=lambda item: (distances.get(item, sys.maxsize), components[item]),
    )
    group_ids = {component: f"g{number}" for number, component in enumerate(ordered_components, start=1)}
    groups: list[dict[str, Any]] = []
    for component in ordered_components:
        group_pages = list(components[component])
        cyclic = len(group_pages) > 1 or any(
            page in adjacency.get(page, set()) for page in group_pages
        )
        groups.append(
            {
                "id": group_ids[component],
                "distance": distances[component],
                "pages": group_pages,
                "predecessors": [group_ids[item] for item in sorted(component_reverse[component], key=lambda value: group_ids[value])],
                "successors": [group_ids[item] for item in sorted(component_edges[component], key=lambda value: group_ids[value])],
                "cyclic": cyclic,
            }
        )

    page_rows = [
        {
            "path": path,
            "kind": pages[path].kind,
            "distance": distances[component_of[path]],
            "group": group_ids[component_of[path]],
            "predecessors": sorted(reverse.get(path, set()) & reachable),
            "successors": sorted(adjacency.get(path, set()) & reachable),
        }
        for path in sorted(reachable, key=lambda item: (distances[component_of[item]], item))
    ]
    edge_rows = [
        {
            "from": source,
            "to": target,
            "relations": sorted(relations),
        }
        for (source, target), relations in sorted(edges.items())
        if source in reachable and target in reachable
    ]
    root_rows = [
        {"raw": raw_rel, "page": page, "relation": relation}
        for page, reasons in sorted(root_reasons.items())
        for raw_rel, relation in sorted(reasons)
    ]
    owner_sources = sorted(
        {
            page
            for page, reasons in before_roots.items()
            if pages.get(page) is not None
            and pages[page].kind == "source"
            and any(raw_rel in changed_raw and relation == "declares_raw" for raw_rel, relation in reasons)
        }
    )
    layers = [
        {
            "distance": distance,
            "groups": [item["id"] for item in groups if item["distance"] == distance],
        }
        for distance in sorted({item["distance"] for item in groups})
    ]
    return {
        "changes": changes,
        "owner_sources": owner_sources,
        "roots": root_rows,
        "pages": page_rows,
        "edges": edge_rows,
        "groups": groups,
        "layers": layers,
    }


def raw_impact_between_revisions(
    repository: Path,
    base: str,
    candidate: str,
) -> dict[str, Any] | None:
    before_raw = raw_snapshot_from_revision(repository, base)
    after_raw = raw_snapshot_from_revision(repository, candidate)
    if before_raw == after_raw:
        return None
    return build_raw_impact(
        before_raw,
        after_raw,
        impact_pages_from_revision(repository, base),
        impact_pages_from_revision(repository, candidate),
    )


def raw_impact_from_worktree(vault: Path, base: str) -> dict[str, Any] | None:
    before_raw = raw_snapshot_from_revision(vault, base)
    after_raw = raw_snapshot_from_worktree(vault)
    if before_raw == after_raw:
        return None
    return build_raw_impact(
        before_raw,
        after_raw,
        impact_pages_from_revision(vault, base),
        impact_pages_from_worktree(vault),
    )


def repository_index_path(repository: Path) -> Path:
    result = run_git(repository, ["rev-parse", "--git-path", "index"])
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else repository / path


def candidate_path(root: Path, rel: str) -> Path:
    pure = PurePosixPath(rel)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise WikiError(f"Unsafe candidate path: {rel!r}")
    return root.joinpath(*pure.parts)


def materialize_base_tree(source: Path, base: str, destination: Path) -> dict[str, TreeEntry]:
    entries = exact_tree_entries(source, base)
    environment = os.environ.copy()
    environment.setdefault("GIT_TERMINAL_PROMPT", "0")
    process = subprocess.Popen(
        ["git", "--literal-pathspecs", "-C", str(source), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        for rel, entry in entries.items():
            if entry.object_type != "blob":
                raise WikiError(
                    f"Unsupported non-file Git tree entry: {rel}",
                    code=EXIT_CONFLICT,
                )
            process.stdin.write(entry.oid.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().decode("ascii", "replace").strip().split()
            if len(header) != 3 or header[1] != "blob" or not header[2].isdigit():
                raise WikiError(
                    f"Git could not read candidate base file: {rel}",
                    code=EXIT_CONFLICT,
                )
            remaining = int(header[2])
            target = candidate_path(destination, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                while remaining:
                    chunk = process.stdout.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise WikiError(
                            f"Git returned incomplete candidate base bytes: {rel}",
                            code=EXIT_CONFLICT,
                        )
                    handle.write(chunk)
                    remaining -= len(chunk)
            if process.stdout.read(1) != b"\n":
                raise WikiError(
                    f"Git returned an invalid candidate base record: {rel}",
                    code=EXIT_CONFLICT,
                )
        process.stdin.close()
        return_code = process.wait()
        if return_code != 0:
            stderr = process.stderr.read().decode("utf-8", "replace").strip()
            raise WikiError(
                f"Git could not materialize the candidate base: {stderr or 'unknown error'}",
                code=EXIT_CONFLICT,
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    return entries


def capture_file(source: Path, *, rel: str) -> bytes:
    """Capture one stable worktree byte sequence without applying Git filters."""

    for _attempt in range(2):
        try:
            before = source.stat()
            data = source.read_bytes()
            after = source.stat()
        except FileNotFoundError:
            if not os.path.lexists(source):
                raise WikiError(
                    f"The included file changed while it was being captured: {rel}",
                    code=EXIT_CONFLICT,
                    next_step="Retry save after the writer has finished.",
                )
            continue
        before_signature = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_signature = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_signature == after_signature:
            return data
    raise WikiError(
        f"The included file changed while it was being captured: {rel}",
        code=EXIT_CONFLICT,
        next_step="Retry save after the writer has finished.",
    )


def update_candidate_index(
    repository: Path,
    rel: str,
    base_entries: dict[str, TreeEntry],
) -> None:
    path = candidate_path(repository, rel)
    if not path.is_file():
        run_git(repository, ["update-index", "--force-remove", "--", rel])
        return
    oid = run_git(
        repository,
        ["hash-object", "--no-filters", "-w", "--", str(path)],
    ).stdout.strip()
    previous = base_entries.get(rel)
    mode = previous.mode if previous is not None and previous.object_type == "blob" else "100644"
    run_git(repository, ["update-index", "--add", "--cacheinfo", mode, oid, rel])


def candidate_raw_review(
    repository: Path,
    base: str,
    candidate: str,
    scope: set[str],
    operation: str,
    worktree_impact: dict[str, Any] | None,
    worktree_dirty: set[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    impact = raw_impact_between_revisions(repository, base, candidate)
    findings: list[dict[str, Any]] = []
    normalized = operation.strip().casefold().replace("_", "-")
    if normalized == "raw-update" and worktree_impact is not None:
        missing_raw = sorted(
            {item["path"] for item in worktree_impact["changes"]} - scope
        )
        impacted_pages = {item["path"] for item in worktree_impact["pages"]}
        missing_pages = sorted((worktree_dirty & impacted_pages) - scope)
        for rel in [*missing_raw, *missing_pages]:
            findings.append(
                finding(
                    "E_RAW_REVIEW_SCOPE",
                    rel,
                    "raw-update must include every changed raw and every changed page in its impact graph",
                )
            )
    if impact is None:
        return (worktree_impact if findings else None), findings
    existing_changes = [
        item
        for item in impact["changes"]
        if item["status"] in {"modified", "deleted"}
    ]
    if existing_changes and normalized != "raw-update":
        findings.append(
            finding(
                "E_RAW_OPERATION",
                existing_changes[0]["path"],
                "modifying or deleting committed raw requires operation raw-update",
            )
        )
    if existing_changes:
        missing_sources = sorted(set(impact["owner_sources"]) - scope)
        if missing_sources:
            findings.append(
                finding(
                    "E_RAW_REVIEW_SCOPE",
                    missing_sources[0],
                    "raw-update must explicitly include every original owner source for review",
                )
            )
    return impact, findings


def candidate_tag_policy_findings(
    source: Path,
    base: str,
    candidate_repository: Path,
    candidate: str,
) -> list[dict[str, Any]]:
    base_entries = exact_tree_entries(source, base)
    candidate_entries = exact_tree_entries(candidate_repository, candidate)
    if TAG_POLICY_PATH in base_entries and TAG_POLICY_PATH not in candidate_entries:
        return [
            finding(
                "E_TAG_POLICY",
                TAG_POLICY_PATH,
                "a tag policy tracked by the base checkpoint cannot be deleted",
            )
        ]
    return []


def candidate_tag_operation_findings(
    repository: Path,
    base: str,
    candidate: str,
    operation: str,
    changes: Sequence[str],
) -> list[dict[str, Any]]:
    """Keep the two approved tag checkpoints mechanically separate."""

    normalized = operation.strip().casefold().replace("_", "-")
    if normalized == "tag-policy":
        unexpected = sorted(path for path in changes if path != TAG_POLICY_PATH)
        if unexpected:
            return [
                finding(
                    "E_TAG_CHECKPOINT",
                    unexpected[0],
                    "tag-policy checkpoints may change only tags-review.csv",
                )
            ]
        return []
    if normalized != "tag-maintenance":
        return []

    page_changes = sorted(path for path in changes if path != "index.csv")
    if not page_changes and "index.csv" in changes:
        return [
            finding(
                "E_TAG_CHECKPOINT",
                "index.csv",
                "tag-maintenance cannot be used for an index-only checkpoint",
            )
        ]
    for rel in page_changes:
        if not is_indexable_rel(rel):
            return [
                finding(
                    "E_TAG_CHECKPOINT",
                    rel,
                    "tag-maintenance checkpoints may change only indexable Markdown pages",
                )
            ]
        before = revision_blob_bytes(repository, base, rel)
        after = revision_blob_bytes(repository, candidate, rel)
        if before is None or after is None:
            return [
                finding(
                    "E_TAG_CHECKPOINT",
                    rel,
                    "tag-maintenance cannot create or delete pages",
                )
            ]
        try:
            before_text = before.decode("utf-8")
            after_text = after.decode("utf-8")
        except UnicodeDecodeError:
            return [
                finding(
                    "E_TAG_CHECKPOINT",
                    rel,
                    "tag-maintenance pages must remain valid UTF-8",
                )
            ]
        before_values, _before_body, before_errors = parse_frontmatter_text(before_text)
        after_values, _after_body, after_errors = parse_frontmatter_text(after_text)
        before_tags = before_values.get("tags", [])
        after_tags = after_values.get("tags", [])
        if (
            before_errors
            or after_errors
            or not isinstance(before_tags, list)
            or not isinstance(after_tags, list)
            or any(not isinstance(item, str) for item in before_tags)
            or any(not isinstance(item, str) for item in after_tags)
        ):
            return [
                finding(
                    "E_TAG_CHECKPOINT",
                    rel,
                    "tag-maintenance requires valid tags metadata in both checkpoints",
                )
            ]
        old_tags = ordered_strings(before_tags)
        new_tags = ordered_strings(after_tags)
        if old_tags == new_tags:
            return [
                finding(
                    "E_TAG_CHECKPOINT",
                    rel,
                    "tag-maintenance page changes must modify tags",
                )
            ]
        expected = replace_frontmatter_list(before_text, "tags", new_tags).encode("utf-8")
        if after != expected:
            return [
                finding(
                    "E_TAG_CHECKPOINT",
                    rel,
                    "tag-maintenance page changes must be limited to the top-level tags field",
                )
            ]
    return []


def revision_text(repository: Path, revision: str, rel: str) -> str | None:
    result = run_git(repository, ["show", f"{revision}:{rel}"], check=False, binary=True)
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def candidate_structural_risks(
    repository: Path,
    base: str,
    candidate: str,
    operation: str,
    changes: Sequence[str],
) -> tuple[str, ...]:
    normalized = operation.strip().casefold().replace("_", "-")
    risks = [f"operation:{normalized}"] if normalized in HIGH_RISK_OPERATIONS else []
    base_entries = exact_tree_entries(repository, base)
    candidate_entries = exact_tree_entries(repository, candidate)
    if base_entries.get(TAG_POLICY_PATH) != candidate_entries.get(TAG_POLICY_PATH):
        risks.append(f"{TAG_POLICY_PATH}: changed persistent tag policy")
    deleted = sorted(
        rel for rel in changes if rel != "index.csv" and rel in base_entries and rel not in candidate_entries
    )
    if deleted:
        risks.append("tracked deletion: " + ", ".join(deleted))
    for rel in changes:
        if not is_indexable_rel(rel):
            continue
        previous_text = revision_text(repository, base, rel)
        current_text = revision_text(repository, candidate, rel)
        if previous_text is None or current_text is None:
            continue
        old_values, _old_body, _old_errors = parse_frontmatter_text(previous_text)
        new_values, _new_body, _new_errors = parse_frontmatter_text(current_text)
        for field in ("raw", "sources"):
            old = stable_strings(
                old_values.get(field, []) if isinstance(old_values.get(field, []), list) else []
            )
            new = stable_strings(
                new_values.get(field, []) if isinstance(new_values.get(field, []), list) else []
            )
            if old != new:
                risks.append(f"{rel}: changed {field} relationship")
    return tuple(risks)


def build_candidate_checkpoint(
    vault: Path,
    base: str,
    scope: set[str],
    operation: str,
    temporary_root: Path,
    worktree_impact: dict[str, Any] | None,
    worktree_dirty: set[str],
) -> CandidateCheckpoint:
    repository = temporary_root / "candidate"
    disabled_hooks = temporary_root / "disabled-hooks"
    disabled_hooks.mkdir()
    run_git(
        None,
        [
            "-c",
            f"init.templateDir={disabled_hooks}",
            "-c",
            f"core.hooksPath={disabled_hooks}",
            "clone",
            "--shared",
            "--no-checkout",
            "--quiet",
            str(vault),
            str(repository),
        ],
    )
    run_git(repository, ["config", "core.hooksPath", str(disabled_hooks)])
    base_entries = materialize_base_tree(vault, base, repository)
    run_git(repository, ["read-tree", base])

    for rel in sorted(scope - {"index.csv"}):
        _canonical, source = safe_rel(vault, rel, label="included path")
        target = candidate_path(repository, rel)
        if os.path.lexists(source):
            validate_discovered_path(vault, source, label="included path")
            if not source.is_file():
                raise WikiError(f"Included path is not a file: {rel}")
            data = capture_file(source, rel=rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        elif os.path.lexists(target):
            target.unlink()

    records, _record_findings = collect_records(repository, strict=True)
    index_bytes = build_index_bytes(records)
    candidate_index = repository / "index.csv"
    candidate_index.write_bytes(index_bytes)
    current_index = (vault / "index.csv").read_bytes() if (vault / "index.csv").is_file() else None

    for rel in sorted(scope | {"index.csv"}):
        update_candidate_index(repository, rel, base_entries)
    tree = run_git(repository, ["write-tree"]).stdout.strip()
    base_tree = run_git(repository, ["rev-parse", f"{base}^{{tree}}"]).stdout.strip()
    if tree == base_tree:
        commit = base
    else:
        commit = run_git(
            repository,
            ["commit-tree", tree, "-p", base, "-m", save_message(operation)],
            commit_identity=True,
        ).stdout.strip()
        run_git(repository, ["update-ref", "HEAD", commit, base])

    source_git_dir = Path(run_git(vault, ["rev-parse", "--absolute-git-dir"]).stdout.strip())
    changes = exact_tree_changes(repository, base, commit)
    findings = audit_findings(repository, attributes_git_dir=source_git_dir)
    raw_impact, raw_findings = candidate_raw_review(
        repository,
        base,
        commit,
        scope,
        operation,
        worktree_impact,
        worktree_dirty,
    )
    findings.extend(raw_findings)
    findings.extend(candidate_tag_policy_findings(vault, base, repository, commit))
    findings.extend(
        candidate_tag_operation_findings(repository, base, commit, operation, changes)
    )
    findings = sorted(findings, key=lambda item: (item["path"], item["code"], item["message"]))
    risks = candidate_structural_risks(repository, base, commit, operation, changes)
    diff = run_git(
        repository,
        ["diff", "--no-ext-diff", "--no-renames", base, commit, "--"],
        check=False,
    ).stdout
    return CandidateCheckpoint(
        repository=repository,
        commit=commit,
        tree=tree,
        changes=changes,
        risks=risks,
        index_bytes=index_bytes,
        index_changed=current_index != index_bytes,
        original_index_bytes=current_index,
        git_index_bytes=repository_index_path(repository).read_bytes(),
        findings=tuple(findings),
        diff=diff,
        raw_impact=raw_impact,
    )


def save_message(operation: str) -> str:
    clean = re.sub(r"[\r\n]+", " ", operation).strip()
    return f"wiki: {clean[:70]}"


def install_candidate_checkpoint(
    vault: Path,
    base: str,
    checkpoint: CandidateCheckpoint,
    temporary_root: Path,
    original_git_index: bytes,
) -> None:
    """Install an audited tree with a ref CAS and an index-file transaction."""

    disabled_hooks = temporary_root / "disabled-hooks"
    disabled_hooks.mkdir(exist_ok=True)
    if head_oid(vault) != base:
        raise WikiError(
            "The wiki HEAD changed while the save candidate was being reviewed.",
            code=EXIT_CONFLICT,
            next_step="Run begin again and review the new base before retrying.",
        )
    run_git(
        vault,
        [
            "-c",
            f"core.hooksPath={disabled_hooks}",
            "fetch",
            "--quiet",
            "--no-write-fetch-head",
            str(checkpoint.repository),
            "HEAD",
        ],
    )
    if head_oid(vault) != base:
        raise WikiError(
            "The wiki HEAD changed while the save candidate was being imported.",
            code=EXIT_CONFLICT,
            next_step="Run begin again and review the new base before retrying.",
        )

    main_index_path = repository_index_path(vault)
    index_lock = Path(str(main_index_path) + ".lock")
    index_lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(index_lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise WikiError(
            "The Git index is locked by another operation.",
            code=EXIT_CONFLICT,
            next_step="Wait for the other Git operation to finish, then run begin again.",
        ) from exc
    try:
        try:
            current_git_index = main_index_path.read_bytes()
        except FileNotFoundError:
            current_git_index = b""
        if current_git_index != original_git_index:
            raise WikiError(
                "The Git index changed while the save candidate was being reviewed.",
                code=EXIT_CONFLICT,
                next_step="Preserve the staged work, run begin again, and retry with a new base.",
            )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(checkpoint.git_index_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        updated = run_git(
            vault,
            [
                "-c",
                f"core.hooksPath={disabled_hooks}",
                "update-ref",
                "HEAD",
                checkpoint.commit,
                base,
            ],
            check=False,
        )
        if updated.returncode != 0:
            raise WikiError(
                "The wiki HEAD changed before the audited candidate could be saved.",
                code=EXIT_CONFLICT,
                next_step="Run begin again and review the new base before retrying.",
            )
        os.replace(index_lock, main_index_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(index_lock):
            os.unlink(index_lock)

    try:
        current_worktree_index = (vault / "index.csv").read_bytes()
    except FileNotFoundError:
        current_worktree_index = None
    if current_worktree_index == checkpoint.original_index_bytes:
        atomic_write(vault / "index.csv", checkpoint.index_bytes)


def _command_save_with_hooks_disabled(args: argparse.Namespace) -> int:
    vault = vault_from_cwd()
    ensure_wiki_contract(vault)
    base = verify_base(vault, args.base)
    operation = args.operation.strip()
    if not operation:
        raise WikiError("--operation must be non-empty.")
    dirty, staged, _untracked = dirty_path_sets(vault)
    if staged:
        raise WikiError(
            "save requires an empty Git index; pre-existing staged changes are never replaced.",
            code=EXIT_CONFLICT,
            next_step="Review and unstage the existing index state, then run begin again.",
            details={"staged": sorted(staged)},
        )
    main_index_path = repository_index_path(vault)
    try:
        original_git_index = main_index_path.read_bytes()
    except FileNotFoundError:
        original_git_index = b""
    requested = flatten_includes(args.include)
    if not requested:
        raise WikiError("save requires at least one explicit --include path.")
    scope = expand_include_scope(vault, requested, dirty)
    worktree_impact = raw_impact_from_worktree(vault, base)
    with tempfile.TemporaryDirectory(prefix="llm-wiki-save-") as temporary:
        checkpoint = build_candidate_checkpoint(
            vault,
            base,
            scope,
            operation,
            Path(temporary),
            worktree_impact,
            dirty,
        )
        if checkpoint.findings:
            emit(
                {
                    "ok": False,
                    "command": "save",
                    "vault": str(vault),
                    "base": base,
                    "saved": False,
                    "review_required": False,
                    "index_changed": checkpoint.index_changed,
                    "findings": list(checkpoint.findings),
                    "raw_impact": checkpoint.raw_impact,
                    "next": "Correct the findings; the visible pending changes were preserved.",
                }
            )
            return EXIT_AUDIT

        if checkpoint.risks and not args.approved:
            emit(
                {
                    "ok": False,
                    "command": "save",
                    "vault": str(vault),
                    "base": base,
                    "saved": False,
                    "review_required": True,
                    "risks": list(checkpoint.risks),
                    "changes": list(checkpoint.changes),
                    "diff": checkpoint.diff,
                    "raw_impact": checkpoint.raw_impact,
                    "next": "Review the diff, then repeat save with --approved if it matches the user's intent.",
                }
            )
            return EXIT_REVIEW

        if checkpoint.commit == base:
            if checkpoint.index_changed:
                try:
                    current_worktree_index = (vault / "index.csv").read_bytes()
                except FileNotFoundError:
                    current_worktree_index = None
                if current_worktree_index == checkpoint.original_index_bytes:
                    atomic_write(vault / "index.csv", checkpoint.index_bytes)
            emit(
                {
                    "ok": True,
                    "command": "save",
                    "vault": str(vault),
                    "base": base,
                    "saved": False,
                    "review_required": False,
                    "commit": base,
                    "changes": [],
                    "index_changed": checkpoint.index_changed,
                    "raw_impact": checkpoint.raw_impact,
                    "next": "No checkpoint was needed because the selected candidate already matches HEAD.",
                }
            )
            return EXIT_OK

        install_candidate_checkpoint(
            vault,
            base,
            checkpoint,
            Path(temporary),
            original_git_index,
        )
        emit(
            {
                "ok": True,
                "command": "save",
                "vault": str(vault),
                "base": base,
                "saved": True,
                "review_required": False,
                "approved": bool(args.approved),
                "risks": list(checkpoint.risks),
                "commit": checkpoint.commit,
                "changes": list(checkpoint.changes),
                "index_changed": checkpoint.index_changed,
                "raw_impact": checkpoint.raw_impact,
                "clean": not dirty_path_sets(vault)[0],
            }
        )
        return EXIT_OK


def command_save(args: argparse.Namespace) -> int:
    global _GIT_HOOKS_PATH_OVERRIDE

    with tempfile.TemporaryDirectory(prefix="llm-wiki-hooks-") as temporary:
        disabled_hooks = Path(temporary) / "disabled-hooks"
        disabled_hooks.mkdir()
        previous = _GIT_HOOKS_PATH_OVERRIDE
        _GIT_HOOKS_PATH_OVERRIDE = str(disabled_hooks)
        try:
            return _command_save_with_hooks_disabled(args)
        finally:
            _GIT_HOOKS_PATH_OVERRIDE = previous


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        description="Manage a Git-backed LLM Wiki vault with deterministic filesystem and index operations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser(
        "init",
        help="Create and checkpoint a new wiki vault.",
        description="Create a new wiki in an empty directory and record its initial Git checkpoint.",
    )
    init.add_argument("vault", help="Empty target directory to initialize as the dedicated wiki worktree.")
    init.add_argument("--name", required=True, help="Root home page filename and H1, without .md.")
    init.add_argument("--home-summary", required=True, help="Retrieval summary for the home MOC.")
    init.set_defaults(func=command_init)

    begin = subparsers.add_parser(
        "begin",
        help="Inspect HEAD and pending work without writing.",
        description="Inspect the current Git HEAD and visible changes, return the base OID, and report a layered impact graph when raw evidence changed.",
    )
    begin.set_defaults(func=command_begin)

    add = subparsers.add_parser(
        "add",
        help="Copy raw inputs and create a pending source skeleton.",
        description="Copy input files into Git-versioned raw storage and create or extend a source-page draft.",
    )
    add.add_argument("inputs", nargs="+", help="Source material files to copy byte-for-byte into raw/.")
    add.add_argument("--base", required=True, help="Git commit OID returned by the most recent begin command.")
    add.add_argument("--name", required=True, help="Source page filename and H1, without .md.")
    add.add_argument(
        "--identifier",
        action="append",
        help="Optional source identity in KEY=VALUE form; repeat for multiple identifiers.",
    )
    add.add_argument(
        "--parent",
        action="append",
        help="Optional existing parent source-page reference; repeat for multiple parents.",
    )
    add.add_argument("--raw-dir", help="Optional directory name below raw/; defaults to --name.")
    add.set_defaults(func=command_add)

    context = subparsers.add_parser(
        "context",
        help="Query index.csv with a structured QueryPlan.",
        description="Query index.csv and rank candidate pages without writing to the vault.",
    )
    context.add_argument("--plan", required=True, help="JSON QueryPlan compiled from the user's question.")
    context.set_defaults(func=command_context)

    tags = subparsers.add_parser(
        "tags",
        help="Use and maintain a user-approved persistent tag policy.",
        description="Use the persistent tag vocabulary or run the manually triggered tag review workflow.",
    )
    tag_actions = tags.add_subparsers(dest="tags_action", required=True)
    tags_vocabulary = tag_actions.add_parser(
        "vocabulary",
        help="Return preferred, forbidden, and renamed tags.",
        description="Read the persistent tag decision ledger and return its vocabulary without writing.",
    )
    tags_vocabulary.set_defaults(func=command_tags_vocabulary)
    tags_check = tag_actions.add_parser(
        "check",
        help="Check proposed page tags against the persistent policy.",
        description="Reject forbidden or noncanonical proposed tags and report genuinely new tags without writing.",
    )
    tags_check.add_argument(
        "--tags-json",
        required=True,
        help="JSON array of final tag strings proposed for one page.",
    )
    tags_check.set_defaults(func=command_tags_check)
    tags_collect = tag_actions.add_parser(
        "collect",
        help="Collect the current tag inventory into a review CSV.",
        description="Collect tags into a review CSV without changing Markdown, index.csv, Git HEAD, or the Git index.",
    )
    tags_collect.add_argument(
        "--base",
        required=True,
        help="Clean Git commit OID returned by begin; HEAD and the worktree must still match it.",
    )
    tags_collect.add_argument(
        "--output",
        help="Optional new CSV path outside the vault; defaults to a unique review CSV in the vault root.",
    )
    tags_collect.set_defaults(func=command_tags_collect)
    tags_apply = tag_actions.add_parser(
        "apply",
        help="Apply a reviewed tag normalization CSV to page frontmatter.",
        description="Validate a reviewed tag plan against a clean base and update only affected page tags.",
    )
    tags_apply.add_argument(
        "--base",
        required=True,
        help="The same clean Git commit OID used to collect the reviewed tag plan.",
    )
    tags_apply.add_argument(
        "--plan",
        required=True,
        help="Reviewed CSV created by tags collect.",
    )
    tags_apply.add_argument(
        "--amendments",
        help="Optional reviewed CSV updating historical policy rows absent from the current plan.",
    )
    tags_apply.add_argument(
        "--approved",
        action="store_true",
        help="Confirm that the user reviewed and approved the complete tag plan.",
    )
    tags_apply.set_defaults(func=command_tags_apply)
    tags_merge = tag_actions.add_parser(
        "merge",
        help="Merge reviewed decisions into the persistent tag policy.",
        description="Verify the matching page checkpoint, preview the policy change, and atomically update tags-review.csv after approval.",
    )
    tags_merge.add_argument(
        "--base",
        required=True,
        help="Clean page-checkpoint OID returned by begin after the page tag changes were saved.",
    )
    tags_merge.add_argument(
        "--plan",
        required=True,
        help="Reviewed CSV previously applied to the matching page checkpoint.",
    )
    tags_merge.add_argument(
        "--amendments",
        help="Optional reviewed CSV updating historical policy rows absent from the current plan.",
    )
    tags_merge.add_argument(
        "--approved",
        action="store_true",
        help="Confirm that the user reviewed and approved the complete policy merge.",
    )
    tags_merge.set_defaults(func=command_tags_merge)

    audit = subparsers.add_parser(
        "audit",
        help="Run the authoritative read-only wiki health check.",
        description="Run the read-only health check for repository structure, pages, links, raw sources, and index.csv.",
    )
    audit.add_argument(
        "--scope",
        choices=("changed", "all"),
        default="all",
        help="Use all for the complete health check, or changed for changed-path diagnostics plus global structure findings.",
    )
    audit.add_argument(
        "--format",
        choices=("json", "text", "csv"),
        default="json",
        help="Report as JSON for agents, concise text for people, or CSV for structured processing.",
    )
    audit.set_defaults(func=command_audit)

    save = subparsers.add_parser(
        "save",
        help="Rebuild index.csv, audit, and create an explicitly scoped checkpoint.",
        description="Validate an explicit change scope, rebuild index.csv, audit, and create one Git checkpoint.",
    )
    save.add_argument("--base", required=True, help="Git commit OID returned by the most recent begin command.")
    save.add_argument(
        "--operation",
        required=True,
        help="Short semantic label used for risk detection and the Git checkpoint message; use raw-update for committed raw changes.",
    )
    save.add_argument(
        "--include",
        nargs="+",
        action="append",
        required=True,
        help="Explicit changed path or directory scope; use index.csv for an index-only repair or no-op check.",
    )
    save.add_argument(
        "--approved",
        action="store_true",
        help="Confirm that the user reviewed and approved the reported high-risk structural change.",
    )
    save.set_defaults(func=command_save)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", "unknown")
    try:
        return int(args.func(args))
    except WikiError as exc:
        reported_vault = (
            Path(args.vault).expanduser().resolve()
            if command == "init" and hasattr(args, "vault")
            else vault_from_cwd()
        )
        payload: dict[str, Any] = {
            "ok": False,
            "command": command,
            "vault": str(reported_vault),
            "error": str(exc),
        }
        payload.update(exc.details)
        if exc.next_step:
            payload["next"] = exc.next_step
        emit(payload)
        return exc.code
    except (OSError, ValueError) as exc:
        reported_vault = (
            Path(args.vault).expanduser().resolve()
            if command == "init" and hasattr(args, "vault")
            else vault_from_cwd()
        )
        emit(
            {
                "ok": False,
                "command": command,
                "vault": str(reported_vault),
                "error": str(exc),
                "next": "Inspect the visible worktree state, correct the input, and retry.",
            }
        )
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
