"""船舶口径的唯一规则源：船籍归类、船型归大类、同船/同类判定与航次关联校验。

船舶档案列表、船舶详情与航次关联校验都只允许引用本模块，
往后规则有调整（例如新增一种船舶大类）只改这里这一份。

约定：
- 入参一律是船舶档案原始 dict（store 里的那一份字段），本模块不写回、不修改入参；
- 识别不到的情况统一归为 ``UNKNOWN``，绝不抛异常，避免档案缺字段时列表/详情打不开；
- 规则匹配前统一做大小写与空白归一化，保证三处调用方拿到一致结论。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 字段名：列表/详情/校验取数都走这里的常量，避免键名在各处各写一遍
# ---------------------------------------------------------------------------
F_CODE = "船舶编号"
F_NAME = "船舶名称"
F_TYPE = "船舶类型"
F_REGISTRY = "船籍"

F_VOYAGE_VESSEL = "关联船舶"

# 展示/校验用的派生字段（挂在返回给前端的浅拷贝上，绝不写回 store）
DERIVED_REGISTRY_CLASS = "船籍类别"
DERIVED_VESSEL_CLASS = "船舶大类"

# 识别不出来时的统一口径
UNKNOWN = "未知"

# ---------------------------------------------------------------------------
# 规则表：新增船舶类型 / 新增船籍口径时，只在这两张表里加一行
# ---------------------------------------------------------------------------
# 船籍归类：命中国内关键词的视为国轮，有船籍但不命中关键词的视为外轮，空值为未知
REGISTRY_DOMESTIC_KEYWORDS = ("中国", "中华人民共和国", "国内", "中资", "香港", "澳门", "台湾")
REGISTRY_CLASS_DOMESTIC = "国轮"
REGISTRY_CLASS_FOREIGN = "外轮"

# 船舶类型归大类：按关键词归并，顺序即优先级（前面的规则先命中）
# 新增一种具体船型时，只需把对应关键词补到合适的大类里
VESSEL_CLASS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("集装箱船", ("集装箱", "货柜", "箱船", "container")),
    ("干散货船", ("散货", "干货", "矿砂", "煤船", "bulk")),
    ("液货船", ("油船", "油轮", "液货", "化工", "液化气", "lng", "lpg", "tanker")),
    ("杂货船", ("杂货", "件杂", "多用途", "general")),
    ("客船", ("客船", "客轮", "客滚", "邮轮", "渡船", "passenger")),
    ("拖轮船", ("拖轮", "拖船", "tug")),
)
VESSEL_CLASS_OTHER = "其他"


def _text(value: Any) -> str:
    """统一归一化：去首尾空白、转小写，用于关键词匹配。"""
    return str(value if value is not None else "").strip().lower()


def _display(value: Any) -> str:
    """展示用原始文本：仅去首尾空白，保留原始大小写。"""
    return str(value if value is not None else "").strip()


# ---------------------------------------------------------------------------
# 两套判断：船籍一套、船舶类型一套
# ---------------------------------------------------------------------------
def registry_of(vessel: dict[str, Any] | None) -> str:
    """按船籍字段归类为国轮 / 外轮 / 未知。

    规则只有这一份：含国内关键词（含港澳台）→ 国轮；有非空船籍但不命中 → 外轮；空值 → 未知。
    """
    raw = _text(vessel.get(F_REGISTRY) if vessel else "")
    if not raw:
        return UNKNOWN
    if any(keyword in raw for keyword in REGISTRY_DOMESTIC_KEYWORDS):
        return REGISTRY_CLASS_DOMESTIC
    return REGISTRY_CLASS_FOREIGN


def vessel_class_of(vessel: dict[str, Any] | None) -> str:
    """把具体船舶类型归并成船舶大类，识别不到归“其他”。"""
    raw = _text(vessel.get(F_TYPE) if vessel else "")
    if not raw:
        return UNKNOWN
    for label, keywords in VESSEL_CLASS_RULES:
        if any(keyword in raw for keyword in keywords):
            return label
    return VESSEL_CLASS_OTHER


def vessel_identity(vessel: dict[str, Any]) -> tuple[str, str]:
    """同船判定所用的归一化身份（船舶编号, 船舶名称），两者都归一化后比较。"""
    return _text(vessel.get(F_CODE)), _text(vessel.get(F_NAME))


def is_same_vessel(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """是不是同一艘船：船舶编号或船舶名称任一一致即视为同一艘。

    编号是主口径；历史数据里只有名称对得上的也认，避免旧关联被误伤。
    """
    left_code, left_name = vessel_identity(left)
    right_code, right_name = vessel_identity(right)
    if left_code and right_code and left_code == right_code:
        return True
    return bool(left_name and right_name and left_name == right_name)


def is_same_vessel_class(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """是不是同一类船舶：船籍类别与船舶大类两套判断必须同时一致才算同类。

    任一边识别为“未知”的维度不参与否决——档案缺字段不能直接判死，
    两边都识别得出且不一致时才返回 False。
    """
    registries = (registry_of(left), registry_of(right))
    classes = (vessel_class_of(left), vessel_class_of(right))
    if UNKNOWN not in registries and registries[0] != registries[1]:
        return False
    if UNKNOWN not in classes and classes[0] != classes[1]:
        return False
    return True


def annotate(vessel: dict[str, Any]) -> dict[str, Any]:
    """给船舶档案挂派生判定字段，供列表与详情共用。

    返回浅拷贝，原始档案（store 里的既有数据）一字段都不改，
    展示口径上也只新增键，既有列的取值完全不变。
    """
    view = dict(vessel)
    view[DERIVED_REGISTRY_CLASS] = registry_of(vessel)
    view[DERIVED_VESSEL_CLASS] = vessel_class_of(vessel)
    return view


def find_vessels(reference: Any, vessels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用航次上的“关联船舶”文本反查档案：编号或名称能对上的都算候选。"""
    target = _text(reference)
    if not target:
        return []
    matches: list[dict[str, Any]] = []
    for vessel in vessels:
        code, name = vessel_identity(vessel)
        if (code and code == target) or (name and name == target):
            matches.append(vessel)
    return matches


# ---------------------------------------------------------------------------
# 航次关联校验：同船、同船籍、同船型都在这里判
# ---------------------------------------------------------------------------
@dataclass
class VoyageLinkCheck:
    """航次与船舶档案关联校验结论。"""

    ok: bool
    errors: list[str] = field(default_factory=list)
    vessel: dict[str, Any] | None = None

    @property
    def registry_class(self) -> str:
        return registry_of(self.vessel) if self.vessel else UNKNOWN

    @property
    def vessel_class(self) -> str:
        return vessel_class_of(self.vessel) if self.vessel else UNKNOWN


def check_voyage_link(values: dict[str, Any], vessels: list[dict[str, Any]]) -> VoyageLinkCheck:
    """航次登记时的关联校验，与列表/详情用的是同一份归类规则。

    - 关联文本在档案里查不到 → 不允许登记（历史航次不经过此路径，不受影响）；
    - 编号与名称分别命中不同档案（自相矛盾）→ 不允许登记；
    - 可在提交内容里用 ``船籍类别`` / ``船舶大类`` 声明本航次要求，
      与档案口径不一致 → 不允许登记；不声明则只做同船反查，不否决。
    """
    reference = values.get(F_VOYAGE_VESSEL)
    matches = find_vessels(reference, vessels)
    if not matches:
        return VoyageLinkCheck(
            ok=False,
            errors=[f"关联船舶「{_display(reference) or '空'}」在船舶档案中查无此船，无法建立航次关联"],
        )

    ref = _text(reference)
    code_hits = {vessel.get(F_CODE) for vessel in matches if _text(vessel.get(F_CODE)) == ref}
    name_hits = {vessel.get(F_NAME) for vessel in matches if _text(vessel.get(F_NAME)) == ref}
    if len(matches) > 1 and code_hits and name_hits:
        # 同一个关联文本同时按编号和名称命中了不同档案，无法确定是哪一艘
        identities = "、".join(
            f"{_display(vessel.get(F_CODE))}/{_display(vessel.get(F_NAME))}" for vessel in matches
        )
        return VoyageLinkCheck(
            ok=False,
            errors=[f"关联船舶「{_display(reference)}」同时匹配到多艘不同船舶（{identities}），请按船舶编号明确关联"],
        )

    vessel = matches[0]
    errors: list[str] = []

    expected_registry = _display(values.get(DERIVED_REGISTRY_CLASS))
    if expected_registry:
        actual_registry = registry_of(vessel)
        if actual_registry != UNKNOWN and expected_registry != actual_registry:
            errors.append(
                f"船籍口径不一致：航次要求「{expected_registry}」，"
                f"船舶「{_display(vessel.get(F_NAME))}」为「{actual_registry}」"
            )

    expected_vessel_class = _display(values.get(DERIVED_VESSEL_CLASS))
    if expected_vessel_class:
        actual_vessel_class = vessel_class_of(vessel)
        if actual_vessel_class != UNKNOWN and expected_vessel_class != actual_vessel_class:
            errors.append(
                f"船舶类型不一致：航次要求「{expected_vessel_class}」，"
                f"船舶「{_display(vessel.get(F_NAME))}」属于「{actual_vessel_class}」"
            )

    return VoyageLinkCheck(ok=not errors, errors=errors, vessel=vessel)
