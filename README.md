# 三方服务 Explicit Info

将一份 PRD 或已有评测说明，整理成评测范围、用户场景、BMK 分布、六部分说明文档、同板三张分布图和十道 Showcase；经用户确认示例与修改方向后，补齐完整单轮评测集，交付飞书多维表格。

它提供整理、规划和出题初稿。范围、配比和示例需要人工确认，最终题目仍需逐题审阅。

## 安装与调用

将本仓库完整下载或克隆到 Codex 的技能目录，文件结构应为：

```text
~/.codex/skills/Explicit-Info-Doc/SKILL.md
```

也可以下载发布压缩包，将其中的 `Explicit-Info-Doc` 文件夹放入该目录。已有安装时先备份旧目录，避免直接覆盖个人修改。保留 `references`、`scripts` 和 `agents` 的相对路径。

在 Codex 中选择「三方服务Explicit Info」，或输入以下调用内容，并附上一份来源链接：

```text
$Explicit-Info-Doc
<一份 PRD 或已有评测说明的飞书链接>
```

一次处理一份来源。后续在同一会话按提示确认范围、场景、BMK 和示例；需要调整时直接指出修改项。

## 使用前准备

- 本包的四个脚本使用 Python 3 标准库；外部读写依赖可用的 `lark-cli`。
- 安装并配置 `lark-doc`、`lark-apps`、`lark-whiteboard`、`lark-shared`、`lark-base`；来源涉及电子表格时还需 `lark-sheets`。技能按阶段使用，不要求每次全部加载。
- 当前飞书账号需具备对应来源、妙搭项目及交付资源的访问权限。安装技能不等于已取得资源权限。
- PRD 入口默认连接包内配置的共用妙搭应用。迁移应用时按[连接约定](references/rules/应用连接与状态管理.md)使用独立配置与状态目录。说明文档入口不依赖妙搭。
- Rubrics 编写方法已随包提供，无需另行安装 `evaluation-rubrics`。

## 工作流程

```text
PRD → 解析业务功能 → 妙搭编辑与 PM 确认 ─┐
                                      ├→ 用户场景确认 → BMK 确认
已有评测说明 → 整理范围 → 用户确认 ──────┘
→ 说明文档、画板和十道 Showcase
→ 用户确认示例与修改方向
→ 保留认可题、补齐评测集 → 飞书多维表格
```

Showcase 展示 4 道广度、3 道厚度、3 道深度题；完整评测集使用用户确认的 BMK 配比。所有题目遵循单轮自足、题目价值、自然度、业务价值，以及整集难度、覆盖和多样性要求。具体执行以 [SKILL.md](SKILL.md) 和阶段规则为准。

## 包内结构

| 路径 | 用途 |
| --- | --- |
| `SKILL.md` | 总流程、底线与阶段路由 |
| `agents/openai.yaml` | 展示名称与默认调用提示 |
| `references/rules/` | 范围、场景、BMK、Query、文档、连接及交付规则 |
| `references/materials/` | 方法资料、十套历史案例、画板参考图 |
| `scripts/read_stage.py` | 按阶段读取必要规则 |
| `scripts/source_snapshot.py` | 解析来源结构并提取证据快照 |
| `scripts/runtime.py` | 项目状态、妙搭连接和结构校验 |
| `scripts/dataset_workflow.py` | 示例反馈、题目补齐、Base 导出与回读对账 |
| `tests/` | 确定性行为的回归测试 |

模型负责业务归纳、配比、题目设计和语义验收；脚本负责结构、数量、状态与对账。脚本通过不代表题目质量或真实执行已经验收。

## 维护验证

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

项目运行状态和当前业务来源保存在工作目录，不应写入技能仓库。本包包含内部历史参考资料及共用应用配置；仓库访问范围由维护者管理。
