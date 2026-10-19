"""行程结构兜底测试：跨日去重、主题均衡、天数分配。

这些是「模型负责文采、代码负责底线」的确定性规则，
不依赖大模型即可测试 —— 也正是它们最需要防回归
（「多日只游 1-2 处地方，景点反复出现」就是这里漏了一环）。
"""
from __future__ import annotations

from typing import List

from app.schemas.itinerary import Activity, DayPlan, Itinerary, ItineraryForm
from app.schemas.knowledge import CityContext, KnowledgeItem, RetrievalBundle
from app.services import itinerary_service as svc


def make_item(name: str, tags: List[str]) -> KnowledgeItem:
    return KnowledgeItem(
        id=abs(hash(name)) % 100000,
        kind="attraction",
        name=name,
        aliases=[],
        city="test",
        city_name="测试城",
        traditional_in=[],
        traditional_in_names=[],
        item_type="全国重点文物保护单位",
        tags=tags,
        desc=f"{name}的简介文本，用于测试占位。",
        tips=f"{name}的参访锦囊。",
        ingredients=[],
        confidence="high",
        source="test",
    )


def make_bundle(items: List[KnowledgeItem], city_name: str = "测试城") -> RetrievalBundle:
    return RetrievalBundle(
        cities=[
            CityContext(
                city_id="test",
                city_name=city_name,
                intro="测试城简介。",
                attractions=items,
                foods=[],
            )
        ],
        fingerprint="test",
    )


def make_plan(day_places: List[List[str]], city_name: str = "测试城") -> Itinerary:
    days = []
    for i, places in enumerate(day_places, 1):
        days.append(
            DayPlan(
                theme="测试主题",
                title=f"{city_name}·第{i}日测试",
                totalHours="6.0",
                suggestedDeparture="09:00",
                activities=[
                    Activity(time="09:00 - 12:00", place=p, type="参访", desc="d", tips="t")
                    for p in places
                ],
            )
        )
    return Itinerary(
        title="测试行程",
        destinations=[city_name],
        highlights=[],
        rationale="",
        days=days,
    )


# ---------------------------------------------------------------- 跨日去重


def test_dedupe_across_days_replaces_repeats():
    """同城多日反复用同一批点位时，应被替换为本城未用过的候选。"""
    items = [make_item(f"点位{i}", ["古建营造"]) for i in range(1, 7)]
    bundle = make_bundle(items)
    form = ItineraryForm(destinations=["测试城"], days=3, pace="研学适中", themes=[])
    plan = make_plan([["点位1", "点位2"], ["点位1", "点位2"], ["点位1", "点位2"]])

    fixed = svc._dedupe_across_days(plan, form, None, bundle, ["测试城"])

    assert fixed > 0, "应检出并替换重复点位"
    allp = [a.place for d in plan.days for a in d.activities]
    assert len(allp) == len(set(allp)), f"仍有跨日重复：{allp}"


def test_dedupe_across_days_keeps_theme_when_capacity_enough():
    """主题点位足够覆盖天数时，即使重复也保留（主题优先于去重）。"""
    items = [
        make_item("红色甲", ["红色记忆"]),
        make_item("红色乙", ["红色记忆"]),
        make_item("红色丙", ["红色记忆"]),
        make_item("普通一", ["古建营造"]),
        make_item("普通二", ["山川自然"]),
    ]
    bundle = make_bundle(items)
    form = ItineraryForm(destinations=["测试城"], days=3, pace="研学适中", themes=["红色记忆"])
    # 每日都含主题点位，且第 1、2 日重复用「红色甲」
    plan = make_plan([["红色甲", "普通一"], ["红色甲", "普通二"], ["红色乙", "普通一"]])

    svc._dedupe_across_days(plan, form, None, bundle, ["测试城"])

    # 「红色甲」重复但属当日唯一主题点位，且该城有 3 个红色点位覆盖 3 天 → 保留
    day2 = [a.place for a in plan.days[1].activities]
    assert "红色甲" in day2, f"主题点位不应被去重掉：{day2}"


def test_dedupe_across_days_drops_theme_when_capacity_short():
    """主题点位少于天数时，优先去重：宁可某天不带主题，也不要重复。

    注意：候选池必须不少于「天数 × 每日活动数」，否则重复在数学上无法避免
    （测试自己踩过这个坑 —— 5 个点位填 6 个位置时断言必然失败）。
    """
    items = [
        make_item("唯一红色", ["红色记忆"]),
        make_item("普通一", ["古建营造"]),
        make_item("普通二", ["山川自然"]),
        make_item("普通三", ["非遗民俗"]),
        make_item("普通四", ["晋商文化"]),
        make_item("普通五", ["博物馆藏"]),
    ]
    bundle = make_bundle(items)
    form = ItineraryForm(destinations=["测试城"], days=3, pace="研学适中", themes=["红色记忆"])
    # 唯一红色点位被排在第 1、3 日 → 跨日重复
    plan = make_plan([["唯一红色", "普通一"], ["普通二", "普通三"], ["唯一红色", "普通四"]])

    svc._dedupe_across_days(plan, form, None, bundle, ["测试城"])

    allp = [a.place for d in plan.days for a in d.activities]
    assert len(allp) == len(set(allp)), f"主题点位不足时应优先去重，实际：{allp}"


# ---------------------------------------------------------------- 天数分配


def test_allocate_days_contiguous_blocks():
    """天数按府邑连续成块，不应在府邑间来回折返。"""
    assert svc.allocate_days_to_cities(5, ["A", "B"]) == ["A", "A", "A", "B", "B"]
    assert svc.allocate_days_to_cities(4, ["A", "B", "C", "D"]) == ["A", "B", "C", "D"]
    assert svc.allocate_days_to_cities(6, ["A", "B", "C", "D"]) == ["A", "A", "B", "B", "C", "D"]


def test_allocate_days_never_drops_city():
    """每处府邑至少分到一日（前提是天数 >= 府邑数）。"""
    cities = ["A", "B", "C", "D"]
    for days in range(4, 11):
        got = svc.allocate_days_to_cities(days, cities)
        assert len(got) == days
        assert set(got) == set(cities), f"{days} 日时遗漏府邑：{got}"


def test_validate_rejects_too_few_days():
    """天数不足以为每处府邑留一日时应拒绝。"""
    form = ItineraryForm(destinations=["A", "B", "C", "D"], days=2, pace="研学适中")
    try:
        svc.validate_plan_form(form)
    except svc.PlanUnreasonableError as e:
        assert e.days == 2 and e.cities == 4
    else:
        raise AssertionError("应抛出 PlanUnreasonableError")


# ---------------------------------------------------------------- 主题取点


def test_select_themed_attractions_puts_primary_first():
    """按主题取点时，主主题景点应排在前面且保证出现。"""
    items = [
        make_item("普通一", ["古建营造"]),
        make_item("博物馆甲", ["博物馆藏"]),
        make_item("普通二", ["山川自然"]),
        make_item("博物馆乙", ["博物馆藏"]),
    ]
    picks = svc._select_themed_attractions(items, 0, ["博物馆藏"], per_day=4)
    names = [a.name for a in picks]
    assert "博物馆甲" in names and "博物馆乙" in names, names
    assert names[:2] == ["博物馆甲", "博物馆乙"], f"主主题应排前：{names}"


def test_select_themed_attractions_rotates_across_days():
    """同城多日时，主主题候选应在日间轮转，避免每天给出同一批。"""
    items = [make_item(f"馆{i}", ["博物馆藏"]) for i in range(1, 5)]
    d0 = [a.name for a in svc._select_themed_attractions(items, 0, ["博物馆藏"], per_day=4)]
    d1 = [a.name for a in svc._select_themed_attractions(items, 1, ["博物馆藏"], per_day=4)]
    assert d0 != d1, f"相邻两日候选完全相同：{d0}"
