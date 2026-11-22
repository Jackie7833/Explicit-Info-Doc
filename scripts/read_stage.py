#!/usr/bin/env python3
"""Read exact sections of the bundled rules, without following their links.

The mapping below is an index, not another copy of the business rules. Only the
five Markdown rule files are opened; no state, attachments, or remote resources
are read. Section bodies, including their nested headings, are preserved.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys


SKILL_ROOT = Path(__file__).resolve().parent.parent
MAX_PART_BYTES = 12 * 1024
RULE_FILES = {
    "app": "references/rules/应用连接与状态管理.md",
    "scope": "references/rules/范围整理与PM确认.md",
    "bmk": "references/rules/用户场景与BMK规划.md",
    "doc": "references/rules/文档撰写与验收.md",
    "query": "references/rules/Query构建与验收.md",
}


@dataclass(frozen=True)
class Part:
    purpose: str
    sections: tuple


# References are (file alias, exact Markdown heading), never line-number ranges.
PARTS = {
    "intake": Part("识别单一来源、项目身份及续接动作", (
        ("scope", "输入判断与事实边界"),
        ("app", "来源身份与项目隔离"),
        ("app", "变更、边界输入与中断恢复"),
    )),
    "scope-core": Part("两条入口共用的事实边界、业务判断与重复包含校验", (
        ("scope", "输入判断与事实边界"),
        ("app", "来源身份与项目隔离"),
        ("scope", "两条入口统一：模型先判断真实业务功能"),
        ("scope", "两条入口统一：重复与包含关系校验"),
    )),
    "extract": Part("PRD 全量解析与可追溯事实快照", (
        ("scope", "PRD：解析完整有效功能清单"),
        ("app", "解析证据与原子功能快照"),
    )),
    "abilities": Part("两套能力树归纳、校验与项目创建", (
        ("scope", "PRD：模型归纳两套能力树"),
        ("app", "模型建议、检查与项目创建"),
    )),
    "pm-page": Part("妙搭连接、页面约定与交付验收", (
        ("app", "阶段依赖路由"),
        ("app", "实际应用连接"),
        ("scope", "妙搭页面与范围切换约定"),
        ("scope", "妙搭交付、PM 回传与验收"),
    )),
    "scope-save": Part("统一范围输出及本地状态保存", (
        ("scope", "两条分支统一输出"),
        ("app", "统一评测范围"),
    )),
    "description-scope": Part("说明文档范围提取、保存及会话确认", (
        ("scope", "评测说明文档：仅提取与规范评测范围"),
        ("scope", "两条分支统一输出"),
        ("app", "统一评测范围"),
    )),
    "pm-result": Part("线上实际确认、回传结果及统一范围", (
        ("app", "实际应用连接"),
        ("app", "读取确认结果与本次明确默认授权"),
        ("scope", "妙搭交付、PM 回传与验收"),
        ("scope", "两条分支统一输出"),
    )),
    "resume": Part("变更分类、确认失效及中断恢复", (
        ("app", "变更、边界输入与中断恢复"),
    )),
    "business-evidence": Part("官网与真实用户反馈、定向补查及逐题业务依据", (
        ("bmk", "业务依据：来源选择与补查"),
    )),
    "scenario-design": Part("第一轮用户活动归纳及场景确认", (
        ("bmk", "两轮交互与前提"),
        ("bmk", "第一轮：从用户活动归纳场景"),
    )),
    "scenario-state": Part("场景提案、确认记录及续接保存", (
        ("app", "确认记录契约"),
        ("app", "用户场景提案"),
        ("app", "变更、边界输入与中断恢复"),
    )),
    "quality-core": Part("共用总纲：评测边界、题目质量与整体质量", (
        ("bmk", "最高优先级总纲：评测边界、题目质量、整体质量"),
    )),
    "quality-dimensions": Part("广厚深维度判定与可行性", (
        ("bmk", "广度、厚度、深度的判定"),
    )),
    "query-feasibility": Part("BMK 规划使用的 Query 可行性接口", (
        ("query", "与 BMK 规划的接口"),
    )),
    "allocation": Part("BMK 分配、重算、确认与状态契约", (
        ("bmk", "两轮交互与前提"),
        ("bmk", "配比分配方法"),
        ("bmk", "第二轮：BMK 确认与重算"),
        ("app", "确认记录契约"),
        ("app", "BMK 方案"),
    )),
    "document-base": Part("写入前提、标题及正文第一至三部分", (
        ("doc", "输入、参考与执行"),
        ("app", "交付前的画板写入授权"),
        ("doc", "标题与标题层级"),
        ("doc", "一、评测目标"),
        ("doc", "二、评测范围"),
        ("doc", "三、评测标准"),
    )),
    "distribution": Part("第四部分正文、三图及分布表格", (
        ("doc", "四、数据分布"),
    )),
    "query-source": Part("Query 依据、论文适用边界与 BMK 接口", (
        ("query", "依据与适用边界"),
    )),
    "query-hard": Part("Query 硬性限制与单轮边界", (
        ("query", "一、硬性限制"),
    )),
    "query-naturalness": Part("Query 自然度与信息组织", (
        ("query", "二、自然度要求"),
    )),
    "query-labels": Part("Query 维度、标签、环境与 Rubrics", (
        ("query", "三、维度、标签与题目组织"),
        ("query", "四、环境信息与 Rubrics"),
    )),
    "query-output": Part("Showcase 与完整评测集的 Query 输出规则", (
        ("query", "五、两条链路的共同规则与差异"),
    )),
    "query-acceptance": Part("Query 逐题统一验收清单", (
        ("query", "六、统一验收清单"),
    )),
    "examples": Part("第五部分十道示例、字段及入选验收", (
        ("doc", "五、题目示例"),
    )),
    "document-finish": Part("第六部分、回读交付及完成证据", (
        ("doc", "六、评测集链接"),
        ("doc", "回读验收、修正与交付"),
        ("app", "文档、画板和完成证据"),
    )),
    "rubric-edit": Part("第五部分字段的措辞修改、受影响验收及输出记录", (
        ("doc", "字段规则"),
        ("doc", "回读验收、修正与交付"),
        ("app", "文档、画板和完成证据"),
    )),
    "board-write": Part("三图绘制要求、阶段工具及画板写入授权", (
        ("doc", "三图同一画板"),
        ("app", "阶段依赖路由"),
        ("app", "交付前的画板写入授权"),
    )),
    "acceptance": Part("原产物回读、预览验收及完成证据", (
        ("doc", "回读验收、修正与交付"),
        ("app", "文档、画板和完成证据"),
    )),
    "revision-impact": Part("修订影响、范围失效与状态通路", (
        ("app", "变更、边界输入与中断恢复"),
        ("app", "统一评测范围"),
    )),
    "revision-output": Part("修订后的文档、画板与完成证据同步验收", (
        ("doc", "回读验收、修正与交付"),
        ("app", "文档、画板和完成证据"),
    )),
    "dataset-feedback": Part("文档交付后的示例回读、聊天反馈与版本确认", (
        ("bmk", "完整评测集：showcase反馈与确认"),
    )),
    "dataset-state-core": Part("评测集交接、确认与可续接状态契约", (
        ("app", "保存示例、反馈并确认"),
        ("app", "规范题目与批次保存"),
    )),
    "dataset-reconcile": Part("Base 导出、远端写入对账与回读诊断", (
        ("app", "远端写入对账与回读诊断"),
        ("app", "远端结果与完成证据"),
    )),
    "dataset-production": Part("保留好题、补齐配额、七列内容与逐题验收", (
        ("bmk", "完整评测集：逐题生成与验收"),
    )),
    "dataset-publish": Part("多维表格写入、首标签顺序回读及原文档链接回填", (
        ("app", "完整评测集：多维表格交付"),
    )),
}

STAGES = {
    "intake": ("输入识别与恢复", ("intake",)),
    "prd": ("PRD 范围整理", (
        "scope-core", "extract", "abilities", "pm-page", "scope-save")),
    "description": ("已有说明文档范围整理", (
        "scope-core", "description-scope")),
    "pm-return": ("妙搭回传", ("pm-result", "resume")),
    "scenarios": ("用户场景建议与确认", (
        "business-evidence", "scenario-design", "scenario-state")),
    "bmk": ("BMK 规划与修订", ("query-feasibility", "quality-core", "quality-dimensions", "business-evidence", "allocation")),
    "document": ("完整文档撰写", (
        "document-base", "distribution", "quality-core", "quality-dimensions", "business-evidence", "query-source", "query-hard",
        "query-naturalness", "query-labels", "query-output", "query-acceptance", "examples", "document-finish")),
    "showcase": ("十道示例设计与验收", (
        "query-source", "query-hard", "query-naturalness", "query-labels", "query-output", "query-acceptance",
        "quality-core", "quality-dimensions", "business-evidence", "examples", "acceptance")),
    "rubrics": ("示例通过标准的纯措辞修订", ("rubric-edit",)),
    "board": ("原画板绘制与修订", ("board-write", "acceptance")),
    "revise": ("修订影响与原产物同步", ("revision-impact", "revision-output")),
    "deliver": ("最终回读与交付", ("query-hard", "query-labels", "query-acceptance", "acceptance")),
    "dataset-review": ("文档交付后的示例与意见确认", (
        "query-source", "query-hard", "query-naturalness", "query-labels", "query-output", "query-acceptance",
        "dataset-feedback", "dataset-state-core")),
    "dataset": ("按确认方案补齐完整评测集", (
        "query-source", "query-hard", "query-naturalness", "query-labels", "query-output", "query-acceptance",
        "quality-core", "quality-dimensions", "business-evidence", "dataset-production", "dataset-state-core",
        "dataset-reconcile")),
    "dataset-deliver": ("多维表格交付与原文档回填", (
        "dataset-publish", "dataset-state-core")),
}


class RuleError(ValueError):
    """A requested section cannot safely be returned."""


@dataclass(frozen=True)
class Heading:
    title: str
    level: int
    start: int
    end: int


def parse_headings(lines):
    """Find ATX headings outside CommonMark backtick/tilde fenced blocks."""
    found = []
    fence_char = None
    fence_length = 0
    for index, line in enumerate(lines):
        line = line.rstrip("\r\n")
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence_char:
            if fence and fence[1][0] == fence_char:
                if len(fence[1]) >= fence_length and not fence[2].strip():
                    fence_char = None
            continue
        if fence and (fence[1][0] != "`" or "`" not in fence[2]):
            fence_char, fence_length = fence[1][0], len(fence[1])
            continue
        match = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$", line)
        if match:
            title = (match[2] or "").strip()
            title = re.sub(r"[ \t]+#+[ \t]*$", "", title)
            found.append((title, len(match[1]), index))
    headings = []
    for position, (title, level, start) in enumerate(found):
        end = next((item[2] for item in found[position + 1:] if item[1] <= level), len(lines))
        headings.append(Heading(title, level, start, end))
    return headings


class RuleReader:
    def __init__(self, root=SKILL_ROOT):
        self.root = Path(root)
        self.cache = {}

    def section(self, alias, title):
        if alias not in RULE_FILES:
            raise RuleError(f"未知规则文件别名 {alias!r}；可用：{', '.join(RULE_FILES)}")
        if alias not in self.cache:
            path = self.root / RULE_FILES[alias]
            try:
                lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
            except (OSError, UnicodeError) as error:
                raise RuleError(f"无法读取 {RULE_FILES[alias]}：{error}") from error
            self.cache[alias] = (lines, parse_headings(lines))
        lines, headings = self.cache[alias]
        matches = [heading for heading in headings if heading.title == title]
        if not matches:
            raise RuleError(f"{RULE_FILES[alias]} 中不存在精确标题 {title!r}；不会回退读取全文")
        if len(matches) != 1:
            locations = ", ".join(str(item.start + 1) for item in matches)
            raise RuleError(f"{RULE_FILES[alias]} 中标题 {title!r} 不唯一（行 {locations}）")
        heading = matches[0]
        return heading, "".join(lines[heading.start:heading.end])

    def render(self, sections):
        chunks = []
        selected = {}
        for alias, title in sections:
            heading, body = self.section(alias, title)
            for earlier in selected.setdefault(alias, []):
                if heading.start < earlier.end and earlier.start < heading.end:
                    raise RuleError(f"同组引用重叠：{alias}/{earlier.title} 与 {title}")
            selected[alias].append(heading)
            location = f"{RULE_FILES[alias]}:{heading.start + 1}-{heading.end}"
            chunks.append(f"来源：{location}\n\n{body}" + ("" if body.endswith("\n") else "\n"))
        output = "\n".join(chunks)
        size = len(output.encode("utf-8"))
        if size > MAX_PART_BYTES:
            raise RuleError(
                f"所选部分为 {size} 字节，超过单组 {MAX_PART_BYTES} 字节上限；"
                "请按已有子标题缩小读取范围或调整索引分组，不会截断输出")
        return output

    def part(self, part_id):
        if part_id not in PARTS:
            raise RuleError(f"未知分组 {part_id!r}")
        return self.render(PARTS[part_id].sections)

    def menu(self, stage):
        purpose, part_ids = STAGES[stage]
        lines = [f"{stage} — {purpose}；用 {stage} --part <ID> 逐组读取。",
                 "字节数为对应分组实际输出的 UTF-8 大小；引用链接不自动展开。"]
        for part_id in part_ids:
            part = PARTS[part_id]
            size = len(self.part(part_id).encode("utf-8"))
            titles = "；".join(f"{alias}/{title}" for alias, title in part.sections)
            lines.append(f"- {part_id} | {part.purpose} | {size} 字节\n  {titles}")
        return "\n".join(lines) + "\n"

    def check(self):
        referenced = set()
        for stage, (_, part_ids) in STAGES.items():
            if len(part_ids) != len(set(part_ids)):
                raise RuleError(f"阶段 {stage} 含重复分组")
            referenced.update(part_ids)
        missing = referenced - PARTS.keys()
        unused = PARTS.keys() - referenced
        if missing or unused:
            raise RuleError(f"分组索引不完整：缺失 {sorted(missing)}，未使用 {sorted(unused)}")
        sizes = {part_id: len(self.part(part_id).encode("utf-8")) for part_id in sorted(referenced)}
        largest = max(sizes, key=sizes.get)
        return (f"通过：{len(STAGES)} 个阶段，{len(sizes)} 个分组；所有引用标题唯一存在，"
                f"组内无重叠。最大分组 {largest}：{sizes[largest]} / {MAX_PART_BYTES} 字节。\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", nargs="?", choices=STAGES, help="默认仅显示该阶段的分组目录")
    parser.add_argument("--part", help="只返回指定阶段内一个分组的完整原文")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="列出阶段")
    action.add_argument("--check", action="store_true", help="检查引用与实际输出大小")
    action.add_argument("--file", choices=RULE_FILES, help="按文件别名定位单个精确标题")
    parser.add_argument("--section", help="与 --file 配合使用的精确标题")
    args = parser.parse_args(argv)
    if args.stage and (args.list or args.check or args.file):
        parser.error("阶段读取不能与 --list、--check 或 --file 混用")
    if args.part and not args.stage:
        parser.error("--part 必须指定阶段")
    if bool(args.file) != bool(args.section):
        parser.error("--file 与 --section 必须一起使用")
    if not (args.stage or args.list or args.check or args.file):
        parser.error("请指定阶段，或使用 --list、--check、--file 配合 --section")
    reader = RuleReader()
    try:
        if args.list:
            output = "".join(f"{stage}\t{purpose}\n" for stage, (purpose, _) in STAGES.items())
        elif args.check:
            output = reader.check()
        elif args.file:
            output = reader.render(((args.file, args.section),))
        elif args.part:
            if args.part not in STAGES[args.stage][1]:
                raise RuleError(f"阶段 {args.stage} 无分组 {args.part!r}；请先读取该阶段目录")
            output = reader.part(args.part)
        else:
            output = reader.menu(args.stage)
    except RuleError as error:
        parser.exit(2, f"规则读取失败：{error}\n")
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
