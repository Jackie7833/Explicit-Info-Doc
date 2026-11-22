"""Behavioral tests for the read-only, portable stage-rule reader."""

import hashlib
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts/read_stage.py"
MODULE = runpy.run_path(str(SCRIPT))
RuleReader = MODULE["RuleReader"]
RuleError = MODULE["RuleError"]
PARTS = MODULE["PARTS"]
STAGES = MODULE["STAGES"]
RULE_FILES = MODULE["RULE_FILES"]
MAX_PART_BYTES = MODULE["MAX_PART_BYTES"]


def invoke(*args, root=SKILL_ROOT, cwd=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, str(root / "scripts/read_stage.py"), *args],
        cwd=cwd, env=env, capture_output=True, check=False,
    )


def stage_sections(stage):
    return {reference for part_id in STAGES[stage][1] for reference in PARTS[part_id].sections}


def copy_skill(destination):
    (destination / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, destination / "scripts/read_stage.py")
    for relative in RULE_FILES.values():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SKILL_ROOT / relative, target)


def tree_snapshot(root):
    return {
        str(path.relative_to(root)): (path.stat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*") if path.is_file()
    }


class StageReaderTests(unittest.TestCase):
    def test_all_real_routes_resolve_unique_complete_sections_within_limit(self):
        reader = RuleReader()
        result = invoke("--check")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        used = {part_id for _, parts in STAGES.values() for part_id in parts}
        self.assertEqual(used, set(PARTS))
        for part_id in used:
            with self.subTest(part=part_id):
                output = reader.part(part_id)
                self.assertLessEqual(len(output.encode()), MAX_PART_BYTES)
                for alias, title in PARTS[part_id].sections:
                    heading, body = reader.section(alias, title)
                    raw_lines = (SKILL_ROOT / RULE_FILES[alias]).read_bytes().decode().splitlines(keepends=True)
                    self.assertEqual(body, "".join(raw_lines[heading.start:heading.end]))
                    self.assertIn(body, output)
                    self.assertIn(f"{RULE_FILES[alias]}:{heading.start + 1}-{heading.end}", output)

    def test_both_source_routes_share_business_and_containment_rules(self):
        required = {
            ("scope", "两条入口统一：模型先判断真实业务功能"),
            ("scope", "两条入口统一：重复与包含关系校验"),
        }
        for stage in ("prd", "description"):
            self.assertTrue(required <= stage_sections(stage), stage)
        self.assertNotIn(("scope", "PRD：解析完整有效功能清单"), stage_sections("description"))

    def test_showcase_includes_quality_dimensions_and_complete_fifth_section(self):
        sections = stage_sections("showcase")
        self.assertTrue({
            ("bmk", "最高优先级总纲：评测边界、题目质量、整体质量"),
            ("bmk", "广度、厚度、深度的判定"),
            ("doc", "五、题目示例"),
        } <= sections)
        reader = RuleReader()
        output = "".join(reader.part(part_id) for part_id in STAGES["showcase"][1])
        for alias, title in (("bmk", "真实性与 RealUserSim 的应用"),
                             ("bmk", "维度可行性把关"), ("doc", "字段规则")):
            self.assertIn(reader.section(alias, title)[1], output)

    def test_showcase_and_dataset_share_one_query_rule(self):
        query_sections = {
            ("query", "依据与适用边界"),
            ("query", "一、硬性限制"),
            ("query", "二、自然度要求"),
            ("query", "三、维度、标签与题目组织"),
            ("query", "四、环境信息与 Rubrics"),
            ("query", "五、两条链路的共同规则与差异"),
            ("query", "六、统一验收清单"),
        }
        for stage in ("document", "showcase", "dataset"):
            with self.subTest(stage=stage):
                self.assertTrue(query_sections <= stage_sections(stage))
        reader = RuleReader()
        for stage in ("showcase", "dataset"):
            output = "".join(reader.part(part_id) for part_id in STAGES[stage][1])
            for alias, title in query_sections:
                self.assertIn(reader.section(alias, title)[1], output)
        self.assertIn("Showcase", reader.section("query", "五、两条链路的共同规则与差异")[1])
        self.assertIn("完整评测集", reader.section("query", "五、两条链路的共同规则与差异")[1])

    def test_bmk_uses_query_feasibility_interface_and_downstream_review_uses_full_query(self):
        interface = ("query", "与 BMK 规划的接口")
        query = ("query", "一、硬性限制")
        self.assertIn(interface, stage_sections("bmk"))
        self.assertNotIn(query, stage_sections("bmk"))
        self.assertIn(query, stage_sections("dataset-review"))
        self.assertIn(query, stage_sections("deliver"))

    def test_paper_reference_is_scoped_to_expression_and_intent_density(self):
        reader = RuleReader()
        source = reader.section("query", "依据与适用边界")[1]
        self.assertIn("WildChat", source)
        self.assertIn("不提供当前三方服务的中文用户分布", source)
        naturalness = reader.section("query", "二、自然度要求")[1]
        self.assertIn("信息密度和意图前置程度", naturalness)
        self.assertIn("不能模拟逐步透露信息", naturalness)

    def test_narrow_routes_do_not_pull_in_upstream_planning(self):
        reader = RuleReader()
        rubrics = "".join(reader.part(part_id) for part_id in STAGES["rubrics"][1])
        self.assertEqual({alias for alias, _ in stage_sections("rubrics")}, {"doc", "app"})
        self.assertIn(reader.section("doc", "字段规则")[1], rubrics)
        self.assertNotIn("## 五、题目示例", rubrics)
        self.assertNotIn("### Golden Case 选择与验收", rubrics)
        self.assertNotIn("## 配比分配方法", rubrics)
        board = "".join(reader.part(part_id) for part_id in STAGES["board"][1])
        self.assertIn(reader.section("doc", "三图同一画板")[1], board)
        self.assertIn(reader.section("app", "交付前的画板写入授权")[1], board)
        self.assertNotIn("## 五、题目示例", board)
        self.assertNotIn("### 字段规则", board)
        scenarios = "".join(reader.part(part_id) for part_id in STAGES["scenarios"][1])
        self.assertIn("--kind scenarios", scenarios)
        self.assertIn("--proposal-id", scenarios)
        self.assertNotIn("BMK 工作数据结构", scenarios)
        self.assertNotIn("## 配比分配方法", scenarios)
        self.assertNotIn("## 第二轮：BMK 确认与重算", scenarios)

    def test_only_rule_files_are_opened_and_links_are_not_followed(self):
        allowed = {(SKILL_ROOT / relative).resolve() for relative in RULE_FILES.values()}
        original = Path.read_bytes
        opened = []

        def checked_read(path):
            self.assertIn(path.resolve(), allowed)
            opened.append(path.resolve())
            return original(path)

        with patch.object(Path, "read_bytes", checked_read):
            reader = RuleReader()
            reader.check()
        self.assertEqual(set(opened), allowed)

    def test_dataset_stages_load_feedback_production_and_publish_separately(self):
        feedback = ("bmk", "完整评测集：showcase反馈与确认")
        production = ("bmk", "完整评测集：逐题生成与验收")
        publish = ("app", "完整评测集：多维表格交付")
        state = ("app", "保存示例、反馈并确认")
        self.assertTrue({feedback, state, ("query", "一、硬性限制")} <= stage_sections("dataset-review"))
        self.assertNotIn(production, stage_sections("dataset-review"))
        self.assertNotIn(publish, stage_sections("dataset-review"))
        self.assertTrue({production, state, ("bmk", "最高优先级总纲：评测边界、题目质量、整体质量")}
                        <= stage_sections("dataset"))
        self.assertTrue({publish, state} <= stage_sections("dataset-deliver"))
        self.assertFalse({feedback, production, publish, state} & stage_sections("document"))
        self.assertFalse({production, publish} & stage_sections("rubrics"))

    def test_unknown_stage_part_alias_or_title_fails_without_fallback(self):
        requests = (
            ("unknown-stage",), ("rubrics", "--part", "quality"),
            ("--file", "unknown-file", "--section", "字段规则"),
            ("--file", "doc", "--section", "不存在的标题"),
            ("--file", "doc"), ("--section", "字段规则"),
        )
        for request in requests:
            with self.subTest(request=request):
                result = invoke(*request)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertTrue(result.stderr)

    def test_fences_do_not_create_headings_or_prematurely_end_a_section(self):
        source = (
            "# 文件\r\n## 目标\r\n保留原文。\r\n"
            "````markdown\r\n## 伪标题\r\n```\r\n## 仍在围栏\r\n````\r\n"
            "~~~text\r\n## 波浪伪标题\r\n~~~ closing-info-is-not-a-close\r\n"
            "## 仍在波浪围栏\r\n~~~\r\n### 真子标题\r\n末段。\r\n"
            "## 下一节\r\n不要输出。\r\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / RULE_FILES["doc"]
            path.parent.mkdir(parents=True)
            path.write_bytes(source.encode())
            reader = RuleReader(root)
            heading, body = reader.section("doc", "目标")
            expected = source[source.index("## 目标"):source.index("## 下一节")]
            self.assertEqual(body.encode(), expected.encode())
            self.assertEqual(heading.start, 1)
            self.assertIn(reader.section("doc", "真子标题")[1], body)
            for title in ("伪标题", "仍在围栏", "波浪伪标题", "仍在波浪围栏"):
                with self.assertRaises(RuleError):
                    reader.section("doc", title)

    def test_duplicate_heading_missing_heading_and_oversized_part_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "skill"
            copy_skill(root)
            path = root / RULE_FILES["doc"]
            original = path.read_bytes()
            mutations = (
                original + "\n## 字段规则\n重复。\n".encode(),
                original.replace("### 字段规则".encode(), "### 已改名字段".encode()),
                original.replace("### 字段规则\n".encode(),
                                 ("### 字段规则\n" + "过长原文。" * MAX_PART_BYTES + "\n").encode()),
            )
            for mutated in mutations:
                path.write_bytes(mutated)
                for request in (("--check",), ("rubrics", "--part", "rubric-edit")):
                    with self.subTest(mutation=len(mutated), request=request):
                        result = invoke(*request, root=root)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, b"")
                        self.assertTrue(result.stderr)

    def test_portability_menu_size_selected_output_and_no_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "moved skill with spaces"
            cwd = base / "unrelated cwd"
            cwd.mkdir()
            copy_skill(root)
            # Invalid state and missing referenced attachments must not matter.
            (root / "state.json").write_text("this is deliberately not JSON")
            doc_path = root / RULE_FILES["doc"]
            marker = "ONLY_SELECTED_RULE_BODY_SENTINEL"
            doc_path.write_bytes(doc_path.read_bytes().replace(
                "### 字段规则\n".encode(), f"### 字段规则\n{marker}\n".encode()))
            before = tree_snapshot(base)
            for request in (("--list",), ("--check",)):
                result = invoke(*request, root=root, cwd=cwd)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
            menu = invoke("rubrics", root=root, cwd=cwd)
            selected = invoke("rubrics", "--part", "rubric-edit", root=root, cwd=cwd)
            direct = invoke("--file", "doc", "--section", "字段规则", root=root, cwd=cwd)
            for result in (menu, selected, direct):
                self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertNotIn(marker.encode(), menu.stdout)
            self.assertIn(marker.encode(), selected.stdout)
            self.assertIn(marker.encode(), direct.stdout)
            self.assertIn(f"{len(selected.stdout)} 字节".encode(), menu.stdout)
            self.assertLess(len(menu.stdout), 1500)
            self.assertNotIn("回读验收、修正与交付".encode(), direct.stdout)
            self.assertNotIn(str(SKILL_ROOT).encode(), selected.stdout)
            self.assertEqual(tree_snapshot(base), before)


if __name__ == "__main__":
    unittest.main()
