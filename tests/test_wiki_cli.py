from __future__ import annotations

import csv
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.wiki_support import (
    SCRIPT_PATH,
    any_nested_key,
    candidate_paths,
    git_head,
    git_show_bytes,
    git_status,
    json_list,
    page_text,
    read_index,
    run_cli,
    run_git,
    set_frontmatter_scalar,
    snapshot_files,
    write_page,
)


INDEX_HEADER = ["path", "kind", "summary", "aliases", "tags"]
TAG_PLAN_HEADER = ["tag", "page_count", "action", "target"]
TAG_POLICY_NAME = "tags-review.csv"
SPREADSHEET_FORMULA_PREFIXES = frozenset("=+-@\t\r\n")


def tag_plan_cell(value: str) -> str:
    if value.startswith("'") or value[:1] in SPREADSHEET_FORMULA_PREFIXES:
        return "'" + value
    return value


def read_tag_plan(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_tag_plan(
    path: Path,
    rows: list[dict[str, str]],
    *,
    header: list[str] | None = None,
) -> None:
    fieldnames = header or TAG_PLAN_HEADER
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def decide_tag(
    path: Path,
    tag: str,
    action: str,
    target: str = "",
) -> None:
    header, rows = read_tag_plan(path)
    if header != TAG_PLAN_HEADER:
        raise AssertionError(f"unexpected tag plan header: {header!r}")
    matches = [row for row in rows if row["tag"] == tag_plan_cell(tag)]
    if len(matches) != 1:
        raise AssertionError(f"expected one tag-plan row for {tag!r}: {rows!r}")
    matches[0]["action"] = action
    matches[0]["target"] = target
    write_tag_plan(path, rows)


def load_wiki_runtime():
    module_name = "llm_wiki_runtime_for_save_tests"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load wiki runtime")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class WikiCliTestCase(unittest.TestCase):
    maxDiff = None

    def assert_exit(self, result, expected: int) -> dict[str, object]:
        self.assertEqual(result.returncode, expected, result.diagnostic())
        return result.json()

    def assert_envelope(
        self,
        payload: dict[str, object],
        command: str,
        vault: Path,
        *,
        ok: bool,
    ) -> None:
        self.assertIs(payload.get("ok"), ok, payload)
        self.assertEqual(payload.get("command"), command, payload)
        reported_vault = payload.get("vault")
        self.assertIsInstance(reported_vault, str, payload)
        assert isinstance(reported_vault, str)
        self.assertEqual(Path(reported_vault).resolve(), vault.resolve())

    def init_vault(self, root: Path, *, name: str = "首页") -> Path:
        vault = root / "vault"
        result = run_cli(
            "init",
            str(vault),
            "--name",
            name,
            "--home-summary",
            "面向检索与综合的知识入口。",
            cwd=root,
        )
        payload = self.assert_exit(result, 0)
        self.assert_envelope(payload, "init", vault, ok=True)
        return vault

    def save(
        self,
        vault: Path,
        base: str,
        *,
        operation: str = "add",
        include: list[str],
        approved: bool = False,
        expected: int = 0,
    ) -> dict[str, object]:
        arguments = ["save", "--base", base, "--operation", operation]
        arguments.extend(["--include", *include])
        if approved:
            arguments.append("--approved")
        result = run_cli(*arguments, cwd=vault)
        payload = self.assert_exit(result, expected)
        self.assert_envelope(payload, "save", vault, ok=expected == 0)
        return payload

    def collect_tags(
        self,
        vault: Path,
        base: str,
        *,
        output: Path | None = None,
        expected: int = 0,
    ) -> tuple[dict[str, object], Path | None]:
        arguments = ["tags", "collect", "--base", base]
        if output is not None:
            arguments.extend(["--output", str(output)])
        result = run_cli(*arguments, cwd=vault)
        payload = self.assert_exit(result, expected)
        self.assert_envelope(payload, "tags", vault, ok=expected == 0)
        if expected == 0 or "action" in payload:
            self.assertEqual(payload.get("action"), "collect", payload)
        if expected != 0:
            return payload, None
        plan_value = payload.get("plan")
        self.assertIsInstance(plan_value, str, payload)
        assert isinstance(plan_value, str)
        plan = Path(plan_value)
        self.assertTrue(plan.is_absolute(), plan)
        self.assertTrue(plan.is_file(), plan)
        return payload, plan

    def apply_tags(
        self,
        vault: Path,
        base: str,
        plan: Path,
        *,
        amendments: Path | None = None,
        approved: bool = False,
        expected: int = 0,
    ) -> dict[str, object]:
        arguments = ["tags", "apply", "--base", base, "--plan", str(plan)]
        if amendments is not None:
            arguments.extend(["--amendments", str(amendments)])
        if approved:
            arguments.append("--approved")
        result = run_cli(*arguments, cwd=vault)
        payload = self.assert_exit(result, expected)
        self.assert_envelope(payload, "tags", vault, ok=expected == 0)
        if expected == 0 or "action" in payload:
            self.assertEqual(payload.get("action"), "apply", payload)
        return payload

    def tag_vocabulary(
        self,
        vault: Path,
        *,
        expected: int = 0,
    ) -> dict[str, object]:
        payload = self.assert_exit(run_cli("tags", "vocabulary", cwd=vault), expected)
        self.assert_envelope(payload, "tags", vault, ok=expected == 0)
        if expected == 0 or "action" in payload:
            self.assertEqual(payload.get("action"), "vocabulary", payload)
        return payload

    def check_tags(
        self,
        vault: Path,
        tags: list[str],
        *,
        expected: int = 0,
    ) -> dict[str, object]:
        payload = self.assert_exit(
            run_cli(
                "tags",
                "check",
                "--tags-json",
                json.dumps(tags, ensure_ascii=False),
                cwd=vault,
            ),
            expected,
        )
        self.assert_envelope(payload, "tags", vault, ok=expected == 0)
        if expected == 0 or "action" in payload:
            self.assertEqual(payload.get("action"), "check", payload)
        return payload

    def merge_tags(
        self,
        vault: Path,
        base: str,
        plan: Path,
        *,
        amendments: Path | None = None,
        approved: bool = False,
        expected: int = 0,
    ) -> dict[str, object]:
        arguments = ["tags", "merge", "--base", base, "--plan", str(plan)]
        if amendments is not None:
            arguments.extend(["--amendments", str(amendments)])
        if approved:
            arguments.append("--approved")
        payload = self.assert_exit(run_cli(*arguments, cwd=vault), expected)
        self.assert_envelope(payload, "tags", vault, ok=expected == 0)
        if expected == 0 or "action" in payload:
            self.assertEqual(payload.get("action"), "merge", payload)
        return payload


class InitAndSchemaTests(WikiCliTestCase):
    def test_help_exposes_the_current_commands(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.diagnostic())
        for command in ("init", "begin", "add", "context", "audit", "save", "tags"):
            self.assertRegex(result.stdout, rf"\b{command}\b")

    def test_init_creates_a_unicode_obsidian_vault_and_clean_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root, name="知识网络")

            for directory in ("inbox", "raw", "sources", "notes", "assets"):
                self.assertTrue((vault / directory).is_dir(), directory)
            for filename in (
                "AGENTS.md",
                "知识网络.md",
                "index.csv",
                TAG_POLICY_NAME,
                ".gitattributes",
                ".gitignore",
            ):
                self.assertTrue((vault / filename).is_file(), filename)

            home = (vault / "知识网络.md").read_text(encoding="utf-8")
            self.assertRegex(home, r"(?m)^kind:\s*[\"']?moc[\"']?\s*$")
            self.assertRegex(home, r"(?m)^summary:\s*.+$")
            self.assertRegex(home, r"(?m)^tags:\s*\[\]\s*$")
            self.assertRegex(home, r"(?m)^# 知识网络\s*$")

            header, rows = read_index(vault)
            self.assertEqual(header, INDEX_HEADER)
            self.assertEqual([row["path"] for row in rows], ["知识网络.md"])
            self.assertEqual(rows[0]["kind"], "moc")
            self.assertEqual(json.loads(rows[0]["aliases"]), [])
            self.assertEqual(json.loads(rows[0]["tags"]), [])
            self.assertNotIn(b"\r\n", (vault / "index.csv").read_bytes())
            policy_bytes = (vault / TAG_POLICY_NAME).read_bytes()
            self.assertTrue(policy_bytes.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(
                read_tag_plan(vault / TAG_POLICY_NAME),
                (TAG_PLAN_HEADER, []),
            )

            self.assertEqual(git_status(vault), [])
            top = run_git(vault, "rev-parse", "--show-toplevel")
            assert isinstance(top.stdout, str)
            self.assertEqual(Path(top.stdout.strip()).resolve(), vault.resolve())
            commits = run_git(vault, "rev-list", "--count", "HEAD")
            self.assertEqual(str(commits.stdout).strip(), "1")
            self.assertRegex((vault / ".gitattributes").read_text(encoding="utf-8"), r"(?m)^raw/\*\*")
            self.assertIn(".obsidian/", (vault / ".gitignore").read_text(encoding="utf-8"))
            self.assertIn("/tags-review-*.csv", (vault / ".gitignore").read_text(encoding="utf-8"))

    def test_init_requires_git_before_writing_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "no-git-vault"
            result = run_cli(
                "init",
                str(vault),
                "--name",
                "首页",
                "--home-summary",
                "摘要",
                cwd=root,
                extra_env={"PATH": ""},
            )
            payload = self.assert_exit(result, 2)
            self.assert_envelope(payload, "init", vault, ok=False)
            self.assertIn("git", json.dumps(payload, ensure_ascii=False).lower())
            self.assertFalse(vault.exists(), "failed init must not leave a partial vault")

    def test_init_refuses_to_overwrite_existing_user_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "existing"
            vault.mkdir()
            agent = vault / "AGENTS.md"
            agent.write_bytes(b"# User contract\r\nKEEP\r\n")
            before = snapshot_files(vault)

            result = run_cli(
                "init",
                str(vault),
                "--name",
                "首页",
                "--home-summary",
                "摘要",
                cwd=root,
            )
            payload = self.assert_exit(result, 2)
            self.assert_envelope(payload, "init", vault, ok=False)
            self.assertEqual(snapshot_files(vault), before)
            self.assertFalse((vault / ".git").exists())


class TagMaintenanceTests(WikiCliTestCase):
    def test_collect_is_read_only_deterministic_and_handles_special_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            home = vault / "首页.md"
            home.write_text(
                home.read_text(encoding="utf-8").replace("tags: []", 'tags: ["MOC标签"]'),
                encoding="utf-8",
                newline="\n",
            )
            source = write_page(
                vault,
                "sources/标签来源.md",
                "标签来源",
                "source",
                summary="覆盖来源页的标签收集。",
                aliases=[],
                tags=["来源标签"],
                sources=[],
                raw=[],
            )
            note = write_page(
                vault,
                "notes/标签样本.md",
                "标签样本",
                "note",
                summary="覆盖标签清单中的 Unicode 和 CSV 特殊字符。",
                aliases=[],
                tags=["AI", "机器学习", "中文,标签", '引"号', "层级/标签", "=1+1", "AI"],
                sources=[],
                body="正文中的 #正文标签 不进入标签清单。",
            )
            inbox = write_page(
                vault,
                "inbox/待整理.md",
                "待整理",
                "inbox",
                tags=["AI"],
                body="等待整理的用户输入。",
            )
            self.save(
                vault,
                git_head(vault),
                include=["首页.md", "sources/标签来源.md", "notes/标签样本.md", "inbox/待整理.md"],
            )
            self.assertTrue(home.is_file())
            self.assertTrue(source.is_file())
            self.assertTrue(note.is_file())
            self.assertTrue(inbox.is_file())
            base = git_head(vault)
            vault_before = snapshot_files(vault)
            index_before = (vault / "index.csv").read_bytes()
            git_index_before = (vault / ".git" / "index").read_bytes()

            first_output = root / "第一次 标签审阅.csv"
            first_payload, first_plan = self.collect_tags(vault, base, output=first_output)
            self.assertEqual(first_plan, first_output.resolve())
            self.assertEqual(first_payload.get("base"), base)
            self.assertEqual(first_payload.get("tag_count"), 8)
            self.assertEqual(first_payload.get("page_count"), 4)
            header, rows = read_tag_plan(first_plan)
            self.assertEqual(header, TAG_PLAN_HEADER)
            self.assertEqual(
                {row["tag"]: row for row in rows},
                {
                    tag_plan_cell(tag): {
                        "tag": tag_plan_cell(tag),
                        "page_count": str(count),
                        "action": "",
                        "target": "",
                    }
                    for tag, count in {
                        "AI": 2,
                        "机器学习": 1,
                        "中文,标签": 1,
                        '引"号': 1,
                        "层级/标签": 1,
                        "=1+1": 1,
                        "MOC标签": 1,
                        "来源标签": 1,
                    }.items()
                },
            )
            self.assertNotIn("正文标签", {row["tag"] for row in rows})
            self.assertTrue(first_plan.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", first_plan.read_bytes())
            self.assertEqual(snapshot_files(vault), vault_before)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual((vault / ".git" / "index").read_bytes(), git_index_before)
            self.assertEqual(git_head(vault), base)
            self.assertEqual(git_status(vault), [])

            second_output = root / "第二次.csv"
            _second_payload, second_plan = self.collect_tags(vault, base, output=second_output)
            self.assertEqual(second_plan.read_bytes(), first_plan.read_bytes())

            existing_before = first_plan.read_bytes()
            self.collect_tags(vault, base, output=first_plan, expected=2)
            self.assertEqual(first_plan.read_bytes(), existing_before)
            inside = vault / "标签审阅.csv"
            self.collect_tags(vault, base, output=inside, expected=2)
            self.assertFalse(inside.exists())
            runtime = load_wiki_runtime()
            for unsafe in (r"\\?\C:\vault\.git\index.lock", r"\\.\C:\device.csv"):
                with self.subTest(unsafe_output=unsafe):
                    with self.assertRaises(runtime.WikiError):
                        runtime.reject_unsafe_external_path(unsafe, option="--output")
            if os.name == "nt":
                index_lock = vault / ".git" / "index.lock"
                extended = Path("\\\\?\\" + str(index_lock))
                self.collect_tags(vault, base, output=extended, expected=2)
                self.assertFalse(index_lock.exists())
            self.assertEqual(snapshot_files(vault), vault_before)

    def test_collect_empty_inventory_creates_only_an_editable_root_review_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            base = git_head(vault)
            before = snapshot_files(vault)
            git_index_before = (vault / ".git" / "index").read_bytes()
            payload, plan = self.collect_tags(vault, base)
            try:
                self.assertEqual(payload.get("tag_count"), 0)
                self.assertEqual(payload.get("page_count"), 0)
                self.assertEqual(plan.parent.resolve(), vault.resolve())
                self.assertRegex(plan.name, r"^tags-review-.+\.csv$")
                self.assertEqual(read_tag_plan(plan), (TAG_PLAN_HEADER, []))
                self.assertTrue(plan.read_bytes().startswith(b"\xef\xbb\xbf"))
                plan_before = plan.read_bytes()
                with plan.open("a", encoding="utf-8", newline="") as handle:
                    handle.write("")
                after = snapshot_files(vault)
                plan_rel = plan.relative_to(vault).as_posix()
                self.assertEqual(set(after) - set(before), {plan_rel})
                self.assertEqual({key: after[key] for key in before}, before)
                self.assertEqual(after[plan_rel], plan_before)
                self.assertEqual(git_head(vault), base)
                self.assertEqual((vault / ".git" / "index").read_bytes(), git_index_before)
                status = run_git(
                    vault,
                    "-c",
                    "core.quotepath=false",
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                )
                self.assertEqual(str(status.stdout).splitlines(), [])

                other = vault / "other-untracked.txt"
                other.write_text("不能随 review CSV 一并豁免。\n", encoding="utf-8", newline="\n")
                conflict_before = snapshot_files(vault)
                self.apply_tags(vault, base, plan, approved=True, expected=3)
                self.assertEqual(snapshot_files(vault), conflict_before)
                other.unlink()

                applied = self.apply_tags(vault, base, plan, approved=True)
                self.assertIs(applied.get("changed"), False, applied)
                self.assertEqual(applied.get("changed_paths"), [], applied)
                self.assertEqual(plan.read_bytes(), plan_before)
                self.assertEqual((vault / ".git" / "index").read_bytes(), git_index_before)

                run_git(vault, "add", "-f", "--", plan.name)
                staged_index = (vault / ".git" / "index").read_bytes()
                self.apply_tags(vault, base, plan, approved=True, expected=3)
                self.assertEqual((vault / ".git" / "index").read_bytes(), staged_index)
                self.assertEqual(plan.read_bytes(), plan_before)
            finally:
                plan.unlink(missing_ok=True)
            self.assertEqual(snapshot_files(vault), before)

    def test_default_root_plan_may_be_ignored_without_blocking_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            exclude = vault / ".git" / "info" / "exclude"
            with exclude.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("\n/tags-review-*.csv\n")
            base = git_head(vault)

            _payload, plan = self.collect_tags(vault, base)
            try:
                plan_before = plan.read_bytes()
                self.assertEqual(git_status(vault), [])
                applied = self.apply_tags(vault, base, plan, approved=True)
                self.assertIs(applied.get("changed"), False, applied)
                self.assertEqual(applied.get("changed_paths"), [], applied)
                self.assertEqual(plan.read_bytes(), plan_before)
                tracked = run_git(vault, "ls-files", "--", plan.name)
                self.assertEqual(str(tracked.stdout).strip(), "")
            finally:
                plan.unlink(missing_ok=True)

    def test_ignored_untracked_pages_block_collect_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            tracked = write_page(
                vault,
                "notes/已跟踪.md",
                "已跟踪",
                "note",
                summary="标签计划只能基于当前 HEAD 中的页面。",
                aliases=[],
                tags=["已跟踪标签"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/已跟踪.md"])
            self.assertTrue(tracked.is_file())
            base = git_head(vault)
            plan_path = root / "ignored-page-review.csv"
            _payload, plan = self.collect_tags(vault, base, output=plan_path)

            exclude = vault / ".git" / "info" / "exclude"
            with exclude.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("\n/notes/ignored.md\n")
            ignored = write_page(
                vault,
                "notes/ignored.md",
                "ignored",
                "note",
                summary="被 Git ignore 隐藏的新页面不能混入标签维护。",
                aliases=[],
                tags=["隐藏标签"],
                sources=[],
            )
            self.assertEqual(git_status(vault), [])
            before = snapshot_files(vault)

            self.collect_tags(
                vault,
                base,
                output=root / "must-not-exist.csv",
                expected=3,
            )
            self.apply_tags(vault, base, plan, approved=True, expected=3)

            self.assertEqual(snapshot_files(vault), before)
            self.assertEqual(git_head(vault), base)
            self.assertTrue(ignored.is_file())
            self.assertFalse((root / "must-not-exist.csv").exists())

    def test_apply_requires_approval_and_rejects_invalid_or_stale_plans_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            page = write_page(
                vault,
                "notes/旧标签.md",
                "旧标签",
                "note",
                summary="用于验证标签计划的审批和冲突边界。",
                aliases=[],
                tags=["旧标签"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/旧标签.md"])
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base, output=root / "review.csv")
            decide_tag(plan, "旧标签", "rename", "新标签")
            page_before = page.read_bytes()
            index_before = (vault / "index.csv").read_bytes()
            git_index_before = (vault / ".git" / "index").read_bytes()
            plan_before = plan.read_bytes()

            review = self.apply_tags(vault, base, plan, expected=5)
            self.assertIs(review.get("review_required"), True, review)
            self.assertIs(review.get("changed"), False, review)
            self.assertEqual(review.get("changed_paths"), ["notes/旧标签.md"], review)
            self.assertEqual(page.read_bytes(), page_before)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual((vault / ".git" / "index").read_bytes(), git_index_before)
            self.assertEqual(plan.read_bytes(), plan_before)

            header, rows = read_tag_plan(plan)
            invalid_cases: dict[str, tuple[int, list[str], list[dict[str, str]]]] = {
                "wrong-header": (2, ["tag", "action", "target"], [{
                    "tag": "旧标签", "action": "rename", "target": "新标签"
                }]),
                "duplicate-source": (2, header, [rows[0], dict(rows[0])]),
                "missing-source": (2, header, []),
                "extra-source": (2, header, [*rows, {
                    "tag": "不存在", "page_count": "1", "action": "keep", "target": ""
                }]),
                "count-changed": (3, header, [{**rows[0], "page_count": "2"}]),
                "invalid-action": (2, header, [{**rows[0], "action": "merge"}]),
                "rename-without-target": (2, header, [{**rows[0], "target": ""}]),
                "keep-with-target": (2, header, [{**rows[0], "action": "keep", "target": "意外目标"}]),
                "delete-with-target": (2, header, [{**rows[0], "action": "delete", "target": "意外目标"}]),
                "unsafe-target": (2, header, [{**rows[0], "target": "=HYPERLINK(\"x\")"}]),
                "extra-column": (2, [*header, "reason"], [{**rows[0], "reason": "不支持"}]),
            }
            for name, (expected_exit, case_header, case_rows) in invalid_cases.items():
                with self.subTest(case=name):
                    write_tag_plan(plan, case_rows, header=case_header)
                    before = snapshot_files(vault)
                    self.apply_tags(vault, base, plan, approved=True, expected=expected_exit)
                    self.assertEqual(snapshot_files(vault), before)
                    self.assertEqual(git_head(vault), base)
                    self.assertEqual(git_status(vault), [])
                    plan.write_bytes(plan_before)

            plan.write_bytes(b"\xff\xfe\x00")
            self.apply_tags(vault, base, plan, approved=True, expected=2)
            self.assertEqual(page.read_bytes(), page_before)
            plan.write_bytes(plan_before)

            plan.write_text(
                'tag,page_count,action,target\n"旧标签,1,rename,新标签\n',
                encoding="utf-8",
                newline="\n",
            )
            self.apply_tags(vault, base, plan, approved=True, expected=2)
            self.assertEqual(page.read_bytes(), page_before)
            plan.write_bytes(plan_before)

            arbitrary_root_plan = vault / "manual-tags.csv"
            arbitrary_root_plan.write_bytes(plan.read_bytes())
            arbitrary_before = snapshot_files(vault)
            self.apply_tags(vault, base, arbitrary_root_plan, approved=True, expected=2)
            self.assertEqual(snapshot_files(vault), arbitrary_before)
            arbitrary_root_plan.unlink()

            unrelated = vault / "pending.txt"
            unrelated.write_text("未完成工作。\n", encoding="utf-8", newline="\n")
            dirty_before = snapshot_files(vault)
            self.apply_tags(vault, base, plan, approved=True, expected=3)
            self.assertEqual(snapshot_files(vault), dirty_before)
            unrelated.unlink()

            page.write_text(
                page.read_text(encoding="utf-8").replace('["旧标签"]', '["另一个标签"]'),
                encoding="utf-8",
                newline="\n",
            )
            stale_before = snapshot_files(vault)
            self.apply_tags(vault, base, plan, approved=True, expected=3)
            self.assertEqual(snapshot_files(vault), stale_before)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual((vault / ".git" / "index").read_bytes(), git_index_before)
            self.assertEqual(git_head(vault), base)

            self.save(vault, base, operation="edit", include=["notes/旧标签.md"])
            current = git_head(vault)
            head_changed_before = snapshot_files(vault)
            self.apply_tags(vault, base, plan, approved=True, expected=3)
            self.assertEqual(snapshot_files(vault), head_changed_before)
            self.assertEqual(git_head(vault), current)

    def test_apply_detects_a_same_page_edit_at_the_atomic_write_boundary(self) -> None:
        runtime = load_wiki_runtime()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            page = write_page(
                vault,
                "notes/并发编辑.md",
                "并发编辑",
                "note",
                summary="标签应用不得覆盖同页并发人工编辑。",
                aliases=[],
                tags=["旧标签"],
                sources=[],
                body="ORIGINAL BODY",
            )
            self.save(vault, git_head(vault), include=["notes/并发编辑.md"])
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base, output=root / "race-review.csv")
            decide_tag(plan, "旧标签", "rename", "新标签")
            index_before = (vault / "index.csv").read_bytes()
            original_fdopen = runtime.os.fdopen
            injected = False

            class EditAfterTemporaryWrite:
                def __init__(self, handle):
                    self.handle = handle

                def __enter__(self):
                    return self.handle.__enter__()

                def __exit__(self, exc_type, exc, traceback):
                    nonlocal injected
                    result = self.handle.__exit__(exc_type, exc, traceback)
                    if not injected:
                        injected = True
                        page.write_text(
                            page.read_text(encoding="utf-8").replace(
                                "ORIGINAL BODY",
                                "CONCURRENT HUMAN EDIT",
                            ),
                            encoding="utf-8",
                            newline="\n",
                        )
                    return result

            def edit_when_temporary_file_closes(descriptor, *args, **kwargs):
                return EditAfterTemporaryWrite(original_fdopen(descriptor, *args, **kwargs))

            args = SimpleNamespace(base=base, plan=str(plan), approved=True)
            previous_cwd = Path.cwd()
            try:
                os.chdir(vault)
                with mock.patch.object(
                    runtime.os,
                    "fdopen",
                    side_effect=edit_when_temporary_file_closes,
                ):
                    with self.assertRaises(runtime.WikiError) as raised:
                        runtime.command_tags_apply(args)
            finally:
                os.chdir(previous_cwd)

            self.assertTrue(injected)
            self.assertEqual(raised.exception.code, 3)
            current = page.read_text(encoding="utf-8")
            self.assertIn("CONCURRENT HUMAN EDIT", current)
            self.assertIn('tags: ["旧标签"]', current)
            self.assertNotIn('tags: ["新标签"]', current)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual(git_head(vault), base)

    def test_apply_is_exact_preserves_other_bytes_and_is_noop_for_keep(self) -> None:
        runtime = load_wiki_runtime()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            page = vault / "notes" / "保真.md"
            original = (
                "\ufeff---\n"
                "kind: note\n"
                "summary: 保留未知属性、注释和正文。\n"
                "aliases: []\n"
                "tags:\n"
                "  - \"旧,标签\"\n"
                "  # 用户注释必须保留\n"
                "  - 保留\n"
                "sources: []\n"
                "custom-nested:\n"
                "  tags: not-managed\n"
                "  owner: user\n"
                "---\n"
                "# 保真\n\n"
                "正文中的 tags: 旧,标签 不是属性。\n"
            ).encode("utf-8")
            page.write_bytes(original)
            self.save(vault, git_head(vault), include=["notes/保真.md"])
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base, output=root / "preserve.csv")
            decide_tag(plan, "旧,标签", "keep")
            decide_tag(plan, "保留", "keep")
            page_mtime = page.stat().st_mtime_ns
            index_before = (vault / "index.csv").read_bytes()
            keep_plan_before = plan.read_bytes()

            noop = self.apply_tags(vault, base, plan, approved=True)
            self.assertIs(noop.get("approved"), True, noop)
            self.assertIs(noop.get("changed"), False, noop)
            self.assertEqual(noop.get("changed_paths"), [], noop)
            self.assertEqual(page.read_bytes(), original)
            self.assertEqual(page.stat().st_mtime_ns, page_mtime)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual(plan.read_bytes(), keep_plan_before)
            self.assertEqual(git_status(vault), [])

            decide_tag(plan, "旧,标签", "rename", "新标签")
            approved_plan = plan.read_bytes()
            applied = self.apply_tags(vault, base, plan, approved=True)
            self.assertIs(applied.get("approved"), True, applied)
            self.assertIs(applied.get("changed"), True, applied)
            self.assertEqual(applied.get("changed_paths"), ["notes/保真.md"], applied)
            expected = original.replace(
                b'tags:\n  - "\xe6\x97\xa7,\xe6\xa0\x87\xe7\xad\xbe"\n'
                b'  # \xe7\x94\xa8\xe6\x88\xb7\xe6\xb3\xa8\xe9\x87\x8a\xe5\xbf\x85\xe9\xa1\xbb\xe4\xbf\x9d\xe7\x95\x99\n'
                b'  - \xe4\xbf\x9d\xe7\x95\x99\n',
                'tags: ["新标签", "保留"]\n  # 用户注释必须保留\n'.encode("utf-8"),
            )
            self.assertEqual(page.read_bytes(), expected)
            values, _body, errors = runtime.parse_frontmatter_text(page.read_text(encoding="utf-8"))
            self.assertEqual(errors, [])
            self.assertEqual(values.get("tags"), ["新标签", "保留"])
            self.assertIn(b"  tags: not-managed\n", page.read_bytes())
            self.assertIn("正文中的 tags: 旧,标签".encode("utf-8"), page.read_bytes())
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual(plan.read_bytes(), approved_plan)
            self.assertEqual(git_head(vault), base)
            status = git_status(vault)
            self.assertEqual(len(status), 1, status)
            self.assertIn("notes/", status[0])

    def test_mapping_rules_reject_chains_and_preserve_order_for_valid_merges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            page = write_page(
                vault,
                "notes/tag-order.md",
                "tag-order",
                "note",
                summary="标签映射按页面首次出现顺序去重。",
                aliases=[],
                tags=["A", "B", "C", "A", "D", "E", "F", "G"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/tag-order.md"])
            base = git_head(vault)
            _payload, source_plan = self.collect_tags(
                vault,
                base,
                output=root / "mapping-source.csv",
            )
            header, source_rows = read_tag_plan(source_plan)
            source_by_tag = {row["tag"]: row for row in source_rows}

            invalid_decisions = {
                "cycle": {"A": ("rename", "B"), "B": ("rename", "A")},
                "target-delete": {"A": ("rename", "B"), "B": ("delete", "")},
                "self-rename": {"A": ("rename", "A")},
            }
            for name, decisions in invalid_decisions.items():
                with self.subTest(case=name):
                    rows = [dict(row) for row in source_rows]
                    for row in rows:
                        action, target = decisions.get(row["tag"], ("keep", ""))
                        row["action"] = action
                        row["target"] = target
                    write_tag_plan(source_plan, rows, header=header)
                    page_before = page.read_bytes()
                    self.apply_tags(vault, base, source_plan, approved=True, expected=2)
                    self.assertEqual(page.read_bytes(), page_before)
                    self.assertEqual(git_status(vault), [])
                    write_tag_plan(source_plan, source_rows, header=header)

            valid_rows = [dict(source_by_tag[tag]) for tag in ("G", "F", "E", "D", "C", "B", "A")]
            decisions = {
                "A": ("rename", "Z"),
                "B": ("rename", "Z"),
                "C": ("rename", "D"),
                "D": ("keep", ""),
                "E": ("delete", ""),
                "F": ("rename", tag_plan_cell("=canonical")),
                "G": ("keep", ""),
            }
            for row in valid_rows:
                row["action"], row["target"] = decisions[row["tag"]]
            write_tag_plan(source_plan, valid_rows)
            source_plan.write_bytes(source_plan.read_bytes().replace(b"\n", b"\r\n"))
            applied = self.apply_tags(vault, base, source_plan, approved=True)
            self.assertEqual(applied.get("changed_paths"), ["notes/tag-order.md"], applied)
            self.assertRegex(page.read_text(encoding="utf-8"), r'(?m)^tags: \["Z", "D", "=canonical", "G"\]$')
            self.assertNotRegex(page.read_text(encoding="utf-8"), r'(?m)^tags: \["D", "Z"')

    def test_tag_apply_flows_through_context_save_and_audit_with_exact_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            page = write_page(
                vault,
                "notes/检索标签.md",
                "检索标签",
                "note",
                summary="标签改变后由内存索引检索，再由 save 重建正式索引。",
                aliases=[],
                tags=["旧检索词"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/检索标签.md"])
            base = git_head(vault)
            index_before = (vault / "index.csv").read_bytes()
            _payload, plan = self.collect_tags(vault, base)
            self.assertEqual(plan.parent.resolve(), vault.resolve())
            decide_tag(plan, "旧检索词", "rename", "新检索词")
            reviewed_plan = plan.read_bytes()
            applied = self.apply_tags(vault, base, plan, approved=True)
            changed_paths = applied.get("changed_paths")
            self.assertEqual(changed_paths, ["notes/检索标签.md"], applied)
            self.assertIsInstance(changed_paths, list, applied)
            assert isinstance(changed_paths, list)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)

            context = self.assert_exit(
                run_cli(
                    "context",
                    "--plan",
                    json.dumps({"required_tags": ["新检索词"]}, ensure_ascii=False),
                    cwd=vault,
                ),
                0,
            )
            self.assertEqual(candidate_paths(context), ["notes/检索标签.md"])
            self.assertIs(context.get("overlay"), True, context)
            self.assertIn("index_warning", context)
            drift = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertTrue(any_nested_key(drift, "code", "E_INDEX_DRIFT"), drift)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)

            unrelated = vault / "私人草稿.txt"
            unrelated.write_text("不要纳入标签检查点。\n", encoding="utf-8", newline="\n")
            preview = self.save(
                vault,
                base,
                operation="tag-maintenance",
                include=changed_paths,
                expected=5,
            )
            self.assertIs(preview.get("review_required"), True, preview)
            self.assertEqual(git_head(vault), base)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual(plan.read_bytes(), reviewed_plan)

            saved = self.save(
                vault,
                base,
                operation="tag-maintenance",
                include=changed_paths,
                approved=True,
            )
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(plan.read_bytes(), reviewed_plan)
            committed = run_git(
                vault,
                "-c",
                "core.quotepath=false",
                "diff",
                "--name-only",
                base,
                "HEAD",
                "--",
            )
            self.assertEqual(
                set(str(committed.stdout).splitlines()),
                {"index.csv", "notes/检索标签.md"},
            )
            self.assertTrue(unrelated.is_file())
            visible_status = run_git(
                vault,
                "-c",
                "core.quotepath=false",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
            self.assertIn("私人草稿.txt", str(visible_status.stdout))
            ignored_plan = run_git(vault, "check-ignore", "--", plan.name)
            self.assertEqual(str(ignored_plan.stdout).strip(), plan.name)
            committed_plan = run_git(vault, "ls-tree", "--name-only", "HEAD", "--", plan.name)
            self.assertEqual(str(committed_plan.stdout).strip(), "")
            unrelated.unlink()

            page_checkpoint = git_head(vault)
            policy_before = (vault / TAG_POLICY_NAME).read_bytes()
            merge_preview = self.merge_tags(vault, page_checkpoint, plan, expected=5)
            self.assertIs(merge_preview.get("review_required"), True, merge_preview)
            self.assertEqual((vault / TAG_POLICY_NAME).read_bytes(), policy_before)
            merged = self.merge_tags(vault, page_checkpoint, plan, approved=True)
            self.assertEqual(merged.get("changed_paths"), [TAG_POLICY_NAME], merged)
            self.assertEqual(plan.read_bytes(), reviewed_plan)

            policy_preview = self.save(
                vault,
                page_checkpoint,
                operation="tag-policy",
                include=[TAG_POLICY_NAME],
                expected=5,
            )
            self.assertIs(policy_preview.get("review_required"), True, policy_preview)
            policy_saved = self.save(
                vault,
                page_checkpoint,
                operation="tag-policy",
                include=[TAG_POLICY_NAME],
                approved=True,
            )
            self.assertIs(policy_saved.get("saved"), True, policy_saved)
            policy_commit = git_head(vault)
            policy_changes = run_git(
                vault,
                "-c",
                "core.quotepath=false",
                "diff",
                "--name-only",
                page_checkpoint,
                policy_commit,
                "--",
            )
            self.assertEqual(str(policy_changes.stdout).splitlines(), [TAG_POLICY_NAME])

            vocabulary = self.tag_vocabulary(vault)
            self.assertIn("新检索词", vocabulary.get("preferred_tags", []))
            self.assertIn("旧检索词", vocabulary.get("forbidden_tags", []))
            self.assertEqual(vocabulary.get("rename_map"), {"旧检索词": "新检索词"})
            checked = self.check_tags(vault, ["新检索词", "确有必要的新标签"])
            self.assertEqual(checked.get("accepted_tags"), ["新检索词"])
            self.assertEqual(checked.get("new_tags"), ["确有必要的新标签"])
            rejected = self.check_tags(vault, ["旧检索词"], expected=3)
            self.assertTrue(rejected.get("rejected_tags"), rejected)

            plan.unlink()
            healthy = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assertIs(healthy.get("valid"), True, healthy)
            self.assertEqual(git_status(vault), [])

            new_base = git_head(vault)
            _again_payload, again_plan = self.collect_tags(
                vault,
                new_base,
                output=root / "after.csv",
            )
            _header, rows = read_tag_plan(again_plan)
            self.assertEqual([row["tag"] for row in rows], ["新检索词"])
            again = self.apply_tags(vault, new_base, again_plan, approved=True)
            self.assertIs(again.get("changed"), False, again)
            self.assertEqual(again.get("changed_paths"), [], again)
            self.assertEqual(git_status(vault), [])

    def test_collect_inherits_history_and_amendments_update_absent_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            policy = vault / TAG_POLICY_NAME
            write_tag_plan(
                policy,
                [
                    {"tag": "保留词", "page_count": "4", "action": "keep", "target": ""},
                    {"tag": "历史删除", "page_count": "2", "action": "delete", "target": ""},
                    {"tag": "历史旧名", "page_count": "7", "action": "rename", "target": "本轮标签"},
                    {"tag": "旧名称", "page_count": "3", "action": "rename", "target": "规范名称"},
                ],
            )
            policy_base = git_head(vault)
            self.save(
                vault,
                policy_base,
                operation="tag-policy",
                include=[TAG_POLICY_NAME],
                approved=True,
            )
            page = write_page(
                vault,
                "notes/历史策略.md",
                "历史策略",
                "note",
                summary="验证历史标签决策的继承和补丁。",
                aliases=[],
                tags=["保留词", "旧名称", "规范名称", "本轮标签", "全新词"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/历史策略.md"])
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base)
            header, rows = read_tag_plan(plan)
            self.assertEqual(header, TAG_PLAN_HEADER)
            by_tag = {row["tag"]: row for row in rows}
            self.assertEqual(by_tag["保留词"]["action"], "keep")
            self.assertEqual(by_tag["旧名称"]["action"], "rename")
            self.assertEqual(by_tag["旧名称"]["target"], "规范名称")
            self.assertEqual(by_tag["规范名称"]["action"], "keep")
            self.assertEqual(by_tag["本轮标签"]["action"], "keep")
            self.assertEqual(by_tag["全新词"]["action"], "")
            self.assertNotIn("历史旧名", by_tag)
            self.assertNotIn("历史删除", by_tag)

            decide_tag(plan, "本轮标签", "rename", "最终标签")
            decide_tag(plan, "全新词", "keep")
            amendments = vault / "tags-review-amendments-test.csv"
            write_tag_plan(
                amendments,
                [
                    {
                        "tag": "历史旧名",
                        "page_count": "7",
                        "action": "rename",
                        "target": "最终标签",
                    }
                ],
            )
            without_amendment = self.apply_tags(vault, base, plan, approved=True, expected=2)
            self.assertIn("conflict", json.dumps(without_amendment, ensure_ascii=False).lower())
            applied = self.apply_tags(
                vault,
                base,
                plan,
                amendments=amendments,
                approved=True,
            )
            self.assertEqual(applied.get("changed_paths"), ["notes/历史策略.md"], applied)
            self.assertRegex(
                page.read_text(encoding="utf-8"),
                r'(?m)^tags: \["保留词", "规范名称", "最终标签", "全新词"\]$',
            )
            self.save(
                vault,
                base,
                operation="tag-maintenance",
                include=["notes/历史策略.md"],
                approved=True,
            )
            page_checkpoint = git_head(vault)
            self.merge_tags(
                vault,
                page_checkpoint,
                plan,
                amendments=amendments,
                approved=True,
            )
            self.save(
                vault,
                page_checkpoint,
                operation="tag-policy",
                include=[TAG_POLICY_NAME],
                approved=True,
            )
            _policy_header, policy_rows = read_tag_plan(policy)
            merged = {row["tag"]: row for row in policy_rows}
            self.assertEqual(merged["历史旧名"]["target"], "最终标签")
            self.assertEqual(merged["历史旧名"]["page_count"], "7")
            self.assertEqual(merged["历史删除"]["page_count"], "2")
            self.assertEqual(merged["本轮标签"]["target"], "最终标签")
            self.assertEqual(merged["全新词"]["action"], "keep")
            self.assertTrue(policy.read_bytes().startswith(b"\xef\xbb\xbf"))
            plan.unlink()
            amendments.unlink()
            self.assertEqual(git_status(vault), [])

    def test_existing_vault_without_policy_lazily_creates_it_on_first_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            run_git(vault, "rm", "--", TAG_POLICY_NAME)
            run_git(vault, "commit", "-m", "fixture: vault predates tag policy")
            self.assertFalse((vault / TAG_POLICY_NAME).exists())
            self.assertIs(self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0).get("valid"), True)
            vocabulary = self.tag_vocabulary(vault)
            self.assertEqual(vocabulary.get("preferred_tags"), [])
            self.assertEqual(vocabulary.get("forbidden_tags"), [])
            self.assertEqual(vocabulary.get("rename_map"), {})
            checked = self.check_tags(vault, ["首次标签"])
            self.assertEqual(checked.get("new_tags"), ["首次标签"])

            page = write_page(
                vault,
                "notes/首次策略.md",
                "首次策略",
                "note",
                summary="首次维护创建持久标签策略。",
                aliases=[],
                tags=["首次标签"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/首次策略.md"])
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base)
            decide_tag(plan, "首次标签", "keep")
            self.assertIs(self.apply_tags(vault, base, plan, approved=True).get("changed"), False)
            merged = self.merge_tags(vault, base, plan, approved=True)
            self.assertEqual(merged.get("changed_paths"), [TAG_POLICY_NAME], merged)
            self.assertTrue((vault / TAG_POLICY_NAME).is_file())
            self.save(
                vault,
                base,
                operation="tag-policy",
                include=[TAG_POLICY_NAME],
                approved=True,
            )
            self.assertEqual(
                read_tag_plan(vault / TAG_POLICY_NAME)[1],
                [{"tag": "首次标签", "page_count": "1", "action": "keep", "target": ""}],
            )
            plan.unlink()
            self.assertTrue(page.is_file())
            self.assertEqual(git_status(vault), [])

    def test_policy_and_current_inventory_reject_casefold_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            policy = vault / TAG_POLICY_NAME
            base = git_head(vault)
            write_tag_plan(
                policy,
                [
                    {"tag": "AI", "page_count": "1", "action": "keep", "target": ""},
                    {"tag": "ai", "page_count": "1", "action": "keep", "target": ""},
                ],
            )
            invalid = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertTrue(any_nested_key(invalid, "code", "E_TAG_POLICY"), invalid)
            self.save(
                vault,
                base,
                operation="tag-policy",
                include=[TAG_POLICY_NAME],
                approved=True,
                expected=4,
            )
            self.assertEqual(git_head(vault), base)

            write_tag_plan(policy, [])
            page = write_page(
                vault,
                "notes/大小写冲突.md",
                "大小写冲突",
                "note",
                summary="标签清单中的大小写折叠冲突必须失败关闭。",
                aliases=[],
                tags=["AI", "ai"],
                sources=[],
            )
            self.save(
                vault,
                base,
                operation="edit",
                include=[TAG_POLICY_NAME, "notes/大小写冲突.md"],
            )
            current = git_head(vault)
            self.collect_tags(vault, current, output=root / "collision.csv", expected=2)
            self.assertTrue(page.is_file())

    def test_amendments_are_historical_only_and_preserve_page_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            policy = vault / TAG_POLICY_NAME
            write_tag_plan(
                policy,
                [
                    {"tag": "历史标签", "page_count": "2", "action": "keep", "target": ""},
                    {"tag": "当前标签", "page_count": "1", "action": "keep", "target": ""},
                ],
            )
            self.save(
                vault,
                git_head(vault),
                operation="tag-policy",
                include=[TAG_POLICY_NAME],
                approved=True,
            )
            write_page(
                vault,
                "notes/补丁边界.md",
                "补丁边界",
                "note",
                summary="补丁只能修改缺席的历史标签决策。",
                aliases=[],
                tags=["当前标签"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/补丁边界.md"])
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base)
            cases = {
                "unknown": {"tag": "未知历史", "page_count": "1", "action": "delete", "target": ""},
                "overlap": {"tag": "当前标签", "page_count": "1", "action": "delete", "target": ""},
                "count": {"tag": "历史标签", "page_count": "9", "action": "delete", "target": ""},
            }
            for name, row in cases.items():
                with self.subTest(case=name):
                    amendment = vault / f"tags-review-amendments-{name}.csv"
                    write_tag_plan(amendment, [row])
                    before = snapshot_files(vault)
                    self.apply_tags(
                        vault,
                        base,
                        plan,
                        amendments=amendment,
                        approved=True,
                        expected=2,
                    )
                    self.assertEqual(snapshot_files(vault), before)
                    amendment.unlink()
            plan.unlink()

    def test_regular_workflows_never_create_or_apply_a_tag_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            page = write_page(
                vault,
                "notes/普通维护.md",
                "普通维护",
                "note",
                summary="普通工作流不得隐式运行标签维护。",
                aliases=[],
                tags=["散乱标签", "散乱标签"],
                sources=[],
            )
            original = page.read_bytes()
            self.save(vault, git_head(vault), operation="edit", include=["notes/普通维护.md"])
            self.assertEqual(page.read_bytes(), original)
            before = snapshot_files(vault)
            self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assert_exit(
                run_cli(
                    "context",
                    "--plan",
                    json.dumps({"required_tags": ["散乱标签"]}, ensure_ascii=False),
                    cwd=vault,
                ),
                0,
            )
            self.assertEqual(snapshot_files(vault), before)
            self.assertEqual(page.read_bytes(), original)
            self.assertEqual(
                sorted(path.name for path in vault.glob("*.csv")),
                ["index.csv", TAG_POLICY_NAME],
            )


class IndexPipelineTests(WikiCliTestCase):
    def test_save_builds_a_canonical_index_without_rewriting_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            note = write_page(
                vault,
                "notes/确定性索引.md",
                "确定性索引",
                "note",
                summary="索引由页面文件头确定生成。",
                aliases=["Index", "索引", "Index"],
                tags=["wiki", "index", "wiki"],
                sources=[],
                body="正文和未知属性必须原样保留。",
                extra_properties={"user-property": "keep-me"},
            )
            original_page = note.read_bytes()

            payload = self.save(
                vault,
                base,
                include=["notes/确定性索引.md"],
            )
            self.assertIs(payload.get("saved"), True, payload)
            self.assertNotEqual(git_head(vault), base)
            self.assertEqual(git_status(vault), [])
            self.assertEqual(note.read_bytes(), original_page)

            header, rows = read_index(vault)
            self.assertEqual(header, INDEX_HEADER)
            self.assertEqual([row["path"] for row in rows], sorted(row["path"] for row in rows))
            row = next(row for row in rows if row["path"] == "notes/确定性索引.md")
            self.assertEqual(row["kind"], "note")
            self.assertEqual(row["summary"], "索引由页面文件头确定生成。")
            self.assertEqual(json.loads(row["aliases"]), ["Index", "索引"])
            self.assertEqual(json.loads(row["tags"]), ["index", "wiki"])

    def test_body_only_save_does_not_rewrite_identical_index_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            note = write_page(
                vault,
                "notes/no-op.md",
                "no-op",
                "note",
                summary="元数据保持不变。",
                aliases=[],
                tags=[],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/no-op.md"])
            before_bytes = (vault / "index.csv").read_bytes()
            before_mtime = (vault / "index.csv").stat().st_mtime_ns

            with note.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("新增正文，不改变文件头。\n")
            self.save(vault, git_head(vault), operation="edit", include=["notes/no-op.md"])

            self.assertEqual((vault / "index.csv").read_bytes(), before_bytes)
            self.assertEqual((vault / "index.csv").stat().st_mtime_ns, before_mtime)
            self.assertEqual(git_status(vault), [])

    def test_known_frontmatter_ambiguities_block_audit_and_save(self) -> None:
        cases = {
            "malformed-key": (
                "kind : note\nsummary: 摘要。\naliases: []\ntags: []\nsources: []",
                "frontmatter",
            ),
            "missing-colon": (
                "kind note\nsummary: 摘要。\naliases: []\ntags: []\nsources: []",
                "malformed",
            ),
            "duplicate-field": (
                "kind: note\nsummary: 第一项。\nsummary: 第二项。\naliases: []\ntags: []\nsources: []",
                "duplicate",
            ),
            "mixed-list": (
                "kind: note\nsummary: 摘要。\naliases: [inline]\n  - block\ntags: []\nsources: []",
                "mix",
            ),
            "scalar-block-list": (
                "kind: note\nsummary:\n  - 不是标量\naliases: []\ntags: []\nsources: []",
                "scalar",
            ),
            "list-as-scalar": (
                "kind: note\nsummary: 摘要。\naliases: not-a-list\ntags: []\nsources: []",
                "list",
            ),
            "indented-known-after-scalar": (
                "kind: note\n  summary: 缩进字段不能作为 kind 的延续。\nsummary: 摘要。\naliases: []\ntags: []\nsources: []",
                "indented",
            ),
            "inline-mapping-list": (
                "kind: note\nsummary: 摘要。\naliases: [a: b]\ntags: []\nsources: []",
                "mapping",
            ),
            "block-mapping-list": (
                "kind: note\nsummary: 摘要。\naliases:\n  - a: b\ntags: []\nsources: []",
                "mapping",
            ),
            "block-inline-comment": (
                "kind: note\nsummary: 摘要。\naliases: []\ntags:\n  - old # 用户注释\n  - other\nsources: []",
                "comment",
            ),
            "inline-list-comment": (
                "kind: note\nsummary: 摘要。\naliases: []\ntags: [old # 用户注释]\nsources: []",
                "comment",
            ),
        }
        for name, (header, expected_text) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temp_dir:
                vault = self.init_vault(Path(temp_dir))
                page = vault / "notes" / f"{name}.md"
                page.write_text(
                    f"---\n{header}\n---\n# {name}\n\n正文。\n",
                    encoding="utf-8",
                    newline="\n",
                )
                before = page.read_bytes()
                base = git_head(vault)

                audit = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 4)
                report = json.dumps(audit, ensure_ascii=False).lower()
                self.assertIn("e_frontmatter", report)
                self.assertIn(expected_text, report)

                saved = self.save(vault, base, include=[f"notes/{name}.md"], expected=4)
                self.assertIs(saved.get("saved"), False, saved)
                self.assertEqual(page.read_bytes(), before)
                self.assertEqual(git_head(vault), base)

    def test_unknown_obsidian_properties_and_body_remain_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            page = vault / "notes" / "Obsidian属性.md"
            page.write_text(
                "---\n"
                "kind: note\n"
                "summary: 保留未知 Obsidian Properties。\n"
                "aliases: []\n"
                "tags: []\n"
                "sources: []\n"
                "cssclasses:\n"
                "  - wide-page\n"
                "custom-nested:\n"
                "  owner: user\n"
                "  kind: nested-value\n"
                "  summary: nested summary must stay unknown\n"
                "---\n"
                "# Obsidian属性\n\n"
                "用户正文必须逐字保留。\n",
                encoding="utf-8",
                newline="\n",
            )
            before = page.read_bytes()

            saved = self.save(vault, git_head(vault), include=["notes/Obsidian属性.md"])
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(page.read_bytes(), before)
            self.assertEqual(git_show_bytes(vault, "HEAD:notes/Obsidian属性.md"), before)

    def test_index_includes_pages_and_excludes_raw_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            raw_rel = "raw/论文/原文.pdf"
            asset_rel = "assets/论文结构.png"
            (vault / raw_rel).parent.mkdir(parents=True)
            (vault / raw_rel).write_bytes(b"%PDF-1.7\x00\xffsource")
            (vault / asset_rel).parent.mkdir(parents=True, exist_ok=True)
            (vault / asset_rel).write_bytes(b"\x89PNG\r\nasset")
            write_page(
                vault,
                "sources/论文.md",
                "论文",
                "source",
                summary="论文证据范围。",
                aliases=[],
                tags=[],
                raw=[f"[[{raw_rel}]]"],
            )
            write_page(
                vault,
                "notes/核心观点.md",
                "核心观点",
                "note",
                summary="跨来源观点。",
                aliases=[],
                tags=["观点"],
                sources=["[[sources/论文]]"],
            )
            write_page(vault, "inbox/待验证.md", "待验证", "inbox", body="用户假设。")

            self.save(
                vault,
                git_head(vault),
                operation="add",
                include=["sources/论文.md", "notes/核心观点.md", "inbox/待验证.md", raw_rel, asset_rel],
            )
            _, rows = read_index(vault)
            paths = [row["path"] for row in rows]
            self.assertEqual(
                paths,
                sorted(["首页.md", "inbox/待验证.md", "notes/核心观点.md", "sources/论文.md"]),
            )
            self.assertNotIn(raw_rel, paths)
            self.assertNotIn(asset_rel, paths)
            inbox_row = next(row for row in rows if row["path"] == "inbox/待验证.md")
            self.assertEqual(inbox_row["summary"], "")
            self.assertEqual(json.loads(inbox_row["aliases"]), [])
            self.assertEqual(json.loads(inbox_row["tags"]), [])

    def test_audit_reports_index_drift_read_only_and_save_rebuilds_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            write_page(
                vault,
                "notes/新增页面.md",
                "新增页面",
                "note",
                summary="用于恢复索引。",
                aliases=[],
                tags=[],
                sources=[],
            )
            (vault / "index.csv").write_bytes(b"manually,broken\r\n1,2\r\n")
            before = snapshot_files(vault)

            result = run_cli("audit", "--scope", "all", cwd=vault)
            payload = self.assert_exit(result, 4)
            self.assert_envelope(payload, "audit", vault, ok=False)
            self.assertIs(payload.get("valid"), False, payload)
            self.assertIn("index", json.dumps(payload, ensure_ascii=False).lower())
            self.assertEqual(snapshot_files(vault), before, "audit must not repair files")
            self.assertEqual(git_head(vault), base)

            saved = self.save(
                vault,
                base,
                operation="add",
                include=["notes/新增页面.md", "index.csv"],
            )
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(read_index(vault)[0], INDEX_HEADER)
            self.assertIn("notes/新增页面.md", [row["path"] for row in read_index(vault)[1]])
            self.assertEqual(git_status(vault), [])
            clean = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assertIs(clean.get("valid"), True, clean)

            text_report = run_cli("audit", "--scope", "all", "--format", "text", cwd=vault)
            self.assertEqual(text_report.returncode, 0, text_report.diagnostic())
            self.assertEqual(text_report.stdout.strip(), "valid")
            csv_report = run_cli("audit", "--scope", "all", "--format", "csv", cwd=vault)
            self.assertEqual(csv_report.returncode, 0, csv_report.diagnostic())
            self.assertEqual(csv_report.stdout.strip(), "code,path,field,message")

    def test_missing_source_summary_blocks_save_and_keeps_the_draft_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            page = write_page(
                vault,
                "sources/不完整来源.md",
                "不完整来源",
                "source",
                aliases=[],
                tags=[],
                raw=[],
            )
            index_before = (vault / "index.csv").read_bytes()

            payload = self.save(vault, base, include=["sources/不完整来源.md"], expected=4)
            self.assertIs(payload.get("saved"), False, payload)
            self.assertIn("summary", json.dumps(payload, ensure_ascii=False).lower())
            self.assertTrue(page.is_file())
            self.assertEqual(git_head(vault), base)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertTrue(git_status(vault))


class ContextTests(WikiCliTestCase):
    def test_query_plan_filters_and_returns_all_matches_when_limit_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            for number, title in enumerate(("检索基础", "检索评估", "检索实践"), start=1):
                write_page(
                    vault,
                    f"notes/{title}.md",
                    title,
                    "note",
                    summary=f"检索系统主题 {number}。",
                    aliases=[f"retrieval-{number}"],
                    tags=["retrieval", "ai"],
                    sources=[],
                )
            write_page(
                vault,
                "sources/检索论文.md",
                "检索论文",
                "source",
                summary="检索论文证据。",
                aliases=[],
                tags=["retrieval"],
                raw=[],
            )
            self.save(
                vault,
                git_head(vault),
                operation="add",
                include=[
                    "notes/检索基础.md",
                    "notes/检索评估.md",
                    "notes/检索实践.md",
                    "sources/检索论文.md",
                ],
            )

            plan = {
                "terms": ["检索"],
                "kinds": ["note"],
                "required_tags": ["retrieval"],
                "path_prefixes": ["notes/"],
            }
            result = run_cli(
                "context",
                "--plan",
                json.dumps(plan, ensure_ascii=False),
                cwd=vault,
            )
            payload = self.assert_exit(result, 0)
            self.assert_envelope(payload, "context", vault, ok=True)
            paths = candidate_paths(payload)
            self.assertEqual(len(paths), 3, "omitting limit must not silently truncate matches")
            self.assertEqual(payload.get("count"), 3)
            self.assertTrue(all(path.startswith("notes/") for path in paths))
            for candidate in payload["candidates"]:  # type: ignore[index]
                assert isinstance(candidate, dict)
                for field in ("path", "kind", "summary", "aliases", "tags", "reasons"):
                    self.assertIn(field, candidate)
                self.assertTrue(candidate["reasons"])

            limited = dict(plan, limit=2)
            payload = self.assert_exit(
                run_cli("context", "--plan", json.dumps(limited, ensure_ascii=False), cwd=vault),
                0,
            )
            self.assertEqual(payload.get("count"), 2)
            self.assertEqual(len(candidate_paths(payload)), 2)

    def test_llm_supplied_cross_language_aliases_recall_unicode_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            write_page(
                vault,
                "notes/检索增强生成.md",
                "检索增强生成",
                "note",
                summary="通过外部知识增强模型回答。",
                aliases=["retrieval augmented generation", "RAG"],
                tags=["retrieval"],
                sources=[],
            )
            self.save(
                vault,
                git_head(vault),
                operation="add",
                include=["notes/检索增强生成.md"],
            )
            plan = {"phrases": ["retrieval augmented generation"]}
            payload = self.assert_exit(
                run_cli("context", "--plan", json.dumps(plan), cwd=vault),
                0,
            )
            self.assertEqual(candidate_paths(payload)[0], "notes/检索增强生成.md")

    def test_context_uses_a_dirty_header_overlay_without_writing_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            first = write_page(
                vault,
                "notes/保留.md",
                "保留",
                "note",
                summary="overlay old",
                aliases=[],
                tags=[],
                sources=[],
            )
            removed = write_page(
                vault,
                "notes/删除.md",
                "删除",
                "note",
                summary="overlay removed",
                aliases=[],
                tags=[],
                sources=[],
            )
            self.save(
                vault,
                git_head(vault),
                operation="add",
                include=["notes/保留.md", "notes/删除.md"],
            )

            set_frontmatter_scalar(first, "summary", "overlay changed")
            removed.unlink()
            write_page(
                vault,
                "notes/新增.md",
                "新增",
                "note",
                summary="overlay new",
                aliases=[],
                tags=[],
                sources=[],
            )
            index_before = (vault / "index.csv").read_bytes()
            status_before = git_status(vault)
            head_before = git_head(vault)

            plan = {"terms": ["overlay"]}
            payload = self.assert_exit(
                run_cli("context", "--plan", json.dumps(plan), cwd=vault),
                0,
            )
            self.assertTrue(payload.get("overlay"), payload)
            self.assertEqual(
                set(candidate_paths(payload)),
                {"notes/保留.md", "notes/新增.md"},
            )
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual(git_status(vault), status_before)
            self.assertEqual(git_head(vault), head_before)

    def test_context_detects_clean_stale_index_and_uses_page_headers_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            write_page(
                vault,
                "notes/索引事实.md",
                "索引事实",
                "note",
                summary="fresh-header-marker",
                aliases=[],
                tags=[],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/索引事实.md"])
            index = vault / "index.csv"
            index.write_text(
                index.read_text(encoding="utf-8").replace("fresh-header-marker", "stale-index-marker"),
                encoding="utf-8",
                newline="\n",
            )
            run_git(vault, "add", "--", "index.csv")
            run_git(vault, "commit", "-m", "commit stale generated index")
            self.assertEqual(git_status(vault), [])
            before = snapshot_files(vault)
            head_before = git_head(vault)

            payload = self.assert_exit(
                run_cli("context", "--plan", json.dumps({"terms": ["fresh-header-marker"]}), cwd=vault),
                0,
            )
            self.assertTrue(payload.get("overlay"), payload)
            self.assertIn("index_warning", payload)
            self.assertEqual(candidate_paths(payload), ["notes/索引事实.md"])
            self.assertEqual(snapshot_files(vault), before)
            self.assertEqual(git_status(vault), [])
            self.assertEqual(git_head(vault), head_before)

    def test_context_recovers_from_missing_or_corrupt_index_without_writing(self) -> None:
        for state in ("missing", "corrupt"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp_dir:
                vault = self.init_vault(Path(temp_dir))
                write_page(
                    vault,
                    "notes/内存索引.md",
                    "内存索引",
                    "note",
                    summary="memory-index-marker",
                    aliases=[],
                    tags=[],
                    sources=[],
                )
                self.save(vault, git_head(vault), include=["notes/内存索引.md"])
                index = vault / "index.csv"
                if state == "missing":
                    index.unlink()
                else:
                    index.write_bytes(b"not,a,valid,index\n\xff")
                before = snapshot_files(vault)
                status_before = git_status(vault)
                head_before = git_head(vault)

                payload = self.assert_exit(
                    run_cli(
                        "context",
                        "--plan",
                        json.dumps({"terms": ["memory-index-marker"]}),
                        cwd=vault,
                    ),
                    0,
                )
                self.assertTrue(payload.get("overlay"), payload)
                self.assertIn("index_warning", payload)
                self.assertEqual(candidate_paths(payload), ["notes/内存索引.md"])
                self.assertEqual(snapshot_files(vault), before)
                self.assertEqual(git_status(vault), status_before)
                self.assertEqual(git_head(vault), head_before)


class RawIngestTests(WikiCliTestCase):
    def add_binary_source(self, root: Path) -> tuple[Path, Path, str, str]:
        vault = self.init_vault(root)
        material = root / "研究论文.pdf"
        material.write_bytes(b"%PDF-1.7\r\n\x00\xff\x10original-bytes\r\n%%EOF")
        base = git_head(vault)
        result = run_cli(
            "add",
            str(material),
            "--base",
            base,
            "--name",
            "检索研究",
            cwd=vault,
        )
        payload = self.assert_exit(result, 0)
        self.assert_envelope(payload, "add", vault, ok=True)
        self.assertTrue(payload.get("pending"), payload)
        return vault, material, base, json.dumps(payload, ensure_ascii=False)

    def test_add_preserves_binary_bytes_and_creates_an_unsaved_source_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault, material, base, _payload_text = self.add_binary_source(root)
            raw_rel = "raw/检索研究/研究论文.pdf"
            source_rel = "sources/检索研究.md"
            raw = vault / raw_rel
            source = vault / source_rel

            self.assertEqual(raw.read_bytes(), material.read_bytes())
            self.assertTrue(source.is_file())
            source_text = source.read_text(encoding="utf-8")
            self.assertRegex(source_text, r"(?m)^kind:\s*[\"']?source[\"']?\s*$")
            self.assertRegex(source_text, r"(?m)^summary:\s*(?:\"\")?\s*$")
            self.assertRegex(source_text, r"(?m)^tags:\s*\[\]\s*$")
            self.assertIn(f"[[{raw_rel}]]", source_text)
            self.assertRegex(source_text, r"(?m)^# 检索研究\s*$")
            self.assertEqual(git_head(vault), base)
            self.assertTrue(git_status(vault))

            audit = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 4)
            self.assertIn("summary", json.dumps(audit, ensure_ascii=False).lower())
            self.assertEqual(raw.read_bytes(), material.read_bytes())

    def test_completed_source_save_commits_exact_raw_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault, material, base, _payload_text = self.add_binary_source(root)
            source_rel = "sources/检索研究.md"
            raw_rel = "raw/检索研究/研究论文.pdf"
            set_frontmatter_scalar(vault / source_rel, "summary", "检索研究的证据范围与限制。")

            payload = self.save(
                vault,
                base,
                operation="ingest",
                include=[source_rel, raw_rel],
            )
            self.assertIs(payload.get("saved"), True, payload)
            self.assertEqual(git_show_bytes(vault, f"HEAD:{raw_rel}"), material.read_bytes())
            self.assertEqual(git_status(vault), [])
            _, rows = read_index(vault)
            source_row = next(row for row in rows if row["path"] == source_rel)
            self.assertEqual(source_row["summary"], "检索研究的证据范围与限制。")

    def test_raw_markdown_preserves_crlf_bytes_through_save_and_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            material = root / "译文.md"
            original = b"\xef\xbb\xbf# Translation\r\n\r\nFirst line.\r\nSecond line.\r\n"
            material.write_bytes(original)
            attributes = run_git(
                vault,
                "check-attr",
                "text",
                "diff",
                "eol",
                "--",
                "raw/译文来源/译文.md",
            )
            self.assertIn("text: unset", str(attributes.stdout))
            self.assertIn("diff: unset", str(attributes.stdout))
            self.assertIn("eol: unset", str(attributes.stdout))

            base = git_head(vault)
            added = self.assert_exit(
                run_cli(
                    "add",
                    str(material),
                    "--base",
                    base,
                    "--name",
                    "译文来源",
                    cwd=vault,
                ),
                0,
            )
            source_rel = str(added["source"])
            raw_rel = str(added["raw"][0]["path"])  # type: ignore[index]
            set_frontmatter_scalar(vault / source_rel, "summary", "保留原始换行的译文来源。")

            saved = self.save(
                vault,
                base,
                operation="ingest",
                include=[source_rel, raw_rel],
            )
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual((vault / raw_rel).read_bytes(), original)
            self.assertEqual(git_show_bytes(vault, f"HEAD:{raw_rel}"), original)
            self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)

            clone = root / "clone"
            run_git(root, "clone", "--quiet", str(vault), str(clone))
            self.assertEqual((clone / raw_rel).read_bytes(), original)
            self.assertEqual(git_status(clone), [])

    def test_exact_duplicate_reuses_the_committed_raw_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault, material, base, _payload_text = self.add_binary_source(root)
            source_rel = "sources/检索研究.md"
            raw_rel = "raw/检索研究/研究论文.pdf"
            set_frontmatter_scalar(vault / source_rel, "summary", "证据范围。")
            self.save(vault, base, operation="ingest", include=[source_rel, raw_rel])
            committed_head = git_head(vault)
            raw_before = (vault / raw_rel).read_bytes()

            copy = root / "同内容副本.pdf"
            copy.write_bytes(material.read_bytes())
            result = run_cli(
                "add",
                str(copy),
                "--base",
                committed_head,
                "--name",
                "重复副本",
                cwd=vault,
            )
            payload = self.assert_exit(result, 0)
            self.assertIs(payload.get("pending"), False, payload)
            self.assertIs(payload.get("reused_source"), True, payload)
            self.assertTrue(any_nested_key(payload, "reused", True), payload)
            self.assertEqual(list((vault / "raw").rglob("*.pdf")), [vault / raw_rel])
            self.assertFalse((vault / "sources" / "重复副本.md").exists())
            self.assertEqual((vault / raw_rel).read_bytes(), raw_before)
            self.assertEqual(git_head(vault), committed_head)
            self.assertEqual(git_status(vault), [])

    def test_modified_committed_raw_requires_review_and_versions_the_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault, _material, base, _payload_text = self.add_binary_source(root)
            source_rel = "sources/检索研究.md"
            raw_rel = "raw/检索研究/研究论文.pdf"
            set_frontmatter_scalar(vault / source_rel, "summary", "证据范围。")
            self.save(vault, base, operation="ingest", include=[source_rel, raw_rel])
            committed_head = git_head(vault)
            original_commit_bytes = git_show_bytes(vault, f"HEAD:{raw_rel}")
            source_before = (vault / source_rel).read_bytes()
            changed = b"changed raw bytes retained as a new Git version"
            (vault / raw_rel).write_bytes(changed)

            audit = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 0)
            self.assertIs(audit.get("valid"), True, audit)
            begin_before = snapshot_files(vault)
            status_before = git_status(vault)
            begun = self.assert_exit(run_cli("begin", cwd=vault), 0)
            impact = begun.get("raw_impact")
            self.assertIsInstance(impact, dict, begun)
            assert isinstance(impact, dict)
            self.assertEqual(
                impact.get("changes"),
                [
                    {
                        "path": raw_rel,
                        "status": "modified",
                        "before_path": raw_rel,
                        "after_path": raw_rel,
                        "before_oid": run_git(vault, "rev-parse", f"HEAD:{raw_rel}").stdout.strip(),
                        "after_oid": run_git(vault, "hash-object", "--no-filters", "--", raw_rel).stdout.strip(),
                    }
                ],
            )
            self.assertEqual(impact.get("owner_sources"), [source_rel])
            self.assertEqual(impact.get("layers"), [{"distance": 1, "groups": ["g1"]}])
            self.assertEqual(snapshot_files(vault), begin_before)
            self.assertEqual(git_status(vault), status_before)

            wrong_operation = self.save(
                vault,
                committed_head,
                operation="edit",
                include=[raw_rel, source_rel],
                expected=4,
            )
            self.assertIn(
                "E_RAW_OPERATION",
                {item["code"] for item in wrong_operation.get("findings", [])},
            )
            missing_owner = self.save(
                vault,
                committed_head,
                operation="raw-update",
                include=[raw_rel],
                expected=4,
            )
            self.assertIn(
                "E_RAW_REVIEW_SCOPE",
                {item["code"] for item in missing_owner.get("findings", [])},
            )
            preview = self.save(
                vault,
                committed_head,
                operation="raw-update",
                include=[raw_rel, source_rel],
                expected=5,
            )
            self.assertIs(preview.get("review_required"), True, preview)
            self.assertEqual(preview.get("raw_impact"), impact)
            self.assertEqual((vault / raw_rel).read_bytes(), changed)
            self.assertEqual(git_show_bytes(vault, f"HEAD:{raw_rel}"), original_commit_bytes)
            self.assertEqual(git_head(vault), committed_head)
            self.assertEqual((vault / source_rel).read_bytes(), source_before)

            saved = self.save(
                vault,
                committed_head,
                operation="raw-update",
                include=[raw_rel, source_rel],
                approved=True,
            )
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(saved.get("raw_impact"), impact)
            self.assertEqual(git_show_bytes(vault, f"HEAD:{raw_rel}"), changed)
            self.assertEqual(git_show_bytes(vault, f"HEAD^:{raw_rel}"), original_commit_bytes)
            self.assertEqual((vault / source_rel).read_bytes(), source_before)
            self.assertEqual(git_status(vault), [])


class GitWorkflowTests(WikiCliTestCase):
    def test_candidate_audit_rejects_raw_that_exists_only_outside_save_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            raw_rel = "raw/omitted/evidence.pdf"
            raw = vault / raw_rel
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"%PDF-1.7 candidate-scope evidence")
            write_page(
                vault,
                "sources/omitted-raw.md",
                "omitted-raw",
                "source",
                summary="引用未纳入候选的原始材料。",
                aliases=[],
                tags=[],
                raw=[f"[[{raw_rel}]]"],
            )
            index_before = (vault / "index.csv").read_bytes()
            git_index_before = (vault / ".git" / "index").read_bytes()

            rejected = self.save(
                vault,
                base,
                operation="ingest",
                include=["sources/omitted-raw.md"],
                expected=4,
            )

            self.assertIs(rejected.get("saved"), False, rejected)
            codes = {
                finding.get("code")
                for finding in rejected.get("findings", [])
                if isinstance(finding, dict)
            }
            self.assertIn("E_RAW_REF", codes)
            self.assertEqual(git_head(vault), base)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual((vault / ".git" / "index").read_bytes(), git_index_before)
            self.assertEqual(raw.read_bytes(), b"%PDF-1.7 candidate-scope evidence")

    def test_edit_after_candidate_capture_remains_dirty_and_is_not_committed(self) -> None:
        runtime = load_wiki_runtime()
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            note = write_page(
                vault,
                "notes/captured.md",
                "captured",
                "note",
                summary="候选只保存捕获时的字节。",
                aliases=[],
                tags=[],
                sources=[],
                body="captured version",
            )
            captured = note.read_bytes()
            original_builder = runtime.build_candidate_checkpoint

            def build_then_edit(*arguments, **keywords):
                checkpoint = original_builder(*arguments, **keywords)
                with note.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write("edited after capture\n")
                return checkpoint

            args = SimpleNamespace(
                base=git_head(vault),
                operation="add",
                include=[["notes/captured.md"]],
                approved=False,
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(vault)
                with mock.patch.object(
                    runtime,
                    "build_candidate_checkpoint",
                    side_effect=build_then_edit,
                ), mock.patch.object(runtime, "emit"), contextlib.redirect_stdout(io.StringIO()):
                    result = runtime.command_save(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result, 0)
            self.assertEqual(git_show_bytes(vault, "HEAD:notes/captured.md"), captured)
            self.assertIn("edited after capture", note.read_text(encoding="utf-8"))
            self.assertTrue(any("notes/captured.md" in line for line in git_status(vault)))

    def test_head_change_before_install_rejects_candidate_without_overwriting_state(self) -> None:
        runtime = load_wiki_runtime()
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            note = write_page(
                vault,
                "notes/cas.md",
                "cas",
                "note",
                summary="引用更新必须比较旧 HEAD。",
                aliases=[],
                tags=[],
                sources=[],
            )
            worktree_before = snapshot_files(vault)
            index_path = vault / ".git" / "index"
            index_before = index_path.read_bytes()
            original_installer = runtime.install_candidate_checkpoint
            concurrent_commit: list[str] = []

            def move_head_then_install(*arguments, **keywords):
                tree = str(run_git(vault, "rev-parse", "HEAD^{tree}").stdout).strip()
                created = str(
                    run_git(
                        vault,
                        "commit-tree",
                        tree,
                        "-p",
                        base,
                        "-m",
                        "concurrent checkpoint",
                    ).stdout
                ).strip()
                run_git(vault, "update-ref", "HEAD", created, base)
                concurrent_commit.append(created)
                return original_installer(*arguments, **keywords)

            args = SimpleNamespace(
                base=base,
                operation="add",
                include=[["notes/cas.md"]],
                approved=False,
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(vault)
                with mock.patch.object(
                    runtime,
                    "install_candidate_checkpoint",
                    side_effect=move_head_then_install,
                ), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(runtime.WikiError) as raised:
                        runtime.command_save(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(raised.exception.code, 3)
            self.assertEqual(git_head(vault), concurrent_commit[0])
            self.assertEqual(index_path.read_bytes(), index_before)
            self.assertEqual(snapshot_files(vault), worktree_before)
            self.assertEqual(note.read_bytes(), worktree_before["notes/cas.md"])

    def test_save_bypasses_pre_commit_hook_and_commits_only_candidate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            hooks = {
                "pre-commit": "hook-added.txt",
                "reference-transaction": "reference-hook-ran.txt",
                "post-index-change": "index-hook-ran.txt",
            }
            for hook_name, marker in hooks.items():
                hook = vault / ".git" / "hooks" / hook_name
                hook.write_text(
                    f"#!/bin/sh\nprintf 'hook ran' > {marker}\n"
                    + ("git add hook-added.txt\n" if hook_name == "pre-commit" else ""),
                    encoding="utf-8",
                    newline="\n",
                )
                hook.chmod(0o755)
            note = write_page(
                vault,
                "notes/hook-safe.md",
                "hook-safe",
                "note",
                summary="保存不执行仓库 hooks。",
                aliases=[],
                tags=[],
                sources=[],
            )
            base = git_head(vault)

            saved = self.save(
                vault,
                base,
                operation="add",
                include=["notes/hook-safe.md"],
            )

            self.assertIs(saved.get("saved"), True, saved)
            for marker in hooks.values():
                self.assertFalse((vault / marker).exists(), marker)
            self.assertEqual(git_show_bytes(vault, "HEAD:notes/hook-safe.md"), note.read_bytes())
            changes = run_git(
                vault,
                "diff",
                "--no-renames",
                "--name-only",
                base,
                str(saved["commit"]),
                "--",
            )
            self.assertEqual(
                set(str(changes.stdout).splitlines()),
                {"index.csv", "notes/hook-safe.md"},
            )
            self.assertEqual(saved.get("changes"), str(changes.stdout).splitlines())

    def test_save_bypasses_clean_filter_and_commits_captured_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            filter_script = root / "mutating_filter.py"
            filter_script.write_text(
                "import sys\n"
                "data = sys.stdin.buffer.read()\n"
                "sys.stdout.buffer.write(data.replace(b'ORIGINAL_FILTER_BYTES', b'FILTERED_FILTER_BYTES'))\n",
                encoding="utf-8",
                newline="\n",
            )
            filter_command = f'"{sys.executable}" "{filter_script}"'
            run_git(vault, "config", "filter.wiki-clean.clean", filter_command)
            attributes = vault / ".gitattributes"
            with attributes.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("notes/*.md filter=wiki-clean\n")
            note = write_page(
                vault,
                "notes/filter-safe.md",
                "filter-safe",
                "note",
                summary="保存绕过 clean filter。",
                aliases=[],
                tags=[],
                sources=[],
                body="ORIGINAL_FILTER_BYTES",
            )
            filtered = run_git(
                vault,
                "hash-object",
                "--path",
                "notes/filter-safe.md",
                "--",
                "notes/filter-safe.md",
            )
            exact = run_git(
                vault,
                "hash-object",
                "--no-filters",
                "--",
                "notes/filter-safe.md",
            )
            self.assertNotEqual(str(filtered.stdout).strip(), str(exact.stdout).strip())

            saved = self.save(
                vault,
                git_head(vault),
                operation="add",
                include=[".gitattributes", "notes/filter-safe.md"],
            )

            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(git_show_bytes(vault, "HEAD:notes/filter-safe.md"), note.read_bytes())

    def test_begin_reports_clean_and_dirty_worktree_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            head = git_head(vault)
            clean = self.assert_exit(run_cli("begin", cwd=vault), 0)
            self.assert_envelope(clean, "begin", vault, ok=True)
            self.assertEqual(clean.get("base"), head)
            self.assertIs(clean.get("clean"), True)
            self.assertEqual(clean.get("changes"), [])

            write_page(vault, "inbox/想法.md", "想法", "inbox", body="一个待验证假设。")
            before = snapshot_files(vault)
            dirty = self.assert_exit(run_cli("begin", cwd=vault), 0)
            self.assertIs(dirty.get("clean"), False)
            self.assertTrue(dirty.get("changes"))
            self.assertEqual(dirty.get("base"), head)
            self.assertEqual(snapshot_files(vault), before)

    def test_explicit_scope_commits_only_selected_paths_and_preserves_other_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            first = write_page(
                vault,
                "notes/in-scope.md",
                "in-scope",
                "note",
                summary="Included page.",
                aliases=[],
                tags=[],
                sources=[],
            )
            second = write_page(
                vault,
                "notes/out-of-scope.md",
                "out-of-scope",
                "note",
                summary="Unrelated dirty page.",
                aliases=[],
                tags=[],
                sources=[],
            )
            payload = self.save(
                vault,
                base,
                operation="add",
                include=["notes/in-scope.md"],
            )
            self.assertIs(payload.get("saved"), True, payload)
            self.assertNotEqual(git_head(vault), base)
            self.assertEqual(
                git_show_bytes(vault, "HEAD:notes/in-scope.md"),
                first.read_bytes(),
            )
            missing = run_git(
                vault,
                "ls-tree",
                "--name-only",
                "HEAD",
                "--",
                "notes/out-of-scope.md",
            )
            self.assertEqual(str(missing.stdout).strip(), "")
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())
            self.assertTrue(any("out-of-scope.md" in line for line in git_status(vault)))
            staged = run_git(vault, "diff", "--cached", "--name-only")
            self.assertEqual(str(staged.stdout).strip(), "")

    def test_stale_base_rejects_save_and_keeps_edits_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            stale = git_head(vault)
            note = write_page(
                vault,
                "notes/versioned.md",
                "versioned",
                "note",
                summary="First version.",
                aliases=[],
                tags=[],
                sources=[],
            )
            self.save(vault, stale, operation="add", include=["notes/versioned.md"])
            current = git_head(vault)
            self.assertNotEqual(current, stale)
            with note.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("Uncommitted next version.\n")

            payload = self.save(
                vault,
                stale,
                operation="edit",
                include=["notes/versioned.md"],
                expected=3,
            )
            self.assertIn("base", json.dumps(payload, ensure_ascii=False).lower())
            self.assertEqual(git_head(vault), current)
            self.assertIn("Uncommitted next version.", note.read_text(encoding="utf-8"))
            self.assertTrue(git_status(vault))

    def test_high_risk_rename_previews_without_writes_then_commits_when_approved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            old = write_page(
                vault,
                "notes/旧名称.md",
                "旧名称",
                "note",
                summary="将被改名。",
                aliases=[],
                tags=[],
                sources=[],
            )
            self.save(vault, git_head(vault), operation="add", include=["notes/旧名称.md"])
            base = git_head(vault)
            new = vault / "notes" / "新名称.md"
            old.rename(new)
            text = new.read_text(encoding="utf-8").replace("# 旧名称", "# 新名称")
            new.write_text(text, encoding="utf-8", newline="\n")
            index_before = (vault / "index.csv").read_bytes()
            worktree_before = snapshot_files(vault)
            git_index_before = (vault / ".git" / "index").read_bytes()

            preview = self.save(
                vault,
                base,
                operation="rename",
                include=["notes/旧名称.md", "notes/新名称.md"],
                expected=5,
            )
            self.assertIs(preview.get("review_required"), True, preview)
            self.assertIs(preview.get("saved"), False, preview)
            self.assertTrue(preview.get("diff"), preview)
            self.assertEqual(git_head(vault), base)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual(snapshot_files(vault), worktree_before)
            self.assertEqual((vault / ".git" / "index").read_bytes(), git_index_before)
            staged = run_git(vault, "diff", "--cached", "--name-only")
            self.assertEqual(str(staged.stdout).strip(), "")
            self.assertTrue(new.is_file())
            self.assertFalse(old.exists())

            saved = self.save(
                vault,
                base,
                operation="rename",
                include=["notes/旧名称.md", "notes/新名称.md"],
                approved=True,
            )
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(git_status(vault), [])
            paths = [row["path"] for row in read_index(vault)[1]]
            self.assertIn("notes/新名称.md", paths)
            self.assertNotIn("notes/旧名称.md", paths)


if __name__ == "__main__":
    unittest.main()
