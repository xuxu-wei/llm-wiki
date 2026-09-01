# 创建 wiki

本流程用于创建新的 vault。已有的无关仓库不能作为初始化目标。

## 初始化

1. 解析用户指定的目标，并在写入前检查它。
2. 确认 Git 和 Python 3.10+ 可用。
3. 选择易读的 Unicode 首页名称，并编写面向检索的首页 summary。优先使用用户搜索时会使用的语言。
4. 从任意目录运行：

   ```text
   python "<skill-dir>/scripts/wiki.py" init "<vault>" --name "<home-name>" --home-summary "<summary>"
   ```

5. 检查 JSON 结果以及初始 Git 差异或检查点。

初始化必须创建独立的 Git worktree，其中包含 `AGENTS.md`、首页 MOC、`index.csv`、空的 `tags-review.csv`、`inbox/`、`raw/`、`sources/`、`notes/`、`assets/`、`.gitattributes` 和 `.gitignore`。`tags-review.csv` 只包含 `tag,page_count,action,target` 表头，使用 UTF-8 BOM 和 LF。遇到冲突时应保留已有用户文件和 Git 配置，不得强制完成初始化。

初始化会创建 `AGENTS.md`；如果已有智能体合同与之冲突，应拒绝继续，不得静默替换或改写。

## 验证

运行 `begin`，然后执行完整的只读 `audit`。确认：

- vault 根目录就是 Git worktree 根目录；
- 初始检查点干净且可恢复；
- 首页是有效的 `moc`，并出现在 `index.csv` 中；
- 空的 `tags-review.csv` 已被 Git 跟踪，临时 `tags-review-*.csv` 被忽略；
- `raw/**` 不进行文本转换；
- `.obsidian/` 被忽略，而 wiki 内容仍由 Git 跟踪；
- vault 可以在 Obsidian 或其他编辑器中作为普通 Markdown 打开。

报告 vault 路径、首页、检查点，以及任何被保留或发生冲突的文件。
