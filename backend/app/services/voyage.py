"""航次管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.domain import vessel_rules
from app.store import store

MODULE = "voyage"
VESSEL_MODULE = "vessel"
REQUIRED_FIELDS = ["航次编号", "关联船舶", "进口航次号"]
STATUS_ORDER = ["待开航", "航行中", "已到港", "已结航"]
ACTION_RULES = {"确认开航": "航行中", "确认到港": "已到港", "结航航次": "已结航"}
NEGATIVE_ACTIONS = []


class VoyageService:
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
            rows = [row for row in rows if keyword in str(row.get("航次编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]
        # 航次关联校验：与船舶列表/详情共用 vessel_rules 这一份口径。
        # 只校验新登记航次，store 里的历史航次数据不会走到这里，关联保持原样。
        link = vessel_rules.check_voyage_link(values, store.rows(VESSEL_MODULE))
        if not link.ok:
            return None, link.errors
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 留档反查到的船籍/船型口径，方便日后核对；既有展示列不受影响
        entry[vessel_rules.DERIVED_REGISTRY_CLASS] = link.registry_class
        entry[vessel_rules.DERIVED_VESSEL_CLASS] = link.vessel_class
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"航次 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于航次管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"航次已{action}"
