"""检测任务派发规则的回归验证：直接用 .venv/bin/python tests/test_task_rules.py 运行。"""
from __future__ import annotations

import copy
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.task import (  # noqa: E402
    MAX_ACTIVE_PER_ASSIGNEE,
    TaskService,
    is_overdue,
)
from app.store import store  # noqa: E402

TODAY = date(2026, 9, 26)


class TaskRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._snapshot = copy.deepcopy(store.rows("task"))
        self.service = TaskService()

    def tearDown(self) -> None:
        store.rows("task")[:] = self._snapshot

    def _find(self, task_no: str) -> dict:
        for row in store.rows("task"):
            if row.get("任务编号") == task_no:
                return row
        raise AssertionError(f"种子数据里找不到 {task_no}")

    def test_list_sorted_by_priority_overdue_and_plan_day(self) -> None:
        items, total = self.service.list_entries(today=TODAY)
        self.assertEqual(total, 8)
        self.assertEqual(
            [item["任务编号"] for item in items],
            [
                "TASK-0003",  # 加急
                "TASK-0001",  # 高 + 超期 09-20
                "TASK-0002",  # 高 + 超期 09-24
                "TASK-0006",  # 中 + 超期 09-25
                "TASK-0007",  # 中 + 已完成不判超期，计划日 09-10
                "TASK-0004",  # 中 + 09-30
                "TASK-0008",  # 中 + 10-02
                "TASK-0005",  # 低
            ],
        )

    def test_overdue_flag_is_derived_and_stable_across_reads(self) -> None:
        first, _ = self.service.list_entries(today=TODAY)
        second, _ = self.service.list_entries(today=TODAY)
        self.assertEqual(
            [(item["任务编号"], item["overdue"]) for item in first],
            [(item["任务编号"], item["overdue"]) for item in second],
        )
        overdue_map = {item["任务编号"]: item["overdue"] for item in first}
        self.assertTrue(overdue_map["TASK-0001"])  # 检测中，计划日已过
        self.assertTrue(overdue_map["TASK-0002"])  # 待派发，计划日已过
        self.assertTrue(overdue_map["TASK-0006"])  # 待复核，计划日已过
        self.assertFalse(overdue_map["TASK-0007"])  # 已完成不判超期
        self.assertFalse(overdue_map["TASK-0003"])  # 计划日未到
        # 派生标记不回写落库：原始行没有 overdue 字段，状态取值原样保留
        self.assertNotIn("overdue", self._find("TASK-0001"))
        self.assertEqual(self._find("TASK-0001")["status"], "检测中")

    def test_is_overdue_edge_cases(self) -> None:
        self.assertFalse(is_overdue({"status": "待派发", "计划完成日": ""}, TODAY))
        self.assertFalse(is_overdue({"status": "待派发", "计划完成日": "不是日期"}, TODAY))
        self.assertFalse(is_overdue({"status": "待派发", "计划完成日": "2026-09-26"}, TODAY))

    def test_dispatch_success_writes_assignee_and_status(self) -> None:
        entry = self._find("TASK-0003")  # 加急，理化项目
        result, message = self.service.run_action(entry["id"], "派发任务", {"承检人员": "李芳"})
        self.assertIsNotNone(result, message)
        self.assertEqual(entry["status"], "检测中")
        self.assertEqual(entry["任务状态"], "检测中")
        self.assertEqual(entry["承检人员"], "李芳")

    def test_dispatch_same_entry_twice_only_takes_effect_once(self) -> None:
        entry = self._find("TASK-0004")  # 微生物项目
        first, _ = self.service.run_action(entry["id"], "派发任务", {"承检人员": "王强"})
        self.assertIsNotNone(first)
        second, message = self.service.run_action(entry["id"], "派发任务", {"承检人员": "王强"})
        self.assertIsNone(second)
        self.assertIn("重复派发不生效", message)
        self.assertEqual(entry["承检人员"], "王强")

    def test_dispatch_same_task_no_only_takes_effect_once(self) -> None:
        original = self._find("TASK-0002")
        duplicate = dict(original)
        duplicate["id"] = 900
        store.rows("task").append(duplicate)
        first, _ = self.service.run_action(original["id"], "派发任务", {"承检人员": "王强"})
        self.assertIsNotNone(first)
        second, message = self.service.run_action(duplicate["id"], "派发任务", {"承检人员": "张伟"})
        self.assertIsNone(second)
        self.assertIn("同一任务编号只能生效一次", message)
        self.assertEqual(duplicate["status"], "待派发")

    def test_dispatch_blocked_when_qualification_mismatch(self) -> None:
        entry = self._find("TASK-0002")  # 微生物项目，李芳只有理化资质
        result, message = self.service.run_action(entry["id"], "派发任务", {"承检人员": "李芳"})
        self.assertIsNone(result)
        self.assertIn("资质", message)
        self.assertEqual(entry["status"], "待派发")

    def test_dispatch_blocked_when_assignee_not_in_archive(self) -> None:
        entry = self._find("TASK-0005")
        result, message = self.service.run_action(entry["id"], "派发任务", {"承检人员": "赵六"})
        self.assertIsNone(result)
        self.assertIn("不在承检资质档案", message)

    def test_dispatch_blocked_when_assignee_at_capacity(self) -> None:
        # 张伟在手：TASK-0001（检测中）、TASK-0006（待复核）、TASK-0008（检测中），已达上限
        assignees = {item["姓名"]: item for item in self.service.list_assignees()}
        self.assertEqual(assignees["张伟"]["在手任务"], MAX_ACTIVE_PER_ASSIGNEE)
        entry = self._find("TASK-0003")  # 理化项目，张伟资质本来匹配
        result, message = self.service.run_action(entry["id"], "派发任务", {"承检人员": "张伟"})
        self.assertIsNone(result)
        self.assertIn("上限", message)
        self.assertEqual(entry["status"], "待派发")

    def test_dispatch_requires_assignee(self) -> None:
        entry = self._find("TASK-0005")
        result, message = self.service.run_action(entry["id"], "派发任务", {})
        self.assertIsNone(result)
        self.assertIn("指定承检人员", message)

    def test_confirm_finish_clears_overdue_and_keeps_review_untouched(self) -> None:
        review_before = copy.deepcopy(store.rows("review"))
        entry = self._find("TASK-0001")  # 检测中且超期
        self.assertTrue(is_overdue(entry, TODAY))
        result, _ = self.service.run_action(entry["id"], "确认完成")
        self.assertIsNotNone(result)
        self.assertEqual(entry["status"], "已完成")
        self.assertTrue(entry["实际完成日"])
        self.assertFalse(is_overdue(entry, TODAY))
        self.assertEqual(store.rows("review"), review_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
