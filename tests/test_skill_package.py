from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import sys
import unittest

from tests.wiki_support import REPO_ROOT, SCRIPT_PATH, SKILL_DIR


SKILL_PATH = SKILL_DIR / "SKILL.md"
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
EXPECTED_REFERENCES = {
    "contract.md",
    "create.md",
    "ingest.md",
    "query.md",
    "maintain.md",
    "obsidian.md",
    "tools-and-research.md",
}
EXPECTED_TEMPLATES = {
    "AGENTS-for-wiki.md",
    "home.md",
    "source.md",
    "note.md",
    "inbox.md",
    ".gitattributes",
    ".gitignore",
}
CHINESE_FIRST_FILES = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "docs" / "design-spec.md",
    SKILL_PATH,
    SKILL_DIR / "agents" / "openai.yaml",
)


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.DOTALL)
    if match is None:
        raise AssertionError("SKILL.md must begin with YAML frontmatter")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.startswith((" ", "\t")):
            continue
        if ":" not in line:
            raise AssertionError(f"invalid frontmatter line: {line!r}")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"\'')
    return fields, text[match.end() :]


def local_markdown_links(text: str) -> list[str]:
    results: list[str] = []
    for target in LINK_PATTERN.findall(text):
        path = target.split("#", 1)[0]
        if not path or "://" in path:
            continue
        if path.lower().endswith(".md"):
            results.append(path)
    return results


def narrative_text(text: str) -> str:
    """Return prose-like text while excluding fenced code and inline identifiers."""
    kept: list[str] = []
    in_fence = False
    in_frontmatter = text.startswith("---\n")
    for line_number, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if in_frontmatter:
            if line_number and stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    prose = "\n".join(kept)
    prose = re.sub(r"`[^`]*`", "", prose)
    prose = re.sub(r"https?://\S+", "", prose)
    return prose


def presentation_wrap_lines(text: str) -> list[tuple[int, str]]:
    """Find adjacent ordinary prose lines that should be one semantic paragraph."""
    findings: list[tuple[int, str]] = []
    in_fence = False
    in_frontmatter = text.startswith("---\n")
    previous_was_prose = False
    structural = re.compile(
        r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```|~~~|---$|___$|\*\*\*$|\[[^]]+\]:)"
    )
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if in_frontmatter:
            if line_number > 1 and stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            previous_was_prose = False
            continue
        if in_fence or not stripped:
            previous_was_prose = False
            continue
        is_prose = not (
            structural.match(stripped)
            or line.startswith(("    ", "\t"))
            or stripped.startswith("<!--")
        )
        if is_prose and previous_was_prose:
            findings.append((line_number, stripped))
        previous_was_prose = is_prose
    return findings


class SkillPackageAcceptanceTests(unittest.TestCase):
    maxDiff = None

    def test_installable_skill_has_only_the_current_runtime_resources(self) -> None:
        actual_top_level = {
            path.name
            for path in SKILL_DIR.iterdir()
            if path.name not in {"__pycache__", ".DS_Store"}
        }
        self.assertEqual(
            actual_top_level,
            {"SKILL.md", "agents", "scripts", "references", "templates"},
        )
        self.assertEqual(
            {path.name for path in (SKILL_DIR / "scripts").iterdir() if path.suffix == ".py"},
            {"wiki.py"},
        )
        self.assertEqual(
            {path.name for path in (SKILL_DIR / "agents").iterdir() if path.is_file()},
            {"openai.yaml"},
        )
        self.assertEqual(
            {path.name for path in (SKILL_DIR / "references").iterdir() if path.is_file()},
            EXPECTED_REFERENCES,
        )
        self.assertEqual(
            {path.name for path in (SKILL_DIR / "templates").iterdir() if path.is_file()},
            EXPECTED_TEMPLATES,
        )

    def test_installable_skill_manifest_matches_git_tracking(self) -> None:
        git_marker = REPO_ROOT / ".git"
        if git_marker.is_file():
            marker = git_marker.read_text(encoding="utf-8").strip()
            self.assertTrue(marker.startswith("gitdir:"), marker)
            git_dir = Path(marker.removeprefix("gitdir:").strip())
            if not git_dir.is_absolute():
                git_dir = (REPO_ROOT / git_dir).resolve()
        else:
            git_dir = git_marker
        result = subprocess.run(
            (
                "git",
                f"--git-dir={git_dir}",
                f"--work-tree={REPO_ROOT}",
                "ls-files",
                "-z",
                "--",
                "skills/llm-wiki",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        prefix = "skills/llm-wiki/"
        tracked = {
            path.removeprefix(prefix)
            for path in result.stdout.decode("utf-8").split("\0")
            if path
        }
        actual = {
            path.relative_to(SKILL_DIR).as_posix()
            for path in SKILL_DIR.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.name != ".DS_Store"
            and path.suffix != ".pyc"
        }
        self.assertEqual(tracked, actual)

    def test_skill_frontmatter_trigger_and_agent_metadata_are_minimal(self) -> None:
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        metadata, _body = frontmatter(skill_text)
        self.assertEqual(set(metadata), {"name", "description"})
        self.assertEqual(metadata["name"], "llm-wiki")
        description = metadata["description"].lower()
        for trigger in ("创建", "摄入", "查询", "标签", "审计", "维护"):
            self.assertIn(trigger, description, f"description must trigger {trigger} tasks")
        self.assertLess(len(skill_text.splitlines()), 500)

        agent_text = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")
        keys = set(re.findall(r"(?m)^  ([a-z_][a-z0-9_]*):", agent_text))
        self.assertEqual(keys, {"display_name", "short_description", "default_prompt"})
        self.assertNotRegex(agent_text, r"(?m)^(?:dependencies|tools|policy|icon|brand_color):")
        self.assertIn("$llm-wiki", agent_text)
        self.assertIn("来源", agent_text)
        self.assertIn("知识库", agent_text)

    def test_skill_links_every_reference_directly_and_references_do_not_chain(self) -> None:
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        linked = {
            Path(path).name
            for path in local_markdown_links(skill_text)
            if Path(path).parts and Path(path).parts[0] == "references"
        }
        self.assertEqual(linked, EXPECTED_REFERENCES)
        for name in EXPECTED_REFERENCES:
            target = f"references/{name}"
            self.assertIn(target, skill_text, f"SKILL.md must route directly to {target}")
            reference_text = (SKILL_DIR / "references" / name).read_text(encoding="utf-8")
            self.assertEqual(
                local_markdown_links(reference_text),
                [],
                f"{name} must not require another Markdown reference",
            )

    def test_skill_keeps_route_specific_detail_out_of_the_always_loaded_body(self) -> None:
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        _metadata, body = frontmatter(skill_text)
        self.assertNotRegex(body, r"(?m)^path,kind,summary,aliases,tags$")
        self.assertNotRegex(body, r"(?m)^\s*wiki\.py\s+(?:init|begin|add|context|audit|save|tags)\b")
        for route_word in ("创建", "摄入", "查询", "维护", "Obsidian"):
            self.assertIn(route_word, body)

    def test_skill_explains_the_runtime_commands_and_routes_to_authoritative_help(self) -> None:
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        _metadata, body = frontmatter(skill_text)
        self.assertIn("scripts/wiki.py", body)
        self.assertIn('python "<skill-dir>/scripts/wiki.py" <command>', body)
        self.assertIn('python "<skill-dir>/scripts/wiki.py" --help', body)
        self.assertIn('python "<skill-dir>/scripts/wiki.py" <command> --help', body)
        self.assertRegex(body, r"`init`[^\n]*(?:创建)[^\n]*(?:Git|检查点)")
        self.assertRegex(body, r"`begin`[^\n]*(?:只读)[^\n]*HEAD[^\n]*(?:基线|变更)")
        self.assertRegex(body, r"`add`[^\n]*raw[^\n]*(?:来源|草稿)")
        self.assertRegex(body, r"`context`[^\n]*(?:结构化查询)[^\n]*(?:候选|检索)")
        self.assertRegex(body, r"`audit`[^\n]*(?:只读)[^\n]*(?:健康|合同)")
        self.assertRegex(body, r"`save`[^\n]*(?:索引)[^\n]*(?:审计)[^\n]*(?:检查点)")
        self.assertRegex(body, r"`tags`[^\n]*(?:标签)[^\n]*(?:审阅|用户|手动)")
        self.assertRegex(body, r"审计[^\n]*\[维护\]\(references/maintain\.md\)")
        self.assertRegex(body, r"标签[^\n]*\[维护\]\(references/maintain\.md\)")
        self.assertIn("精确参数", body)
        for reference in ("create.md", "ingest.md", "query.md", "maintain.md", "contract.md"):
            self.assertIn(f"references/{reference}", body)

    def test_non_code_documents_are_chinese_first_and_semantically_wrapped(self) -> None:
        files = list(CHINESE_FIRST_FILES)
        files.extend(sorted((SKILL_DIR / "references").glob("*.md")))
        files.extend(sorted((SKILL_DIR / "templates").glob("*.md")))
        for path in files:
            with self.subTest(path=path.relative_to(REPO_ROOT).as_posix()):
                text = path.read_text(encoding="utf-8")
                prose = narrative_text(text)
                chinese_characters = len(re.findall(r"[\u3400-\u9fff]", prose))
                latin_words = len(re.findall(r"[A-Za-z]{3,}", prose))
                self.assertGreaterEqual(chinese_characters, 10)
                self.assertGreater(
                    chinese_characters,
                    latin_words,
                    "Chinese prose must be the primary language",
                )
                if path.suffix == ".md":
                    self.assertEqual(
                        presentation_wrap_lines(text),
                        [],
                        "ordinary prose must use semantic paragraphs, not presentation wrapping",
                    )

    def test_page_templates_encode_current_shape(self) -> None:
        expected_kind = {
            "home.md": "moc",
            "source.md": "source",
            "note.md": "note",
            "inbox.md": "inbox",
        }
        for filename, kind in expected_kind.items():
            with self.subTest(template=filename):
                text = (SKILL_DIR / "templates" / filename).read_text(encoding="utf-8")
                self.assertRegex(text, rf"(?m)^kind:\s*{kind}\s*$")
                self.assertRegex(text, r"(?m)^#\s+\S")
                if kind != "inbox":
                    self.assertRegex(text, r"(?m)^summary:")
                    self.assertRegex(text, r"(?m)^tags:")
        source = (SKILL_DIR / "templates" / "source.md").read_text(encoding="utf-8")
        note = (SKILL_DIR / "templates" / "note.md").read_text(encoding="utf-8")
        self.assertRegex(source, r"(?m)^raw:")
        self.assertRegex(note, r"(?m)^sources:")

        attributes = (SKILL_DIR / "templates" / ".gitattributes").read_text(encoding="utf-8")
        self.assertRegex(attributes, r"(?m)^raw/\*\*\s+-text\s+-diff\s+-eol\s*$")
        ignore = (SKILL_DIR / "templates" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".obsidian/", ignore)

    def test_ingest_keeps_a_linked_material_bundle_in_one_source(self) -> None:
        ingest = (SKILL_DIR / "references" / "ingest.md").read_text(encoding="utf-8")
        for phrase in (
            "同一资料包",
            "相对链接",
            "同一 `source` 的多个 `raw`",
            "主读材料",
            "控制性原文",
            "附件角色",
            "派生关系另建 `source`",
        ):
            self.assertIn(phrase, ingest)

    def test_runtime_uses_only_python_standard_library_imports(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SCRIPT_PATH))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        non_standard = sorted(
            name
            for name in imported_roots
            if name != "__future__" and name not in sys.stdlib_module_names
        )
        self.assertEqual(non_standard, [], f"non-standard runtime imports: {non_standard}")

    def test_repository_guidance_and_readme_use_the_current_entrypoint(self) -> None:
        for path in (REPO_ROOT / "README.md", REPO_ROOT / "AGENTS.md"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("skills/llm-wiki/scripts/wiki.py", text)
        workflows = list((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
        for workflow in workflows:
            text = workflow.read_text(encoding="utf-8")
            self.assertIn("skills/llm-wiki/scripts/wiki.py", text, workflow.name)

    def test_each_public_command_has_substantive_help(self) -> None:
        expectations = {
            "init": (("create", "checkpoint"), ("vault", "--name", "--home-summary")),
            "begin": (("inspect", "head", "base"), ()),
            "add": (
                ("raw", "source"),
                ("inputs", "--base", "--name", "--identifier", "--parent", "--raw-dir"),
            ),
            "context": (("query", "index.csv"), ("--plan",)),
            "audit": (("read-only", "health", "structure"), ("--scope", "--format")),
            "save": (
                ("rebuild", "index.csv", "audit", "checkpoint"),
                ("--base", "--operation", "--include", "--approved"),
            ),
        }
        for command, (purpose_terms, arguments) in expectations.items():
            with self.subTest(command=command):
                completed = subprocess.run(
                    (sys.executable, str(SCRIPT_PATH), command, "--help"),
                    cwd=REPO_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
                )
                help_text = completed.stdout.lower()
                for term in purpose_terms:
                    self.assertIn(term, help_text, f"{command} help must explain {term!r}")
                for argument in arguments:
                    self.assertIn(argument, help_text, f"{command} help must document {argument}")

        tag_help = subprocess.run(
            (sys.executable, str(SCRIPT_PATH), "tags", "--help"),
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(tag_help.returncode, 0, tag_help.stderr)
        for term in ("tag", "collect", "apply", "review"):
            self.assertIn(term, tag_help.stdout.lower())
        tag_subcommands = {
            "collect": ("--base", "--output"),
            "apply": ("--base", "--plan", "--approved"),
        }
        for subcommand, arguments in tag_subcommands.items():
            with self.subTest(command=f"tags {subcommand}"):
                completed = subprocess.run(
                    (sys.executable, str(SCRIPT_PATH), "tags", subcommand, "--help"),
                    cwd=REPO_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                for argument in arguments:
                    self.assertIn(argument, completed.stdout)

        source = SCRIPT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SCRIPT_PATH))
        undocumented: list[str] = []
        public_commands: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "add_parser" and node.args:
                command_node = node.args[0]
                if isinstance(command_node, ast.Constant) and isinstance(command_node.value, str):
                    public_commands.add(command_node.value)
            if node.func.attr != "add_argument" or not node.args:
                continue
            label_node = node.args[0]
            label = label_node.value if isinstance(label_node, ast.Constant) else "<dynamic>"
            help_keyword = next((item for item in node.keywords if item.arg == "help"), None)
            if help_keyword is None:
                undocumented.append(str(label))
                continue
            if isinstance(help_keyword.value, ast.Constant) and not str(help_keyword.value.value).strip():
                undocumented.append(str(label))
        self.assertEqual(undocumented, [], "every public CLI argument must have non-empty help")
        self.assertEqual(
            public_commands,
            {*expectations, "tags", "collect", "apply"},
            "the public parser surface must match the documented commands",
        )


if __name__ == "__main__":
    unittest.main()
