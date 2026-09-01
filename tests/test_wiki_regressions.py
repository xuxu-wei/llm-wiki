from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import re
import runpy
import tempfile
from types import SimpleNamespace
import unicodedata
import unittest
from unittest import mock

from tests.test_wiki_cli import WikiCliTestCase, decide_tag, load_wiki_runtime
from tests.wiki_support import (
    any_nested_key,
    git_head,
    git_index_bytes,
    git_show_bytes,
    git_status,
    page_text,
    read_index,
    run_cli,
    run_git,
    SCRIPT_PATH,
    set_frontmatter_scalar,
    snapshot_files,
    write_page,
)


def findings_text(payload: dict[str, object]) -> str:
    return json.dumps(payload.get("findings", []), ensure_ascii=False).lower()


def finding_codes(payload: dict[str, object]) -> set[str]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise AssertionError(f"audit payload has no findings list: {payload!r}")
    codes: set[str] = set()
    for item in findings:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            raise AssertionError(f"invalid audit finding: {item!r}")
        codes.add(item["code"])
    return codes


def without_raw_property(text: str) -> str:
    return re.sub(r"(?m)^raw:.*(?:\n|$)", "", text, count=1)


class GitStateRegressionTests(WikiCliTestCase):
    def test_save_rejects_partially_staged_content_without_touching_index_or_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            note = write_page(
                vault,
                "notes/部分暂存.md",
                "部分暂存",
                "note",
                summary="暂存版本与工作区版本不同。",
                aliases=[],
                tags=[],
                sources=[],
                body="staged body",
            )
            run_git(vault, "add", "--", "notes/部分暂存.md")
            staged_before = git_index_bytes(vault, "notes/部分暂存.md")
            with note.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("worktree-only body\n")
            worktree_before = note.read_bytes()
            index_before = (vault / "index.csv").read_bytes()
            index_mtime = (vault / "index.csv").stat().st_mtime_ns
            status_before = git_status(vault)

            payload = self.save(
                vault,
                base,
                operation="add",
                include=["notes/部分暂存.md"],
                expected=3,
            )
            self.assertRegex(json.dumps(payload, ensure_ascii=False).lower(), r"stag|index")
            self.assertEqual(git_head(vault), base)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual((vault / "index.csv").stat().st_mtime_ns, index_mtime)
            self.assertEqual(git_index_bytes(vault, "notes/部分暂存.md"), staged_before)
            self.assertEqual(note.read_bytes(), worktree_before)
            self.assertEqual(git_status(vault), status_before)
            staged_names = run_git(vault, "diff", "--cached", "--name-only", "-z")
            self.assertEqual(str(staged_names.stdout).rstrip("\0"), "notes/部分暂存.md")

    def test_clone_retains_required_directories_and_is_immediately_usable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = self.init_vault(root)
            clone = root / "clone"
            run_git(root, "clone", "--quiet", str(original), str(clone))

            for directory in ("inbox", "raw", "sources", "notes", "assets"):
                self.assertTrue((clone / directory).is_dir(), directory)
            self.assertEqual(git_status(clone), [])
            begin = self.assert_exit(run_cli("begin", cwd=clone), 0)
            self.assertIs(begin.get("clean"), True, begin)
            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=clone), 0)
            self.assertIs(audit.get("valid"), True, audit)


class VaultIdentityRegressionTests(WikiCliTestCase):
    @staticmethod
    def make_repository(path: Path) -> None:
        path.mkdir()
        run_git(path, "init", "--quiet")
        for directory in ("inbox", "raw", "sources", "notes", "assets"):
            (path / directory).mkdir()

    def test_non_contract_repository_has_read_only_health_findings_and_strict_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "ordinary-repo"
            self.make_repository(repo)
            (repo / "README.md").write_text("# Ordinary project\n", encoding="utf-8")
            run_git(repo, "add", "README.md")
            run_git(repo, "commit", "-m", "ordinary project")
            (repo / "index.csv").write_text(
                "path,kind,summary,aliases,tags\n",
                encoding="utf-8",
                newline="\n",
            )
            before = snapshot_files(repo)
            head_before = git_head(repo)
            status_before = git_status(repo)
            staged_before = run_git(repo, "diff", "--cached", "--binary").stdout

            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=repo), 4)
            self.assert_envelope(audit, "audit", repo, ok=False)
            self.assertIs(audit.get("valid"), False, audit)
            self.assertIn("E_VAULT_TRACKING", finding_codes(audit))
            self.assertIn("E_HOME_HEAD_COUNT", finding_codes(audit))

            strict_commands = (
                ("begin",),
                ("add", "README.md", "--base", head_before, "--name", "来源"),
                ("context", "--plan", '{"query":"ordinary"}'),
                (
                    "save",
                    "--base",
                    head_before,
                    "--operation",
                    "edit",
                    "--include",
                    "README.md",
                ),
            )
            for arguments in strict_commands:
                with self.subTest(command=arguments[0]):
                    payload = self.assert_exit(run_cli(*arguments, cwd=repo), 2)
                    self.assert_envelope(payload, arguments[0], repo, ok=False)
                    self.assertRegex(
                        json.dumps(payload, ensure_ascii=False).lower(),
                        r"(?:wiki|contract)",
                    )

            self.assertEqual(snapshot_files(repo), before)
            self.assertEqual(git_head(repo), head_before)
            self.assertEqual(git_status(repo), status_before)
            self.assertEqual(run_git(repo, "diff", "--cached", "--binary").stdout, staged_before)
            self.assertIn("?? index.csv", status_before)

    def test_unborn_git_repository_reports_a_missing_head_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "unborn-repo"
            repo.mkdir()
            run_git(repo, "init", "--quiet")
            before = snapshot_files(repo)
            status_before = git_status(repo)

            payload = self.assert_exit(run_cli("audit", "--scope", "all", cwd=repo), 4)
            self.assert_envelope(payload, "audit", repo, ok=False)
            self.assertIs(payload.get("valid"), False, payload)
            self.assertIn("E_VAULT_HEAD", finding_codes(payload))
            self.assertEqual(snapshot_files(repo), before)
            self.assertEqual(git_status(repo), status_before)


class AuditHealthRegressionTests(WikiCliTestCase):
    def test_valid_complete_health_check_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            files_before = snapshot_files(vault)
            head_before = git_head(vault)
            status_before = git_status(vault)
            staged_before = run_git(vault, "diff", "--cached", "--binary").stdout
            unstaged_before = run_git(vault, "diff", "--binary").stdout

            payload = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assert_envelope(payload, "audit", vault, ok=True)
            self.assertIs(payload.get("valid"), True, payload)
            self.assertEqual(payload.get("findings"), [])
            self.assertEqual(snapshot_files(vault), files_before)
            self.assertEqual(git_head(vault), head_before)
            self.assertEqual(git_status(vault), status_before)
            self.assertEqual(run_git(vault, "diff", "--cached", "--binary").stdout, staged_before)
            self.assertEqual(run_git(vault, "diff", "--binary").stdout, unstaged_before)

    def test_health_check_reports_missing_core_file_and_directory_in_every_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            (vault / "AGENTS.md").unlink()
            (vault / "assets" / ".gitkeep").unlink()
            (vault / "assets").rmdir()
            files_before = snapshot_files(vault)
            head_before = git_head(vault)
            status_before = git_status(vault)
            staged_before = run_git(vault, "diff", "--cached", "--binary").stdout

            payload = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            codes = finding_codes(payload)
            self.assertIn("E_VAULT_FILE", codes)
            self.assertIn("E_VAULT_DIR", codes)

            text_result = run_cli("audit", "--scope", "all", "--format", "text", cwd=vault)
            self.assertEqual(text_result.returncode, 4, text_result.diagnostic())
            self.assertRegex(text_result.stdout, r"(?m)^invalid$")
            self.assertIn("E_VAULT_FILE", text_result.stdout)
            self.assertIn("E_VAULT_DIR", text_result.stdout)

            csv_result = run_cli("audit", "--scope", "all", "--format", "csv", cwd=vault)
            self.assertEqual(csv_result.returncode, 4, csv_result.diagnostic())
            rows = list(csv.DictReader(io.StringIO(csv_result.stdout)))
            self.assertEqual(set(rows[0]), {"code", "path", "field", "message"})
            self.assertTrue({"E_VAULT_FILE", "E_VAULT_DIR"} <= {row["code"] for row in rows})

            self.assertEqual(snapshot_files(vault), files_before)
            self.assertEqual(git_head(vault), head_before)
            self.assertEqual(git_status(vault), status_before)
            self.assertEqual(run_git(vault, "diff", "--cached", "--binary").stdout, staged_before)

    def test_non_file_index_reports_structured_read_only_findings_in_every_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            index_path = vault / "index.csv"
            index_path.unlink()
            index_path.mkdir()
            files_before = snapshot_files(vault)
            head_before = git_head(vault)
            status_before = git_status(vault)
            staged_before = run_git(vault, "diff", "--cached", "--binary").stdout
            unstaged_before = run_git(vault, "diff", "--binary").stdout

            payload = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assert_envelope(payload, "audit", vault, ok=False)
            self.assertIs(payload.get("valid"), False, payload)
            self.assertTrue(
                any(
                    item.get("code") == "E_VAULT_FILE" and item.get("path") == "index.csv"
                    for item in payload.get("findings", [])
                    if isinstance(item, dict)
                ),
                payload,
            )

            text_result = run_cli("audit", "--scope", "all", "--format", "text", cwd=vault)
            self.assertEqual(text_result.returncode, 4, text_result.diagnostic())
            self.assertRegex(text_result.stdout, r"(?m)^invalid$")
            self.assertRegex(text_result.stdout, r"(?m)^E_VAULT_FILE index\.csv\b")

            csv_result = run_cli("audit", "--scope", "all", "--format", "csv", cwd=vault)
            self.assertEqual(csv_result.returncode, 4, csv_result.diagnostic())
            rows = list(csv.DictReader(io.StringIO(csv_result.stdout)))
            self.assertTrue(
                any(row["code"] == "E_VAULT_FILE" and row["path"] == "index.csv" for row in rows),
                rows,
            )

            self.assertTrue(index_path.is_dir())
            self.assertEqual(snapshot_files(vault), files_before)
            self.assertEqual(git_head(vault), head_before)
            self.assertEqual(git_status(vault), status_before)
            self.assertEqual(run_git(vault, "diff", "--cached", "--binary").stdout, staged_before)
            self.assertEqual(run_git(vault, "diff", "--binary").stdout, unstaged_before)

    def test_changed_scope_keeps_clean_global_tracking_and_home_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            run_git(vault, "rm", "AGENTS.md")
            write_page(
                vault,
                "第二首页.md",
                "第二首页",
                "moc",
                summary="用于验证 HEAD 中首页身份约束。",
                aliases=[],
                tags=[],
            )
            run_git(vault, "add", "第二首页.md")
            run_git(vault, "commit", "-m", "break global wiki structure")
            self.assertEqual(git_status(vault), [])
            files_before = snapshot_files(vault)
            head_before = git_head(vault)

            complete = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            changed = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 4)
            required = {"E_VAULT_TRACKING", "E_HOME_HEAD_COUNT"}
            self.assertTrue(required <= finding_codes(complete), complete)
            self.assertTrue(required <= finding_codes(changed), changed)
            self.assertEqual(snapshot_files(vault), files_before)
            self.assertEqual(git_head(vault), head_before)
            self.assertEqual(git_status(vault), [])


class TagPolicyRegressionTests(WikiCliTestCase):
    def test_policy_accepts_uniform_crlf_but_rejects_other_noncanonical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            policy = vault / "tags-review.csv"
            canonical = (
                b"\xef\xbb\xbf"
                b"tag,page_count,action,target\n"
                b"alpha,1,keep,\n"
                b"beta,2,delete,\n"
            )
            crlf = canonical.replace(b"\n", b"\r\n")

            policy.write_bytes(crlf)
            vocabulary = self.tag_vocabulary(vault)
            self.assertEqual(vocabulary.get("preferred_tags"), ["alpha"])
            self.assertEqual(vocabulary.get("forbidden_tags"), ["beta"])
            self.assertEqual(policy.read_bytes(), crlf)

            invalid_variants = {
                "missing-bom": crlf[3:],
                "unsorted": (
                    b"\xef\xbb\xbf"
                    b"tag,page_count,action,target\r\n"
                    b"beta,2,delete,\r\n"
                    b"alpha,1,keep,\r\n"
                ),
                "mixed-newlines": crlf.replace(b"\r\n", b"\n", 1),
                "bare-carriage-return": canonical.replace(b"\n", b"\r", 1),
                "extra-blank-row": crlf + b"\r\n",
            }
            for name, data in invalid_variants.items():
                with self.subTest(case=name):
                    policy.write_bytes(data)
                    before = policy.read_bytes()
                    self.tag_vocabulary(vault, expected=2)
                    self.assertEqual(policy.read_bytes(), before)

    def test_legacy_policy_is_usable_after_an_autocrlf_clone_without_empty_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            attributes = vault / ".gitattributes"
            attributes.write_text(
                attributes.read_text(encoding="utf-8").replace(
                    "tags-review.csv text eol=lf\n",
                    "",
                ),
                encoding="utf-8",
                newline="\n",
            )
            run_git(vault, "rm", "--", "tags-review.csv")
            run_git(vault, "add", "--", ".gitattributes")
            run_git(vault, "commit", "-m", "fixture: legacy tag policy attributes")

            write_page(
                vault,
                "notes/旧仓库标签.md",
                "旧仓库标签",
                "note",
                summary="验证旧仓库在自动换行转换后的标签策略。",
                aliases=[],
                tags=["保留标签"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/旧仓库标签.md"])
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base, output=root / "review.csv")
            assert plan is not None
            decide_tag(plan, "保留标签", "keep")
            self.apply_tags(vault, base, plan, approved=True)
            self.merge_tags(vault, base, plan, approved=True)
            self.save(
                vault,
                base,
                operation="tag-policy",
                include=["tags-review.csv"],
                approved=True,
            )

            clone = root / "autocrlf-clone"
            run_git(
                root,
                "-c",
                "core.autocrlf=true",
                "clone",
                "--quiet",
                str(vault),
                str(clone),
            )
            run_git(clone, "config", "core.autocrlf", "true")
            policy = clone / "tags-review.csv"
            policy_bytes = policy.read_bytes()
            self.assertTrue(policy_bytes.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", policy_bytes)
            self.assertNotIn(b"\n", policy_bytes.replace(b"\r\n", b""))
            self.assertEqual(git_status(clone), [])

            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=clone), 0)
            self.assertIs(audit.get("valid"), True, audit)
            vocabulary = self.tag_vocabulary(clone)
            self.assertEqual(vocabulary.get("preferred_tags"), ["保留标签"])
            checked = self.check_tags(clone, ["保留标签"])
            self.assertEqual(checked.get("accepted_tags"), ["保留标签"])

            clone_base = git_head(clone)
            _payload, clone_plan = self.collect_tags(
                clone,
                clone_base,
                output=root / "clone-review.csv",
            )
            assert clone_plan is not None
            before_bytes = policy.read_bytes()
            before_mtime = policy.stat().st_mtime_ns
            merged = self.merge_tags(clone, clone_base, clone_plan, approved=True)
            self.assertIs(merged.get("changed"), False, merged)
            self.assertEqual(merged.get("changed_paths"), [], merged)
            self.assertEqual(policy.read_bytes(), before_bytes)
            self.assertEqual(policy.stat().st_mtime_ns, before_mtime)
            self.assertEqual(git_status(clone), [])

    def test_audit_and_save_reject_deleting_a_tracked_tag_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            policy = vault / "tags-review.csv"
            policy.unlink()
            before_status = git_status(vault)

            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertTrue(
                {"E_VAULT_FILE", "E_TAG_POLICY"} & finding_codes(audit),
                audit,
            )
            self.tag_vocabulary(vault, expected=2)
            self.check_tags(vault, ["任意标签"], expected=2)
            self.save(
                vault,
                base,
                operation="tag-policy",
                include=["tags-review.csv"],
                approved=True,
                expected=4,
            )
            self.assertEqual(git_head(vault), base)
            self.assertEqual(git_status(vault), before_status)
            self.assertFalse(policy.exists())

    def test_conflicting_tag_policy_is_one_structured_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            policy = vault / "tags-review.csv"
            policy.write_bytes(
                b"\xef\xbb\xbf"
                + (
                    "tag,page_count,action,target\n"
                    "old,1,rename,middle\n"
                    "middle,1,rename,final\n"
                ).encode("utf-8")
            )
            before = snapshot_files(vault)
            status_before = git_status(vault)
            payload = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertIn("E_TAG_POLICY", finding_codes(payload))
            policy_findings = [
                item
                for item in payload.get("findings", [])
                if isinstance(item, dict) and item.get("code") == "E_TAG_POLICY"
            ]
            self.assertEqual(len(policy_findings), 1, payload)
            self.assertEqual(policy_findings[0].get("path"), "tags-review.csv")
            self.assertEqual(snapshot_files(vault), before)
            self.assertEqual(git_status(vault), status_before)

    def test_policy_bytes_require_review_and_temporary_plan_cannot_be_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            policy = vault / "tags-review.csv"
            policy.write_bytes(
                b"\xef\xbb\xbf"
                + "tag,page_count,action,target\nOld,1,rename,New\n".encode("utf-8")
            )
            base = git_head(vault)
            preview = self.save(
                vault,
                base,
                operation="add",
                include=["tags-review.csv"],
                expected=5,
            )
            self.assertTrue(preview.get("review_required"), preview)
            self.assertEqual(git_head(vault), base)
            self.save(
                vault,
                base,
                operation="add",
                include=["tags-review.csv"],
                approved=True,
            )
            checked = self.check_tags(vault, ["old"], expected=3)
            self.assertEqual(
                checked.get("rejected_tags"),
                [
                    {
                        "tag": "old",
                        "reason": "noncanonical_rename_source",
                        "replacement": "New",
                    }
                ],
            )

            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base)
            assert plan is not None
            rejected = self.save(
                vault,
                base,
                operation="add",
                include=[plan.name],
                approved=True,
                expected=4,
            )
            self.assertIn("E_TAG_POLICY", finding_codes(rejected))
            self.assertEqual(git_head(vault), base)
            self.assertTrue(plan.is_file())

    def test_tag_checkpoint_operations_reject_mixed_or_non_tag_page_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            page = write_page(
                vault,
                "notes/策略夹带.md",
                "策略夹带",
                "note",
                summary="策略检查点不能夹带页面变化。",
                aliases=[],
                tags=["原标签"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/策略夹带.md"])
            base = git_head(vault)
            with page.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("Unrelated body edit.\n")
            (vault / "tags-review.csv").write_bytes(
                b"\xef\xbb\xbf"
                + "tag,page_count,action,target\n原标签,1,keep,\n".encode("utf-8")
            )
            status_before = git_status(vault)

            rejected = self.save(
                vault,
                base,
                operation="tag-policy",
                include=["tags-review.csv", "notes/策略夹带.md"],
                approved=True,
                expected=4,
            )
            self.assertIn("E_TAG_CHECKPOINT", finding_codes(rejected))
            self.assertEqual(git_head(vault), base)
            self.assertEqual(git_status(vault), status_before)

        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            page = write_page(
                vault,
                "notes/标签夹带正文.md",
                "标签夹带正文",
                "note",
                summary="标签检查点只能修改顶层标签。",
                aliases=[],
                tags=["旧标签"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/标签夹带正文.md"])
            base = git_head(vault)
            text = page.read_text(encoding="utf-8")
            page.write_text(
                text.replace('tags: ["旧标签"]', 'tags: ["新标签"]')
                + "Unapproved body change.\n",
                encoding="utf-8",
                newline="\n",
            )
            status_before = git_status(vault)

            rejected = self.save(
                vault,
                base,
                operation="tag-maintenance",
                include=["notes/标签夹带正文.md"],
                approved=True,
                expected=4,
            )
            self.assertIn("E_TAG_CHECKPOINT", finding_codes(rejected))
            self.assertEqual(git_head(vault), base)
            self.assertEqual(git_status(vault), status_before)

    def test_merge_rechecks_plan_and_never_overwrites_a_concurrent_policy_create(self) -> None:
        runtime = load_wiki_runtime()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            run_git(vault, "rm", "--", "tags-review.csv")
            run_git(vault, "commit", "-m", "fixture: legacy vault")
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base, output=root / "review.csv")
            assert plan is not None
            original_plan = plan.read_bytes()
            original_verify = runtime.verify_tag_policy_checkpoint
            verify_calls = 0

            def mutate_plan_after_verify(*args, **kwargs):
                nonlocal verify_calls
                verify_calls += 1
                result = original_verify(*args, **kwargs)
                if verify_calls == 2:
                    plan.write_bytes(original_plan + b"\n")
                return result

            arguments = SimpleNamespace(
                base=base,
                plan=str(plan),
                amendments=None,
                approved=True,
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(vault)
                with mock.patch.object(
                    runtime,
                    "verify_tag_policy_checkpoint",
                    side_effect=mutate_plan_after_verify,
                ):
                    with self.assertRaises(runtime.WikiError) as stale:
                        runtime.command_tags_merge(arguments)
                self.assertEqual(stale.exception.code, 3)
                self.assertEqual(verify_calls, 2)
                self.assertFalse((vault / "tags-review.csv").exists())

                plan.write_bytes(original_plan)
                concurrent = (
                    b"\xef\xbb\xbf"
                    + "tag,page_count,action,target\nconcurrent,1,delete,\n".encode("utf-8")
                )
                original_link = runtime.os.link

                def publish_concurrent_policy(source, destination):
                    Path(destination).write_bytes(concurrent)
                    return original_link(source, destination)

                with mock.patch.object(runtime.os, "link", side_effect=publish_concurrent_policy):
                    with self.assertRaises(runtime.WikiError) as raced:
                        runtime.command_tags_merge(arguments)
                self.assertEqual(raced.exception.code, 3)
                self.assertEqual((vault / "tags-review.csv").read_bytes(), concurrent)
            finally:
                os.chdir(previous_cwd)

    def test_apply_rechecks_policy_before_the_first_page_write(self) -> None:
        runtime = load_wiki_runtime()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            page = write_page(
                vault,
                "notes/策略竞态.md",
                "策略竞态",
                "note",
                summary="页面写入前必须重新确认标签策略。",
                aliases=[],
                tags=["Old"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/策略竞态.md"])
            base = git_head(vault)
            _payload, plan = self.collect_tags(vault, base, output=root / "apply.csv")
            assert plan is not None
            decide_tag(plan, "Old", "rename", "New")
            policy = vault / "tags-review.csv"
            concurrent = (
                b"\xef\xbb\xbf"
                + "tag,page_count,action,target\nconcurrent,1,keep,\n".encode("utf-8")
            )
            original_policy_reader = runtime.current_tag_policy_bytes

            def change_policy_at_final_guard(path):
                policy.write_bytes(concurrent)
                return original_policy_reader(path)

            arguments = SimpleNamespace(
                base=base,
                plan=str(plan),
                amendments=None,
                approved=True,
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(vault)
                with mock.patch.object(
                    runtime,
                    "current_tag_policy_bytes",
                    side_effect=change_policy_at_final_guard,
                ):
                    with self.assertRaises(runtime.WikiError) as raised:
                        runtime.command_tags_apply(arguments)
            finally:
                os.chdir(previous_cwd)
            self.assertEqual(raised.exception.code, 3)
            self.assertIn('tags: ["Old"]', page.read_text(encoding="utf-8"))
            self.assertEqual(policy.read_bytes(), concurrent)
            self.assertEqual(git_head(vault), base)

    def test_merge_rejects_mode_changes_and_casefold_policy_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            page = write_page(
                vault,
                "notes/模式.md",
                "模式",
                "note",
                summary="标签页面检查点不得夹带文件模式变化。",
                aliases=[],
                tags=["Old"],
                sources=[],
            )
            self.save(vault, git_head(vault), include=["notes/模式.md"])
            source = git_head(vault)
            _payload, plan = self.collect_tags(vault, source, output=root / "mode.csv")
            assert plan is not None
            decide_tag(plan, "Old", "rename", "New")
            self.apply_tags(vault, source, plan, approved=True)
            self.save(
                vault,
                source,
                operation="tag-maintenance",
                include=["notes/模式.md"],
                approved=True,
            )
            run_git(vault, "update-index", "--chmod=+x", "--", "notes/模式.md")
            run_git(vault, "commit", "--amend", "--no-edit")
            page_checkpoint = git_head(vault)
            rejected = self.merge_tags(vault, page_checkpoint, plan, approved=True, expected=3)
            self.assertIn("mode", json.dumps(rejected, ensure_ascii=False).lower())
            self.assertEqual(
                (vault / "tags-review.csv").read_bytes(),
                git_show_bytes(vault, f"{page_checkpoint}:tags-review.csv"),
            )
            self.assertTrue(page.is_file())

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            run_git(vault, "rm", "--", "tags-review.csv")
            run_git(vault, "commit", "-m", "fixture: legacy vault")
            fixture = root / "variant.csv"
            fixture.write_bytes(b"\xef\xbb\xbftag,page_count,action,target\n")
            oid = str(run_git(vault, "hash-object", "-w", str(fixture)).stdout).strip()
            run_git(
                vault,
                "update-index",
                "--add",
                "--cacheinfo",
                "100644",
                oid,
                "Tags-Review.csv",
            )
            run_git(vault, "commit", "-m", "fixture: casefold policy path")
            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertIn("E_TAG_POLICY", finding_codes(audit))
            self.tag_vocabulary(vault, expected=2)


class RawVersionRegressionTests(WikiCliTestCase):
    def test_old_raw_attribute_rule_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            attributes = vault / ".gitattributes"
            attributes.write_text(
                attributes.read_text(encoding="utf-8").replace(
                    "raw/** -text -diff -eol",
                    "raw/** -text -diff",
                ),
                encoding="utf-8",
                newline="\n",
            )

            audit = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 4)
            self.assertIn("E_RAW_ATTRIBUTES", finding_codes(audit))
            saved = self.save(
                vault,
                base,
                operation="edit",
                include=[".gitattributes"],
                expected=4,
            )
            self.assertIs(saved.get("saved"), False, saved)
            self.assertEqual(git_head(vault), base)

    def test_add_new_raw_version_updates_existing_source_as_a_reviewed_pending_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            version_one_dir = root / "version-one"
            version_two_dir = root / "version-two"
            version_one_dir.mkdir()
            version_two_dir.mkdir()
            version_one = version_one_dir / "paper.pdf"
            version_two = version_two_dir / "paper.pdf"
            version_one.write_bytes(b"%PDF-1.7\x00version-one\xff")
            version_two.write_bytes(b"%PDF-1.7\x00version-two\xfe")

            base = git_head(vault)
            first_add = self.assert_exit(
                run_cli(
                    "add",
                    str(version_one),
                    "--base",
                    base,
                    "--name",
                    "持续更新来源",
                    cwd=vault,
                ),
                0,
            )
            self.assertIs(first_add.get("pending"), True, first_add)
            source_rel = "sources/持续更新来源.md"
            first_raw_rel = first_add["raw"][0]["path"]  # type: ignore[index]
            source = vault / source_rel
            set_frontmatter_scalar(source, "summary", "持续更新来源的证据范围。")
            source_text = source.read_text(encoding="utf-8")
            source_text = source_text.replace('aliases: []', 'aliases: ["Durable Alias"]')
            source_text = source_text.replace('tags: []', 'tags: ["longitudinal"]')
            source_text = source_text.replace(
                "raw:",
                'identifiers: ["doi=10.0000/example"]\nraw:',
                1,
            )
            source_text += "用户维护的分析正文必须保留。\n"
            source.write_text(source_text, encoding="utf-8", newline="\n")
            self.save(vault, base, operation="ingest", include=[source_rel, first_raw_rel])

            checkpoint = git_head(vault)
            source_before = source.read_text(encoding="utf-8")
            first_bytes = (vault / first_raw_rel).read_bytes()
            index_before = (vault / "index.csv").read_bytes()
            second_add = self.assert_exit(
                run_cli(
                    "add",
                    str(version_two),
                    "--base",
                    checkpoint,
                    "--name",
                    "持续更新来源",
                    cwd=vault,
                ),
                0,
            )
            self.assertIs(second_add.get("pending"), True, second_add)
            self.assertIs(second_add.get("reused_source"), True, second_add)
            self.assertEqual(second_add.get("source"), source_rel)
            self.assertFalse(any_nested_key(second_add.get("raw"), "reused", True))
            second_raw_rel = second_add["raw"][0]["path"]  # type: ignore[index]
            self.assertNotEqual(second_raw_rel, first_raw_rel)
            self.assertEqual((vault / first_raw_rel).read_bytes(), first_bytes)
            self.assertEqual((vault / second_raw_rel).read_bytes(), version_two.read_bytes())

            source_after = source.read_text(encoding="utf-8")
            self.assertEqual(without_raw_property(source_after), without_raw_property(source_before))
            self.assertIn(f"[[{first_raw_rel}]]", source_after)
            self.assertIn(f"[[{second_raw_rel}]]", source_after)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            changed_audit = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 0)
            self.assertIs(changed_audit.get("valid"), True, changed_audit)

            preview = self.save(
                vault,
                checkpoint,
                operation="ingest",
                include=[source_rel, second_raw_rel],
                expected=5,
            )
            self.assertIs(preview.get("review_required"), True, preview)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            saved = self.save(
                vault,
                checkpoint,
                operation="ingest",
                include=[source_rel, second_raw_rel],
                approved=True,
            )
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(git_show_bytes(vault, f"HEAD:{first_raw_rel}"), version_one.read_bytes())
            self.assertEqual(git_show_bytes(vault, f"HEAD:{second_raw_rel}"), version_two.read_bytes())
            self.assertEqual(without_raw_property(source.read_text(encoding="utf-8")), without_raw_property(source_before))
            self.assertEqual(git_status(vault), [])

    def test_effective_raw_filter_attribute_is_rejected_even_with_literal_no_text_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            attributes = vault / ".gitattributes"
            with attributes.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("raw/** filter=mutate\n")
            effective = run_git(vault, "check-attr", "filter", "--", "raw/probe.bin")
            self.assertIn("filter: mutate", str(effective.stdout))
            index_before = (vault / "index.csv").read_bytes()

            audit = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 4)
            self.assertIn("e_raw_attributes", findings_text(audit))
            saved = self.save(vault, base, operation="edit", include=[".gitattributes"], expected=4)
            self.assertIs(saved.get("saved"), False, saved)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            staged = run_git(vault, "diff", "--cached", "--name-only")
            self.assertEqual(str(staged.stdout).strip(), "")

    def test_candidate_audit_uses_the_source_repository_info_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            base = git_head(vault)
            info_attributes = vault / ".git" / "info" / "attributes"
            info_attributes.write_text("raw/** text\n", encoding="utf-8", newline="\n")
            material = root / "local-attributes.pdf"
            material.write_bytes(b"%PDF-1.7\x00source-repository-attributes\xff")
            added = self.assert_exit(
                run_cli("add", str(material), "--base", base, "--name", "本地属性", cwd=vault),
                0,
            )
            source_rel = str(added["source"])
            raw_rel = str(added["raw"][0]["path"])  # type: ignore[index]
            set_frontmatter_scalar(vault / source_rel, "summary", "候选必须使用来源仓库的实际属性环境。")

            rejected = self.save(
                vault,
                base,
                operation="ingest",
                include=[source_rel, raw_rel],
                expected=4,
            )

            self.assertIs(rejected.get("saved"), False, rejected)
            self.assertIn("E_RAW_ATTRIBUTES", finding_codes(rejected))
            self.assertEqual(git_head(vault), base)


class RawPathRegressionTests(WikiCliTestCase):
    def test_portable_keys_detect_case_and_unicode_collisions_without_rewriting_paths(self) -> None:
        runtime = runpy.run_path(str(SCRIPT_PATH))
        portable_findings = runtime["portable_path_findings"]
        decomposed = "raw/资料/cafe\u0301.pdf"
        composed = unicodedata.normalize("NFC", decomposed)
        paths = [decomposed, composed, "assets/Figure.png", "assets/figure.png"]

        findings = portable_findings(paths)
        collisions = [item for item in findings if item["code"] == "E_PATH_COLLISION"]
        normalization = [item for item in findings if item["code"] == "E_PATH_NORMALIZATION"]
        self.assertEqual(len(collisions), 2, findings)
        self.assertEqual([item["path"] for item in normalization], [decomposed])
        collision_text = json.dumps(collisions, ensure_ascii=False)
        self.assertIn(decomposed, collision_text)
        self.assertIn(composed, collision_text)

    def test_head_core_paths_participate_in_portable_collision_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            index_oid = str(run_git(vault, "rev-parse", "HEAD:index.csv").stdout).strip()
            run_git(vault, "update-index", "--add", "--cacheinfo", "100644", index_oid, "Index.csv")
            run_git(vault, "commit", "-m", "inject core path collision")

            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)

            self.assertIn("E_PATH_COLLISION", finding_codes(audit))
            self.assertIn("Index.csv", json.dumps(audit.get("findings"), ensure_ascii=False))

    def test_head_raw_blob_map_preserves_exact_git_path_identity(self) -> None:
        runtime = runpy.run_path(str(SCRIPT_PATH))
        head_raw_blobs = runtime["head_raw_blobs"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            material = root / "material.bin"
            material.write_bytes(b"exact-path-identity")
            oid_result = run_git(vault, "hash-object", "-w", str(material))
            oid = str(oid_result.stdout).strip()
            decomposed = "raw/资料/cafe\u0301.bin"
            composed = unicodedata.normalize("NFC", decomposed)
            for rel in (decomposed, composed):
                run_git(vault, "update-index", "--add", "--cacheinfo", "100644", oid, rel)
            run_git(vault, "commit", "-m", "inject portable path collision")

            blobs = head_raw_blobs(vault)
            self.assertEqual(blobs[decomposed], oid)
            self.assertEqual(blobs[composed], oid)
            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertIn("E_PATH_COLLISION", finding_codes(audit))

    def test_add_rejects_raw_control_names_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            base = git_head(vault)
            before = snapshot_files(vault)
            status_before = git_status(vault)
            control_names = (".gitignore", ".gitattributes", ".gitmodules", "AGENTS.md", ".gitkeep", ".git")
            for number, control_name in enumerate(control_names):
                with self.subTest(control_name=control_name):
                    input_dir = root / f"control-{number}"
                    input_dir.mkdir()
                    material = input_dir / control_name
                    material.write_bytes(f"control-{number}".encode("ascii"))
                    payload = self.assert_exit(
                        run_cli(
                            "add",
                            str(material),
                            "--base",
                            base,
                            "--name",
                            f"控制名{number}",
                            cwd=vault,
                        ),
                        2,
                    )
                    self.assertRegex(json.dumps(payload, ensure_ascii=False).lower(), r"unsafe|reserved|raw")
                    self.assertEqual(snapshot_files(vault), before)
                    self.assertEqual(git_status(vault), status_before)

            harmless = root / "harmless.bin"
            harmless.write_bytes(b"harmless")
            raw_dir = self.assert_exit(
                run_cli(
                    "add",
                    str(harmless),
                    "--base",
                    base,
                    "--name",
                    "控制目录",
                    "--raw-dir",
                    ".git",
                    cwd=vault,
                ),
                2,
            )
            self.assertIn(".git", json.dumps(raw_dir, ensure_ascii=False).lower())
            self.assertEqual(snapshot_files(vault), before)

    def test_nested_gitkeep_and_existing_raw_control_files_are_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            clean = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assertIs(clean.get("valid"), True, clean)
            controls = {
                "raw/nested/.gitkeep": b"version-one",
                "raw/config/.gitignore": b"*\n",
                "raw/attributes/.gitattributes": b"* text\n",
                "raw/modules/.gitmodules": b"[submodule \"x\"]\n",
                "raw/agent/AGENTS.md": b"untrusted instructions\n",
            }
            for rel, content in controls.items():
                path = vault / Path(rel)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            run_git(vault, "add", "-f", "--", *controls)
            run_git(vault, "commit", "-m", "inject forbidden raw controls")
            (vault / "raw" / "nested" / ".gitkeep").write_bytes(b"version-two")

            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertIn("E_PATH_UNSAFE", finding_codes(audit))
            self.assertIn("E_RAW_IMMUTABLE", finding_codes(audit))
            report = findings_text(audit)
            for rel in controls:
                self.assertIn(rel.lower(), report)

    def test_explicit_ignored_raw_is_committed_with_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = self.init_vault(root)
            ignore = vault / ".gitignore"
            with ignore.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("raw/**/ignored.pdf\n")
            run_git(vault, "add", ".gitignore")
            run_git(vault, "commit", "-m", "ignore test raw path")
            base = git_head(vault)
            material = root / "ignored.pdf"
            material.write_bytes(b"%PDF-1.7\x00ignored-but-explicit\xff")

            added = self.assert_exit(
                run_cli("add", str(material), "--base", base, "--name", "显式材料", cwd=vault),
                0,
            )
            source_rel = str(added["source"])
            raw_rel = str(added["raw"][0]["path"])  # type: ignore[index]
            set_frontmatter_scalar(vault / source_rel, "summary", "显式纳入的材料不能被 ignore 规则丢弃。")
            ignored = run_git(vault, "-c", "core.quotePath=false", "check-ignore", raw_rel)
            self.assertEqual(str(ignored.stdout).strip(), raw_rel)

            saved = self.save(vault, base, operation="ingest", include=[source_rel, raw_rel])
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(git_show_bytes(vault, f"HEAD:{raw_rel}"), material.read_bytes())
            clone = root / "clone"
            run_git(root, "clone", "--quiet", str(vault), str(clone))
            self.assertEqual((clone / Path(raw_rel)).read_bytes(), material.read_bytes())


class LinkRegressionTests(WikiCliTestCase):
    def test_directory_qualified_links_allow_duplicate_stems(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            write_page(
                vault,
                "sources/共享名称.md",
                "共享名称",
                "source",
                summary="同名来源页面。",
                aliases=[],
                tags=[],
                raw=[],
            )
            write_page(
                vault,
                "notes/共享名称.md",
                "共享名称",
                "note",
                summary="同名知识页面。",
                aliases=[],
                tags=[],
                sources=["[[sources/共享名称]]"],
            )
            write_page(
                vault,
                "notes/同名导航.md",
                "同名导航",
                "moc",
                summary="使用完整目录区分同名节点。",
                aliases=[],
                tags=[],
                sources=[],
                body="来源：[[sources/共享名称]]；观点：[[notes/共享名称]]。",
            )
            saved = self.save(
                vault,
                git_head(vault),
                operation="add",
                include=["sources/共享名称.md", "notes/共享名称.md", "notes/同名导航.md"],
            )
            self.assertIs(saved.get("saved"), True, saved)
            self.assertEqual(git_status(vault), [])
            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assertIs(audit.get("valid"), True, audit)
            paths = [row["path"] for row in read_index(vault)[1]]
            self.assertIn("sources/共享名称.md", paths)
            self.assertIn("notes/共享名称.md", paths)

    def test_rename_with_a_broken_body_wikilink_is_blocked_before_index_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            target = write_page(
                vault,
                "notes/原目标.md",
                "原目标",
                "note",
                summary="被其他页面引用。",
                aliases=[],
                tags=[],
                sources=[],
            )
            write_page(
                vault,
                "notes/引用者.md",
                "引用者",
                "note",
                summary="包含正文 wikilink。",
                aliases=[],
                tags=[],
                sources=[],
                body="仍指向 [[notes/原目标|原目标]]。",
            )
            self.save(
                vault,
                git_head(vault),
                operation="add",
                include=["notes/原目标.md", "notes/引用者.md"],
            )
            base = git_head(vault)
            renamed = vault / "notes" / "新目标.md"
            target.rename(renamed)
            renamed.write_text(
                renamed.read_text(encoding="utf-8").replace("# 原目标", "# 新目标"),
                encoding="utf-8",
                newline="\n",
            )
            index_before = (vault / "index.csv").read_bytes()

            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            report = findings_text(audit)
            self.assertIn("notes/引用者.md", report)
            self.assertRegex(report, r"wikilink|原目标|missing")
            saved = self.save(
                vault,
                base,
                operation="rename",
                include=["notes/原目标.md", "notes/新目标.md"],
                approved=True,
                expected=4,
            )
            self.assertIs(saved.get("saved"), False, saved)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual(git_head(vault), base)
            self.assertTrue(renamed.is_file())


class HomeAndPathRegressionTests(WikiCliTestCase):
    def test_cli_json_is_utf8_even_when_python_stdio_uses_a_non_utf8_codepage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "中文资料库"
            result = run_cli(
                "init",
                str(vault),
                "--name",
                "研究首页",
                "--home-summary",
                "中文输出不依赖调用方设置环境变量。",
                cwd=root,
                extra_env={"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"},
            )
            payload = self.assert_exit(result, 0)
            self.assert_envelope(payload, "init", vault, ok=True)
            self.assertEqual(payload.get("home"), "研究首页.md")

    def test_readme_is_excluded_and_exactly_one_root_home_moc_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            readme = vault / "README.md"
            readme.write_text("# Human repository notes\n\nNo frontmatter is required here.\n", encoding="utf-8")
            run_git(vault, "add", "README.md")
            run_git(vault, "commit", "-m", "add human README")
            clean = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assertIs(clean.get("valid"), True, clean)
            self.assertNotIn("README.md", [row["path"] for row in read_index(vault)[1]])

            second = write_page(
                vault,
                "第二首页.md",
                "第二首页",
                "moc",
                summary="不允许的第二个根 MOC。",
                aliases=[],
                tags=[],
            )
            duplicate = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertIn("e_home_count", findings_text(duplicate))

            second.unlink()
            (vault / "首页.md").unlink()
            missing = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertIn("e_home_count", findings_text(missing))
            self.assertTrue(readme.is_file())

    def test_portably_invalid_requested_page_names_fail_before_init_writes(self) -> None:
        invalid_names = ("CON", "trailing.", "trailing ", "bad:name")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for number, name in enumerate(invalid_names):
                with self.subTest(name=name):
                    vault = root / f"invalid-{number}"
                    result = run_cli(
                        "init",
                        str(vault),
                        "--name",
                        name,
                        "--home-summary",
                        "摘要",
                        cwd=root,
                    )
                    payload = self.assert_exit(result, 2)
                    self.assert_envelope(payload, "init", vault, ok=False)
                    self.assertRegex(json.dumps(payload, ensure_ascii=False).lower(), r"invalid|portable")
                    self.assertFalse(vault.exists(), f"invalid name left a partial vault: {name!r}")

    def test_uppercase_markdown_extension_is_reported_as_noncanonical_on_every_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            page = vault / "notes" / "Upper.MD"
            page.write_text(
                page_text(
                    "Upper",
                    "note",
                    summary="Uppercase extension is not canonical.",
                    aliases=[],
                    tags=[],
                    sources=[],
                ),
                encoding="utf-8",
                newline="\n",
            )
            index_before = (vault / "index.csv").read_bytes()

            audit = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 4)
            report = findings_text(audit)
            self.assertIn("notes/upper.md", report)
            self.assertRegex(report, r"noncanonical|portable|extension|e_path")
            saved = self.save(
                vault,
                base,
                operation="add",
                include=["notes/Upper.MD"],
                expected=4,
            )
            self.assertIs(saved.get("saved"), False, saved)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertTrue(page.is_file())


class AuditScopeRegressionTests(WikiCliTestCase):
    def test_changed_scope_omits_unrelated_head_debt_while_save_still_runs_full_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            historical = write_page(
                vault,
                "notes/历史问题.md",
                "历史问题",
                "note",
                summary="稍后通过完整审计发现。",
                aliases=[],
                tags=[],
                sources=[],
                body="Initially valid.",
            )
            touched = write_page(
                vault,
                "notes/本次修改.md",
                "本次修改",
                "note",
                summary="本次范围内的有效页面。",
                aliases=[],
                tags=[],
                sources=[],
                body="Valid body.",
            )
            self.save(
                vault,
                git_head(vault),
                operation="add",
                include=["notes/历史问题.md", "notes/本次修改.md"],
            )

            with historical.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("Broken historical link: [[notes/不存在]].\n")
            run_git(vault, "add", "notes/历史问题.md")
            run_git(vault, "commit", "-m", "inject historical audit debt")
            base = git_head(vault)
            with touched.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("Current valid body-only edit.\n")
            index_before = (vault / "index.csv").read_bytes()

            changed = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 0)
            self.assertIs(changed.get("valid"), True, changed)
            self.assertNotIn("notes/历史问题.md", findings_text(changed))

            complete = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 4)
            self.assertIn("notes/历史问题.md", findings_text(complete))
            self.assertRegex(findings_text(complete), r"wikilink|不存在|missing")

            saved = self.save(
                vault,
                base,
                operation="edit",
                include=["notes/本次修改.md"],
                expected=4,
            )
            self.assertIs(saved.get("saved"), False, saved)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertIn("Current valid body-only edit.", touched.read_text(encoding="utf-8"))
            self.assertEqual(git_head(vault), base)


class FinalReviewRegressionTests(WikiCliTestCase):
    def test_save_requires_an_explicit_include_and_supports_an_explicit_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)

            help_result = run_cli("save", "--help", cwd=vault)
            self.assertEqual(help_result.returncode, 0, help_result.diagnostic())
            self.assertIn("--include INCLUDE [INCLUDE ...]", help_result.stdout)
            self.assertNotIn("[--include", help_result.stdout)

            no_op = self.save(
                vault,
                base,
                operation="edit",
                include=["首页.md"],
            )
            self.assertIs(no_op.get("saved"), False, no_op)
            self.assertEqual(git_head(vault), base)
            self.assertEqual(git_status(vault), [])

            note = write_page(
                vault,
                "notes/待保存.md",
                "待保存",
                "note",
                summary="合法但尚未授权保存的页面。",
                aliases=[],
                tags=[],
                sources=[],
            )
            unrelated = vault / "unrelated.txt"
            unrelated.write_bytes(b"unrelated user bytes\r\n")
            head_before = git_head(vault)
            index_before = (vault / "index.csv").read_bytes()
            note_before = note.read_bytes()
            unrelated_before = unrelated.read_bytes()
            status_before = git_status(vault)

            missing_scope = run_cli(
                "save",
                "--base",
                base,
                "--operation",
                "edit",
                cwd=vault,
            )
            payload = self.assert_exit(missing_scope, 2)
            self.assertIn("--include", json.dumps(payload, ensure_ascii=False))
            self.assertEqual(git_head(vault), head_before)
            self.assertEqual((vault / "index.csv").read_bytes(), index_before)
            self.assertEqual(note.read_bytes(), note_before)
            self.assertEqual(unrelated.read_bytes(), unrelated_before)
            self.assertEqual(git_status(vault), status_before)

    def test_save_rebuilds_a_missing_index_before_full_audit_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            base = git_head(vault)
            write_page(
                vault,
                "notes/索引恢复.md",
                "索引恢复",
                "note",
                summary="从 Markdown 文件头重建缺失索引。",
                aliases=[],
                tags=["index"],
                sources=[],
            )
            (vault / "index.csv").unlink()

            saved = self.save(
                vault,
                base,
                operation="repair-index",
                include=["notes/索引恢复.md", "index.csv"],
            )
            self.assertIs(saved.get("saved"), True, saved)
            self.assertTrue((vault / "index.csv").is_file())
            self.assertIn("notes/索引恢复.md", [row["path"] for row in read_index(vault)[1]])
            self.assertEqual(git_status(vault), [])
            audit = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assertIs(audit.get("valid"), True, audit)

    def test_init_rejects_reserved_home_names_before_creating_a_vault(self) -> None:
        reserved = (
            "README",
            "CHANGELOG",
            "CONTRIBUTING",
            "SECURITY",
            "CODE_OF_CONDUCT",
            "code-of-conduct",
            "LICENSE",
            "AGENTS",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for number, name in enumerate(reserved):
                vault = root / f"vault-{number}"
                result = run_cli(
                    "init",
                    str(vault),
                    "--name",
                    name,
                    "--home-summary",
                    "不应写入。",
                    cwd=root,
                )
                payload = self.assert_exit(result, 2)
                self.assertRegex(json.dumps(payload, ensure_ascii=False).lower(), r"reserved|保留")
                self.assertFalse(vault.exists(), f"reserved name left a partial vault: {name}")

    def test_link_audit_ignores_markdown_examples_but_still_checks_real_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = self.init_vault(Path(temp_dir))
            write_page(
                vault,
                "notes/真实目标.md",
                "真实目标",
                "note",
                summary="真实链接可以解析。",
                aliases=[],
                tags=[],
                sources=[],
            )
            examples = write_page(
                vault,
                "notes/链接示例.md",
                "链接示例",
                "note",
                summary="代码与注释中的链接文本只是示例。",
                aliases=[],
                tags=[],
                sources=[],
                body=(
                    "真实链接：[[notes/真实目标]]。\n\n"
                    "```markdown\n[[notes/围栏内缺失]]\n```\n\n"
                    "~~~text\n![[assets/围栏内缺失.png]]\n~~~~\n\n"
                    "行内示例 `[[notes/行内缺失]]` 与 ``![[assets/行内缺失.png]]``。\n\n"
                    "<!-- 多行注释\n[[notes/注释内缺失]]\n-->\n\n"
                    "转义示例：\\[[notes/转义缺失]] 与 \\![[assets/转义缺失.png]]。\n"
                ),
            )

            saved = self.save(
                vault,
                git_head(vault),
                operation="add",
                include=["notes/真实目标.md", "notes/链接示例.md"],
            )
            self.assertIs(saved.get("saved"), True, saved)
            clean = self.assert_exit(run_cli("audit", "--scope", "all", cwd=vault), 0)
            self.assertIs(clean.get("valid"), True, clean)

            with examples.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("真实坏链：[[notes/真实缺失]]。\n")
            broken = self.assert_exit(run_cli("audit", "--scope", "changed", cwd=vault), 4)
            report = findings_text(broken)
            self.assertIn("真实缺失", report)
            self.assertNotIn("围栏内缺失", report)
            self.assertNotIn("行内缺失", report)
            self.assertNotIn("注释内缺失", report)
            self.assertNotIn("转义缺失", report)


if __name__ == "__main__":
    unittest.main()
