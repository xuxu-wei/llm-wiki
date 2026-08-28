from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "llm-wiki"
SCRIPT_PATH = SKILL_DIR / "scripts" / "wiki.py"


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def diagnostic(self) -> str:
        command = " ".join(self.argv)
        return (
            f"command: {command}\n"
            f"exit: {self.returncode}\n"
            f"stdout:\n{self.stdout}\n"
            f"stderr:\n{self.stderr}"
        )

    def json(self) -> dict[str, Any]:
        try:
            value = json.loads(self.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"CLI stdout is not one JSON object: {exc}\n{self.diagnostic()}"
            ) from exc
        if not isinstance(value, dict):
            raise AssertionError(
                f"CLI stdout must be a JSON object, got {type(value).__name__}\n"
                f"{self.diagnostic()}"
            )
        return value


def command_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "GIT_AUTHOR_NAME": "LLM Wiki Tests",
            "GIT_AUTHOR_EMAIL": "llm-wiki-tests@example.invalid",
            "GIT_COMMITTER_NAME": "LLM Wiki Tests",
            "GIT_COMMITTER_EMAIL": "llm-wiki-tests@example.invalid",
        }
    )
    if extra:
        env.update(extra)
    return env


def run_cli(
    *arguments: str,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> CommandResult:
    argv = (sys.executable, str(SCRIPT_PATH), *arguments)
    completed = subprocess.run(
        argv,
        cwd=cwd or REPO_ROOT,
        env=command_environment(extra_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return CommandResult(argv, completed.returncode, completed.stdout, completed.stderr)


def run_git(
    vault: Path,
    *arguments: str,
    text: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=vault,
        env=command_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed with {completed.returncode}:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def git_head(vault: Path) -> str:
    result = run_git(vault, "rev-parse", "HEAD")
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def git_status(vault: Path) -> list[str]:
    result = run_git(vault, "status", "--porcelain=v1", "--untracked-files=all")
    assert isinstance(result.stdout, str)
    return [line for line in result.stdout.splitlines() if line]


def git_show_bytes(vault: Path, revision_path: str) -> bytes:
    result = run_git(vault, "show", revision_path, text=False)
    assert isinstance(result.stdout, bytes)
    return result.stdout


def git_index_bytes(vault: Path, relative_path: str) -> bytes:
    return git_show_bytes(vault, f":{relative_path}")


def json_list(values: list[str] | tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def page_text(
    title: str,
    kind: str,
    *,
    summary: str | None = None,
    aliases: list[str] | None = None,
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    raw: list[str] | None = None,
    body: str = "正文。",
    extra_properties: dict[str, str] | None = None,
) -> str:
    lines = ["---", f"kind: {kind}"]
    if summary is not None:
        lines.append(f"summary: {json.dumps(summary, ensure_ascii=False)}")
    if aliases is not None:
        lines.append(f"aliases: {json_list(aliases)}")
    if tags is not None:
        lines.append(f"tags: {json_list(tags)}")
    if sources is not None:
        lines.append(f"sources: {json_list(sources)}")
    if raw is not None:
        lines.append(f"raw: {json_list(raw)}")
    for key, value in (extra_properties or {}).items():
        lines.append(f"{key}: {value}")
    lines.extend(["---", f"# {title}", "", body, ""])
    return "\n".join(lines)


def write_page(
    vault: Path,
    relative_path: str,
    title: str,
    kind: str,
    **kwargs: Any,
) -> Path:
    path = vault / Path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page_text(title, kind, **kwargs), encoding="utf-8", newline="\n")
    return path


def set_frontmatter_scalar(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError(f"{path} has no frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise AssertionError(f"{path} has unterminated frontmatter") from exc
    replacement = f"{key}: {json.dumps(value, ensure_ascii=False)}"
    for index in range(1, end):
        if lines[index].split(":", 1)[0].strip() == key:
            lines[index] = replacement
            break
    else:
        lines.insert(end, replacement)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def read_index(vault: Path) -> tuple[list[str], list[dict[str, str]]]:
    with (vault / "index.csv").open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def snapshot_files(root: Path, *, include_git: bool = False) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if not include_git and relative.parts and relative.parts[0] == ".git":
            continue
        snapshot[relative.as_posix()] = path.read_bytes()
    return snapshot


def candidate_paths(payload: dict[str, Any]) -> list[str]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise AssertionError(f"context payload has no candidate list: {payload!r}")
    paths: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("path"), str):
            raise AssertionError(f"invalid context candidate: {candidate!r}")
        paths.append(candidate["path"])
    return paths


def any_nested_key(value: Any, key: str, expected: Any) -> bool:
    if isinstance(value, dict):
        if value.get(key) == expected:
            return True
        return any(any_nested_key(item, key, expected) for item in value.values())
    if isinstance(value, list):
        return any(any_nested_key(item, key, expected) for item in value)
    return False
