# 知识库合同

本文档是目录结构、页面文件头、索引和生命周期的语义依据。Python 执行确定性规则，LLM 提供语义。

## 知识库结构

vault 是独立 Git worktree 的根目录。新建 vault 的根目录包含 `AGENTS.md`、首页 Markdown、`index.csv`、`tags-review.csv`、`inbox/`、`raw/`、`sources/`、`notes/`、`assets/`、`.gitattributes` 和 `.gitignore`。既有 vault 可以在首次标签策略合并时创建 `tags-review.csv`；一旦该文件进入 HEAD，受支持的工作流不得删除它。

允许使用子目录。运行时将路径规范化为 vault 相对路径，并拒绝 vault 外路径或发生冲突的路径。

| 位置 | 作用 | 是否索引 |
|---|---|---|
| `raw/` | Git 版本化证据文件，包括 PDF、XLSX、媒体和快照 | 否 |
| `sources/` | 来源卡片，给出原始证据的核心内容、能支持什么及其局限 | 是 |
| `notes/` | 可复用的概念、论点、问题、比较、综合和 MOC | 是 |
| `inbox/` | 尚未整理的用户思考和摘录 | 是 |
| `assets/` | 图表、截图和导出图形等展示附件 | 否 |
| 首页 | 供人类导航的根 MOC | 是 |
| `tags-review.csv` | 跨轮次保留的人工标签决策 | 否 |

证据文件应放入 `raw/`，即使页面中也会展示该文件。`assets/` 用于呈现。一个来源卡片可以对应一个或多个 raw 文件；其他页面引用来源卡片。

## 页面文件头

可索引页面使用兼容 Obsidian 的 frontmatter：

| 字段 | 合同 |
|---|---|
| `kind` | 必填：`source`、`note`、`moc` 或 `inbox` |
| `summary` | `source`、`note` 和 MOC 必填；`inbox` 可选 |
| `aliases` | 可选的别名列表 |
| `tags` | `source`、`note` 和 MOC 必填，但可以为 `[]`；`inbox` 可选 |
| `sources` | 可选的来源页链接列表；在 `source` 页面中可用于标识父来源 |
| `raw` | `source` 页面可选；raw 文件链接列表 |

目录决定允许使用的 kind：`sources/` 使用 `source`；`notes/` 使用 `note` 或 `moc`；`inbox/` 使用 `inbox`；首页使用 `moc`。

`summary` 描述当前 wiki 页面作为检索节点的内容。它不是论文摘要，也不是维护索引时生成的内容。LLM 在创建页面或改变页面语义时编写它。

使用便于人类阅读的 Unicode 文件名，使 H1 与文件名 stem 一致；名称可能有歧义时，在持久 wikilink 中使用完整 vault 路径。来源页以可识别的来源命名，笔记页以人类会搜索的概念、问题或结论命名。重命名后在 `aliases` 中保留有用的旧名称。

运行时验证已知字段，同时保留未知 Properties 和正文。它可以为索引规范化列表值，但不会仅为调整字段顺序而重写 Markdown。

页面 frontmatter 中的 `tags` 是当前标签事实源。`index.csv` 是从页面生成的检索视图。根目录 `tags-review.csv` 是跨轮次人工决策账本，不代表当前标签清单；其固定字段为 `tag,page_count,action,target`，`page_count` 记录该标签最后一次进入审阅时的页面数。

账本中的 `keep` 保留标签，`delete` 禁止再次生成该标签，`rename` 禁止使用源标签并给出最终目标。Python 从 keep 标签和 rename 目标生成 `preferred_tags`，从 delete 标签和 rename 源生成 `forbidden_tags`，但不判断同义关系、粒度或新标签是否在语义上必要。创建或修改页面标签前，LLM 必须读取该词表，优先复用首选标签，并在写入前通过确定性检查。

全库标签规范化只在用户明确触发并批准方案后执行。`tags collect` 默认在 wiki 根目录生成被忽略的 `tags-review-<random>.csv`，也可以显式输出到 vault 外的新文件；临时表继承已有决策，真正的新标签留待人工审阅。可选 amendments 只能修订当前清单之外的历史记录。运行时以可逆前导 `'` 编码可能被电子表格解释为公式的单元格；临时文件不属于持久 wiki，不进入索引或检查点。

页面标签和生成的 `index.csv` 先形成检查点，随后把本轮决策覆盖或新增到 `tags-review.csv`，并单独形成策略检查点。未在本轮出现的历史记录不得删减，amendments 必须保留历史 `page_count`。运行时联合拒绝 rename 链、环、自重命名、目标被删除、NFC/casefold 冲突和未决或非法方案，不自动展平或替用户消解冲突。

## 生成的索引

`index.csv` 由 Git 跟踪，但绝不手动编辑。其准确文件头为：

```csv
path,kind,summary,aliases,tags
```

每个索引页面提供其 vault 相对 `.md` 路径和对应的四个文件头字段。`aliases` 和 `tags` 在 CSV 单元格中表示为 JSON 数组；缺失的列表变为 `[]`，缺失的 `inbox` `summary` 变为空字符串。文件使用 UTF-8 和 LF，记录按 `path` 排序。

首页以及 `sources/`、`notes/` 和 `inbox/` 下的 Markdown 会进入索引。`raw/`、`assets/`、`.obsidian/`、`AGENTS.md` 和仓库文档不进入索引。

Git 检测相关页面变更。保存检查点时，Python 扫描全部可索引文件头，生成完整的规范化 CSV，仅在字节不同时写入。审计重新生成同一视图并进行比较。CSV 绝不反向更新 Markdown。

MOC 与索引并存：`index.csv` 用于机器召回，首页和其他 MOC 用于选择性人工导航。Git 保存持久历史。

## 健康检查

`audit --scope all` 对当前 HEAD 与工作树的结构、页面、链接、raw 和索引合同执行完整的只读健康检查；`audit --scope changed` 聚焦变更范围。

`begin`、`add`、`context`、`tags` 和 `save` 使用同一合同规则执行严格前置验证。`audit` 和候选 `save` 在 `tags-review.csv` 存在时验证其结构与策略一致性；已被 HEAD 跟踪的账本缺失时报告合同错误。`save` 可以从 Markdown 重建缺失的 `index.csv`，再用与独立 `audit` 相同的规则验证候选检查点。

## 原始文件与生命周期

所有 raw 文件通过普通 Git 提交，并排除文本转换，因此每个检查点都保存该时刻的精确路径和字节。新增材料不得覆盖现有文件，内容完全重复时复用已有 raw 路径；已提交 raw 可以通过受审阅的 `raw-update` 修改、改名或删除，旧状态由父提交保留。

提取结果、OCR、转换后的表格和分析缓存默认是临时文件。只有将其作为新的 raw 来源摄入，并记录它与父来源的关系时，才长期保存。

随着理解加深，来源页可以继续完善。`inbox` 页面可以保持未验证状态、晋升或经批准后删除。笔记可以修改、拆分、合并或由新笔记取代。MOC 由人类维护。Git 保存每个被接受的状态。
