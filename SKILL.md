---
name: Explicit-Info-Doc
description: 根据一份 PRD 或已有评测说明，为 MCP、CLI、Skill 等三方服务确认评测范围、用户场景与 BMK 分布，交付六部分 Explicit Info 文档、同板三图和十道 Showcase；经用户确认示例与修改方向后，按 BMK 补齐单轮评测集并交付飞书多维表格。支持妙搭回传与项目续接。
---

# 三方服务 Explicit Info

本 Skill 有两条入口：PRD 先解析真实业务功能并经妙搭确认；已有评测说明先整理评测范围并由用户确认。两条入口随后统一经过用户场景、BMK 配比、文档/画板、Showcase 和评测集流程。

## 不可变底线

- 一次只处理一份当前来源；不同来源或新一期必须建立独立项目，不按服务名称复用状态。
- 只保留真实可评测的业务功能。登录、鉴权、授权、断网反馈、意图识别、信息收集和多轮追问等工程测试或产品策略不作为功能条目。
- Explicit 题目必须是单轮自足请求，可使用本轮附件和可读取预置业务资源；不依赖前轮聊天、后续追问或用户确认。允许单轮内多步执行和推理。
- BMK、Showcase 和完整评测集共同受“题目价值、真实性/自然度、业务价值、整体难度/覆盖/多样性”约束；不为凑配额放宽边界或制造伪需求。
- 文档、同一原生画板三图和完成证据共同交付；十道 Showcase 固定展示 4 广/3 厚/3 深，完整评测集沿用户确认的 BMK 配额生成，不复制 Showcase 比例。
- 只有用户确认当前展示方案后才能进入下一关；“继续”、沉默或讨论性意见不等于确认。外部写入前按阶段申请并核验权限。

## 主流程

```text
来源识别
  ├─ PRD → 全量解析功能 → 模型筛选真实业务功能 → 归纳两套能力树 → 妙搭确认 → 回读确认范围
  └─ 说明文档 → 提取并规范一级能力/用户需求/能力范围 → 用户确认范围
两条入口汇合
  → 用户场景确认（只确认场景）
  → BMK 确认（总量、广厚深、能力和两类场景分布，附不超过 5 条原因）
  → 申请/核验画板写入权限
  → 撰写六部分文档、三图和十道 Showcase
  → 文档/画板回读验收与交付
  → 用户确认 Showcase 和修改方向
  → 保留认可题，按 BMK 分批补齐完整单轮评测集
  → 新建多维表格、真实回读并回填第六部分
```

范围未确认时不输出场景；场景未确认时不输出 BMK；BMK 未确认或权限未完成时不写最终文档/画板。PRD 范围解析必须覆盖整个“需求详情”及其等价章节的所有子标题、表格、同步块和交互说明，由模型提炼原子业务功能后再筛选工程/策略项；不能只读取功能总览或固定 4.1。说明文档入口不创建妙搭。

## 读取策略

规则按阶段加载，不全文加载所有资料。先用阶段菜单定位，再按当前动作读取必要分组：

```text
python3 <skill>/scripts/read_stage.py <stage>
python3 <skill>/scripts/read_stage.py <stage> --part <分组ID>
```

BMK 只读取 `query-feasibility` 判断任务空间是否能形成真实单轮题；不生成逐题 Query。文档、Showcase、文档交付后的反馈确认、完整评测集和最终交付按动作读取 `query-source`、`query-hard`、`query-naturalness`、`query-labels`、`query-output`、`query-acceptance` 中所需分组。Query 细则以 [Query构建与验收](references/rules/Query构建与验收.md) 为唯一来源。

评测集续接先读摘要，再读局部内容：

1. `runtime.py state ... --summary` 或 `dataset_workflow.py ... state --summary`，确认项目、版本、阻塞和下一入口。
2. 用 `dataset_workflow.py ... rows --id/--ability/--cell --limit ... --compact` 读取有限题目视图。
3. 只有需要判断题目价值、真实性、业务价值、单轮边界、BMK 映射或 Rubrics 时，才读取完整题目；批量新增优先使用 `append`，避免反复携带累计 rows。
4. 评测集写入前用 `export-base` 生成固定七列、分批不超过 200 条的本地写入文件；Lark 实际写入后用 `diagnostic` 对真实 `record-list` 回读、manifest、已核验映射和上次成功快照做有界对账。冲突、歧义、分页未完成或人工改动均阻断自动完成。

摘要、哈希和脚本校验只能定位结构、数量和版本问题，不能替代 BMK、Showcase、逐题语义或最终视觉验收。

## 阶段路由

| 动作 | stage |
|---|---|
| 新来源与项目身份 | `intake` |
| PRD解析、能力归纳、妙搭创建 | `prd` |
| 说明文档范围整理与确认 | `description` |
| 妙搭回传 | `pm-return` |
| 用户场景建议与确认 | `scenarios` |
| BMK规划、可行性修订与确认 | `bmk` |
| 首次六部分文档 | `document` |
| 十道 Showcase 设计/修改/验收 | `showcase` |
| 只改 Rubrics 措辞 | `rubrics` |
| 画板绘制或修订 | `board` |
| 影响不明的修订 | `revise` |
| 文档、画板和 Showcase 交付验收 | `deliver` |
| 文档交付后收集/确认 Showcase 意见 | `dataset-review` |
| 按 BMK 补齐完整评测集 | `dataset` |
| 多维表格写入、回读和链接回填 | `dataset-deliver` |

局部动作优先走对应 stage；影响不明先走 `revise`。规则加载器只定位原文，不替代模型做业务归纳。

## 分工与依赖

- **模型**：读取来源和必要证据，判断真实业务功能，归纳能力和场景，设计/验收 BMK、Query、Showcase 及 Rubrics。
- **脚本**：做确定性解析、ID/结构/数字/重复/配额校验、状态保存、增量合并和有界查询；不替代语义判断。
- **妙搭**：展示和编辑评测范围、切换联动、保存 PM 确认结果；不替代模型归纳。
- **Lark 技能**：按阶段负责飞书文档、画板、权限和多维表格的实际操作；安装提醒由使用方负责，缺失时保留进度并如实说明。

参考资料按[参考资料索引](references/materials/参考资料索引.md)的用途读取。RealUserSim、论文和自然度 PDF 只校准 Query 的表达与信息组织，不提供当前服务的用户频率、产品能力、业务价值或 BMK 比例；具体适配和验收规则在对应阶段 reference 中。Rubrics 方法随包提供，外部 `evaluation-rubrics` 未安装时不阻塞本地方法读取，但不得声称已调用外部 Skill。
