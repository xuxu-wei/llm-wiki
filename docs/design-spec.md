# LLM Wiki 设计与实施规范

> 状态：供审阅
> 范围：`skills/llm-wiki/`、测试、CI 和仓库说明
> 默认环境：由 Git 管理的本地 Markdown wiki，Obsidian 作为人工界面，Codex/LLM 作为语义协作者

## 1. 目标与职责

LLM Wiki 把工作交给最适合的一层：

| 层 | 职责 |
|---|---|
| Git | 文件差异、检查点、历史和恢复 |
| Python | 路径、文件、frontmatter、CSV/JSON、索引、检索、审计和 Git 编排等确定性操作 |
| LLM | 理解问题、阅读材料、撰写语义元数据、组织知识、判断证据和综合观点 |
| 人类 | 提供材料与思考，决定含义、取舍和高风险变更 |
| Obsidian | 阅读、编辑、Properties、wikilink、backlink、Graph 和 MOC |
| 专业工具 | 读取或分析 PDF、表格、网页、图像等格式，并把结果交给 LLM |

核心原则：

1. Markdown 页面是语义事实源。
2. Git 是强制依赖，也是持久历史与恢复边界。
3. `index.csv` 是从 Markdown 文件头确定性生成的可重建视图。
4. PDF、XLSX、图片和其他原始材料直接由普通 Git 跟踪。
5. 重复、机械、可验证的工作由 Python 完成；模糊、语义和写作工作由 LLM 完成。
6. 公共运行时支持 Python 3.10+，且仅使用标准库。
7. 核心格式不依赖 Obsidian，但默认围绕 Obsidian 的使用体验设计。
8. PDF 解析、OCR、电子表格分析和网页读取复用专业工具，不进入核心运行时。
9. 标签事实保存在 Markdown；跨轮次人工决策保存在 Git 跟踪的 `tags-review.csv`，两者不得互相冒充。

本文规定产品行为、公共数据合同和可观察结果。标准库调用方式、异常分支、临时文件和 Git 内部步骤由实现与单元测试决定。

## 2. 人类—LLM—Wiki 交互模型

### 2.1 材料进入 wiki

用户可以提供本地文件、网页、文字、图片或已有笔记。LLM 先判断材料的用途：

- 需要原样保存的证据进入 `raw/`，并建立 `source` 页面；
- 用户自己的思考进入 `inbox/` 或 `note`；
- 只服务于页面展示的附件进入 `assets/`；
- 仅用于当前分析的中间产物不持久化。

LLM 阅读材料后撰写来源页，说明来源是什么、能够支持什么以及存在什么限制。多个来源形成稳定概念、比较或判断时，再建立笔记并通过 `sources` 和 wikilinks 连接证据。

### 2.2 用户输入与整理思考

用户可以直接在 Obsidian 中向 `inbox/` 写入只有 `kind: inbox` 和正文的草稿，无需先完成摘要或分类。

整理草稿时，LLM：

1. 保留用户原意并识别问题、主张和待验证部分；
2. 查找相关来源页、笔记和 MOC；
3. 把可复用内容整理为笔记，补齐 `summary` 和 `tags`；
4. 区分用户观点、LLM 推断和有来源支持的结论；
5. 涉及合并、删除或大幅改写用户文字时先让用户审阅。

未经证实的思考可以保留，但不得伪装成有来源支持的结论。

拟定标签前，LLM 先读取 Python 从人工决策账本生成的首选、禁用和重命名策略，优先复用已有规范标签。只有首选词表不能充分描述材料时才提出新标签，写入前再由 Python 检查。

### 2.3 LLM 阅读、联系与综合

LLM 先通过索引查找候选，再打开真实页面；链接扩展围绕当前问题进行。LLM 可以比较多个来源的共同点、分歧和证据强弱，发现页面之间缺失的联系，形成综合笔记，建议拆分、合并、改名或加入 MOC，并在必要时回到原始材料核对内容。

引用 wiki 观点时应能指出支撑它的来源页。证据不足时保留不确定性，不得根据索引摘要补全事实。

### 2.4 用户与 LLM 共同打磨内容

LLM 生成的内容直接进入普通 Markdown 页面。用户可以在 Obsidian 中修改，Git 差异用于展示变化并保存检查点。

LLM 开始写入任务前先检查工作树：

- 工作树干净时，以当前 HEAD 为基线；
- 存在人类编辑时，先审计并形成独立检查点；
- 存在未完成的 LLM 变更时，继续该变更或由用户决定如何处理，不与新任务静默混合。

LLM 只复核发生语义变化的页面及必要的直接邻居。用户可以要求继续打磨、比较 Git 中的页面状态、恢复内容或把成熟页面加入 MOC。

### 2.5 文档生命周期

```text
外部材料 → raw + source ─┐
                         ├→ note ↔ note → MOC/首页
用户快速输入 → inbox ───┘
```

- `raw` 保存证据，不被整理操作改写；
- `source` 随对来源理解的加深而更新；
- `inbox` 可以保留、整理或经用户确认后删除；
- `note` 可以持续修订、拆分、合并或由更准确的笔记取代；
- MOC 随知识结构变化而人工策展；
- 每个接受状态通过 Git 检查点保存。

## 3. Wiki 仓库合同

### 3.1 目录结构

```text
<vault>/
├── AGENTS.md
├── 首页.md
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

首页名称在初始化时配置。目录可以按需创建子目录。`.obsidian/` 默认保留在本地，不属于公共 wiki 状态。

Python 使用标准路径接口把输入统一为 vault 内的相对路径；无法解析到 vault 内或发生路径冲突时拒绝写入。

### 3.2 目录职责

| 位置 | 职责 | 是否进入页面索引 |
|---|---|---|
| `raw/` | 摄入材料的原始字节，如 PDF、XLSX、图片和网页快照 | 否 |
| `sources/` | 来源卡片，记录来源提供的证据及其限制 | 是 |
| `notes/` | 跨来源可复用的概念、论点、问题、比较和综合 | 是 |
| `inbox/` | 用户尚未整理的想法、摘记、问题和临时输入 | 是 |
| `assets/` | 为页面展示服务的附件，如示意图、截图和导出图表 | 否 |
| 首页与 MOC | 人工策展的主题入口和导航 | 是 |
| `tags-review.csv` | 跨轮次保留的人工标签决策账本 | 否 |

`raw/` 保存证据文件，`sources/` 让证据成为可查找、可链接、可评价的知识节点。一个来源页可以引用一个或多个 raw 文件。网页、粘贴文本和口述记录在可行时也应固化到 raw；无法固化时，来源页必须保留访问方式并说明证据限制。

`assets/` 只用于呈现。例如，论文 PDF 放入 `raw/`，解释论文结构的示意图放入 `assets/`，对论文的整理放入 `sources/`，从多篇论文形成的观点放入 `notes/`。需要作为证据引用的文件必须摄入 `raw/`。

### 3.3 页面类型与字段

可索引页面使用兼容 Obsidian Properties 的 frontmatter：

| 字段 | 类型 | 规则 |
|---|---|---|
| `kind` | string | 必填；`source`、`note`、`moc` 或 `inbox` |
| `summary` | string | `source`、`note` 和 `moc` 必填；`inbox` 可省略 |
| `aliases` | list[string] | 可省略；页面、概念或来源的替代称呼 |
| `tags` | list[string] | `source`、`note` 和 `moc` 必须存在，可为 `[]`；`inbox` 可省略 |
| `sources` | list[link] | 可省略；笔记和 MOC 的证据来源，或来源页的父来源 |
| `raw` | list[link] | 来源页可用；指向本地 raw 文件 |

目录与 `kind` 必须一致：

- `sources/` 下的 Markdown 使用 `source`；
- `notes/` 下的 Markdown 使用 `note` 或 `moc`；
- `inbox/` 下的 Markdown 使用 `inbox`；
- 根首页使用 `moc`。

`summary` 概括当前页面作为知识节点的内容和用途。来源页的 `summary` 说明该页整理的主题与证据范围，不复制材料自身的摘要；笔记的 `summary` 概括本页观点或概念；MOC 的 `summary` 概括导航范围。LLM 在创建页面或页面语义变化时撰写 `summary`，索引生成器只读取它。

Python 结构化读取已知字段，保留未知 Obsidian 属性和正文。已知字段无法解析或不符合合同时阻止保存，并指出页面与字段；运行时不得猜测含义或静默重写用户正文。

Python 在生成 `index.csv` 时规范化列表值；LLM 决定哪些 `aliases` 和 `tags` 在语义上合适。拟写标签前，Python 从 `tags-review.csv` 返回首选标签、禁用标签和重命名映射；LLM 先复用合适的首选标签，只在词表不足时提出新标签，并在写入前调用确定性检查。

### 3.4 命名与链接

CLI 使用 `PAGE_NAME`，页面文件名就是 Obsidian 中可见的节点名称。

- 来源页使用人类可识别的来源标题，必要时加作者或年份消歧；
- 笔记使用核心概念、问题或结论命名；
- MOC 使用其覆盖的主题范围命名；
- 中文和其他 Unicode 名称均可使用，通常优先采用用户实际搜索时使用的语言；
- 页面一级标题与文件名一致；
- 改名时把仍有检索价值的旧名加入 `aliases`；
- 持久链接使用包含目录的 wikilink，避免同名页面产生歧义。

raw 文件名保留原始或可追溯的名称。知识点体现在笔记名称中，来源主题体现在来源页名称中。路径发生冲突时，Python 生成不覆盖现有文件的新路径。

### 3.5 MOC 与首页

MOC（Map of Content）是人工策展的导航页，表达从哪里进入、哪些页面应放在一起以及推荐怎样阅读，不追求完整列出全部页面。

首页是根 MOC。其他 MOC 可以放在 `notes/` 中，并像普通页面一样参与链接图。

`index.csv` 服务于机器召回，MOC 服务于人类理解、浏览和策展。Python 不从 CSV 自动撰写 MOC；LLM 根据真实页面和用户意图维护 MOC。

## 4. 原始材料与来源

### 4.1 摄入流程

材料摄入形成一个完整的知识变更：

1. Python 把输入复制到 `raw/`，保持原始字节；
2. 内容完全相同时复用已经存在的 raw 路径；
3. LLM 创建或更新来源页，填写页面级 `summary`、`aliases`、`tags`、`raw` 和必要的来源说明；
4. 需要长期复用的跨来源认识写入笔记；
5. Python 重建索引、执行审计并形成 Git 检查点。

Git blob identity 用于 wiki 内的精确重复判断。每个 raw 文件由一个来源页声明；一个来源页可以声明多个 raw 文件，其他页面通过该来源页引用证据。

### 4.2 不可变性

已提交 raw 的路径和字节不可由技能改写。材料内容发生变化时，添加新的 raw 路径并更新来源关系；同一路径出现不同内容时阻止覆盖。

`.gitattributes` 保证 `raw/**` 按二进制字节跟踪。所有 raw，包括 PDF 和电子表格，直接进入 Git 提交。

### 4.3 派生文件

文本提取、OCR、临时 CSV、分析缓存和工具中间文件默认是临时产物。需要长期引用的派生结果应作为新的来源材料摄入，并在来源页中说明它与父来源的关系。

核心运行时不解析专业二进制格式。LLM 通过 PDF、表格或研究工具读取材料，把有价值的语义结果写入 Markdown。工具输出不得未经审阅就成为 wiki 事实。

## 5. `index.csv`

### 5.1 数据合同

`index.csv` 位于 vault 根目录，由 Git 跟踪，禁止人工逐行维护。固定字段为：

```csv
path,kind,summary,aliases,tags
```

- `path` 是包含 `.md` 的 vault 相对路径；
- `kind`、`summary`、`aliases` 和 `tags` 来自页面文件头；
- `aliases` 和 `tags` 在单元格内使用 JSON 数组；
- 缺省列表写为 `[]`，`inbox` 缺少 `summary` 时写空字符串；
- 文件使用 UTF-8 和 LF，记录按 `path` 排序。

索引范围包括根首页以及 `sources/`、`notes/` 和 `inbox/` 下的 Markdown。`raw/`、`assets/`、`.obsidian/`、`AGENTS.md` 和仓库说明不进入索引。

### 5.2 维护流程

Git 判断可索引 Markdown 是否新增、修改、删除或重命名。Git 不逐行更新 CSV。

建立检查点时：

1. 可索引 Markdown 未变化且现有索引一致时，跳过索引生成；
2. 页面变化或索引不一致时，Python 扫描全部可索引页面，但只读取文件头；
3. Python 生成完整且规范化的 CSV；
4. 新旧字节相同则不写，字节不同时替换 `index.csv`；
5. `audit` 独立执行同一生成过程，并要求结果与待保存索引完全一致。

索引缺失、损坏或被手工修改时，`audit` 必须发现。修复方式是从 Markdown 文件头重建。CSV 不反向修改页面。

该流程的正确性不依赖 Git hook。hook 可以调用只读 `audit`，但不得在提交过程中修改页面或索引。

### 5.3 语义元数据同步

Git 指出哪些文档发生变化，LLM 判断 `summary`、`aliases` 和 `tags` 是否仍准确。建立检查点时，LLM 只审阅发生语义变化的页面：

- 新页面、改名页面和类型变化页面必须审阅；
- 正文核心主题、主张或适用范围变化时，同步更新相关元数据；
- 排版、拼写或机械性链接修复不要求重写准确的元数据；
- 未变化页面只参与 Python 的文件头扫描，不由 LLM 重新阅读。

索引维护的 token 成本取决于本次语义变化，而不是 vault 总大小。

### 5.4 标签规范化

标签系统分为三层：页面 frontmatter 中的 `tags` 是当前事实源，根目录 `tags-review.csv` 是跨轮次人工决策账本，`tags-review-<random>.csv` 是单轮维护的临时提案。`index.csv` 只从页面生成；策略账本不是当前标签清单，也不记录页面路径。

`tags-review.csv` 与临时表使用固定字段 `tag,page_count,action,target`。账本的 `page_count` 是该标签最后一次进入审阅时的页面数，不是实时计数。新 vault 初始化空账本；既有 vault 首次合并策略时创建。一旦账本进入 HEAD，受支持的流程不得删除它。

日常页面标签流程为：

1. `tags vocabulary` 从 keep 标签和 rename 最终目标生成 `preferred_tags`，从 delete 标签和 rename 源生成 `forbidden_tags`，并返回 `rename_map`。
2. LLM 优先使用首选词表，不生成禁用标签。只有词表不能较完整描述材料时才提出新标签。
3. 写入前运行 `tags check --tags-json <JSON>`。被禁用或可以重命名的标签必须修正，未知但合法的新标签被报告并留待后续人工维护。

全库标签规范化只由用户明确触发，并形成页面和策略两个分离的检查点：

1. 在干净页面基线 B0 上运行 `tags collect`。Python 扫描全部可索引文件头，在 wiki 根目录生成被忽略的临时 CSV；显式 `--output` 只能指定 vault 外的新文件。
2. 当前已有历史决策的标签继承 `action/target`，当前出现的历史 rename 目标预填 keep，真正的新标签保持未决。LLM 只修改 `action` 和 `target`，用户审阅并确认。
3. 若当前决定会与未出现在本轮清单中的历史 rename 源形成链，用户另行审阅 amendments。补丁只能覆盖账本已有且未出现在当前 plan 的标签，保持历史 `page_count`，并直接改到最终目标。
4. `tags apply` 联合验证 plan、amendments、B0 和当前页面状态；未批准时只预览，批准后只修改受影响页面的顶层 `tags`。多对一映射按首次出现顺序去重，其他 Properties、BOM、换行和正文保持不变。
5. 实际页面 diff 与批准方案一致后，`save --operation tag-maintenance` 只保存页面和生成的 `index.csv`，得到 B1。没有页面变化时不创建空检查点。
6. 在干净 B1 上由 `tags merge` 预览并原子更新账本。本轮记录覆盖或新增，未在本轮出现的历史记录及其计数原样保留。
7. `save --operation tag-policy` 只保存 `tags-review.csv`。两个实际需要的检查点都成功后，才可删除临时 plan 和 amendments。

CSV 输出使用 UTF-8 BOM 和 LF，输入兼容 UTF-8 BOM、CRLF 和任意行序。可能触发电子表格公式解析的单元格使用可逆前导 `'`。Python 在首次写入前联合拒绝未决 action、非法 target、遗漏或重复、目标被 delete、自重命名、rename 链或环、NFC/casefold 冲突以及非规范公式单元格；允许多个旧标签指向同一最终标签，但不自动展开链或替用户消解冲突。

临时 CSV 不属于持久 wiki，不进入索引或 Git 检查点，运行时也不自动删除。普通页面维护、`audit` 和 `save` 不自动启动全库标签规范化。

## 6. 检索合同

### 6.1 默认管线

自然语言问题先由 LLM 编译为精简的结构化查询，再由 Python 查询 `index.csv`：

```text
用户问题
→ LLM 编译精简的结构化 QueryPlan
→ Python 确定性查询 index.csv
→ 返回候选及匹配理由
→ LLM 打开真实 Markdown
→ 沿 wikilinks、backlinks、sources 和 MOC 扩展
→ 必要时执行有界全文搜索
→ 需要核对原始证据时读取 raw
→ 综合并回答
```

LLM 处理中文分词、同义词、缩写、跨语言表达和问题意图；Python 负责低 token、可复现的过滤与排序。

### 6.2 QueryPlan 与候选

QueryPlan 只包含：

| 字段 | 用途 |
|---|---|
| `phrases` | 需要优先匹配的短语 |
| `terms` | 关键词、同义词、译名和缩写 |
| `kinds` | 用户明确限定时使用 |
| `required_tags` | 用户明确要求时作为过滤条件 |
| `boost_tags` | 提高相关候选排序，不排除其他页面 |
| `path_prefixes` | 限定知识区域 |
| `limit` | 可选；限制候选数量；省略时返回全部匹配 |

Python 在 `path`、文件名、`aliases`、`tags` 和 `summary` 上执行固定规则的匹配与稳定排序，并返回 JSON。分数只表示排序依据，不表示语义置信度。

LLM 打开候选的真实 Markdown 后才能引用或综合。候选不足时，最多先改写一次 QueryPlan，再回退到有界全文搜索。

### 6.3 未提交变化

查询是只读操作。可索引页面存在未提交变化时，Python 从当前文件头构建临时索引视图，使新增、修改、删除和重命名立即可查，但不写 `index.csv`。正式索引在建立检查点时更新。

## 7. Git 检查点与风险边界

### 7.1 检查点模型

vault 根目录必须是专用 Git worktree 的根目录；可以使用 linked worktree，但不能只是其他项目仓库中的普通子目录。技能使用普通 Git 接口，不建立独立事务日志。

一个写工作流只有满足以下条件才算完成：

1. 基于明确的 HEAD；
2. 只包含本次 wiki 变更和相应生成的 `index.csv`；
3. 通过与独立 `audit` 相同规则的候选检查；
4. 按风险级别完成人工或 LLM 审阅；
5. 形成 Git 检查点。

`add` 形成待处理变更，`save` 成功后工作流完成。`save` 拒绝本次操作范围之外的 vault 变更，并保证索引与本次提交的页面集合一致。

查询和 `audit` 保持只读。`audit --scope all` 执行完整健康检查，`audit --scope changed` 聚焦变更范围；两者使用同一规则源，后者仍报告影响整个 wiki 身份或结构的问题。失败时保留可见差异并报告，不自动执行 `stash`、`reset`、`clean` 或丢弃用户文件。wiki 外的无关变更不得加入检查点。

### 7.2 风险级别

| 级别 | 例子 | 默认行为 |
|---|---|---|
| 常规 | 按用户要求新增 raw、来源页或笔记，同步元数据，修复明确链接，重建索引 | `audit` 通过后可自动提交 |
| 需审阅 | 人工编辑检查点、全库标签规范化、改名、删除、合并、拆分、大幅改写用户内容、改变来源关系、解决冲突 | 展示方案或差异，用户确认后写入或提交 |
| 禁止 | 修改或覆盖已提交 raw、丢弃未确认修改、提交无关文件、由 Python 编造语义元数据 | 停止并说明原因 |

用户可以要求审阅任何常规操作。常规自动提交不等于自动决定语义；`summary`、`tags`、页面边界和证据判断仍由 LLM 在写入前完成。

## 8. CLI 合同

所有公共命令都是技能随附的 `scripts/wiki.py` 子命令，不是独立系统命令。单一入口为：

```text
python scripts/wiki.py <command>
```

精确参数由总帮助和子命令帮助定义：

```text
python scripts/wiki.py --help
python scripts/wiki.py <command> --help
```

| 命令 | 主要输入 | 结果 |
|---|---|---|
| `init VAULT --name NAME --home-summary TEXT` | 新 vault 位置和首页信息 | 建立 Git wiki、基础页面和首次检查点 |
| `begin` | 当前 vault | 返回基线、dirty 状态和需要先处理的人类或 LLM 变更 |
| `add INPUT... --base OID --name PAGE_NAME` | 材料、基线和 Unicode 页面名 | 安装 raw，创建或更新来源草稿，报告重复与冲突 |
| `context --plan JSON` | LLM 生成的 QueryPlan | 返回候选 JSON，可通过 `limit` 限制数量，必要时请求全文回退 |
| `tags vocabulary` | 当前策略账本 | 只读返回首选标签、禁用标签和重命名映射 |
| `tags check --tags-json JSON` | 拟写入页面的标签数组 | 只读返回接受、新增、拒绝及替换建议 |
| `tags collect --base OID [--output CSV]` | 干净页面基线和可选的 vault 外新文件 | 默认在根目录生成继承历史决策的临时评审表 |
| `tags apply --base OID --plan CSV [--amendments CSV] [--approved]` | 页面基线和完整人工方案 | 联合验证并预览；批准后只更新页面标签 |
| `tags merge --base OID --plan CSV [--amendments CSV] [--approved]` | 页面检查点和同一人工方案 | 预览或原子更新持久策略账本 |
| `audit [--scope changed\|all]` | 当前 wiki | `all` 完整检查仓库健康，`changed` 聚焦变化范围 |
| `save --base OID --operation KIND --include PATH... [--approved]` | 本次明确变更 | 重建索引、审计，并在满足风险规则后创建检查点 |

CLI 面向 LLM 的正常输出为 JSON。错误必须指出操作、文件和可执行的下一步。`audit` 可以提供适合人工查看的 text 或 CSV 输出。

行为约束：

- `init` 不覆盖已有仓库配置或用户文件；
- `begin` 不修改工作区；
- `add` 不覆盖已有 raw 或页面；
- `add` 返回待处理状态，不代表工作流已经完成；
- `context` 与 `audit` 不写文件；
- `tags vocabulary` 和 `tags check` 只读；未知标签可以作为 `new_tags` 返回，禁用标签必须在写入前修正；
- `tags collect` 要求写入前工作树完全干净；省略 `--output` 时只在 wiki 根目录创建被忽略的 `tags-review-<random>.csv`，显式输出必须位于 vault 外且不能覆盖已有文件，不修改 Git 暂存区或 HEAD；
- `tags apply` 要求调用者原样复用 collect 返回的页面基线，且页面状态仍然有效；未提供 `--approved` 时返回退出码 `5` 且不写页面，批准后也不改动计划、策略账本、索引或 HEAD；
- `tags merge` 要求页面应用已形成精确检查点且工作树干净；未提供 `--approved` 时返回退出码 `5` 且不写账本，批准后只原子更新 `tags-review.csv`；
- `tag-maintenance` 与 `tag-policy` 都是高风险保存操作；前者只保存页面和生成的索引，后者只保存策略账本；
- `audit` 只要求当前目录可以定位为 Git worktree 根；结构损坏时尽量返回完整 findings；
- `begin`、`add`、`context`、`tags` 和 `save` 严格拒绝不满足仓库合同的 wiki；
- `save` 与独立 `audit` 使用同一审计规则，并允许在审计候选检查点前重建缺失的 `index.csv`；
- `save` 的候选检查点只包含明确纳入的路径及其必需生成物；
- 需审阅操作没有 `--approved` 时只返回差异，不提交；
- 所有命令都从 vault 根目录解析相对路径。

## 9. 技能包设计

技能包结构：

```text
skills/llm-wiki/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── wiki.py
├── references/
│   ├── contract.md
│   ├── create.md
│   ├── ingest.md
│   ├── query.md
│   ├── maintain.md
│   ├── obsidian.md
│   └── tools-and-research.md
└── templates/
    ├── AGENTS-for-wiki.md
    ├── home.md
    ├── source.md
    ├── note.md
    ├── inbox.md
    ├── .gitattributes
    └── .gitignore
```

`SKILL.md` 只保留技能触发条件、各层职责、任务路由、写入安全边界以及参考文档加载条件。详细结构、工作流和命令行为放入对应参考文档。

`note.md` 同时提供整合多个来源的持久笔记所需的页面结构和写作提示，`SKILL.md` 只负责按任务导航。

任务路由：

- 创建 wiki 时读取 `create.md` 和 `contract.md`；
- 摄入证据或记录用户思考时读取 `ingest.md`，需要专业格式读取或外部研究时追加 `tools-and-research.md`；
- 查询和综合已有知识时读取 `query.md`；
- 创建、扩展或重构整合多个来源的持久 note 时，另读并使用 `note.md`；
- 审计、修复、重命名、重组或保存检查点时读取 `maintain.md`，仅在修复结构时追加 `contract.md`；
- 处理 Obsidian 专属编辑、Properties、附件、MOC 或 Graph 行为时读取 `obsidian.md`。

普通查询不加载创建或实现说明，普通摄入不加载完整维护手册。所有参考文档由 `SKILL.md` 直接链接，不形成必读链。

`wiki.py` 使用 Python 标准库实现确定性文件、索引、检索、审计和 Git 操作；内部模块划分与调用顺序由代码和测试决定。

## 10. 实施任务与出口标准

### 10.1 公共合同

任务：

- 固定页面、CSV、JSON 和 CLI 的基准样例；
- 固定目录、元数据、raw 不可变性和 Git 检查点合同；
- 使技能说明、模板、运行时和测试使用同一合同。

出口标准：合同测试可以区分有效与无效 vault，且没有未决的 schema 或 CLI 决策。

### 10.2 确定性运行时

任务：

- 实现页面文件头、路径、raw、索引、链接和 Git 支持；
- 覆盖成功、冲突和失败保持状态；
- 保证相同输入生成相同字节结果。

出口标准：同一 vault 在 Windows、Linux 和 macOS 上生成相同 `index.csv`，重复执行不会产生无效差异，失败不会丢失用户文件。

### 10.3 工作流

任务：

- 实现 `init`、`begin`、`add`、`context`、`tags`、`audit` 和 `save`；
- 实现未提交变化的只读检索视图；
- 实现标签词表、写前检查、继承历史决策的人工评审、确定性应用和策略合并流程；
- 实现常规与需审阅的 Git 检查点；
- 建立端到端测试样例。

出口标准：从创建 vault、摄入材料、自然语言查询、标签维护、Obsidian 人工编辑到再次保存可以完整运行。

### 10.4 技能资源

任务：

- 保持 `SKILL.md` 简洁，并按需加载参考文档；
- 提供与页面合同一致的模板；
- 同步根 README、CI、测试和 `agents/openai.yaml`；
- 验证安装包只包含运行所需资源。

出口标准：任一典型任务只需加载完成该任务所需的最小参考文档集合。

## 11. 验收标准

### 11.1 Wiki 仓库与页面

- 新建 wiki 可以直接作为 Obsidian vault 使用。
- `source`、`note`、`moc` 和 `inbox` 能按合同保存页面级元数据与来源关系。
- 中文和其他 Unicode 文件名、Properties、wikilinks、backlinks 和 Graph 可以正常使用。
- 无效页面阻止保存并指出页面和字段，不改写未知属性或用户正文。
- raw、来源页、笔记、收件箱、展示附件和 MOC 的职责在模板与行为中一致。
- 新建 vault 跟踪空的 `tags-review.csv`，既有 vault 可以在首次批准的策略合并时建立账本。

### 11.2 索引

- `index.csv` 完整反映所有可索引页面的 `path` 与页面元数据。
- 页面新增、修改、删除和重命名后能得到正确索引；`raw/` 和 `assets/` 不进入索引。
- 相同输入重复生成不产生差异。
- 索引缺失、损坏或被手工修改时，`audit` 能发现，`save` 能从 Markdown 恢复。
- CSV 不反向修改 Markdown。

### 11.3 检索

- 用户可以直接用自然语言提问。
- 自然语言问题能得到相关候选页面，系统随后读取真实页面并按需扩展。
- 默认流程不把整份 `index.csv` 放入上下文。
- 中文、英文、别名和跨语言查询能通过 LLM 提供的术语召回相关页面。
- 索引不足时能回退到全文和 raw，并指出回答实际使用的页面或来源。
- 未提交页面变化可以被只读查询看到，查询不写文件。

### 11.4 Git 与 raw

- Git 缺失时拒绝创建或写入 wiki。
- 每次成功 `save` 都形成可审阅、可恢复的检查点；未保存草稿保持可见且可继续处理。
- 常规操作可以自动提交，高风险操作必须先得到用户确认。
- 人类编辑与新的 LLM 变更不会被静默混入同一检查点。
- 已提交 raw 的路径和字节不会被技能改变，二进制材料摄入前后保持一致。
- 完全重复的材料复用已有 raw，同路径不同内容不会覆盖。
- 操作失败不自动丢弃修改，也不提交 wiki 外的无关文件。

### 11.5 健康检查

- `audit --scope all` 对合法 wiki 返回退出码 `0` 和 `valid: true`，且不改变工作树、HEAD 或暂存区。
- `audit --scope changed` 聚焦变更范围，同时保留影响整个 wiki 身份或结构的 findings。
- 缺失核心文件或目录、HEAD 跟踪不完整、首页缺失或重复时，`audit` 返回退出码 `4` 和结构化 findings，而不是在首个前置错误处停止。
- `E_VAULT_HEAD`、`E_VAULT_TRACKING`、`E_HOME_HEAD_COUNT` 和 `E_VAULT_DIR` 分别稳定表示 HEAD、核心路径跟踪、HEAD 首页数量和必需目录问题；既有页面、链接、raw 和索引错误码保持不变。
- 普通非 wiki Git 仓库执行 `audit` 时只读报告健康问题；不能定位为 Git worktree 根时报告调用错误。
- JSON、text 和 CSV 审计输出都保持有效，健康检查不自动修复文件，也不扫描全部 Git 祖先中的 raw 历史。
- `save` 在 `index.csv` 缺失时能够先从 Markdown 重建索引，再使用同一规则完成候选检查点审计。
- `tags-review.csv` 存在时，完整审计验证其结构和策略一致性；已被 HEAD 跟踪的账本不能通过受支持流程删除。

### 11.6 Obsidian 与协作

- 首页和 MOC 提供人工导航，`index.csv` 提供机器召回，两者互不覆盖。
- 用户在 Obsidian 中编辑或改名后，建立检查点前会审阅受影响页面的元数据，最终索引与 frontmatter 一致。
- `inbox` 可以保存无来源、未验证的用户思考，整理时再补齐持久元数据。
- LLM 生成内容可以由用户继续修改、比较和恢复，不需要专用 AI 文件格式。

### 11.7 技能交付

- `skills/llm-wiki/` 是唯一可安装目录，只包含运行所需的 `SKILL.md`、`agents/`、`scripts/`、`references/` 和 `templates/`。
- `SKILL.md` frontmatter 只有 `name` 和 `description`，description 能触发创建、摄入、查询、标签规范化、审计和维护任务。
- `agents/openai.yaml` 与 `SKILL.md` 保持一致，且不增加未要求的界面字段。
- `SKILL.md` 少于 500 行，只保留核心职责、路由和安全边界。
- 每个参考文档都由 `SKILL.md` 直接链接并注明加载条件；参考文档之间不形成必读链。
- 路由测试证明普通查询只加载查询指南，摄入、维护和 Obsidian 任务只增加各自需要的指南。
- 模板生成的页面符合页面合同，正常操作文档使用 `wiki.py`。
- `SKILL.md` 说明七个公共子命令来自随附的 `wiki.py`，提供总帮助、子命令帮助、简短用途地图和工作流文档路由，不复制精确参数表。
- 每个子命令的 `--help` 解释自身用途及参数，`--help` 是精确 CLI 接口的唯一事实源。
- 技能和文档以中文作为主要语言，正文按语义段落换行，不使用固定宽度排版换行。
- Python 运行时支持 3.10+，且仅使用标准库。
- 测试不修改已跟踪文件，也不把缓存加入版本控制。

必须通过：

```text
python -X utf8 <skill-creator>/scripts/quick_validate.py skills/llm-wiki
python -m unittest discover -s tests -v
python skills/llm-wiki/scripts/wiki.py --help
```

### 11.8 端到端场景

1. 初始化包含中文首页的 Obsidian vault，并得到干净的 Git 检查点。
2. 摄入中文 PDF 并创建来源页；存在耐久知识增量时创建或更新笔记；重复摄入复用 raw，原字节不变。
3. 用户在 Obsidian 中修改并重命名页面；系统先形成人工编辑检查点，再继续 LLM 工作。
4. 用户用自然语言跨来源页与笔记查询；系统通过索引候选、链接扩展和必要的 raw 核对回答。
5. 索引被删除或手工修改后，`audit` 发现漂移，`save` 从 Markdown 文件头确定性重建，并在重复运行时保持字节不变。
6. 用户明确要求规范标签；系统在根目录生成继承历史决策的临时评审表，LLM 处理新标签，用户修订并批准，Python 更新页面后分别保存页面与索引检查点、策略账本检查点。

### 11.9 标签管理

- 普通页面维护、`audit` 和 `save` 不会自动启动全库标签规范化。
- `tags vocabulary` 稳定返回 keep 标签和 rename 目标组成的首选词表、delete 标签和 rename 源组成的禁用词表以及 rename 映射；`tags check` 在页面写入前报告新标签并拒绝禁用标签。
- `tags collect` 为当前每个标签生成一行 `tag,page_count,action,target`，继承已有决策并把真正的新标签留空；除创建根级被忽略的临时表外，不改变页面、HEAD、暂存区、`index.csv` 或账本。
- 中文以及包含逗号、引号或 `/` 的标签能够在 UTF-8 BOM CSV 中无损往返；空标签集合只生成表头。
- LLM 只为未决项提出保留、重命名或删除方案；歧义项通过 `required_tags` 查询有限页面，用户审阅前不修改 wiki。
- plan 与 amendments 联合拒绝缺行、重复、未决或非法动作、目标被删除、自重命名、rename 链或环、NFC/casefold 冲突和非规范公式单元格；amendments 不能新增标签、与 plan 重叠或改变历史 `page_count`。
- `tags apply` 经批准后只修改实际受影响页面的顶层 `tags`，多对一结果去重并逐字节保留其他 Properties 和正文；页面检查点只包含这些页面和生成的 `index.csv`。
- `tags merge` 保留本轮未出现的全部历史记录，覆盖或新增本轮决策；未批准、陈旧状态或写入前发现的并发变化均零写入，极晚发生的写后竞态保留可见差异并返回冲突。策略检查点只包含 `tags-review.csv`。
- 临时 plan 和 amendments 不进入检查点、不被自动删除，并且只有两个实际需要的检查点均成功后才可清理。
