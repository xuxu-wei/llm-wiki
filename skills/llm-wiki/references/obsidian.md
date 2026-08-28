# Obsidian 工作流

默认将 Obsidian 用作人工阅读和编辑界面，同时保证 vault 作为纯 Markdown 仍然有效。

## 编写

- 使用 Obsidian Properties 编辑页面 frontmatter 字段。
- 允许使用中文和其他 Unicode 文件名。图谱中显示的知识节点名称来自可见文件名和 H1 标题。
- 名称重复可能导致 wikilink 歧义时，使用 `[[full/path/to/page]]`。使用普通 wikilinks 和 backlinks 表达语义关系；`sources` 仅用于证据谱系。
- 保持首页和其他 MOC 精选且易读。它们应说明如何有效浏览知识图谱，而不是列出每个页面。
- 将截图、说明性图表和导出的图形放入 `assets/`。作为证据使用的附件放入 `raw/`，并在其来源卡片中引用。

默认 `.gitignore` 将 `.obsidian/` 保留在本地，因此插件、工作区状态和个人设置不会成为共享知识合同的一部分。

## 人工编辑

用户可以直接编辑任何 Markdown 页面，也可以在 `inbox/` 中仅使用 `kind: inbox` 记录未完成的想法。LLM 开始新的写入任务前，使用 `begin` 检测这些变更。审查相关语义元数据和受影响的链接，然后创建独立且经批准的人工检查点。

用户在 Obsidian 中重命名页面后，应审查反向链接，按需更新持久链接，在有帮助时将旧名称加入 `aliases`，通过 `save` 重新生成索引，并在提交重命名前取得批准。

用户要求相关功能时，可以使用可选的 Obsidian Markdown、Bases、Canvas 或 CLI 技能。其输出必须遵守页面和 Git 合同；wiki 无需任何插件也应保持可读。
