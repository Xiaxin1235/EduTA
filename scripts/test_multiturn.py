# -*- coding: utf-8 -*-
"""Verification script for multi-turn dialogue, co-reference resolution, and ranking queries."""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.api.main import chat, _SESSIONS


class TestMultiTurnDialogue(unittest.TestCase):
    def setUp(self):
        _SESSIONS.clear()
        self.session_id = "test_sess_001"

    def test_multi_turn_school_flow(self):
        print("\n--- Turn 1: Specific School + Teacher Tablet Fact ---")
        q1 = "七台河市新兴区罗泉学校给老师配置了多少台平板电脑？"
        res1 = chat(q1, session_id=self.session_id)
        self.assertFalse(res1.get("clarify"), f"Turn 1 should not clarify: {res1.get('next_prompt')}")
        rep1 = res1.get("report", {})
        ov1 = rep1.get("overview", {})
        self.assertEqual(ov1.get("object_type"), "school_fact")
        self.assertEqual(ov1.get("school_name"), "七台河市新兴区罗泉学校")
        self.assertEqual(ov1.get("target_value"), 14)
        print(f"Turn 1 Result: {ov1.get('school_name')} 教师平板 = {ov1.get('target_value')} 台")

        print("\n--- Turn 2: Follow-up Student Tablet (No school name) ---")
        q2 = "那学生平板配置了多少台？"
        res2 = chat(q2, session_id=self.session_id)
        self.assertFalse(res2.get("clarify"), f"Turn 2 should inherit school: {res2.get('next_prompt')}")
        rep2 = res2.get("report", {})
        ov2 = rep2.get("overview", {})
        self.assertEqual(ov2.get("object_type"), "school_fact")
        self.assertEqual(ov2.get("school_name"), "七台河市新兴区罗泉学校")
        self.assertEqual(ov2.get("target_value"), 18)
        print(f"Turn 2 Result: {ov2.get('school_name')} 学生平板 = {ov2.get('target_value')} 台")

        print("\n--- Turn 3: Pronoun Ranking Query ('它在区里排第几名？') ---")
        q3 = "它在区里排第几名？"
        res3 = chat(q3, session_id=self.session_id)
        self.assertFalse(res3.get("clarify"), f"Turn 3 should resolve pronoun: {res3.get('next_prompt')}")
        rep3 = res3.get("report", {})
        ov3 = rep3.get("overview", {})
        self.assertEqual(ov3.get("object_type"), "school_ranking")
        self.assertEqual(ov3.get("school_name"), "七台河市新兴区罗泉学校")
        self.assertEqual(ov3.get("rank"), 2)
        self.assertEqual(ov3.get("total_schools"), 10)
        self.assertIn("第 2 名", ov3.get("direct_answer", ""))
        print(f"Turn 3 Result: {ov3.get('school_name')} 排位 = 第 {ov3.get('rank')} / {ov3.get('total_schools')} 名")

        print("\n--- Turn 4: Pronoun Comprehensive Evaluation ('它的数字化水平怎么样？') ---")
        q4 = "它的数字化水平怎么样？"
        res4 = chat(q4, session_id=self.session_id)
        self.assertFalse(res4.get("clarify"), f"Turn 4 should resolve pronoun: {res4.get('next_prompt')}")
        rep4 = res4.get("report", {})
        ov4 = rep4.get("overview", {})
        self.assertEqual(ov4.get("school_name"), "七台河市新兴区罗泉学校")
        self.assertIsNotNone(ov4.get("total_score"))
        print(f"Turn 4 Result: {ov4.get('school_name')} 综合得分 = {ov4.get('total_score')} 分")

    def test_region_query_and_exclusion(self):
        print("\n--- Test Region Query & Generic Word Exclusion ---")
        q = "新兴区 2021 数字化大盘怎么样？"
        res = chat(q, session_id="region_sess")
        self.assertFalse(res.get("clarify"))
        ov = res.get("report", {}).get("overview", {})
        self.assertEqual(ov.get("object_type"), "region")
        self.assertEqual(ov.get("school_count"), 12)
        print(f"Region Result: {ov.get('region_name')} 均分 = {ov.get('total_score')} 分, 学校数 = {ov.get('school_count')}")

        # Follow-up asking about top schools
        q_top = "该地区最好的学校是哪几所？"
        res_top = chat(q_top, session_id="region_sess")
        self.assertFalse(res_top.get("clarify"))
        ov_top = res_top.get("report", {}).get("overview", {})
        self.assertEqual(ov_top.get("object_type"), "region")
        top_list = res_top.get("report", {}).get("top_schools", [])
        self.assertTrue(len(top_list) > 0)
        print(f"Top schools in {ov_top.get('region_name')}: {[s['school_name'] for s in top_list[:3]]}")


if __name__ == "__main__":
    unittest.main()
