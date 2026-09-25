"""船舶身份、船籍与船舶类型的统一判定规则。

列表展示、船舶详情和航次关联校验都只能使用这里的口径，避免同一组规则散落在多个
业务入口后逐步演变出不同结果。函数保持无状态，不直接读写仓库，方便复用和测试。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

VESSEL_MODULE = "vessel"
VESSEL_ID_FIELD = "船舶编号"
VESSEL_NAME_FIELD = "船舶名称"
VESSEL_TYPE_FIELD = "船舶类型"
REGISTRY_FIELD = "船籍"
REGISTRY_ALIAS_FIELDS = ("船籍", "船旗国", "国籍")

# 新增船籍或船舶类型时，只需要维护这里的归一化别名。
REGISTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "中国": ("中国", "中华人民共和国", "中国大陆", "cn", "chn", "prc"),
    "中国香港": ("中国香港", "香港", "hk", "hkg"),
    "巴拿马": ("巴拿马", "panama", "pa", "pan"),
    "利比里亚": ("利比里亚", "liberia", "lr", "lbr"),
    "马绍尔群岛": ("马绍尔群岛", "马绍尔", "marshall islands", "mh", "mhl"),
    "新加坡": ("新加坡", "singapore", "sg", "sgp"),
}

VESSEL_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "集装箱船": ("集装箱船", "集装箱", "货柜船", "container ship", "container"),
    "散货船": ("散货船", "散装船", "干散货船", "bulk carrier", "bulk"),
    "杂货船": ("杂货船", "普通货船", "general cargo ship", "general cargo"),
    "油轮": ("油轮", "油船", "液货船", "tanker", "oil tanker"),
    "滚装船": ("滚装船", "滚装", "ro-ro ship", "ro-ro", "roro"),
    "客船": ("客船", "客轮", "邮轮", "passenger ship", "cruise ship"),
}

UNKNOWN_REGISTRY_LABEL = "未登记"
UNKNOWN_TYPE_LABEL = "未登记"
OTHER_TYPE_CATEGORY = "其他船舶"
CLASSIFICATION_KEY = "船舶判定"


@dataclass(frozen=True)
class VesselProfile:
    """船舶档案经过统一规则归一后的判定结果。"""

    registry: str | None
    registry_label: str
    vessel_type: str | None
    vessel_type_label: str
    vessel_type_category: str


def normalize_text(value: Any) -> str:
    """统一空白与大小写；保留业务原始中文值，避免展示归一化后被改写。"""

    return " ".join(str(value or "").split()).strip()


def _alias_key(value: Any) -> str:
    return normalize_text(value).lower()


def _canonical_value(value: Any, aliases: Mapping[str, tuple[str, ...]]) -> str | None:
    raw = normalize_text(value)
    if not raw:
        return None

    key = _alias_key(raw)
    for canonical, alias_values in aliases.items():
        if key == _alias_key(canonical) or key in {_alias_key(alias) for alias in alias_values}:
            return canonical

    # 未配置别名的历史原值不强行归入“其他/未知”，按原值参与相等判断。
    return raw


def canonical_registry(value: Any) -> str | None:
    return _canonical_value(value, REGISTRY_ALIASES)


def canonical_vessel_type(value: Any) -> str | None:
    return _canonical_value(value, VESSEL_TYPE_ALIASES)


def vessel_type_category(value: Any) -> str:
    canonical = canonical_vessel_type(value)
    if canonical is None:
        return UNKNOWN_TYPE_LABEL
    if canonical in VESSEL_TYPE_ALIASES:
        return canonical
    return OTHER_TYPE_CATEGORY


def get_vessel_profile(vessel: Mapping[str, Any]) -> VesselProfile:
    registry = canonical_registry(_first_value(vessel, REGISTRY_ALIAS_FIELDS))
    vessel_type = canonical_vessel_type(vessel.get(VESSEL_TYPE_FIELD))
    return VesselProfile(
        registry=registry,
        registry_label=registry or UNKNOWN_REGISTRY_LABEL,
        vessel_type=vessel_type,
        vessel_type_label=vessel_type or UNKNOWN_TYPE_LABEL,
        vessel_type_category=vessel_type_category(vessel.get(VESSEL_TYPE_FIELD)),
    )


def annotate_vessel(vessel: Mapping[str, Any]) -> dict[str, Any]:
    """返回带统一判定结果的视图；浅拷贝并新增字段，不改写档案原始数据。"""

    profile = get_vessel_profile(vessel)
    view = dict(vessel)
    view[CLASSIFICATION_KEY] = {
        "船籍": profile.registry_label,
        "船舶类型": profile.vessel_type_label,
        "船舶类型大类": profile.vessel_type_category,
    }
    return view


def is_same_registry(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_profile = get_vessel_profile(left)
    right_profile = get_vessel_profile(right)
    return (
        left_profile.registry is not None
        and right_profile.registry is not None
        and left_profile.registry == right_profile.registry
    )


def is_same_vessel_type(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_profile = get_vessel_profile(left)
    right_profile = get_vessel_profile(right)
    return (
        left_profile.vessel_type is not None
        and right_profile.vessel_type is not None
        and left_profile.vessel_type == right_profile.vessel_type
    )


def is_same_vessel_class(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """同一类船舶：船籍与船舶类型两套判断都一致。"""

    return is_same_registry(left, right) and is_same_vessel_type(left, right)


def is_same_vessel(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """判断是否指向同一艘船：优先船舶编号，编号缺失时再用船舶名称。"""

    left_id = normalize_text(left.get(VESSEL_ID_FIELD))
    right_id = normalize_text(right.get(VESSEL_ID_FIELD))
    if left_id and right_id:
        return _alias_key(left_id) == _alias_key(right_id)

    left_name = normalize_text(left.get(VESSEL_NAME_FIELD))
    right_name = normalize_text(right.get(VESSEL_NAME_FIELD))
    return bool(left_name and right_name and _alias_key(left_name) == _alias_key(right_name))


def find_vessel(rows: list[dict[str, Any]], reference: Any) -> dict[str, Any] | None:
    """航次可通过内部 id、船舶编号或船舶名称关联船舶档案。"""

    target = normalize_text(reference)
    if not target:
        return None

    for row in rows:
        if str(row.get("id", "")).strip() == target:
            return row

    target_by_id = {VESSEL_ID_FIELD: reference}
    for row in rows:
        if is_same_vessel(row, target_by_id):
            return row

    target_by_name = {VESSEL_NAME_FIELD: reference}
    for row in rows:
        if is_same_vessel(row, target_by_name):
            return row
    return None


def validate_voyage_vessel(
    values: Mapping[str, Any],
    vessel_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """校验航次提交值与已关联船舶档案是否一致。

    历史档案可能缺少船籍字段：档案缺少的判断维度不阻塞新建，避免追溯旧数据；一旦档案
    中存在该维度，就必须与提交值使用同一份归一化规则比较。
    """

    reference = values.get("关联船舶")
    if not normalize_text(reference):
        reference = values.get(VESSEL_NAME_FIELD)
    vessel = find_vessel(vessel_rows, reference)
    if vessel is None:
        return None, [f"关联船舶「{normalize_text(reference)}」在船舶档案中不存在"]

    issues: list[str] = []
    submitted_registry = _first_value(values, REGISTRY_ALIAS_FIELDS)
    profile = get_vessel_profile(vessel)
    if normalize_text(submitted_registry) and profile.registry is not None:
        registry_view = {REGISTRY_FIELD: submitted_registry}
        if not is_same_registry(registry_view, vessel):
            actual = normalize_text(_first_value(vessel, REGISTRY_ALIAS_FIELDS)) or profile.registry_label
            issues.append(f"关联船舶船籍为「{actual}」，与提交的「{normalize_text(submitted_registry)}」不一致")

    submitted_type = values.get(VESSEL_TYPE_FIELD)
    if normalize_text(submitted_type) and profile.vessel_type is not None:
        type_view = {VESSEL_TYPE_FIELD: submitted_type}
        if not is_same_vessel_type(type_view, vessel):
            actual = normalize_text(vessel.get(VESSEL_TYPE_FIELD)) or profile.vessel_type_label
            issues.append(f"关联船舶类型为「{actual}」，与提交的「{normalize_text(submitted_type)}」不一致")

    return vessel, issues


def _first_value(values: Mapping[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        value = values.get(field)
        if normalize_text(value):
            return value
    return None
