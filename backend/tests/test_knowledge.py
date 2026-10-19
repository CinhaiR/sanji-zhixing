"""知识库与地理校验测试

覆盖重点：
1. 城市名归一与最长匹配（晋中 vs 晋城、下辖县市归属）
2. 地理错配检测 —— 本项目的核心准确性保障（朔州不得吃平遥牛肉）
3. 泛化别名误报的抑制（应县凉粉 属朔州，不应被当成浑源凉粉）
4. 自动修正的文本质量（不产生「应县右玉羊肉」这类残留）
5. FTS5 检索与知识库覆盖度

运行：
    cd backend
    python -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import SessionLocal  # noqa: E402
from app.schemas.itinerary import Activity, DayPlan, Itinerary  # noqa: E402
from app.services import knowledge_service as ks  # noqa: E402


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    ks.ensure_knowledge_loaded(session)
    yield session
    session.close()


@pytest.fixture(scope="module")
def matcher(db):
    return ks.CityMatcher.from_db(db)


def make_plan(destinations, days):
    """days: [(标题, [(点位, 餐食, 简介), ...]), ...]"""
    out_days = []
    for i, (title, acts) in enumerate(days):
        out_days.append(
            DayPlan(
                theme=title,
                title=title,
                totalHours="6",
                suggestedDeparture="08:30",
                activities=[
                    Activity(
                        time="09:00 - 12:00",
                        place=place,
                        type="重点国保研习",
                        desc=desc,
                        tips="",
                        foodPairing=food,
                    )
                    for place, food, desc in acts
                ],
            )
        )
    return Itinerary(
        title="测试行程",
        destinations=list(destinations),
        highlights=["测试"],
        days=out_days,
    )


# ---------------------------------------------------------------- 城市匹配


def test_normalize_direct_names(matcher):
    assert matcher.normalize_city("朔州") == "shuozhou"
    assert matcher.normalize_city("大同") == "datong"
    assert matcher.normalize_city("太原") == "taiyuan"


def test_normalize_with_suffix(matcher):
    assert matcher.normalize_city("朔州市") == "shuozhou"
    assert matcher.normalize_city("忻州市") == "xinzhou"


def test_normalize_subordinate_counties(matcher):
    """下辖县市应归到正确地级市 —— 这是错配判定的基础"""
    assert matcher.normalize_city("平遥") == "jinzhong"
    assert matcher.normalize_city("应县") == "shuozhou"
    assert matcher.normalize_city("洪洞") == "linfen"
    assert matcher.normalize_city("解州") == "yuncheng"
    assert matcher.normalize_city("碛口") == "lvliang"


def test_normalize_unknown(matcher):
    assert matcher.normalize_city("不存在的城") is None


def test_longest_match_prefers_specific(matcher):
    """「晋中」不得被误判为「晋城」"""
    assert matcher.find_names("晋中平遥") == ["晋中"]
    assert matcher.find_names("晋城皇城相府") == ["晋城"]


def test_jinzhong_and_jincheng_are_distinct(matcher):
    assert matcher.normalize_city("晋中") != matcher.normalize_city("晋城")


# ---------------------------------------------------------------- 知识库完整性


def test_knowledge_loaded(db):
    stats = ks.get_stats(db)
    assert stats.cities == 11, "应为山西 11 个地级市"
    assert stats.attractions >= 30
    assert stats.foods >= 40
    assert stats.knowledge_fingerprint


def test_every_city_has_material(db):
    """每个府邑都要有景点与特产，否则行程兜底无法落地"""
    for city in ks.get_cities(db):
        assert city.attraction_count >= 1, f"{city.name} 缺景点"
        assert city.food_count >= 2, f"{city.name} 缺特产"


def test_food_claims_correct_city(db):
    """核心断言：易错特产必须挂在正确城市下"""
    expectations = {
        "平遥牛肉": "jinzhong",
        "太谷饼": "jinzhong",
        "运城闻喜煮饼": "yuncheng",
        "浑源凉粉": "datong",
        "大同刀削面": "datong",
        "右玉羊肉": "shuozhou",
        "应县紫皮蒜": "shuozhou",
        "五台山台蘑": "xinzhou",
        "柳林碗团": "lvliang",
        "长治腊驴肉": "changzhi",
        "阳城烧肝": "jincheng",
        "临汾牛肉丸子面": "linfen",
    }
    foods = {f.name: f for f in ks.list_items(db, kind="food", limit=500)}
    for name, city in expectations.items():
        assert name in foods, f"知识库缺少特产：{name}"
        assert foods[name].city == city, f"{name} 应属 {city}，实际 {foods[name].city}"


def test_alias_resolves_to_correct_city(db):
    """别名也要能正确定位产地（「闻喜煮饼」是「运城闻喜煮饼」的别名）"""
    idx = ks.FoodIndex.from_db(db)
    hits = idx.find_all("在朔州吃闻喜煮饼")
    assert hits, "别名未匹配到特产"
    term, allowed = hits[0][2], hits[0][3]
    assert "闻喜煮饼" in term
    assert "yuncheng" in allowed and "shuozhou" not in allowed


# ---------------------------------------------------------------- 错配检测


def test_detect_classic_mismatch(db):
    """朔州吃平遥牛肉 —— 本项目最初的经典错误"""
    plan = make_plan(
        ["朔州"],
        [("朔州·第1日", [("应县木塔（佛宫寺释迦塔）", "平遥牛肉配浑源凉粉", "辽代木塔。")])],
    )
    report = ks.validate_plan_geo(db, plan, fix=False)
    found = {i.found for i in report.issues}
    assert "平遥牛肉" in found, "未检出平遥牛肉错配"
    assert "浑源凉粉" in found, "未检出浑源凉粉错配"
    assert all(i.kind == "food_city_mismatch" for i in report.issues)
    assert not report.ok


def test_no_false_positive_for_local_version(db):
    """「应县凉粉」是朔州本地叫法，不得因别名「凉粉」属大同而误报"""
    plan = make_plan(
        ["朔州"],
        [("朔州·第1日", [("应县木塔（佛宫寺释迦塔）", "应县凉粉配右玉羊肉", "辽代木塔。")])],
    )
    report = ks.validate_plan_geo(db, plan, fix=False)
    assert report.issues == [], f"出现误报：{[i.detail for i in report.issues]}"


def test_allowed_in_traditional_city(db):
    """莜面栲栳栳允许出现在同源地区（大同/忻州/吕梁/晋中）"""
    plan = make_plan(
        ["大同"],
        [("大同·第1日", [("云冈石窟", "大同刀削面配莜面栲栳栳", "北魏石窟。")])],
    )
    report = ks.validate_plan_geo(db, plan, fix=False)
    assert report.issues == []


def test_cross_city_day_uses_activity_city(db):
    """活动自身点明另一已选目的地时，按其城市判定餐食（不误报）"""
    plan = make_plan(
        ["朔州", "晋中"],
        [
            ("朔州·第1日", [("崇福寺", "右玉羊肉", "金代弥陀殿。")]),
            ("晋中·第2日", [("平遥古城", "平遥牛肉配太谷饼", "世界文化遗产。")]),
        ],
    )
    report = ks.validate_plan_geo(db, plan, fix=False)
    assert report.issues == [], f"跨城正常安排被误报：{[i.detail for i in report.issues]}"


def test_detect_foreign_place(db):
    """非目的地的景点应被标记为外地地名"""
    plan = make_plan(
        ["朔州"],
        [("朔州·第1日", [("云冈石窟", "右玉羊肉", "北魏石窟。")])],
    )
    report = ks.validate_plan_geo(db, plan, fix=False)
    kinds = {i.kind for i in report.issues}
    assert "foreign_city_mention" in kinds


# ---------------------------------------------------------------- 自动修正质量


def test_autofix_replaces_with_local_food(db):
    plan = make_plan(
        ["朔州"],
        [("朔州·第1日", [("应县木塔（佛宫寺释迦塔）", "平遥牛肉配闻喜煮饼", "辽代木塔。")])],
    )
    report = ks.validate_plan_geo(db, plan)
    assert report.fixed_count >= 2
    food = plan.days[0].activities[0].foodPairing
    for bad in ("平遥牛肉", "闻喜煮饼"):
        assert bad not in food, f"未替换掉 {bad}：{food}"

    local = {f.name for f in ks.list_items(db, kind="food", city="shuozhou", limit=100)}
    # 替换后应当只包含本城特产
    assert any(name in food for name in local), f"替换结果不含本地特产：{food}"


def test_autofix_leaves_no_orphan_prefix(db):
    """修正后不得出现「应县右玉羊肉」这类残片"""
    plan = make_plan(
        ["朔州"],
        [("朔州·第1日", [("应县木塔（佛宫寺释迦塔）", "应县凉粉配平遥牛肉", "辽代木塔。")])],
    )
    ks.validate_plan_geo(db, plan)
    food = plan.days[0].activities[0].foodPairing
    assert "平遥牛肉" not in food
    # 应县凉粉是本地正当内容，应被保留
    assert "应县凉粉" in food, f"本地正当内容被破坏：{food}"
    # 不得出现两个连缀的地名残片
    assert "应县右玉" not in food, f"出现残片拼接：{food}"


def test_autofix_no_duplicate_local_food(db):
    plan = make_plan(
        ["朔州"],
        [("朔州·第1日", [("应县木塔（佛宫寺释迦塔）", "平遥牛肉配太谷饼", "辽代木塔。")])],
    )
    ks.validate_plan_geo(db, plan)
    food = plan.days[0].activities[0].foodPairing
    local = [f.name for f in ks.list_items(db, kind="food", city="shuozhou", limit=100)]
    used = [name for name in local if name in food]
    assert len(used) == len(set(used)), f"替换后出现重复特产：{food}"


def test_autofix_disabled_keeps_text(db):
    plan = make_plan(
        ["朔州"],
        [("朔州·第1日", [("崇福寺", "平遥牛肉", "金代弥陀殿。")])],
    )
    report = ks.validate_plan_geo(db, plan, fix=False)
    assert plan.days[0].activities[0].foodPairing == "平遥牛肉"
    assert report.fixed_count == 0
    assert report.issues


# ---------------------------------------------------------------- 校验文本


def test_validate_text_flags_mismatch(db):
    issues, detected = ks.validate_text(db, "在朔州要尝尝平遥牛肉", "朔州")
    assert any(i.found == "平遥牛肉" for i in issues)
    assert "朔州" in detected


def test_validate_text_clean_input(db):
    issues, _ = ks.validate_text(db, "在朔州要尝尝右玉羊肉与朔州大烩菜", "朔州")
    assert issues == []


def test_validate_text_infers_city_without_param(db):
    """不传 city 时应自行从文本推断，且不得抛异常"""
    issues, detected = ks.validate_text(db, "在朔州要吃平遥牛肉")
    assert "朔州" in detected
    assert any(i.found == "平遥牛肉" for i in issues)


def test_validate_text_no_city_no_crash(db):
    """文本中没有任何地名时不应抛异常（曾存在未定义变量缺陷）"""
    issues, detected = ks.validate_text(db, "随便写点没有地名的文字")
    assert detected == []
    assert issues == []


def test_validate_text_food_name_contains_city_alias(db):
    """「平遥牛肉」自带「平遥」二字，会被识别为产地晋中，不应误判为错配"""
    issues, detected = ks.validate_text(db, "来一碗平遥牛肉")
    assert "晋中" in detected
    assert issues == [], "产地即文本所指城市，不应报错配"


def test_validate_text_prefix_aware(db):
    """与行程校验一致：应县凉粉属朔州，不应因别名「凉粉」而误报"""
    issues, _ = ks.validate_text(db, "朔州的应县凉粉很好吃", "朔州")
    assert issues == [], f"误报：{[i.detail for i in issues]}"


# ---------------------------------------------------------------- 检索


def test_fts_search_works(db):
    res = ks.search_items(db, "木塔", limit=10)
    assert res, "FTS5 检索无结果"
    assert any("木塔" in i.name for i in res)


def test_fts_search_food(db):
    res = ks.search_items(db, "牛肉", limit=10)
    assert any(i.kind == "food" for i in res)


def test_search_filter_by_city(db):
    res = ks.search_items(db, "凉粉", city="大同", limit=10)
    assert res
    assert all(i.city == "datong" for i in res), [i.name for i in res]


def test_retrieve_for_destinations(db):
    bundle = ks.retrieve_for_destinations(db, ["朔州", "晋中"])
    assert len(bundle.cities) == 2
    names = {c.city_name for c in bundle.cities}
    assert names == {"朔州", "晋中"}
    for c in bundle.cities:
        assert c.attractions and c.foods
    assert bundle.unmatched_destinations == []


def test_retrieve_unmatched_destination(db):
    bundle = ks.retrieve_for_destinations(db, ["朔州", "火星"])
    assert "火星" in bundle.unmatched_destinations


def test_prompt_context_contains_local_food(db):
    bundle = ks.retrieve_for_destinations(db, ["朔州"])
    text = ks.build_prompt_context(bundle)
    assert "右玉羊肉" in text
    assert "平遥牛肉" not in text, "朔州上下文不应出现晋中名产"
    assert "仅限本地" in text or "同源地区" in text


# ---------------------------------------------------------------- 缓存 key


def test_cache_key_normalizes_order(db):
    from app.schemas.itinerary import ItineraryForm
    from app.services.cache_service import build_cache_key

    fp = ks.knowledge_fingerprint(db)
    a = ItineraryForm(destinations=["朔州", "大同"], days=3, themes=["古建营造", "风味美食"])
    b = ItineraryForm(destinations=["大同", "朔州"], days=3, themes=["风味美食", "古建营造"])
    assert build_cache_key(a, fp) == build_cache_key(b, fp), "参数顺序不同不应导致缓存未命中"


def test_cache_key_changes_with_knowledge(db):
    from app.schemas.itinerary import ItineraryForm
    from app.services.cache_service import build_cache_key

    form = ItineraryForm(destinations=["朔州"], days=2)
    assert build_cache_key(form, "aaaa") != build_cache_key(form, "bbbb"), (
        "知识库指纹变化必须使缓存失效"
    )


def test_cache_key_changes_with_days():
    from app.schemas.itinerary import ItineraryForm
    from app.services.cache_service import build_cache_key

    a = ItineraryForm(destinations=["朔州"], days=2)
    b = ItineraryForm(destinations=["朔州"], days=3)
    assert build_cache_key(a, "fp") != build_cache_key(b, "fp")


# ---------------------------------------------------------------- 历史数据兼容


def test_coerce_days_current_format():
    """规范格式：[{day}, ...]"""
    from app.services.itinerary_service import _coerce_days

    raw = [{"title": "朔州·第1日", "theme": "访古", "activities": []}]
    days = _coerce_days(raw)
    assert len(days) == 1
    assert days[0].title == "朔州·第1日"


def test_coerce_days_legacy_string():
    """旧格式：整份行程被存成 JSON 字符串"""
    import json

    from app.services.itinerary_service import _coerce_days

    whole = {
        "title": "三晋文旅·朔州2日",
        "destinations": ["朔州"],
        "highlights": ["h"],
        "days": [{"title": "朔州·第1日", "theme": "访古", "activities": []}],
    }
    days = _coerce_days(json.dumps(whole, ensure_ascii=False))
    assert len(days) == 1
    assert days[0].title == "朔州·第1日"


def test_coerce_days_legacy_char_array():
    """旧格式：字符串被逐字符存成数组"""
    import json

    from app.services.itinerary_service import _coerce_days

    whole = {
        "title": "t",
        "destinations": ["朔州"],
        "highlights": [],
        "days": [{"title": "朔州·第1日", "theme": "访古", "activities": []}],
    }
    char_array = list(json.dumps(whole, ensure_ascii=False))
    days = _coerce_days(char_array)
    assert len(days) == 1
    assert days[0].title == "朔州·第1日"


def test_coerce_days_garbage_does_not_raise():
    """损坏数据不应抛异常，最差返回空列表"""
    from app.services.itinerary_service import _coerce_days

    assert _coerce_days("not json at all") == []
    assert _coerce_days(None) == []
    assert _coerce_days(12345) == []
    assert _coerce_days([{"title": "ok", "activities": []}, "坏数据"]) != []


def test_list_itineraries_does_not_crash(db):
    """历史行程列表接口必须对旧数据健壮（曾因 days 为字符串而 500）"""
    from app.services.itinerary_service import list_itineraries

    rows = list_itineraries(db)
    assert isinstance(rows, list)
    for r in rows:
        assert isinstance(r.days, list)


# ---------------------------------------------------------------- 智能体对话


def test_agent_history_first_must_be_user():
    """讯飞要求 history 首元素为 user；开场白（agent）须被丢弃"""
    from app.schemas.agent import ChatMessage
    from app.services import agent_service

    built = agent_service._build_history(
        [
            ChatMessage(role="agent", content="欢迎语"),
            ChatMessage(role="user", content="问题1"),
            ChatMessage(role="agent", content="回答1"),
        ]
    )
    assert built[0]["role"] == "user"
    assert built[0]["content"] == "问题1"


def test_agent_history_strictly_alternates():
    """同角色连续出现时须合并，保证 user/assistant 严格交替"""
    from app.schemas.agent import ChatMessage
    from app.services import agent_service

    built = agent_service._build_history(
        [
            ChatMessage(role="user", content="问题1"),
            ChatMessage(role="user", content="问题2"),
            ChatMessage(role="agent", content="回答"),
        ]
    )
    roles = [m["role"] for m in built]
    assert roles == ["user", "assistant"], roles


def test_agent_history_maps_agent_to_assistant():
    from app.schemas.agent import ChatMessage
    from app.services import agent_service

    built = agent_service._build_history(
        [ChatMessage(role="user", content="q"), ChatMessage(role="agent", content="a")]
    )
    assert [m["role"] for m in built] == ["user", "assistant"]


def test_agent_history_truncates_rounds():
    """历史应按配置轮数截断，避免上下文过长（讯飞 20372 错误）"""
    from app.core.config import settings
    from app.schemas.agent import ChatMessage
    from app.services import agent_service

    msgs = []
    for i in range(20):
        msgs.append(ChatMessage(role="user", content=f"q{i}"))
        msgs.append(ChatMessage(role="agent", content=f"a{i}"))
    built = agent_service._build_history(msgs)
    limit = max(1, settings.xingchen_history_rounds) * 2
    assert len(built) <= limit, f"{len(built)} > {limit}"
    assert built[0]["role"] == "user"


def test_agent_history_drops_empty():
    from app.schemas.agent import ChatMessage
    from app.services import agent_service

    built = agent_service._build_history(
        [
            ChatMessage(role="user", content="   "),
            ChatMessage(role="user", content="有效问题"),
        ]
    )
    assert [m["content"] for m in built] == ["有效问题"]


def test_agent_error_code_mapping():
    """讯飞业务错误仍返回 HTTP 200，必须解析 code 并给出可读提示"""
    from app.services import agent_service

    assert "未授权" in agent_service._describe_code(20373, "")
    # 20805 是「输出错误」，不是「草稿状态」——曾误映射为草稿，误导排查方向
    msg_20805 = agent_service._describe_code(20805, "")
    assert "输出" in msg_20805
    assert "草稿" not in msg_20805
    assert "鉴权" in agent_service._describe_code(20900, "")
    # 20004 实测：换用未绑定的 flow 会报此错，提示需指向 API Flowid
    msg_20004 = agent_service._describe_code(20004, "")
    assert "未绑定" in msg_20004
    # 未知错误码也要能描述，不能返回空
    assert agent_service._describe_code(99999, "some msg")


def test_agent_fallback_when_not_configured():
    """未配置凭据时必须降级而非抛异常（两种 provider 都要覆盖）"""
    import asyncio

    from app.core.config import settings
    from app.schemas.agent import ChatRequest
    from app.services import agent_service

    saved = (
        settings.agent_provider,
        settings.deepseek_api_key,
        settings.xingchen_api_key,
        settings.xingchen_api_secret,
        settings.xingchen_flow_id,
    )
    try:
        for provider in ("deepseek", "xingchen"):
            settings.agent_provider = provider
            settings.deepseek_api_key = ""
            settings.xingchen_api_key = ""
            settings.xingchen_api_secret = ""
            settings.xingchen_flow_id = ""
            assert not agent_service.is_configured(), provider
            res = asyncio.run(
                agent_service.chat_with_agent(ChatRequest(message="朔州有什么特产？"))
            )
            assert res.source == "fallback", provider
            assert res.reply
            assert "朔州有什么特产？" in res.reply
    finally:
        (
            settings.agent_provider,
            settings.deepseek_api_key,
            settings.xingchen_api_key,
            settings.xingchen_api_secret,
            settings.xingchen_flow_id,
        ) = saved


def test_agent_provider_switch():
    """AGENT_PROVIDER 决定走哪条链路"""
    from app.core.config import settings
    from app.services import agent_service

    saved = settings.agent_provider
    try:
        settings.agent_provider = "deepseek"
        assert agent_service.active_provider() == "deepseek"
        settings.agent_provider = "xingchen"
        assert agent_service.active_provider() == "xingchen"
    finally:
        settings.agent_provider = saved


def test_agent_empty_message_guarded():
    import asyncio

    from app.schemas.agent import ChatRequest
    from app.services import agent_service

    res = asyncio.run(agent_service.chat_with_agent(ChatRequest(message="   ")))
    assert res.reply


def test_agent_chat_id_within_32_chars():
    """讯飞要求 chat_id 长度不超过 32 位"""
    from app.services import agent_service

    for _ in range(20):
        cid = agent_service._new_chat_id()
        assert len(cid) <= 32, f"{cid} ({len(cid)})"


# ---------------------------------------------------------------- 本地工作流


def test_workflow_kb_loads():
    """知识库问答对应能被解析出来"""
    from app.services import workflow_sim

    workflow_sim.index.load(force=True)
    assert workflow_sim.index.size >= 100, workflow_sim.index.size
    # 11 个府邑应能被识别（从答案文本推断）
    assert len(workflow_sim.index.cities) == 11, workflow_sim.index.cities


def test_workflow_city_inference():
    """归属地应从答案文本推断，不依赖文件标题行"""
    from app.services import workflow_sim as ws

    assert ws.infer_city("平遥牛肉是山西晋中的特产，不是朔州、大同的特产。") == "晋中"
    assert ws.infer_city("应县木塔（佛宫寺释迦塔）位于山西朔州，是……") == "朔州"
    assert ws.infer_city("大同刀削面是山西大同的特产。") == "大同"
    assert ws.infer_city("这句话里没有任何城市名。") == ""


def test_workflow_kind_inference():
    from app.services import workflow_sim as ws

    assert ws.infer_kind(["朔州有哪些景点？"], "朔州的代表景点有……") == "attraction"
    assert ws.infer_kind(["朔州有什么特产？"], "朔州的地方特产有……") == "food"
    assert ws.infer_kind(["朔州是个怎样的地方？"], "雁门关外……") == "city"


def test_workflow_retrieval_prefers_same_city():
    """点名府邑时，同城问答对应优先召回"""
    from app.services import workflow_sim as ws

    pairs, cities = ws.retrieve("朔州有什么特产和好吃的？", top=6)
    assert "朔州" in cities
    assert pairs, "未召回任何条目"
    same_city = [p for p in pairs if p.header == "朔州"]
    assert same_city, "同城条目未被召回"
    # 首位应是朔州本城内容
    assert pairs[0].header == "朔州", pairs[0].header


def test_workflow_retrieval_food_city_correct():
    """特产归属检索：平遥牛肉应召回晋中条目"""
    from app.services import workflow_sim as ws

    pairs, _ = ws.retrieve("平遥牛肉是哪里的特产？", top=6)
    owners = {p.header for p in pairs}
    assert "晋中" in owners, owners


def test_workflow_retrieval_no_result_for_irrelevant():
    """与知识库无关的短问候不应硬凑出一堆条目"""
    from app.services import workflow_sim as ws

    pairs, cities = ws.retrieve("你好", top=6)
    # 允许召回到极少条目，但不应是满额
    assert len(pairs) < 6, [p.questions[0] for p in pairs]
    assert cities == []


def test_workflow_prompt_assembly():
    """提示词装配必须同时注入检索资料与用户问题"""
    from app.services import workflow_sim as ws

    pairs, _ = ws.retrieve("朔州有什么特产？", top=3)
    prompt = ws.build_user_prompt("朔州有什么特产？", pairs)
    assert "朔州有什么特产？" in prompt, "用户问题未注入"
    assert "右玉羊肉" in prompt, "检索资料未注入"


def test_workflow_prompt_template_loaded_from_file():
    """系统提示词应从提示词文件读取，而非内置兜底"""
    from app.services import workflow_sim as ws

    system, user = ws.load_prompts()
    assert "驿路谋士" in system
    # 文件中的关键约束应在系统提示词里
    assert "山西" in system
    assert "{{" in user  # 模板占位符仍在（由 build_user_prompt 替换）


def test_clean_reply_strips_markdown():
    """前端不渲染 Markdown，需清洗模型输出的标记"""
    from app.services import workflow_sim as ws

    assert ws.clean_reply("平遥牛肉是山西**晋中**的特产") == "平遥牛肉是山西晋中的特产"
    assert ws.clean_reply("## 标题\n正文") == "标题\n正文"
    assert ws.clean_reply("```\n代码\n```") == "代码"
    assert ws.clean_reply("") == ""


def test_clean_reply_keeps_plain_text():
    from app.services import workflow_sim as ws

    src = "右玉羊肉产自朔州右玉县。\n\n清炖最见本色。"
    assert ws.clean_reply(src) == src


def test_stream_generator_event_sequence():
    """流式生成器应先发 cities，再发若干 delta，最后以 done 收尾"""
    import asyncio

    from app.core.config import settings
    from app.services import agent_service

    if not settings.deepseek_api_key:
        import pytest

        pytest.skip("未配置 DEEPSEEK_API_KEY")

    async def run():
        events = []
        async for kind, data in agent_service._chat_deepseek_stream("朔州有什么特产？", []):
            events.append((kind, data))
        return events

    events = asyncio.run(run())
    kinds = [k for k, _ in events]
    assert kinds[0] == "cities", kinds[:3]
    assert "delta" in kinds, kinds
    assert kinds[-1] == "done", kinds[-3:]
    # 拼起来的增量与 done 的清洗结果长度应接近（清洗只去标记，不删正文）
    joined = "".join(d for k, d in events if k == "delta")
    done = [d for k, d in events if k == "done"][0]
    assert len(done) > 20
    assert abs(len(joined) - len(done)) <= max(10, len(joined) * 0.1), (
        len(joined),
        len(done),
    )


# ---------------------------------------------------------------- 前端自动构建


def test_builder_paths_and_npm():
    """构建器应正确定位前端目录与 npm 命令"""
    from app.services import frontend_builder as fb

    assert fb.frontend_dir().name == "frontend"
    assert (fb.frontend_dir() / "package.json").exists()
    cmd = fb._npm_command()
    assert cmd is not None, "未找到 npm"
    assert cmd[1:] == ["run", "build"], cmd


def test_builder_summarize_picks_key_lines():
    from app.services import frontend_builder as fb

    out = "vite v5.4.21 building...\n104 modules transformed.\nbuilt in 3.54s\nrandom noise"
    s = fb._summarize(out)
    assert "modules transformed" in s
    assert "built in" in s
    # 失败时也要能取出错误行
    err = "some preamble\nerror TS2307: Cannot find module 'x'\nmore"
    assert "error" in fb._summarize(err).lower()


def test_builder_watcher_respects_gate():
    """未托管前端时不应启动自动构建（Vite 自身有热更新）"""
    from app.core.config import settings
    from app.services import frontend_builder as fb

    saved_serve = settings.serve_frontend
    saved_auto = settings.auto_build_frontend
    try:
        settings.auto_build_frontend = False
        assert fb.start_watcher() is None

        settings.auto_build_frontend = True
        settings.serve_frontend = False
        assert fb.start_watcher() is None

        settings.serve_frontend = True
        # 已启用时应返回一个协程任务
        import asyncio

        async def go():
            t = fb.start_watcher()
            assert t is not None
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        asyncio.run(go())
    finally:
        settings.serve_frontend = saved_serve
        settings.auto_build_frontend = saved_auto


def test_builder_run_build_succeeds():
    """实际执行一次前端构建（较慢，约 5s）"""
    import asyncio

    from app.services import frontend_builder as fb

    ok, out = asyncio.run(fb._run_build())
    assert ok, f"构建失败：\n{out[-800:]}"
    assert (fb.frontend_dist_path() / "index.html").exists()
