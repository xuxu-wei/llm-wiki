from __future__ import annotations

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


class InitAndSchemaTests(WikiCliTestCase):
    def test_help_exposes_the_current_commands(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.diagnostic())
        for command in ("init", "begin", "add", "context", "audit", "save"):
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

            self.assertEqual(git_status(vault), [])
            top = run_git(vault, "rev-parse", "--show-toplevel")
            assert isinstance(top.stdout, str)
            self.assertEqual(Path(top.stdout.strip()).resolve(), vault.resolve())
            commits = run_git(vault, "rev-list", "--count", "HEAD")
            self.assertEqual(str(commits.stdout).strip(), "1")
            self.assertRegex((vault / ".gitattributes").read_text(encoding="utf-8"), r"(?m)^raw/\*\*")
            self.assertIn(".obsidian/", (vault / ".gitignore").read_text(encoding="utf-8"))

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

    def test_modified_committed_raw_blocks_save_without_restoring_or_committing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault, _material, base, _payload_text = self.add_binary_source(root)
            source_rel = "sources/检索研究.md"
            raw_rel = "raw/检索研究/研究论文.pdf"
            set_frontmatter_scalar(vault / source_rel, "summary", "证据范围。")
            self.save(vault, base, operation="ingest", include=[source_rel, raw_rel])
            committed_head = git_head(vault)
            original_commit_bytes = git_show_bytes(vault, f"HEAD:{raw_rel}")
            changed = b"changed raw bytes that must remain visible"
            (vault / raw_rel).write_bytes(changed)

            audit = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 4)
            self.assertIn("raw", json.dumps(audit, ensure_ascii=False).lower())
            saved = self.save(
                vault,
                committed_head,
                operation="edit",
                include=[raw_rel],
                expected=4,
            )
            self.assertIs(saved.get("saved"), False, saved)
            self.assertEqual((vault / raw_rel).read_bytes(), changed)
            self.assertEqual(git_show_bytes(vault, f"HEAD:{raw_rel}"), original_commit_bytes)
            self.assertEqual(git_head(vault), committed_head)
            self.assertTrue(git_status(vault))


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
