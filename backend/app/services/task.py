"""检测任务业务规则：派发顺序、超期判定、承检上限与资质校验都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "关联样品", "检测项目"]
STATUS_ORDER = ["待派发", "检测中", "待复核", "已完成"]
ACTION_RULES = {"派发任务": "检测中", "提交复核": "待复核", "确认完成": "已完成"}
NEGATIVE_ACTIONS = []

# 优先级口径：加急 > 高 > 中 > 低；未登记的取值一律按最低档处理，不报错
PRIORITY_RANK = {"加急": 0, "高": 1, "中": 2, "低": 3}
DEFAULT_PRIORITY_RANK = len(PRIORITY_RANK)

# 同一承检人员同时段在手任务上限；在手 = 已派发但未完成（检测中、待复核）
MAX_ACTIVE_PER_ASSIGNEE = 3
ACTIVE_STATUSES = ("检测中", "待复核")

# 承检资质档案：不在档案里的人员一律按资质不匹配拦下
ASSIGNEE_QUALIFICATIONS = {
    "张伟": {"理化检测", "微生物检测"},
    "李芳": {"理化检测"},
    "王强": {"微生物检测"},
}

# 检测项目与所需资质的对应口径：按项目名关键词命中，未命中的项目不做资质拦截
PROJECT_QUALIFICATION_RULES = (
    ("重金属", "理化检测"),
    ("化学需氧量", "理化检测"),
    ("微生物", "微生物检测"),
    ("菌落", "微生物检测"),
    ("沉降菌", "微生物检测"),
)


def _parse_day(raw: Any) -> date | None:
    """把 YYYY-MM-DD 形式的日期解析出来；解析不了就当没有填。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def is_overdue(entry: dict[str, Any], today: date | None = None) -> bool:
    """超期口径：未完成且计划完成日早于今天；已完成任务不再判超期。"""
    if entry.get("status") == STATUS_ORDER[-1]:
        return False
    plan_day = _parse_day(entry.get("计划完成日"))
    if plan_day is None:
        return False
    return plan_day < (today or date.today())


def required_qualification(entry: dict[str, Any]) -> str | None:
    """按检测项目推断所需资质；识别不了的项目视为无特殊资质要求。"""
    project = str(entry.get("检测项目") or "")
    for keyword, qualification in PROJECT_QUALIFICATION_RULES:
        if keyword in project:
            return qualification
    return None


def _dispatch_sort_key(entry: dict[str, Any], today: date) -> tuple[int, int, date, int]:
    """派发顺序：优先级高的在前；同优先级内超期任务排在普通任务之前，再按计划完成日升序。"""
    return (
        PRIORITY_RANK.get(str(entry.get("任务优先级") or ""), DEFAULT_PRIORITY_RANK),
        0 if is_overdue(entry, today) else 1,
        _parse_day(entry.get("计划完成日")) or date.max,
        int(entry.get("id", 0)),
    )


class TaskService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        today: date | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        today = today or date.today()
        rows = sorted(rows, key=lambda row: _dispatch_sort_key(row, today))
        total = len(rows)
        start = max(page - 1, 0) * size
        # 超期标记是派生字段，挂在副本上返回，不回写落库的状态取值
        items = []
        for row in rows[start:start + size]:
            item = dict(row)
            item["overdue"] = is_overdue(row, today)
            items.append(item)
        return items, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def list_assignees(self) -> list[dict[str, Any]]:
        """承检人员档案：资质与当前在手任务数，供派发前选择。"""
        rows = store.rows(MODULE)
        items = []
        for name, qualifications in ASSIGNEE_QUALIFICATIONS.items():
            active = sum(
                1
                for row in rows
                if row.get("承检人员") == name and row.get("status") in ACTIVE_STATUSES
            )
            items.append({
                "姓名": name,
                "资质": sorted(qualifications),
                "在手任务": active,
                "在手上限": MAX_ACTIVE_PER_ASSIGNEE,
            })
        return items

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["承检人员"] = str(values.get("承检人员") or "")
        entry["计划完成日"] = str(values.get("计划完成日") or "")
        entry["实际完成日"] = str(values.get("实际完成日") or "")
        entry["任务优先级"] = str(values.get("任务优先级") or "中")
        entry["status"] = STATUS_ORDER[0]
        entry["任务状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检测任务可执行范围"
        if action == "派发任务":
            assignee = str((values or {}).get("承检人员") or "").strip()
            return self._dispatch(entry, assignee)
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["任务状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "确认完成":
            entry["实际完成日"] = date.today().isoformat()
        return entry, f"检测任务已{action}"

    def _dispatch(self, entry: dict[str, Any], assignee: str) -> tuple[dict[str, Any] | None, str]:
        task_no = str(entry.get("任务编号") or "")
        # 同一任务编号只生效一次：本条已派发，或同编号的另一条已派发，都拦下
        if entry.get("status") != STATUS_ORDER[0]:
            return None, f"任务 {task_no} 已处于「{entry.get('status')}」，重复派发不生效"
        for row in store.rows(MODULE):
            if row is entry:
                continue
            if str(row.get("任务编号") or "") == task_no and row.get("status") != STATUS_ORDER[0]:
                return None, f"任务编号 {task_no} 已派发过，同一任务编号只能生效一次"
        if not assignee:
            return None, "派发前请先指定承检人员"
        qualifications = ASSIGNEE_QUALIFICATIONS.get(assignee)
        if qualifications is None:
            return None, f"承检人员 {assignee} 不在承检资质档案中，资质不匹配"
        required = required_qualification(entry)
        if required is not None and required not in qualifications:
            return None, f"承检人员 {assignee} 不具备「{required}」资质，不能承接该任务"
        active = sum(
            1
            for row in store.rows(MODULE)
            if row.get("承检人员") == assignee and row.get("status") in ACTIVE_STATUSES
        )
        if active >= MAX_ACTIVE_PER_ASSIGNEE:
            return None, f"承检人员 {assignee} 在手任务 {active} 条，已达同时段上限 {MAX_ACTIVE_PER_ASSIGNEE} 条"
        entry["承检人员"] = assignee
        entry["status"] = "检测中"
        entry["任务状态"] = "检测中"
        entry["pending"] = True
        entry["abnormal"] = False
        return entry, f"检测任务 {task_no} 已派发给 {assignee}"
