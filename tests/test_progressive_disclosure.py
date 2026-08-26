from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "llm-wiki"
SKILL_PATH = SKILL_DIR / "SKILL.md"
SCRIPT_PATH = SKILL_DIR / "scripts" / "wiki_tools.py"
TASK_REFERENCES = ("create.md", "ingest.md", "query.md", "maintain.md")

SPEC = importlib.util.spec_from_file_location("llm_wiki_tools_progressive", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {SCRIPT_PATH}")
wiki_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wiki_tools)


WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['\N{RIGHT SINGLE QUOTATION MARK}-][A-Za-z0-9]+)*")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def word_count(text: str) -> int:
    """Count English prose/code words consistently for documentation budgets."""
    return len(WORD_PATTERN.findall(text))


def run_command(*argv: str) -> tuple[int, str]:
    args = wiki_tools.build_parser().parse_args(list(argv))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = args.func(args)
    return result, output.getvalue()


def parse_json_output(output: str) -> dict[str, object]:
    value = json.loads(output)
    if not isinstance(value, dict):
        raise AssertionError(f"expected a JSON object, got {type(value).__name__}")
    return value


def durable_page(
    title: str,
    page_type: str = "concept",
    *,
    summary: str | None = None,
    aliases: list[str] | None = None,
    body: str = "Durable knowledge.",
) -> str:
    alias_line = ""
    if aliases:
        alias_line = f"aliases: [{', '.join(aliases)}]\n"
    return (
        "---\n"
        f"title: {title}\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        f"type: {page_type}\n"
        f"tags: [{page_type}]\n"
        "sources: []\n"
        f"summary: {summary or title}\n"
        "confidence: medium\n"
        "status: active\n"
        f"{alias_line}"
        "---\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


def source_page(
    raw_source: str,
    digest: str,
    *,
    title: str = "Example Source",
    aliases: list[str] | None = None,
    url: str | None = None,
) -> str:
    identity_fields = ""
    if url is not None:
        identity_fields += f"url: {url}\n"
    if aliases:
        identity_fields += f"aliases: [{', '.join(aliases)}]\n"
    return (
        "---\n"
        f"title: {title}\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        "type: source\n"
        "tags: [source]\n"
        "sources: []\n"
        f"summary: {title} summary\n"
        "confidence: medium\n"
        "status: active\n"
        "source_kind: article\n"
        f"{identity_fields}"
        f"raw_source: {raw_source}\n"
        "raw_hash_scheme: sha256_body_v1\n"
        f"raw_sha256: {digest}\n"
        "raw_hashed_at: 2026-01-01\n"
        "---\n"
        f"# {title}\n\n"
        "Source-grounded summary.\n"
    )


class ProgressiveDisclosureTests(unittest.TestCase):
    def test_documentation_budgets_and_direct_references(self) -> None:
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertGreaterEqual(word_count(skill_text), 600)
        self.assertLessEqual(word_count(skill_text), 900)

        local_links = []
        for target in MARKDOWN_LINK_PATTERN.findall(skill_text):
            clean_target = target.split("#", 1)[0]
            if not clean_target or "://" in clean_target:
                continue
            local_links.append(clean_target)
            path = Path(clean_target)
            self.assertFalse(path.is_absolute(), clean_target)
            self.assertNotIn("..", path.parts, clean_target)
            self.assertEqual(path.parts[0], "references", clean_target)
            self.assertEqual(len(path.parts), 2, clean_target)
            self.assertTrue((SKILL_DIR / path).is_file(), clean_target)

        for name in TASK_REFERENCES:
            target = f"references/{name}"
            self.assertIn(target, local_links, f"SKILL.md must link directly to {target}")
            reference = SKILL_DIR / target
            reference_text = reference.read_text(encoding="utf-8")
            self.assertLessEqual(word_count(reference_text), 450, target)
            nested_markdown = [
                link
                for link in MARKDOWN_LINK_PATTERN.findall(reference_text)
                if link.split("#", 1)[0].lower().endswith(".md")
            ]
            self.assertEqual(nested_markdown, [], f"{target} must not create a deep reference chain")

        agent_template_path = SKILL_DIR / "templates" / "wiki-agent-contract.md"
        self.assertTrue(agent_template_path.is_file())
        self.assertFalse((SKILL_DIR / "templates" / "AGENTS.md").exists())
        agent_template = agent_template_path.read_text(encoding="utf-8")
        self.assertLessEqual(word_count(agent_template), 350)

        query_words = word_count(skill_text) + word_count(
            (SKILL_DIR / "references" / "query.md").read_text(encoding="utf-8")
        )
        self.assertLessEqual(query_words, 1350)

    def test_typical_routes_have_explicit_bounded_file_sets(self) -> None:
        routes = {
            "query": ([SKILL_PATH, SKILL_DIR / "references" / "query.md"], 2, 1350),
            "ingest": ([SKILL_PATH, SKILL_DIR / "references" / "ingest.md"], 2, 1350),
            "research-ingest": (
                [
                    SKILL_PATH,
                    SKILL_DIR / "references" / "ingest.md",
                    SKILL_DIR / "references" / "research-extension.md",
                ],
                3,
                1800,
            ),
            "maintain": ([SKILL_PATH, SKILL_DIR / "references" / "maintain.md"], 2, 1350),
        }
        for route, (paths, expected_files, max_words) in routes.items():
            with self.subTest(route=route):
                self.assertEqual(len(paths), expected_files)
                self.assertTrue(all(path.is_file() for path in paths))
                loaded_words = sum(word_count(path.read_text(encoding="utf-8")) for path in paths)
                self.assertLessEqual(loaded_words, max_words)
                if route != "research-ingest":
                    self.assertNotIn(SKILL_DIR / "references" / "research-extension.md", paths)
                if route in {"query", "ingest", "maintain"}:
                    self.assertNotIn(SKILL_DIR / "references" / "wiki-contract.md", paths)

    def test_query_reference_keeps_read_only_queries_out_of_log(self) -> None:
        query = (SKILL_DIR / "references" / "query.md").read_text(encoding="utf-8").lower()
        self.assertIn("read-only", query)
        self.assertIn("log.md", query)
        self.assertIn("do not append", query)
        self.assertRegex(query, r"when\s+filing")
        self.assertIn("audit trail", query)

    def test_source_templates_separate_core_and_research_profiles(self) -> None:
        core = (SKILL_DIR / "templates" / "source-summary.md").read_text(encoding="utf-8")
        research_path = SKILL_DIR / "templates" / "research-source-summary.md"
        self.assertTrue(research_path.is_file())
        research = research_path.read_text(encoding="utf-8")

        self.assertNotIn("## Citation", core)
        for field in ("venue:", "doi:", "isbn:", "publisher:"):
            self.assertNotIn(field, core.lower())
        for field in ("source_kind:", "authors:", "year:", "venue:", "doi:"):
            self.assertIn(field, research.lower())
        self.assertNotRegex(core.lower(), r"^(?:doi|isbn|venue|publisher):\s*unknown\s*$")


class RawImmutabilityTests(unittest.TestCase):
    def test_hash_fix_and_health_leave_raw_bytes_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw = wiki / "raw" / "articles" / "source.md"
            original = (
                b"\xef\xbb\xbf---\r\nsource_url: https://example.test\r\n"
                b"custom: preserve-me\r\n---\r\nOriginal body\r\n"
            )
            raw.write_bytes(original)

            self.assertEqual(run_command("hash-source", str(raw))[0], 0)
            self.assertEqual(raw.read_bytes(), original)
            self.assertEqual(run_command("fix", str(wiki))[0], 0)
            self.assertEqual(raw.read_bytes(), original)
            self.assertEqual(run_command("health", str(wiki))[0], 0)
            self.assertEqual(raw.read_bytes(), original)

    def test_fix_reports_complex_frontmatter_for_manual_review_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            page = wiki / "concepts" / "complex.md"
            original = (
                b"---\n"
                b"title: Complex\n"
                b"type: concept\n"
                b"tags:\n"
                b"  - nested\n"
                b"custom:\n"
                b"  nested: value\n"
                b"---\n"
                b"# Complex\n"
            )
            page.write_bytes(original)

            result, output = run_command("fix", str(wiki))
            self.assertEqual(result, 0)
            report = parse_json_output(output)
            manual_required = report.get("manual_required")
            self.assertIsInstance(manual_required, list)
            self.assertTrue(
                any("concepts/complex.md" in json.dumps(item) for item in manual_required),
                manual_required,
            )
            self.assertEqual(page.read_bytes(), original)

    def test_atomic_write_failure_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "managed.md"
            original = b"original bytes\r\n"
            path.write_bytes(original)

            with mock.patch.object(wiki_tools.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    wiki_tools.write_text(path, "replacement\n", expected_hash=wiki_tools.bytes_hash(path))

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(f".{path.name}.llm-wiki-*.tmp")), [])

    def test_fix_matches_template_shape_and_converges_after_real_reordering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw = wiki / "raw" / "articles" / "template-source.md"
            raw.write_text("template source body\n", encoding="utf-8")
            source_template = (SKILL_DIR / "templates" / "source-summary.md").read_text(encoding="utf-8")
            source_template = source_template.replace(
                "raw/articles/source-file.ext", "raw/articles/template-source.md"
            ).replace("<sha256>", wiki_tools.body_hash_for_text(raw))
            (wiki / "entities" / "template-page.md").write_text(
                (SKILL_DIR / "templates" / "page.md")
                .read_text(encoding="utf-8")
                .replace("sources: []", "sources: [sources/template-source.md]"),
                encoding="utf-8",
            )
            (wiki / "sources" / "template-source.md").write_text(
                source_template,
                encoding="utf-8",
            )

            result, output = run_command("fix", str(wiki), "--dry-run")
            self.assertEqual(result, 0)
            template_report = parse_json_output(output)
            self.assertEqual(template_report["changed_files"], [])
            self.assertEqual(template_report["manual_required"], [])

            unordered = wiki / "concepts" / "unordered.md"
            unordered.write_text(
                "---\n"
                "status: active\n"
                "summary: Deliberately unordered metadata\n"
                "sources: [sources/template-source.md]\n"
                "tags: [concept]\n"
                "type: concept\n"
                "updated: 2026-01-01\n"
                "created: 2026-01-01\n"
                "title: Unordered\n"
                "confidence: medium\n"
                "---\n\n"
                "# Unordered\n",
                encoding="utf-8",
            )

            result, output = run_command("fix", str(wiki))
            self.assertEqual(result, 0)
            apply_report = parse_json_output(output)
            self.assertEqual(
                [item["path"] for item in apply_report["changed_files"]],
                ["concepts/unordered.md"],
            )

            result, output = run_command("fix", str(wiki), "--dry-run")
            self.assertEqual(result, 0)
            converged = parse_json_output(output)
            self.assertEqual(converged["changed_files"], [])
            self.assertEqual(converged["manual_required"], [])


class BoundedContextTests(unittest.TestCase):
    def test_context_is_relevant_and_uses_default_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            for number in range(15):
                (wiki / "concepts" / f"quantum-{number:02d}.md").write_text(
                    durable_page(
                        f"Quantum Topic {number}",
                        summary=f"Quantum evidence number {number}",
                        aliases=[f"q-{number}"],
                    ),
                    encoding="utf-8",
                )
            (wiki / "concepts" / "unrelated.md").write_text(
                durable_page("Garden Notes", summary="Botanical observations"),
                encoding="utf-8",
            )
            for number in range(7):
                self.assertEqual(
                    run_command(
                        "append-log",
                        str(wiki),
                        "--action",
                        "Update",
                        "--subject",
                        f"quantum note {number}",
                    )[0],
                    0,
                )
            self.assertEqual(
                run_command(
                    "append-log",
                    str(wiki),
                    "--action",
                    "Update",
                    "--subject",
                    "garden note",
                )[0],
                0,
            )

            result, output = run_command("context", str(wiki), "quantum", "--json")
            self.assertEqual(result, 0)
            report = parse_json_output(output)
            self.assertEqual(report["limit"], 12)
            self.assertEqual(report["recent_log_limit"], 5)
            self.assertEqual(report["total_matches"], 15)
            self.assertTrue(report["truncated"])
            results = report["results"]
            recent_log = report["recent_log"]
            self.assertIsInstance(results, list)
            self.assertIsInstance(recent_log, list)
            self.assertEqual(len(results), 12)
            self.assertEqual(len(recent_log), 5)
            self.assertTrue(all("quantum" in json.dumps(item).lower() for item in results))
            self.assertTrue(all("quantum" in item.lower() for item in recent_log))


class IngestLifecycleTests(unittest.TestCase):
    def test_classify_reuses_identical_content_without_duplicate_move(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            existing = wiki / "raw" / "articles" / "same.md"
            incoming = wiki / "raw" / "inbox" / "same.md"
            existing.write_text("identical body\n", encoding="utf-8")
            incoming.write_text("identical body\n", encoding="utf-8")

            result, output = run_command("classify", str(wiki), "--move")
            self.assertEqual(result, 0)
            report = parse_json_output(output)
            item = report["classified"][0]
            self.assertEqual(item["status"], "reused")
            self.assertFalse(item["moved"])
            self.assertTrue(incoming.is_file())
            self.assertFalse((wiki / "raw" / "articles" / "same-2.md").exists())

    def test_preflight_reuses_content_and_blocks_same_name_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            (wiki / "raw" / "articles" / "same.md").write_text("same body\n", encoding="utf-8")
            (wiki / "raw" / "articles" / "conflict.md").write_text("old body\n", encoding="utf-8")
            (wiki / "raw" / "inbox" / "same-copy.md").write_text("same body\n", encoding="utf-8")
            (wiki / "raw" / "inbox" / "conflict.md").write_text("new body\n", encoding="utf-8")

            result, output = run_command("ingest-preflight", str(wiki))
            self.assertEqual(result, 1)
            report = parse_json_output(output)
            self.assertTrue(report["blocking"])
            by_source = {item["source"]: item for item in report["items"]}
            duplicate = by_source["raw/inbox/same-copy.md"]
            conflict = by_source["raw/inbox/conflict.md"]
            self.assertEqual(duplicate["status"], "reused")
            self.assertEqual(duplicate["reuse"], "raw/articles/same.md")
            self.assertEqual(conflict["status"], "conflict")
            self.assertEqual(conflict["reason"], "target_name_exists_with_different_content")
            self.assertTrue((wiki / "raw" / "inbox" / "same-copy.md").is_file())
            self.assertTrue((wiki / "raw" / "inbox" / "conflict.md").is_file())

    def test_finalize_validates_then_updates_index_and_log_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw = wiki / "raw" / "articles" / "example.md"
            raw.write_text("raw body\n", encoding="utf-8")
            digest = wiki_tools.body_hash_for_text(raw)
            page = wiki / "sources" / "example.md"
            page.write_text(source_page("raw/articles/example.md", digest), encoding="utf-8")

            result, output = run_command("ingest-finalize", str(wiki), "sources/example.md")
            self.assertEqual(result, 0)
            report = parse_json_output(output)
            self.assertTrue(report["finalized"])
            self.assertTrue(report["index_updated"])
            self.assertTrue(report["logged"])
            self.assertIn("[[sources/example|Example Source]]", (wiki / "index.md").read_text(encoding="utf-8"))
            self.assertIn("sources/example.md", (wiki / "log.md").read_text(encoding="utf-8"))

            before_index = (wiki / "index.md").read_bytes()
            before_log = (wiki / "log.md").read_bytes()
            page.write_text(source_page("raw/articles/example.md", "0" * 64), encoding="utf-8")
            result, output = run_command("ingest-finalize", str(wiki), "sources/example.md")
            self.assertEqual(result, 1)
            self.assertFalse(parse_json_output(output)["finalized"])
            self.assertEqual((wiki / "index.md").read_bytes(), before_index)
            self.assertEqual((wiki / "log.md").read_bytes(), before_log)


class MaintenanceTests(unittest.TestCase):
    def test_lint_reports_duplicate_stems_ambiguous_links_and_derived_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            (wiki / "concepts" / "shared.md").write_text(durable_page("Shared Concept"), encoding="utf-8")
            (wiki / "entities" / "shared.md").write_text(
                durable_page("Shared Entity", "entity"), encoding="utf-8"
            )
            (wiki / "queries" / "consumer.md").write_text(
                durable_page("Consumer", "query", body="Ambiguous [[shared]] reference."),
                encoding="utf-8",
            )
            raw = wiki / "raw" / "articles" / "upstream.md"
            raw.write_text("changed upstream\n", encoding="utf-8")
            (wiki / "raw" / "derived" / "upstream.md").write_text(
                "---\n"
                "derived_from: raw/articles/upstream.md\n"
                "derivation_method: cleanup\n"
                "derived_at: 2026-01-01\n"
                f"source_hash_at_derivation: {'0' * 64}\n"
                "source_hash_scheme_at_derivation: sha256_body_v1\n"
                "---\n"
                "Derived text.\n",
                encoding="utf-8",
            )

            lint = wiki_tools.lint_wiki(wiki)
            self.assertTrue(any("shared" in item for item in lint["duplicate_stems"]))
            self.assertTrue(any("consumer.md" in item for item in lint["ambiguous_links"]))
            self.assertTrue(any("derived_stale" in item for item in lint["derived_hash_drift"]))
            health = wiki_tools.health_report(wiki)
            self.assertTrue(any(item.get("reason") == "derived_stale" for item in health["drifted_sources"]))

    def test_compact_lint_and_health_are_bounded_and_source_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            (wiki / "concepts" / "focus-a.md").write_text(
                "---\ntitle: Focus A\ntype: concept\n---\n# Focus A\n",
                encoding="utf-8",
            )
            (wiki / "concepts" / "other-b.md").write_text(
                "---\ntitle: Other B\ntype: concept\n---\n# Other B\n",
                encoding="utf-8",
            )

            result, output = run_command("lint", str(wiki), "--json", "--summary", "--limit", "1")
            self.assertEqual(result, 0)
            lint_summary = parse_json_output(output)
            self.assertTrue(lint_summary["summary"])
            self.assertTrue(all(len(items) <= 1 for items in lint_summary["issues"].values()))
            self.assertIn("issue_counts", lint_summary)

            result, output = run_command(
                "lint", str(wiki), "--json", "--source", "concepts/focus-a.md", "--limit", "1"
            )
            self.assertEqual(result, 0)
            scoped_lint = parse_json_output(output)
            self.assertNotIn("other-b.md", json.dumps(scoped_lint["issues"]))

            result, output = run_command("health", str(wiki), "--json", "--no-inventory")
            self.assertEqual(result, 0)
            health = parse_json_output(output)
            self.assertEqual(health["metadata_inventory"], {})

            result, output = run_command(
                "health",
                str(wiki),
                "--json",
                "--summary",
                "--limit",
                "1",
                "--source",
                "concepts/focus-a.md",
            )
            self.assertEqual(result, 0)
            health_summary = parse_json_output(output)
            self.assertTrue(health_summary["summary"])
            for key in ("drifted_sources", "affected_pages"):
                self.assertLessEqual(len(health_summary[key]), 1)

    def test_archive_dry_run_then_apply_rewrites_backlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            old_page = wiki / "concepts" / "old.md"
            new_page = wiki / "concepts" / "new.md"
            consumer = wiki / "syntheses" / "consumer.md"
            old_page.write_text(durable_page("Old Concept"), encoding="utf-8")
            new_page.write_text(durable_page("New Concept"), encoding="utf-8")
            consumer.write_text(
                durable_page("Consumer", "synthesis", body="Use [[concepts/old|Old Concept]]."),
                encoding="utf-8",
            )
            self.assertEqual(run_command("update-index", str(wiki))[0], 0)
            before_index = (wiki / "index.md").read_bytes()
            before_log = (wiki / "log.md").read_bytes()

            result, output = run_command(
                "archive",
                str(wiki),
                "concepts/old.md",
                "--reason",
                "superseded",
                "--replaced-by",
                "concepts/new.md",
            )
            self.assertEqual(result, 0)
            preview = parse_json_output(output)
            self.assertEqual(preview["mode"], "dry-run")
            self.assertFalse(preview["applied"])
            self.assertTrue(old_page.is_file())
            self.assertFalse((wiki / "_archive" / "concepts" / "old.md").exists())
            self.assertEqual((wiki / "index.md").read_bytes(), before_index)
            self.assertEqual((wiki / "log.md").read_bytes(), before_log)

            result, output = run_command(
                "archive",
                str(wiki),
                "concepts/old.md",
                "--reason",
                "superseded",
                "--replaced-by",
                "concepts/new.md",
                "--apply",
            )
            self.assertEqual(result, 0)
            applied = parse_json_output(output)
            self.assertTrue(applied["applied"])
            archived = wiki / "_archive" / "concepts" / "old.md"
            self.assertFalse(old_page.exists())
            self.assertTrue(archived.is_file())
            self.assertIn("status: archived", archived.read_text(encoding="utf-8"))
            self.assertIn("[[concepts/new|Old Concept]]", consumer.read_text(encoding="utf-8"))
            self.assertNotIn("[[concepts/old|", (wiki / "index.md").read_text(encoding="utf-8"))
            self.assertIn("Archive", (wiki / "log.md").read_text(encoding="utf-8"))


class SafetyRegressionTests(unittest.TestCase):
    def test_archive_rejects_raw_file_without_changing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw = wiki / "raw" / "articles" / "preserve.md"
            original = b"raw source bytes\r\n"
            raw.write_bytes(original)
            before_index = (wiki / "index.md").read_bytes()
            before_log = (wiki / "log.md").read_bytes()

            result, output = run_command(
                "archive",
                str(wiki),
                "raw/articles/preserve.md",
                "--reason",
                "must remain raw",
                "--apply",
            )
            self.assertEqual(result, 2)
            report = parse_json_output(output)
            self.assertFalse(report["applied"])
            self.assertIn("durable pages", report["error"])
            self.assertEqual(raw.read_bytes(), original)
            self.assertEqual((wiki / "index.md").read_bytes(), before_index)
            self.assertEqual((wiki / "log.md").read_bytes(), before_log)
            self.assertFalse((wiki / "_archive" / "raw" / "articles" / "preserve.md").exists())

    def test_hash_source_write_rejects_raw_without_changing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw = wiki / "raw" / "articles" / "preserve.md"
            original = b"---\r\nsource_url: https://example.test\r\n---\r\nBody\r\n"
            raw.write_bytes(original)

            result, output = run_command("hash-source", str(raw), "--write")
            self.assertEqual(result, 2)
            report = parse_json_output(output)
            self.assertFalse(report["written"])
            self.assertIn("byte-immutable", report["error"])
            self.assertIn("deprecated", report["deprecation_warning"])
            self.assertEqual(raw.read_bytes(), original)

    def test_comments_make_fix_and_archive_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            page = wiki / "concepts" / "commented.md"
            original = durable_page("Commented").replace(
                "title: Commented\n",
                "# preserve this whole-line comment\ntitle: Commented # preserve this inline comment\n",
            ).encode("utf-8")
            page.write_bytes(original)
            before_index = (wiki / "index.md").read_bytes()
            before_log = (wiki / "log.md").read_bytes()

            result, output = run_command("fix", str(wiki))
            self.assertEqual(result, 0)
            fixed = parse_json_output(output)
            manual_required = fixed["manual_required"]
            self.assertTrue(any("concepts/commented.md" in json.dumps(item) for item in manual_required))
            self.assertTrue(any("comment" in json.dumps(item).lower() for item in manual_required))
            self.assertEqual(page.read_bytes(), original)

            result, output = run_command(
                "archive",
                str(wiki),
                "concepts/commented.md",
                "--reason",
                "test fail closed",
                "--apply",
            )
            self.assertEqual(result, 2)
            archived = parse_json_output(output)
            self.assertFalse(archived["applied"])
            self.assertIn("comment", archived["error"].lower())
            self.assertEqual(page.read_bytes(), original)
            self.assertEqual((wiki / "index.md").read_bytes(), before_index)
            self.assertEqual((wiki / "log.md").read_bytes(), before_log)
            self.assertFalse((wiki / "_archive" / "concepts" / "commented.md").exists())

    def test_existing_core_schema_upgrades_to_research(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            schema_path = wiki / "_meta" / "schema.json"
            self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8"))["profile"], "core")

            result, output = run_command("init", str(wiki), "--research")
            self.assertEqual(result, 0)
            report = parse_json_output(output)
            self.assertEqual(report["schema_profile"], "research")
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            self.assertEqual(schema["profile"], "research")
            self.assertIn("Research Schema Add-on", (wiki / "AGENTS.md").read_text(encoding="utf-8"))

    def test_invalid_schema_is_reported_by_lint_and_health(self) -> None:
        cases = {
            "invalid-json": "{not json\n",
            "non-object": "[]\n",
            "invalid-fields": '{"schema_version": 0, "profile": "future"}\n',
        }
        for name, schema_text in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                wiki = Path(temp_dir)
                self.assertEqual(run_command("init", str(wiki))[0], 0)
                (wiki / "_meta" / "schema.json").write_text(schema_text, encoding="utf-8")

                lint = wiki_tools.lint_wiki(wiki)
                self.assertTrue(lint["schema_config"])
                self.assertTrue(all("_meta/schema.json" in item for item in lint["schema_config"]))
                health = wiki_tools.health_report(wiki)
                self.assertTrue(
                    any(item.startswith("schema_config: _meta/schema.json") for item in health["blocking_issues"])
                )

    def test_archive_rewrites_only_exact_path_when_stems_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            concept = wiki / "concepts" / "shared.md"
            entity = wiki / "entities" / "shared.md"
            replacement = wiki / "concepts" / "replacement.md"
            consumer = wiki / "syntheses" / "consumer.md"
            concept.write_text(durable_page("Shared Concept"), encoding="utf-8")
            entity.write_text(durable_page("Shared Entity", "entity"), encoding="utf-8")
            replacement.write_text(durable_page("Replacement"), encoding="utf-8")
            consumer.write_text(
                durable_page(
                    "Consumer",
                    "synthesis",
                    body=(
                        "Replace [[concepts/shared|Shared Concept]], preserve "
                        "[[entities/shared|Shared Entity]]."
                    ),
                ),
                encoding="utf-8",
            )
            self.assertEqual(run_command("update-index", str(wiki))[0], 0)

            result, output = run_command(
                "archive",
                str(wiki),
                "concepts/shared.md",
                "--reason",
                "superseded",
                "--replaced-by",
                "concepts/replacement.md",
                "--apply",
            )
            self.assertEqual(result, 0, output)
            self.assertTrue(parse_json_output(output)["applied"])
            text = consumer.read_text(encoding="utf-8")
            self.assertIn("[[concepts/replacement|Shared Concept]]", text)
            self.assertIn("[[entities/shared|Shared Entity]]", text)
            self.assertNotIn("[[concepts/shared|", text)

    def test_context_json_and_text_stay_within_character_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            for number in range(40):
                (wiki / "concepts" / f"budget-{number:02d}.md").write_text(
                    durable_page(
                        f"Budget Topic {number} " + "T" * 100,
                        summary="budget " + "S" * 400,
                        aliases=["budget-" + "A" * 80],
                        body="budget " + "B" * 1000,
                    ),
                    encoding="utf-8",
                )
            log_entries = "".join(
                f"\n## [2026-01-01] Update | budget {number}\n- Notes: {'L' * 500}\n"
                for number in range(30)
            )
            (wiki / "log.md").write_text("# Wiki Log\n" + log_entries, encoding="utf-8")

            common = (
                "context",
                str(wiki),
                "budget",
                "--limit",
                "100",
                "--recent-log",
                "50",
                "--char-budget",
                "1200",
            )
            result, json_output = run_command(*common, "--json")
            self.assertEqual(result, 0)
            json_content = json_output.rstrip("\r\n")
            self.assertLessEqual(len(json_content), 1200)
            payload = json.loads(json_content)
            self.assertEqual(payload["char_budget"], 1200)
            self.assertTrue(payload["truncated"])

            result, text_output = run_command(*common)
            self.assertEqual(result, 0)
            self.assertLessEqual(len(text_output.rstrip("\r\n")), 1200)

    def test_health_text_summary_uses_full_counts_before_limiting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            for number in range(6):
                (wiki / "concepts" / f"broken-{number}.md").write_text(
                    durable_page(f"Broken {number}", body=f"Missing [[not-found-{number}]]."),
                    encoding="utf-8",
                )
            full = wiki_tools.health_report(wiki)
            expected = {
                "Blocking issues": len(full["blocking_issues"]),
                "Drifted sources": len(full["drifted_sources"]),
                "Affected source pages": len(full["affected_pages"]),
                "Relationship issues": len(full["relationship_issues"]),
                "Source hash issues": len(full["source_hash_issues"]),
                "Metadata schema issues": len(full["metadata_schema_issues"]),
                "Field order issues": len(full["field_order_issues"]),
            }
            self.assertGreater(expected["Blocking issues"], 1)

            result, output = run_command("health", str(wiki), "--summary", "--limit", "1")
            self.assertEqual(result, 0)
            for label, count in expected.items():
                self.assertRegex(output, rf"(?m)^{re.escape(label)}: {count}$")

    def test_preflight_rejects_escaping_symlink_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wiki = root / "wiki"
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            outside = root / "outside.md"
            outside.write_text("outside source\n", encoding="utf-8")
            link = wiki / "raw" / "inbox" / "escape.md"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"file symlinks are unavailable: {exc}")

            result, output = run_command("ingest-preflight", str(wiki))
            self.assertEqual(result, 2)
            report = parse_json_output(output)
            self.assertRegex(report["error"], r"(?:unsafe|escape|inside wiki)")
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside source\n")

    def test_finalize_rejects_multiple_active_summaries_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw = wiki / "raw" / "articles" / "shared.md"
            raw.write_text("shared raw\n", encoding="utf-8")
            digest = wiki_tools.body_hash_for_text(raw)
            (wiki / "sources" / "one.md").write_text(
                source_page("raw/articles/shared.md", digest, title="Source One"),
                encoding="utf-8",
            )
            (wiki / "sources" / "two.md").write_text(
                source_page("raw/articles/shared.md", digest, title="Source Two"),
                encoding="utf-8",
            )
            before_index = (wiki / "index.md").read_bytes()
            before_log = (wiki / "log.md").read_bytes()

            result, output = run_command("ingest-finalize", str(wiki), "sources/one.md")
            self.assertEqual(result, 1)
            report = parse_json_output(output)
            self.assertFalse(report["finalized"])
            self.assertTrue(any("exactly one active source summary" in issue for issue in report["issues"]))
            self.assertEqual((wiki / "index.md").read_bytes(), before_index)
            self.assertEqual((wiki / "log.md").read_bytes(), before_log)

    def test_finalize_rejects_canonical_identity_and_alias_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wiki = Path(temp_dir)
            self.assertEqual(run_command("init", str(wiki))[0], 0)
            raw_a = wiki / "raw" / "articles" / "alpha.md"
            raw_b = wiki / "raw" / "articles" / "beta.md"
            raw_a.write_text("alpha raw\n", encoding="utf-8")
            raw_b.write_text("beta raw\n", encoding="utf-8")
            (wiki / "sources" / "alpha.md").write_text(
                source_page(
                    "raw/articles/alpha.md",
                    wiki_tools.body_hash_for_text(raw_a),
                    title="Alpha Source",
                    aliases=["shared-source"],
                    url="https://EXAMPLE.test/resource/",
                ),
                encoding="utf-8",
            )
            (wiki / "sources" / "beta.md").write_text(
                source_page(
                    "raw/articles/beta.md",
                    wiki_tools.body_hash_for_text(raw_b),
                    title="Beta Source",
                    aliases=["shared-source"],
                    url="https://example.test/resource",
                ),
                encoding="utf-8",
            )
            before_index = (wiki / "index.md").read_bytes()
            before_log = (wiki / "log.md").read_bytes()

            result, output = run_command("ingest-finalize", str(wiki), "sources/beta.md")
            self.assertEqual(result, 1)
            report = parse_json_output(output)
            issues = "\n".join(report["issues"])
            self.assertIn("duplicate url:", issues)
            self.assertIn("duplicate alias/title", issues)
            self.assertEqual((wiki / "index.md").read_bytes(), before_index)
            self.assertEqual((wiki / "log.md").read_bytes(), before_log)


class NewCliSmokeTests(unittest.TestCase):
    def test_core_and_research_schema_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as core_dir, tempfile.TemporaryDirectory() as research_dir:
            self.assertEqual(run_command("init", core_dir)[0], 0)
            self.assertEqual(run_command("init", research_dir, "--research")[0], 0)
            core = json.loads((Path(core_dir) / "_meta" / "schema.json").read_text(encoding="utf-8"))
            research = json.loads((Path(research_dir) / "_meta" / "schema.json").read_text(encoding="utf-8"))
            self.assertEqual(core["profile"], "core")
            self.assertEqual(research["profile"], "research")
            self.assertEqual(core["schema_version"], 2)

    def test_new_cli_commands_expose_help(self) -> None:
        for command in ("context", "ingest-preflight", "ingest-finalize", "archive"):
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT_PATH), command, "--help"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
