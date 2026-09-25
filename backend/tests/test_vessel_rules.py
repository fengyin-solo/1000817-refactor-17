"""船籍 / 船型口径测试：规则本身 + 列表、详情、航次关联校验三处同源。"""
from __future__ import annotations

import copy
import unittest

from app.domain import vessel_rules as rules
from app.services.vessel import VesselService
from app.services.voyage import VoyageService
from app.store import store


def vessel(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        rules.F_CODE: "V-1",
        rules.F_NAME: "远航号",
        rules.F_TYPE: "集装箱船",
        rules.F_REGISTRY: "中国",
    }
    row.update(overrides)
    return row


class RegistryTests(unittest.TestCase):
    def test_domestic_keywords(self) -> None:
        for text in ("中国", "中华人民共和国", "中资（上海）", "中国香港"):
            with self.subTest(text=text):
                self.assertEqual(rules.registry_of(vessel(**{rules.F_REGISTRY: text})), "国轮")

    def test_foreign(self) -> None:
        for text in ("巴拿马", "Panama", "新加坡"):
            with self.subTest(text=text):
                self.assertEqual(rules.registry_of(vessel(**{rules.F_REGISTRY: text})), "外轮")

    def test_missing_registry_is_unknown(self) -> None:
        self.assertEqual(rules.registry_of(vessel(**{rules.F_REGISTRY: "  "})), rules.UNKNOWN)
        self.assertEqual(rules.registry_of({}), rules.UNKNOWN)
        self.assertEqual(rules.registry_of(None), rules.UNKNOWN)


class VesselClassTests(unittest.TestCase):
    def test_known_classes(self) -> None:
        cases = {
            "集装箱船": "集装箱船",
            "全集装箱货轮": "集装箱船",
            "Container Ship": "集装箱船",
            "干散货船": "干散货船",
            "7万吨级矿砂船": "干散货船",
            "原油油轮": "液货船",
            "LNG液化气船": "液货船",
            "多用途杂货船": "杂货船",
            "客滚船": "客船",
            "港作拖轮": "拖轮船",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(rules.vessel_class_of(vessel(**{rules.F_TYPE: raw})), expected)

    def test_unrecognized_is_other(self) -> None:
        self.assertEqual(rules.vessel_class_of(vessel(**{rules.F_TYPE: "某种新船型"})), rules.VESSEL_CLASS_OTHER)

    def test_missing_type_is_unknown(self) -> None:
        self.assertEqual(rules.vessel_class_of(vessel(**{rules.F_TYPE: ""})), rules.UNKNOWN)
        self.assertEqual(rules.vessel_class_of(None), rules.UNKNOWN)


class SameVesselTests(unittest.TestCase):
    def test_same_by_code_or_name(self) -> None:
        a = vessel()
        self.assertTrue(rules.is_same_vessel(a, vessel(**{rules.F_NAME: "另一个名字"})))
        self.assertTrue(rules.is_same_vessel(a, vessel(**{rules.F_CODE: "V-9"})))
        self.assertFalse(
            rules.is_same_vessel(
                vessel(**{rules.F_CODE: "V-1"}),
                vessel(**{rules.F_CODE: "V-2", rules.F_NAME: "近航号"}),
            )
        )

    def test_case_and_space_insensitive(self) -> None:
        self.assertTrue(
            rules.is_same_vessel(
                vessel(**{rules.F_CODE: "vess-0001"}),
                vessel(**{rules.F_CODE: " VESS-0001 ", rules.F_NAME: "别的"}),
            )
        )


class SameClassTests(unittest.TestCase):
    def test_same_class_requires_both_dimensions(self) -> None:
        base = vessel()
        # 船籍不同、船型相同 → 不同类
        foreign = vessel(**{rules.F_REGISTRY: "巴拿马"})
        self.assertFalse(rules.is_same_vessel_class(base, foreign))
        # 船型不同、船籍相同 → 不同类
        bulk = vessel(**{rules.F_TYPE: "干散货船"})
        self.assertFalse(rules.is_same_vessel_class(base, bulk))
        # 两者都一致 → 同类
        same = vessel()
        self.assertTrue(rules.is_same_vessel_class(base, same))

    def test_unknown_dimension_does_not_reject(self) -> None:
        # 历史档案缺船籍时不能因为缺字段就判不同类
        self.assertTrue(
            rules.is_same_vessel_class(vessel(), vessel(**{rules.F_REGISTRY: ""}))
        )


class SharedSurfaceTests(unittest.TestCase):
    """列表与详情必须由同一份 annotate 产出，结论不能各写各的。"""

    def setUp(self) -> None:
        # 用一组独立档案，避免依赖 seed 文本
        self._vessel_count = len(store.rows("vessel"))
        self.sample = vessel(
            **{
                "id": 9101,
                rules.F_CODE: "RULE-TEST-1",
                rules.F_NAME: "规则测试船",
                rules.F_TYPE: "集装箱船",
                rules.F_REGISTRY: "中国",
            }
        )
        store.rows("vessel").append(copy.deepcopy(self.sample))
        self.service = VesselService()
        self.entry_id = int(self.sample["id"])

    def tearDown(self) -> None:
        store.rows("vessel")[:] = store.rows("vessel")[: self._vessel_count]

    def test_list_detail_and_rule_agree(self) -> None:
        detail = self.service.get_entry(self.entry_id)
        listed = next(
            row for row in self.service.list_entries(page=1, size=200)[0] if row["id"] == self.entry_id
        )
        self.assertIsNotNone(detail)
        self.assertEqual(detail[rules.DERIVED_REGISTRY_CLASS], rules.registry_of(self.sample))
        self.assertEqual(detail[rules.DERIVED_VESSEL_CLASS], rules.vessel_class_of(self.sample))
        self.assertEqual(
            detail[rules.DERIVED_REGISTRY_CLASS], listed[rules.DERIVED_REGISTRY_CLASS]
        )
        self.assertEqual(detail[rules.DERIVED_VESSEL_CLASS], listed[rules.DERIVED_VESSEL_CLASS])

    def test_existing_fields_and_storage_are_untouched(self) -> None:
        stored = store.find("vessel", self.entry_id)
        assert stored is not None
        # store 里的既有数据不允许被派生字段污染
        self.assertNotIn(rules.DERIVED_REGISTRY_CLASS, stored)
        self.assertNotIn(rules.DERIVED_VESSEL_CLASS, stored)
        detail = self.service.get_entry(self.entry_id)
        # 既有列的取值一个不能变
        for key, value in self.sample.items():
            self.assertEqual(detail[key], value)


class VoyageLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._vessel_count = len(store.rows("vessel"))
        self._voyage_count = len(store.rows("voyage"))
        store.rows("vessel").extend(
            [
                vessel(
                    **{
                        "id": 9001,
                        rules.F_CODE: "LINK-1",
                        rules.F_NAME: "国联一号",
                        rules.F_TYPE: "集装箱船",
                        rules.F_REGISTRY: "中国",
                    }
                ),
                vessel(
                    **{
                        "id": 9002,
                        rules.F_CODE: "LINK-2",
                        rules.F_NAME: "远洋二号",
                        rules.F_TYPE: "干散货船",
                        rules.F_REGISTRY: "巴拿马",
                    }
                ),
            ]
        )
        self.service = VoyageService()

    def tearDown(self) -> None:
        store.rows("vessel")[:] = store.rows("vessel")[: self._vessel_count]
        store.rows("voyage")[:] = store.rows("voyage")[: self._voyage_count]

    def _values(self, reference: str, **extra: object) -> dict[str, object]:
        values: dict[str, object] = {
            "航次编号": "VYG-TEST",
            rules.F_VOYAGE_VESSEL: reference,
            "进口航次号": "IN-1",
        }
        values.update(extra)
        return values

    def test_link_by_code_ok_and_stamps_shared_classes(self) -> None:
        entry, errors = self.service.create_entry(self._values("LINK-1"))
        self.assertEqual(errors, [])
        self.assertIsNotNone(entry)
        self.assertEqual(entry[rules.DERIVED_REGISTRY_CLASS], "国轮")
        self.assertEqual(entry[rules.DERIVED_VESSEL_CLASS], "集装箱船")

    def test_link_by_name_ok(self) -> None:
        entry, errors = self.service.create_entry(self._values("远洋二号"))
        self.assertEqual(errors, [])
        self.assertIsNotNone(entry)
        self.assertEqual(entry[rules.DERIVED_REGISTRY_CLASS], "外轮")

    def test_unknown_vessel_rejected(self) -> None:
        entry, errors = self.service.create_entry(self._values("幽灵船"))
        self.assertIsNone(entry)
        self.assertTrue(errors)
        self.assertIn("查无此船", errors[0])

    def test_registry_mismatch_rejected_by_shared_rule(self) -> None:
        entry, errors = self.service.create_entry(
            self._values("LINK-1", **{rules.DERIVED_REGISTRY_CLASS: "外轮"})
        )
        self.assertIsNone(entry)
        self.assertTrue(any("船籍口径不一致" in msg for msg in errors))

    def test_vessel_class_mismatch_rejected_by_shared_rule(self) -> None:
        entry, errors = self.service.create_entry(
            self._values("LINK-1", **{rules.DERIVED_VESSEL_CLASS: "干散货船"})
        )
        self.assertIsNone(entry)
        self.assertTrue(any("船舶类型不一致" for msg in errors))

    def test_unknown_class_on_archive_does_not_block(self) -> None:
        # 档案缺船籍/船型未知时，关联不应被规则误杀
        store.rows("vessel").append(
            vessel(
                **{
                    "id": 9003,
                    rules.F_CODE: "LINK-3",
                    rules.F_NAME: "资料不全船",
                    rules.F_TYPE: "未登记船型",
                    rules.F_REGISTRY: "",
                }
            )
        )
        entry, errors = self.service.create_entry(
            self._values("LINK-3", **{rules.DERIVED_REGISTRY_CLASS: "国轮", rules.DERIVED_VESSEL_CLASS: "其他"})
        )
        self.assertEqual(errors, [])
        self.assertIsNotNone(entry)

    def test_historical_voyages_untouched(self) -> None:
        before = copy.deepcopy(store.rows("voyage"))
        # 触发任何新校验都不应改动既有航次
        self.service.create_entry(self._values("幽灵船"))
        self.assertEqual(store.rows("voyage"), before)
        # 历史航次里的样例关联（档案中并不存在对应船舶）原样保留
        self.assertTrue(any(row.get("关联船舶", "").startswith("航次管理样例") for row in before))


if __name__ == "__main__":
    unittest.main()
