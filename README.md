# LLM Wiki Codex 技能

`llm-wiki` 是一个用于构建和维护来源可追溯、由 Git 管理的 Markdown wiki 的 Codex 技能。Obsidian 是默认的人类交互界面；公开状态始终由普通 Markdown、CSV、二进制来源文件和 Git 历史组成。

唯一可安装的技能目录是 [`skills/llm-wiki`](skills/llm-wiki)。测试、CI、设计文档和仓库指南位于该目录之外。

## 工作模型

- Markdown 页面是语义事实源。
- Git 是强制依赖，负责检查点、差异、历史和恢复。
- PDF、XLSX、图片和其他来源文件直接由 `raw/` 下的普通 Git 跟踪。
- `raw/` 保存原始证据文件，`sources/` 用来源卡片描述证据及其限制，`notes/` 保存可复用的综合内容，`inbox/` 接收尚未整理的人类思考，`assets/` 服务于页面展示。
- `index.csv` 是根据页面 frontmatter 确定性生成、可重建的视图。
- `tags-review.csv` 是跨轮次保留的人工标签决策账本，不是当前标签清单。
- Python 负责可重复的文件、索引、检索、审计和 Git 操作。
- LLM 负责解释来源、撰写语义元数据、组织知识、判断证据和综合内容。
- 日常拟定标签时，Python 提供首选、禁用和重命名策略，LLM 优先复用首选标签，只在词表不足时提出新标签。
- 全库标签规范化由用户明确触发：Python 继承历史决策生成临时评审 CSV，用户批准后分别保存页面变化和策略账本；临时文件不进入检查点。

本设计受 Andrej Karpathy 的 [`LLM Wiki` 构想](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)启发。本仓库是独立且有明确设计取向的实现，与 Andrej Karpathy 无从属关系，也不由其维护。

## Wiki 仓库结构

```text
<vault>/
├── AGENTS.md
├── <home>.md
├── index.csv
├── tags-review.csv
├── inbox/
├── raw/
├── sources/
├── notes/
├── assets/
├── .gitattributes
└── .gitignore
```

页面名称可以使用中文或任何其他 Unicode 语言。可见文件名就是 Obsidian 节点名称，不需要额外的 ASCII slug。

## 运行环境

依赖：

- Git
- Python 3.10 或更高版本
- 仅使用 Python 标准库

确定已安装技能的目录后运行：

```text
python <skill-dir>/scripts/wiki.py --help
python <skill-dir>/scripts/wiki.py <command> --help
```

公开命令包括 `init`、`begin`、`add`、`context`、`tags`、`audit` 和 `save`。`tags` 提供 `vocabulary`、`check`、`collect`、`apply` 和 `merge`：前两者约束日常标签，后三者完成经人工审阅的全库维护和策略合并。命令面向智能体输出 JSON。查询和审计操作只读；`audit --scope all` 执行完整健康检查，`audit --scope changed` 聚焦变更范围；一个完整的写入工作流以通过审计的 Git 检查点结束。

## 使用 Codex 安装

向 Codex 发出以下请求：

```text
使用 $skill-installer 安装 https://github.com/xuxu-wei/llm-wiki/tree/main/skills/llm-wiki
```

安装器会把软件包放到 `$CODEX_HOME/skills/llm-wiki`，通常是 `~/.codex/skills/llm-wiki`。该技能从下一轮 Codex 交互开始可用。

## 安全更新

标准安装器不会覆盖已有目标目录。更新时，应先安装到 `$CODEX_HOME/.skill-staging` 下的唯一目录并完成验证，再把当前安装移动到 `$CODEX_HOME/.skill-backups`，最后替换正式安装。如果替换或安装后验证失败，则恢复备份。不要把暂存副本或备份副本放在 `$CODEX_HOME/skills` 下，否则 Codex 可能把它们识别为重复技能。

验证暂存副本和已安装副本：

```text
python -X utf8 <skill-creator-dir>/scripts/quick_validate.py <candidate>/llm-wiki
python <candidate>/llm-wiki/scripts/wiki.py --help
```

## 开发与验证

```text
python -X utf8 <skill-creator-dir>/scripts/quick_validate.py skills/llm-wiki
python -m unittest discover -s tests -v
python skills/llm-wiki/scripts/wiki.py --help
```

CI 会在 Windows、Ubuntu 和 macOS 上使用 Python 3.10 与 3.13 运行测试套件。

## 贡献者

- [Xuxu Wei](https://github.com/xuxu-wei) — 创建者与维护者。
- OpenAI Codex — 技能架构、运行时、测试与验证。
