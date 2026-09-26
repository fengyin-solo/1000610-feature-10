"""检测任务接口：维护检测任务，覆盖派发任务、提交复核、确认完成等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.task import TaskService

router = APIRouter(prefix="/api/task", tags=["检测任务"])

MODULE_NAME = "task"
service = TaskService()

LIST_FIELDS = ["任务编号", "关联样品", "检测项目", "承检人员", "计划完成日", "实际完成日", "任务优先级", "任务状态"]
STATUSES = ["待派发", "检测中", "待复核", "已完成"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="待派发、检测中、待复核、已完成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号与状态过滤检测任务列表；顺序按优先级、超期与计划完成日由后端统一给出。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/inspectors")
def list_inspectors() -> dict[str, Any]:
    """承检人员名册：返回可检项目与在手负荷，供派发表单提示与后端拦截共用。"""
    items = service.list_inspectors()
    return {"module": MODULE_NAME, "total": len(items), "items": items}


@router.get("/stats")
def task_stats() -> dict[str, Any]:
    """检测任务看板口径：待派发、检测中、超期（计划完成日早于当天且未完成）。"""
    return {"module": MODULE_NAME, "items": service.stats()}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出检测任务清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": MODULE_NAME, "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条检测任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"检测任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条检测任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="检测任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条检测任务执行派发任务、提交复核、确认完成；不允许的动作会被拦下并说明原因。

    派发任务时在 values 中带上「承检人员」；在手任务达上限或资质不匹配会被拦下。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
