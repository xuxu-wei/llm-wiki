from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "llm-wiki"
SCRIPT_PATH = SKILL_DIR / "scripts" / "wiki_tools.py"

SPEC = importlib.util.spec_from_file_location("llm_wiki_tools", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {SCRIPT_PATH}")
wiki_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wiki_tools)


def run_command(*argv: str) -> tuple[int, str]:
    args = wiki_tools.build_parser().parse_args(list(argv))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = args.func(args)
    return result, output.getvalue()


def source_page(raw_source: str, raw_sha256: str) -> str:
    return textwrap.dedent(
        f"""\
        ---
        title: Example Source
        created: 2026-01-01
        updated: 2026-01-01
        type: source
        tags: [source]
        sources: []
        summary: Example source summary
        confidence: medium
        status: active
        source_kind: article
        raw_source: {raw_source}
        raw_hash_scheme: sha256_body_v1
        raw_sha256: {raw_sha256}
        raw_hashed_at: 2026-01-01
        ---

        # Example Source

        A source summary.
        """
    )


class SkillPackageTests(unittest.TestCase):
    def test_installable_layout_and_metadata(self) -> None:
        self.assertEqual(SKILL_DIR.name, "llm-wiki")
        self.assertTrue((SKILL_DIR / "SKILL.md").is_file())
        self.assertFalse((SKILL_DIR / "README.md").exists())

        skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", skill_text, re.DOTALL)
        self.assertIsNotNone(match)
        assert match is not None
        keys = {
            line.split(":", 1)[0].strip()
            for line in match.group(1).splitlines()
            if line and not line.startswith((" ", "\t")) and ":" in line
        }
        self.assertEqual(keys, {"name", "description"})
        self.assertLess(len(skill_text.splitlines()), 500)

        openai_yaml = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "LLM Wiki"', openai_yaml)
        self.assertIn('short_description: "Build and maintain source-grounded Markdown wikis"', openai_yaml)
        self.assertIn('default_prompt: "Use $llm-wiki to build or maintain a source-grounded Markdown wiki."', openai_yaml)
        self.assertNotIn("dependencies:", openai_yaml)


class WikiToolsTests(unittest.TestCase):
    def test_default_is_codex_even_with_claude_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            claude = wiki / "CLAUDE.md"
            claude.write_text("# Existing Claude instructions\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CLAUDE_SESSION": "1", "ANTHROPIC_TEST": "1"}):
                result, _output = run_command("init", str(wiki))
            self.assertEqual(result, 0)
            self.assertTrue((wiki / "AGENTS.md").is_file())
            self.assertEqual(claude.read_text(encoding="utf-8"), "# Existing Claude instructions\n")

    def test_explicit_auto_and_claude_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as auto_dir:
            wiki = Path(auto_dir)
            (wiki / "CLAUDE.md").write_text("# Existing\n", encoding="utf-8")
            result, _output = run_command("init", str(wiki), "--agent-platform", "auto")
            self.assertEqual(result, 0)
            self.assertFalse((wiki / "AGENTS.md").exists())
            self.assertIn(wiki_tools.AGENT_CONFIG_MARKER, (wiki / "CLAUDE.md").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as claude_dir:
            wiki = Path(claude_dir)
            result, _output = run_command("init", str(wiki), "--agent-platform", "claude")
            self.assertEqual(result, 0)
            self.assertTrue((wiki / "CLAUDE.md").is_file())
            self.assertFalse((wiki / "AGENTS.md").exists())

    def test_force_preserves_agent_config_and_append_only_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            agent = wiki / "AGENTS.md"
            agent.write_text("# Project instructions\n\nKEEP-ME\n", encoding="utf-8")
            (wiki / "README.md").write_text("CUSTOM README\n", encoding="utf-8")
            (wiki / "index.md").write_text("CUSTOM INDEX\n", encoding="utf-8")
            log = wiki / "log.md"
            log.write_text("# Wiki Log\n\nPRESERVE-ENTRY\n", encoding="utf-8")

            result, _output = run_command("init", str(wiki), "--force")
            self.assertEqual(result, 0)
            first_agent = agent.read_text(encoding="utf-8")
            self.assertIn("KEEP-ME", first_agent)
            self.assertEqual(first_agent.count(wiki_tools.AGENT_CONFIG_MARKER), 1)
            self.assertNotIn("CUSTOM README", (wiki / "README.md").read_text(encoding="utf-8"))
            self.assertNotIn("CUSTOM INDEX", (wiki / "index.md").read_text(encoding="utf-8"))
            self.assertEqual(log.read_text(encoding="utf-8"), "# Wiki Log\n\nPRESERVE-ENTRY\n")

            result, _output = run_command("init", str(wiki), "--force")
            self.assertEqual(result, 0)
            self.assertEqual(agent.read_text(encoding="utf-8"), first_agent)
            self.assertEqual(log.read_text(encoding="utf-8"), "# Wiki Log\n\nPRESERVE-ENTRY\n")

    def test_invalid_agent_file_does_not_partially_initialize(self) -> None:
        for value in ["../escape.md", "nested/agent.md", "C:escape.md", "agent.md:stream"]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                wiki = Path(temp_dir)
                result, output = run_command("init", str(wiki), "--agent-file", value)
                self.assertEqual(result, 2)
                self.assertIn("root-level Markdown filename", output)
                self.assertFalse((wiki / "raw").exists())

    def test_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "paper.pdf"
            csv = root / "data.csv"
            transcript = root / "meeting.txt"
            unknown = root / "archive.bin"
            for path in [pdf, csv, transcript, unknown]:
                path.write_bytes(b"sample")
            self.assertEqual(wiki_tools.classify_file(pdf), "raw/papers")
            self.assertEqual(wiki_tools.classify_file(csv), "raw/data")
            self.assertEqual(wiki_tools.classify_file(transcript), "raw/transcripts")
            decision = wiki_tools.classification_for_file(unknown, "inbox", None)
            self.assertEqual(decision["status"], "needs_user_classification")

    def test_text_hash_is_newline_and_frontmatter_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crlf = root / "crlf.md"
            lf = root / "lf.md"
            crlf.write_bytes(b"---\r\ntitle: A\r\n---\r\nBody\r\nLine\r\n")
            lf.write_bytes(b"---\ntitle: B\n---\nBody\nLine\n")
            expected = hashlib.sha256(b"Body\nLine\n").hexdigest()
            self.assertEqual(wiki_tools.body_hash_for_text(crlf), expected)
            self.assertEqual(wiki_tools.body_hash_for_text(lf), expected)

    def test_source_empty_sources_is_valid_and_indexable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw = wiki / "raw" / "articles" / "example.md"
            raw.write_text("raw body\n", encoding="utf-8")
            digest = wiki_tools.body_hash_for_text(raw)
            page = wiki / "sources" / "example.md"
            page.write_text(source_page("raw/articles/example.md", digest), encoding="utf-8")

            self.assertEqual(run_command("update-index", str(wiki))[0], 0)
            issues = wiki_tools.lint_wiki(wiki)
            self.assertFalse(any("sources" in issue for issue in issues["missing_fields"]))
            report = wiki_tools.health_report(wiki)
            self.assertFalse(any("sources" in issue for issue in report["metadata_schema_issues"]))
            self.assertIsNone(wiki_tools.fix_frontmatter_file(page, wiki, dry_run=True))
            self.assertIn("[[sources/example|Example Source]]", (wiki / "index.md").read_text(encoding="utf-8"))

            fm, _body, _has_fm = wiki_tools.frontmatter_block(page.read_text(encoding="utf-8"))
            del fm["sources"]
            self.assertIn("sources", wiki_tools.missing_required_fields(page, wiki, fm))

    def test_health_detects_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw = wiki / "raw" / "articles" / "example.md"
            raw.write_text("current raw body\n", encoding="utf-8")
            page = wiki / "sources" / "example.md"
            page.write_text(source_page("raw/articles/example.md", "0" * 64), encoding="utf-8")
            report = wiki_tools.health_report(wiki)
            self.assertTrue(report["update_required"])
            self.assertTrue(any(item["reason"] == "source_summary_raw_hash_drift" for item in report["drifted_sources"]))

    def test_fix_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            page = wiki / "concepts" / "partial.md"
            original = "---\ntitle: Partial\ntype: concept\n---\n# Partial\n"
            page.write_text(original, encoding="utf-8")
            result = wiki_tools.fix_frontmatter_file(page, wiki, dry_run=True)
            self.assertIsNotNone(result)
            self.assertEqual(page.read_text(encoding="utf-8"), original)

    def test_reference_resolver_rejects_cross_platform_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir) / "wiki"
            wiki.mkdir()
            invalid = [
                "../outside.md",
                "raw/a/../../../outside.md",
                "/absolute/path.md",
                "C:/outside.md",
                "C:outside.md",
                "raw/file.md:stream",
            ]
            for value in invalid:
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        wiki_tools.resolve_wiki_reference(wiki, value, "raw_source")

            rel, path = wiki_tools.resolve_wiki_reference(wiki, ".\\raw\\articles\\a.md", "raw_source")
            self.assertEqual(rel, "raw/articles/a.md")
            self.assertEqual(path, (wiki / "raw" / "articles" / "a.md").resolve())

            outside = Path(temp_dir) / "outside"
            outside.mkdir()
            link = wiki / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                pass
            else:
                with self.assertRaises(ValueError):
                    wiki_tools.resolve_wiki_reference(wiki, "linked/file.md", "raw_source")

                concepts = wiki / "concepts"
                concepts.mkdir()
                external_file = outside / "external.md"
                external_file.write_text("outside\n", encoding="utf-8")
                external_link = concepts / "external.md"
                external_link.symlink_to(external_file)
                original_read_text = wiki_tools.read_text

                def guarded_read_text(path: Path) -> str:
                    if not wiki_tools.path_stays_within_wiki(wiki, path):
                        raise AssertionError(f"attempted external read: {path}")
                    return original_read_text(path)

                with mock.patch.object(wiki_tools, "read_text", side_effect=guarded_read_text):
                    lint = wiki_tools.lint_wiki(wiki)
                self.assertTrue(any("escapes wiki root" in item for item in lint["unsafe_paths"]))

    def test_lint_and_health_report_invalid_metadata_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            page = wiki / "sources" / "invalid.md"
            page.write_text(source_page("../outside.md", "0" * 64), encoding="utf-8")
            derived = wiki / "raw" / "derived" / "invalid.md"
            derived.write_text(
                "---\nderived_from: ../outside.md\nderivation_method: cleanup\nderived_at: 2026-01-01\n---\nbody\n",
                encoding="utf-8",
            )

            lint = wiki_tools.lint_wiki(wiki)
            self.assertTrue(any("relative path inside the wiki" in item for item in lint["source_provenance_issues"]))
            self.assertTrue(any("relative path inside the wiki" in item for item in lint["derived_metadata_gaps"]))
            health = wiki_tools.health_report(wiki)
            self.assertTrue(any("relative path inside the wiki" in item for item in health["blocking_issues"]))

    def test_cli_smoke_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            commands = [
                ["init", temp_dir, "--domain", "smoke test", "--research"],
                ["lint", temp_dir, "--fail-on-issues"],
                ["health", temp_dir, "--fail-on-issues"],
                ["fix", temp_dir, "--dry-run"],
            ]
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT_PATH), *command],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
