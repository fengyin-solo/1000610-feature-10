"""检测任务业务规则：状态流转、字段校验与筛选口径都收在这里。

派发口径（与前端约定，全部以后端判定为准）：
1. 派发顺序由「任务优先级 + 计划完成日」共同决定：优先级高在前；同一优先级下，
   超期任务排在普通任务之前，其余按计划完成日升序，最后按 id 兜底保证顺序稳定。
2. 同一承检人员在手（状态为「检测中」）任务数达到上限时不得再派；资质不匹配
   检测项目时同样拦下，均返回可读原因。
3. 「派发任务」只能对「待派发」任务生效一次：重复派发同一任务编号直接拦下。
4. 超期为派生口径：计划完成日早于今天且任务尚未完成（状态不是「已完成」）即超期，
   只随列表返回，不回写落库字段；已落库的 status 等字段一律原样保留。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "关联样品", "检测项目"]
OPTIONAL_FIELDS = ["承检人员", "计划完成日", "任务优先级"]
STATUS_ORDER = ["待派发", "检测中", "待复核", "已完成"]
ACTION_RULES = {"派发任务": "检测中", "提交复核": "待复核", "确认完成": "已完成"}
NEGATIVE_ACTIONS = []

# 同一承检人员同时段可在手（检测中）的任务上限
INHAND_LIMIT = 3

# 优先级排序口径：数字越小越靠前；未登记的优先级排到最后
PRIORITY_RANK = {"高": 0, "中": 1, "低": 2}
PRIORITY_ALIASES = {
    "紧急": "高",
    "高优先级": "高",
    "普通": "中",
    "中优先级": "中",
    "低优先级": "低",
}
DEFAULT_PRIORITY = "中"
FINISHED_STATUS = STATUS_ORDER[-1]

# 承检人员资质名册：只有在册且可检项目覆盖任务「检测项目」时才允许派发。
INSPECTOR_QUALIFICATIONS: dict[str, list[str]] = {
    "王磊": ["化学需氧量", "重金属铅", "pH值"],
    "李娜": ["菌落总数", "大肠菌群"],
    "赵强": ["PM2.5", "噪声", "化学需氧量"],
}


def _normalize_priority(value: Any) -> str:
    text = str(value or "").strip()
    return PRIORITY_ALIASES.get(text, text or DEFAULT_PRIORITY)


def _parse_due_date(value: Any) -> date | None:
    """计划完成日按 YYYY-MM-DD 解析；空值或非法日期返回 None（排序靠后、不算超期）。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _is_overdue(row: dict[str, Any], *, today: date | None = None) -> bool:
    """超期判定：计划完成日早于当天且任务未完成；完成日当天不算超期。"""
    if row.get("status") == FINISHED_STATUS:
        return False
    due = _parse_due_date(row.get("计划完成日"))
    if due is None:
        return False
    return due < (today or date.today())


def _sort_key(row: dict[str, Any], *, today: date) -> tuple[Any, ...]:
    priority = _normalize_priority(row.get("任务优先级"))
    due = _parse_due_date(row.get("计划完成日"))
    return (
        PRIORITY_RANK.get(priority, len(PRIORITY_RANK)),
        0 if _is_overdue(row, today=today) else 1,
        due or date.max,
        int(row.get("id", 0)),
    )


def _decorate(row: dict[str, Any], *, today: date) -> dict[str, Any]:
    """列表输出时附加派生字段（如「超期」标记），只做拷贝，不回写仓库。"""
    view = dict(row)
    view["超期"] = _is_overdue(row, today=today)
    return view


class TaskService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        today = date.today()
        # 派发顺序（优先级 + 超期优先 + 计划完成日）在分页前统一排序，
        # 保证刷新或从其他页面返回时顺序稳定、不依赖手工录入次序。
        rows = sorted(rows, key=lambda row: _sort_key(row, today=today))
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [_decorate(row, today=today) for row in rows[start:start + size]]
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if str(values.get(field) or "").strip():
                entry[field] = values[field]
        entry.setdefault("承检人员", "")
        entry.setdefault("计划完成日", "")
        entry["任务优先级"] = _normalize_priority(entry.get("任务优先级"))
        entry["实际完成日"] = ""
        entry["任务状态"] = STATUS_ORDER[0]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def list_inspectors(self) -> list[dict[str, Any]]:
        """承检人员名册及当前在手负荷，供派发界面展示与后端拦截共用同一份口径。"""
        rows = store.rows(MODULE)
        roster = []
        for name, scopes in INSPECTOR_QUALIFICATIONS.items():
            inhand = sum(
                1
                for row in rows
                if row.get("承检人员") == name and row.get("status") == "检测中"
            )
            roster.append({
                "承检人员": name,
                "可检项目": list(scopes),
                "在手任务数": inhand,
                "在手上限": INHAND_LIMIT,
                "已满负荷": inhand >= INHAND_LIMIT,
            })
        return roster

    def stats(self) -> dict[str, int]:
        rows = store.rows(MODULE)
        return {
            "待派发": sum(1 for row in rows if row.get("status") == "待派发"),
            "检测中": sum(1 for row in rows if row.get("status") == "检测中"),
            "超期": sum(1 for row in rows if _is_overdue(row)),
        }

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
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        values = values or {}
        task_no = str(entry.get("任务编号") or entry_id)
        current = str(entry.get("status") or "")

        if action == "派发任务":
            blocked = self._check_dispatch(entry, values, current, task_no)
            if blocked:
                return None, blocked
            assignee = str(values.get("承检人员") or "").strip()
            # 只落派发必需字段，其余字段（含已落库取值）原样保留
            entry["承检人员"] = assignee
        elif action == "提交复核" and current != "检测中":
            return None, f"任务编号 {task_no} 当前为「{current or '未派发'}」，仅「检测中」任务可提交复核"
        elif action == "确认完成" and current != "待复核":
            return None, f"任务编号 {task_no} 当前为「{current or '未知'}」，仅「待复核」任务可确认完成"

        entry["status"] = target
        if "任务状态" in entry:
            entry["任务状态"] = target
        entry["pending"] = target != FINISHED_STATUS
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "确认完成" and not str(entry.get("实际完成日") or "").strip():
            entry["实际完成日"] = date.today().isoformat()
        return entry, f"检测任务已{action}"

    def _check_dispatch(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        current_status: str,
        task_no: str,
    ) -> str:
        """派发前置校验：任一不通过即返回拦下原因；全部通过返回空串。"""
        # 重复派发同一任务编号只能生效一次：非待派发一律拦下
        if current_status != "待派发":
            return (
                f"任务编号 {task_no} 已派发（当前「{current_status}」），"
                "同一任务编号重复派发不生效"
            )
        assignee = str(values.get("承检人员") or "").strip()
        if not assignee:
            return "请选择承检人员后再派发"

        project = str(entry.get("检测项目") or "").strip()
        scopes = INSPECTOR_QUALIFICATIONS.get(assignee)
        if scopes is None:
            return f"承检人员「{assignee}」不在资质名册内，资质不匹配，已拦下本次派发"
        if project and project not in scopes:
            return (
                f"承检人员「{assignee}」不具备检测项目「{project}」的资质，"
                "资质不匹配，已拦下本次派发"
            )

        inhand = sum(
            1
            for row in store.rows(MODULE)
            if row.get("承检人员") == assignee and row.get("status") == "检测中"
        )
        if inhand >= INHAND_LIMIT:
            return (
                f"承检人员「{assignee}」在手检测中任务已达上限 {INHAND_LIMIT} 件"
                f"（当前 {inhand} 件），已拦下本次派发"
            )
        return ""
