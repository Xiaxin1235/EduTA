# -*- coding: utf-8 -*-
"""Unit tests for Agent modular tool system, Python execution, and chart generation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packages.agent.tools.base import ToolResult
from packages.agent.tools.python_runner import PythonInterpreterTool
from packages.agent.tools.chart_builder import EducationalChartBuilder
from packages.agent.tools.registry import ToolRegistry, default_registry
from packages.agent.tools.orchestrator import AgentToolOrchestrator


class TestAgentTools(unittest.TestCase):
    def setUp(self):
        self.py_tool = PythonInterpreterTool()
        self.orchestrator = AgentToolOrchestrator()

    def test_python_interpreter_basic(self):
        code = "print('Hello from Agent Tool'); a = 1 + 2; print('a =', a)"
        res = self.py_tool.run(code=code)
        self.assertTrue(res.success)
        self.assertIn("Hello from Agent Tool", res.stdout)
        self.assertIn("a = 3", res.stdout)
        self.assertGreater(res.duration_ms, 0)

    def test_python_interpreter_chart_generation(self):
        code = """
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(5, 3))
ax.plot([1, 2, 3], [4, 5, 6], label='测试曲线', color='#c96442')
ax.set_title('中文标题测试 · 武汉市教育数字化')
ax.legend()
plt.tight_layout()
"""
        res = self.py_tool.run(code=code, chart_title="测试曲线图")
        self.assertTrue(res.success, f"Execution failed: {res.error}")
        self.assertEqual(len(res.artifacts), 1)
        art = res.artifacts[0]
        self.assertTrue(os.path.isfile(art["file_path"]))
        self.assertTrue(art["base64"].startswith("data:image/png;base64,"))
        self.assertGreater(len(art["base64"]), 1000)
        print(f"\n[OK] Generated chart: {art['filename']} (Size: {os.path.getsize(art['file_path'])} bytes)")

    def test_chart_builder_and_orchestrator(self):
        # 1. Fact scenario (tablet computers)
        fact_result = {
            "overview": {
                "object_type": "school_fact",
                "school_name": "七台河市新兴区罗泉学校",
                "metric_name": "教师平板电脑配置台数",
                "target_value": 14,
                "unit": "台",
            },
            "context_facts": [
                {"label": "学生平板电脑配置数", "value": "18 台"},
                {"label": "多媒体教室班牌数", "value": "10 台"},
            ],
            "history": [
                {"year": 2021, "value": 10},
                {"year": 2022, "value": 12},
                {"year": 2023, "value": 14},
            ],
        }
        orch_res = self.orchestrator.orchestrate_visualization(fact_result)
        self.assertEqual(len(orch_res["tool_calls"]), 1)
        self.assertTrue(orch_res["tool_calls"][0]["success"])
        self.assertGreater(len(orch_res["charts"]), 0)
        print(f"[OK] Fact chart created: {orch_res['charts'][0]['url']}")

        # 2. Ranking scenario
        rank_result = {
            "overview": {
                "object_type": "school_ranking",
                "school_name": "七台河市新兴区罗泉学校",
                "total_score": 11.2,
                "region_avg": 10.78,
                "rank": 2,
                "total_schools": 10,
            },
            "peer_schools": [
                {"school_name": "七台河市第三中学", "score": 12.5, "is_target": False},
                {"school_name": "七台河市新兴区罗泉学校", "score": 11.2, "is_target": True},
                {"school_name": "长兴学校", "score": 10.1, "is_target": False},
            ],
        }
        orch_rank = self.orchestrator.orchestrate_visualization(rank_result)
        self.assertEqual(len(orch_rank["tool_calls"]), 1)
        self.assertTrue(orch_rank["tool_calls"][0]["success"])
        self.assertGreater(len(orch_rank["charts"]), 0)
        print(f"[OK] Ranking chart created: {orch_rank['charts'][0]['url']}")

        # 3. Macro region scenario
        region_result = {
            "overview": {
                "object_type": "region",
                "region_name": "湖北省 武汉市",
                "year": 2021,
                "total_score": 13.26,
                "school_count": 732,
            },
            "dimensions": [
                {"dimension": "数字资源", "score": 16.5},
                {"dimension": "基础设施", "score": 14.2},
                {"dimension": "教学应用", "score": 13.8},
                {"dimension": "管理保障", "score": 11.0},
                {"dimension": "数字素养", "score": 10.8},
            ],
            "top_schools": [
                {"school_name": "武汉第三寄宿中学", "total_score": 26.57},
                {"school_name": "王家河街中学", "total_score": 25.10},
                {"school_name": "武汉市实验小学", "total_score": 24.30},
            ],
        }
        orch_reg = self.orchestrator.orchestrate_visualization(region_result)
        self.assertEqual(len(orch_reg["tool_calls"]), 1)
        self.assertTrue(orch_reg["tool_calls"][0]["success"])
        self.assertGreater(len(orch_reg["charts"]), 0)
        print(f"[OK] Region macro chart created: {orch_reg['charts'][0]['url']}")


if __name__ == "__main__":
    unittest.main()
