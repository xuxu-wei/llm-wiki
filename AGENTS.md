# 仓库智能体指南

本仓库发布 `llm-wiki` Codex 技能。`skills/llm-wiki/` 是唯一可安装的技能目录。

## 仓库合同

- 把 `SKILL.md`、运行时脚本、references、templates 和 `agents/openai.yaml` 保存在 `skills/llm-wiki/` 下。
- 把仓库文档、CI、测试和设计规范保存在可安装技能目录之外。
- `SKILL.md` frontmatter 只能包含 `name` 和 `description`。
- 保持 `agents/openai.yaml` 与 `SKILL.md` 同步；除非用户明确要求，否则不要增加图标、品牌色、依赖或策略字段。
- 运行时必须支持 Python 3.10+，且仅使用标准库。
- 使用 Codex 和 wiki 本地的 `AGENTS.md` 作为受支持的智能体合同。
- 核心 Markdown/Git 格式不得依赖 Obsidian 或其他可选集成。

## 安全边界

- Git 是强制依赖，也是检查点边界。
- 解析后的路径不得逃逸目标 vault。
- 不得覆盖已有 wiki、智能体合同或页面；新增 raw 不得覆盖现有文件。
- `raw/` 是 Git 版本化证据；运行时不得绕过 `raw-update` 的显式范围、审计和批准。
- Markdown frontmatter 是语义事实源；`index.csv` 必须确定性生成，且不得反向导入页面。
- 读取操作必须保持只读。不得通过隐式 stash、reset、clean 或删除来恢复状态。

## 必须执行的验证

交付变更前：

1. 使用 UTF-8 模式对 `skills/llm-wiki` 运行 OpenAI `quick_validate.py`：`python -X utf8 <skill-creator-dir>/scripts/quick_validate.py skills/llm-wiki`。
2. 运行 `python -m unittest discover -s tests -v`。
3. 运行 `python skills/llm-wiki/scripts/wiki.py --help`。
4. 确认测试没有修改已跟踪文件，也没有把未忽略的缓存加入版本控制。

除非用户明确要求，否则不得执行 `git commit` 或 `git push`，不得发布版本或创建 Git 标签。
