"""行程生成与持久化服务

准确性保障采用两道闸门（缺一不可）：
1. **提示词接地（RAG）**：把知识库中该府邑的真实景点与本地特产注入提示词，并明确禁止跨城推荐
2. **结果审计（LLM 复核 + 代码规则兜底）**：生成后交由审计模型逐条核对地理归属，
   再用确定性规则兜底修正。模型可能失误，代码规则不会。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.itinerary import ItineraryModel
from app.schemas.itinerary import Activity, DayPlan, Itinerary, ItineraryForm, SavedItinerary
from app.services import knowledge_service
from app.services.cache_service import build_cache_key, cache

# ---------------------------------------------------------------- 提示词


def _need_by_city(form: ItineraryForm, cities: List[str]) -> Dict[str, int]:
    """按天数算出每城需要的候选景点数（每日 2 个活动，另留 1 个备选）。

    同城多日时若候选不足，就只能反复用同一批点位（实测 1 城 4 日会全部重复）。
    """
    day_plan = allocate_days_to_cities(form.days, cities)
    return {c: day_plan.count(c) * 2 + 1 for c in set(day_plan) if c}


def _city_order(form: ItineraryForm) -> List[str]:
    """按用户所选府邑顺序返回（保持表单顺序，保证行程动线符合预期）"""
    return [d for d in form.destinations if d]


class PlanUnreasonableError(ValueError):
    """行程安排不合理（如天数不足以为每处府邑留出至少一日）。

    由 API 层捕获并转为 400，前端据此弹窗提示用户重新选择。
    """

    def __init__(self, message: str, *, days: int = 0, cities: int = 0):
        super().__init__(message)
        self.message = message
        self.days = days
        self.cities = cities


def allocate_days_to_cities(days: int, cities: List[str]) -> List[str]:
    """把 days 天按「同城连续成块」的方式均匀分配给 cities。

    返回长度为 days 的城市名列表；每城至少分到一日。

    例：
        4 日 / 4 城 -> [A, B, C, D]
        5 日 / 2 城 -> [A, A, A, B, B]
        6 日 / 4 城 -> [A, A, B, B, C, D]

    这里**不再用取模轮转**（旧实现是 `cities[(i-1) % len]`），
    否则 4 日 2 城会排成 A、B、A、B，在府邑之间来回折返，动线不合理。
    """
    if not cities or days <= 0:
        return []
    base, extra = divmod(days, len(cities))
    out: List[str] = []
    for i, c in enumerate(cities):
        out.extend([c] * (base + (1 if i < extra else 0)))
    return out


def validate_plan_form(form: ItineraryForm) -> None:
    """校验表单能否排出一份合理行程；不合理则抛 PlanUnreasonableError。

    核心规则：**天数必须不少于所择府邑数** —— 否则每处府邑连一日都摊不到，
    必然有府邑被悄悄丢弃（这正是「选 1 日游 4 城却只生成 1 城」的根因）。
    """
    cities = _city_order(form)
    if not cities:
        raise PlanUnreasonableError("请至少择一处意向目的地府邑。", days=form.days, cities=0)

    if form.days < len(cities):
        raise PlanUnreasonableError(
            f"所择 {len(cities)} 处府邑（{'、'.join(cities)}），行旅仅 {form.days} 日，"
            f"每处府邑不足以从容驻足一日，恐难尽兴，亦失游赏之趣。"
            f"请或增其天数至 {len(cities)} 日以上，或减其府邑至 {form.days} 处以内。",
            days=form.days,
            cities=len(cities),
        )


def _select_day_attractions(
    attractions: List[Any], day_index: int, per_day: int = 4
) -> List[Any]:
    """同城多日时轮转取景点，避免每天推荐同样几个点位。

    注意两点（都是踩过的坑）：
    1. **池子小于 per_day 时也要轮转**。旧实现在这种情况下直接原样返回，
       导致同城连续两日排出一模一样的点位。
    2. **步长用 per_day 而非固定 2**。以「窗口 4、步长 2」滑动时，
       相邻两日的候选会重叠 2 个，模型很容易挑中同一批 ——
       改成按窗口宽度滑动，窗口之间就不重叠，直到池子绕回一圈。
    """
    if not attractions:
        return []
    n = len(attractions)
    step = max(1, per_day)
    start = (day_index * step) % n
    rotated = attractions[start:] + attractions[:start]
    return rotated if n <= per_day else rotated[:per_day]


def _select_themed_attractions(
    attractions: List[Any],
    day_index: int,
    themes: Optional[List[str]] = None,
    per_day: int = 4,
    theme_quota: int = 2,
) -> List[Any]:
    """主题优先取点：先保证每天都有若干「契合主主题」的景点，再用其它景点补足。

    为什么不能「先取点再排序」：`_select_day_attractions` 会先截断到 per_day 个，
    若主题景点不在被截断的那一段里，排序也救不回来 ——
    这正是「博物馆之城」线路一个博物馆都排不出来的原因。
    所以这里**先把池子按主题拆开**，主主题池单独轮转，天然保证每日都有。

    `themes[0]` 为主主题（线路的定义性主题都放首位），其余为次要偏好。
    """
    theme_list = [t for t in (themes or []) if t]
    if not theme_list:
        return _select_day_attractions(attractions, day_index, per_day)

    primary = theme_list[0]
    theme_set = set(theme_list)

    matched: List[Any] = []
    secondary: List[Any] = []
    others: List[Any] = []
    for a in attractions:
        tags = set(getattr(a, "tags", []) or [])
        if primary in tags:
            matched.append(a)
        elif theme_set & tags:
            secondary.append(a)
        else:
            others.append(a)

    picks: List[Any] = []
    if matched:
        picks = _select_day_attractions(matched, day_index, theme_quota)
    for pool in (secondary, others):
        need = per_day - len(picks)
        if need <= 0 or not pool:
            continue
        picks = picks + _select_day_attractions(pool, day_index, need)
    return picks


def build_grounded_sections(
    bundle, cities: List[str], themes: Optional[List[str]] = None
) -> str:
    """把检索结果渲染为「逐日可用素材」清单，兼顾接地、主题契合与多样性。

    传入 `themes`（用户所选三晋意趣）时，每日素材会**先保证有契合主题的景点**，
    并以 ★ 标出，让模型优先选取。
    """
    if not bundle or not bundle.cities:
        return ""
    theme_set = {t for t in (themes or []) if t}
    by_name = {c.city_name: c for c in bundle.cities}
    lines: List[str] = []
    lines.append("【逐日可用素材】以下为知识库核定的本地景点与本地特产。")
    lines.append("【硬性约束】任何一天都不得使用该日所属城市之外的景点或特产。")
    if theme_set:
        lines.append(
            f"【主题标注】标 ★ 者契合所选三晋意趣（{'、'.join(sorted(theme_set))}），应优先选取。"
        )
    lines.append("")
    for di, city_name in enumerate(cities):
        ctx = by_name.get(city_name)
        if not ctx:
            continue
        lines.append(f"■ 第 {di + 1} 日 · {ctx.city_name}")
        if ctx.intro:
            lines.append(f"  概况：{ctx.intro}")
        picks = _select_themed_attractions(ctx.attractions, di, themes)
        if picks:
            hint = "，★ 为契合三晋意趣者应优先选取" if theme_set else ""
            lines.append(f"  可选景点（只能从下列中选择，不得虚构{hint}）：")
            for a in picks:
                meta = "／".join(x for x in [a.item_type, a.level or ""] if x)
                mark = "★" if (theme_set and (theme_set & set(getattr(a, "tags", []) or []))) else "·"
                lines.append(f"    {mark} {a.name}（{meta}）{a.desc}")
                if a.tips:
                    lines.append(f"      锦囊：{a.tips}")
        if ctx.foods:
            lines.append("  本地特产／餐食（只能从下列中选择，不得推荐外地名吃）：")
            for f in ctx.foods:
                scope = (
                    f"同源地区：{'、'.join(f.traditional_in_names)}"
                    if f.traditional_in_names
                    else "仅限本地"
                )
                lines.append(f"    · {f.name}（{f.item_type}，{f.taste or '-'}；{scope}）")
        lines.append("")
    return "\n".join(lines).strip()


def _build_prompt(form: ItineraryForm, bundle=None) -> Tuple[str, List[str]]:
    """构建行程生成 Prompt，返回 (prompt, cities)"""
    cities = _city_order(form)
    dest_str = "、".join(cities)
    themes_str = "、".join(form.themes) if form.themes else "古建营造、晋商文化、非遗民俗"
    pace = form.pace or "研学适中"
    companion = form.companion or "独自悠游"

    sections = build_grounded_sections(bundle, cities, form.themes) if bundle else ""
    cities_json = json.dumps(cities, ensure_ascii=False)
    # 用统一的天数分配表，保证「天数 > 府邑数」时每天都有明确归属
    day_plan = allocate_days_to_cities(form.days, cities)
    day_table = "\n".join(f"  第 {i + 1} 日 → {c}" for i, c in enumerate(day_plan))
    # 各府邑驻留天数，供模型理解动线
    dwell = "、".join(
        f"{c} {day_plan.count(c)} 日" for c in cities
    )

    parts: List[str] = []
    parts.append(
        """你是一位精通三晋（山西）文旅的资深行程规划师，熟悉山西各地古建、佛艺、晋商、非遗、山川、风味，也熟悉革命旧址与红色纪念地、各级博物馆与专题馆。

你的首要职业底线是**地理准确**：绝不能把甲地的名产安到乙地（例如「平遥牛肉」是晋中平遥的特产，
绝不能写成朔州的餐食；朔州的名产是右玉羊肉、应县紫皮蒜、朔州大烩菜等）。
也不能把甲地的旧址、纪念馆、博物馆安到乙地（例如「百团大战纪念馆」在阳泉狮脑山，
「八路军太行纪念馆」在长治武乡，「平型关大捷纪念馆」在大同灵丘，「刘胡兰纪念馆」在吕梁文水）。
宁可只写该地确有的内容，也不要为了文采编造不存在的点位或张冠李戴的特产与场馆。"""
    )
    parts.append(
        f"""
【用户需求】
- 意向目的地：{dest_str}
- 行程天数：{form.days} 天
- 同行人：{companion}
- 寻古节奏：{pace}
- 偏好主题：{themes_str}
- 各府邑驻留：{dwell}
"""
    )
    if sections:
        parts.append("\n" + sections + "\n")
    parts.append(
        f"""
【逐日城市安排】必须严格按下表把第 N 日安排给对应城市，不得调换、不得遗漏任何一日：
{day_table}
（destinations 字段请原样填 {cities_json}）

【输出格式】严格只返回 JSON，不要任何解释、Markdown 标记或多余文字：
{{
  "title": "行程标题（含目的地与天数，如：三晋文旅·大同3日悠游慢赏行程）",
  "destinations": {cities_json},
  "highlights": ["线路神韵1", "线路神韵2", "线路神韵3"],
  "rationale": "「为何这样安排」——120-180 字的古风说明，必须**分别点明**下面三事如何影响了本次编排：①同行之人（{companion}）②寻古节奏与意趣（{pace}）③三晋意趣（{themes_str}）。行文须文言雅致、与三晋文旅相称，可引府邑风物为证；不得出现「根据您的选择」「参数」等现代系统口吻。",
  "days": [
    {{
      "theme": "当日主题",
      "title": "第N日标题（必须包含该日城市名，如：朔州·第1日访古与风味巡礼）",
      "totalHours": "预计耗时小时数（如：5.5）",
      "suggestedDeparture": "建议起行时间（如：09:00）",
      "localSpecialties": "当日府邑的本地特产（取该日城市「本地特产／餐食」清单中 2-3 样，用「、」连接；**只在此处标注一次**，不得写进任何 activity）",
      "activities": [
        {{
          "time": "时段（如：09:30 - 12:00）",
          "place": "点位名称（必须取自该日可选景点，写全称）",
          "type": "活动类型（如：重点国保研习 / 文脉沉浸体验 / 古建法式考辨）",
          "desc": "点位简介（严格 25-45 字，融入古建/历史/文化背景）",
          "tips": "参访锦囊（严格 15-30 字，预约、拍摄、避坑提示）"
        }}
      ]
    }}
  ]
}}

【硬性要求】
1. days 数组长度必须等于 {form.days}，**每天恰好 2 个活动**（不要写 3 个），且逐日城市严格照上表
2. 第 N 日的全部活动只能属于上表中第 N 日的城市；本地特产只在 localSpecialties 标注，**activity 内不要再写餐饮**
3. localSpecialties 中出现的美食必须能在该日城市的「本地特产／餐食」清单里找到
4. **主题优先**：每日应优先选取素材中带 ★ 的景点（契合三晋意趣「{themes_str}」）；
   若该日城市有带 ★ 的景点，当日至少安排 1 个，不得全部选成其它主题
5. 各字段务必简短，禁止展开长篇描述 —— 输出过长会被截断导致整份 JSON 失效
6. rationale 是唯一允许稍长的字段，但不得超过 200 字
7. 返回纯 JSON，不要 ```json 包裹，不要任何额外文字"""
    )
    return "\n".join(parts), cities


AUDIT_SYSTEM_PROMPT = """你是山西文旅地理审核员，职责是核查行程中的地理归属错误。

重点核查三类问题：
1. 餐食张冠李戴：某地名产被安排到了非其产地的城市
   （如：平遥牛肉属晋中平遥，出现在朔州即为错误；闻喜煮饼属运城，出现在大同即为错误）
2. 点位跨城：当天行程里出现不属于该日城市、也未在用户所选目的地内的景点或地名
3. 旧址／纪念馆／博物馆错置：红色纪念地与博物馆同样有明确归属
   （如：百团大战纪念馆在阳泉狮脑山、八路军太行纪念馆在长治武乡、
     平型关大捷纪念馆在大同灵丘、刘胡兰纪念馆在吕梁文水、山西博物院在太原）

判定依据是你的山西地理与风物常识，不要臆造。没有把握时不要报告问题。
只返回 JSON，不要任何解释文字。"""


def _build_audit_prompt(plan: Itinerary) -> str:
    lines = ["【用户所选目的地】" + "、".join(plan.destinations), "", "【待审行程】"]
    for di, day in enumerate(plan.days, 1):
        lines.append(f"第{di}日 | 标题：{day.title} | 主题：{day.theme}")
        # 餐饮已上移到「日级主轴」（localSpecialties），审计相应只核这一处
        if (day.localSpecialties or "").strip():
            lines.append(f"      本地特产（主轴）：{day.localSpecialties}")
        for ai, act in enumerate(day.activities):
            lines.append(f"  [{ai}] 点位：{act.place}")
            lines.append(f"      类型：{act.type}")
            if act.desc:
                lines.append(f"      简介：{act.desc[:80]}")
    lines.append("")
    lines.append(
        """请逐条核查并只返回如下 JSON：
{"issues": [{"day": 1, "activity": -1, "field": "localSpecialties", "kind": "food_city_mismatch",
 "found": "平遥牛肉", "expected_city": "朔州", "found_city": "晋中",
 "reason": "平遥牛肉为晋中平遥特产，与朔州无关", "suggestion": "改为右玉羊肉"}]}

字段说明：
- day 从 1 开始；activity 为上面列出的序号，**核查「本地特产（主轴）」时填 -1**
- field 取值 localSpecialties / place / desc / title
- kind 取值 food_city_mismatch（特产张冠李戴）/ foreign_city_mention（外地地名）/ other
- 若完全没有问题，返回 {"issues": []}"""
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- 大模型调用


class LLMError(RuntimeError):
    pass


async def _chat(
    messages: List[Dict[str, str]],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    timeout: Optional[int] = None,
) -> str:
    """调用 DeepSeek Chat Completions，返回正文内容。

    注意：推理模型的 reasoning token 与正文共用 max_tokens 预算，
    预算耗尽时 content 会是空串，此处将其视为失败以触发重试／降级。
    """
    if not settings.deepseek_api_key:
        raise LLMError("未配置 DEEPSEEK_API_KEY")

    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.deepseek_model,
        "messages": messages,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens or settings.deepseek_max_tokens,
    }
    async with httpx.AsyncClient(timeout=timeout or settings.deepseek_timeout) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        usage = data.get("usage") or {}
        raise LLMError(
            f"模型返回空正文（finish_reason={choice.get('finish_reason')}，"
            f"completion_tokens={usage.get('completion_tokens')}）"
        )
    return content


async def _chat_with_retry(
    messages: List[Dict[str, str]],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    attempts: Optional[int] = None,
) -> str:
    """带退避重试的调用；空正文时自动抬高 token 预算重试"""
    tries = attempts if attempts is not None else max(1, settings.llm_max_retries + 1)
    budget = max_tokens or settings.deepseek_max_tokens
    last: Optional[Exception] = None
    for attempt in range(tries):
        try:
            return await _chat(messages, max_tokens=budget, temperature=temperature)
        except Exception as e:
            last = e
            budget = min(int(budget * 1.6), 16000)
            if attempt < tries - 1:
                await asyncio.sleep(1.2 * (attempt + 1))
    raise LLMError(str(last) if last else "调用失败")


def _extract_json(value: str) -> Optional[dict]:
    """从大模型返回文本中提取 JSON 对象"""
    if not value:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    start, end = value.find("{"), value.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(value[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------- 兜底行程（基于知识库）


def _build_mock_plan(db: Optional[Session], form: ItineraryForm) -> Itinerary:
    """知识库驱动的兜底行程。

    完全依据 `kb_items` 组装，因此**天然不存在地理错配**。
    既用作大模型失败时的降级，也用作天数不足时的补齐素材。
    """
    cities = _city_order(form) or ["太原"]
    kb_cities: Dict[str, Any] = {}
    if db is not None:
        try:
            bundle = knowledge_service.retrieve_for_destinations(
                db, cities, form.themes, _need_by_city(form, cities)
            )
            kb_cities = {c.city_name: c for c in bundle.cities}
        except Exception as e:  # pragma: no cover
            print(f"[兜底行程] 知识库检索失败：{e}")

    fallback_attractions = {
        "大同": ["云冈石窟", "华严寺", "善化寺"],
        "朔州": ["应县木塔（佛宫寺释迦塔）", "崇福寺", "广武明长城"],
        "忻州": ["五台山", "佛光寺", "雁门关"],
        "太原": ["晋祠", "天龙山石窟", "永祚寺双塔"],
        "晋中": ["平遥古城", "双林寺", "王家大院"],
        "吕梁": ["碛口古镇", "李家山村", "北武当山"],
        "阳泉": ["娘子关", "藏山"],
        "长治": ["法兴寺", "崇庆寺", "观音堂"],
        "晋城": ["青莲寺", "府城玉皇庙", "皇城相府"],
        "临汾": ["广胜寺", "千佛庵", "壶口瀑布"],
        "运城": ["解州关帝庙", "永乐宫", "飞云楼"],
    }
    fallback_foods = {
        "大同": ["大同刀削面", "浑源凉粉", "大同羊杂"],
        "朔州": ["右玉羊肉", "朔州大烩菜", "应县紫皮蒜"],
        "忻州": ["定襄蒸肉", "五台山台蘑", "忻州瓦酥"],
        "太原": ["太原头脑", "太原稍梅", "太原过油肉"],
        "晋中": ["平遥牛肉", "平遥碗托", "太谷饼"],
        "吕梁": ["柳林碗团", "吕梁柏籽羊肉", "临县红枣"],
        "阳泉": ["阳泉平定过油肉", "阳泉漂抿曲", "阳泉黄瓜干"],
        "长治": ["长治腊驴肉", "长治壶关羊汤", "沁州黄小米"],
        "晋城": ["晋城十大碗", "高平烧豆腐", "阳城烧肝"],
        "临汾": ["临汾牛肉丸子面", "洪洞饽糕", "临汾吴家熏肉"],
        "运城": ["运城羊肉泡馍", "闻喜煮饼", "运城解州羊肉泡"],
    }
    theme_pool = [
        "古城初晤与古刹寻踪",
        "晋商宅院与匠作传习",
        "名山石窟与佛艺寻幽",
        "市井风味与非遗手作",
        "边塞雄关与山川形胜",
        "壁画彩塑与古建法式",
    ]

    days: List[DayPlan] = []
    day_cities = allocate_days_to_cities(form.days, cities)
    for i in range(1, form.days + 1):
        city_name = day_cities[i - 1] if i - 1 < len(day_cities) else cities[-1]
        ctx = kb_cities.get(city_name)
        if ctx and ctx.attractions:
            # 主题优先取点（先分主题池再轮转，保证每日都有契合主题的点位）
            picks = _select_themed_attractions(ctx.attractions, i - 1, form.themes, per_day=3)
            places = [a.name for a in picks]
            descs = [a.desc for a in picks]
            tips_list = [a.tips for a in picks]
        else:
            # 无知识库时用内置兜底清单，同样按日轮转，
            # 否则同城连续两日会排出完全相同的点位
            pool = list(fallback_attractions.get(city_name, [f"{city_name}古城与博物馆"]))
            places = _select_day_attractions(pool, i - 1, per_day=3)
            descs = [f"{p}为{city_name}代表性人文点位。" for p in places]
            tips_list = ["建议提前确认开放时间。"] * len(places)

        foods = [f.name for f in ctx.foods] if ctx and ctx.foods else list(
            fallback_foods.get(city_name, [])
        )
        if not foods:
            foods = [f"{city_name}地方风味"]
        while len(places) < 2:
            places.append(f"{city_name}老城街巷")
            descs.append(f"{city_name}老城街巷留存明清街市肌理，适合漫步体验市井生活。")
            tips_list.append("傍晚开市最热闹。")

        activities: List[Activity] = []
        for k, slot in enumerate([("08:30 - 11:30", "重点国保研习"), ("14:00 - 17:00", "文脉沉浸体验")]):
            if k >= len(places):
                break
            activities.append(
                Activity(
                    time=slot[0],
                    place=places[k],
                    type=slot[1],
                    desc=descs[k],
                    tips=tips_list[k],
                )
            )

        theme = theme_pool[(i - 1) % len(theme_pool)]
        # 本地特产只在「主轴」标注一次（去重），不在各时段内重复出现
        specialties = "、".join(dict.fromkeys(foods[:3]))
        days.append(
            DayPlan(
                theme=theme,
                title=f"{city_name}·第{i}日{theme}",
                totalHours="6.0",
                suggestedDeparture="08:30",
                localSpecialties=specialties,
                activities=activities,
            )
        )

    return Itinerary(
        title=f"三晋文旅·{'与'.join(cities)}{form.days}日{form.pace or '研学适中'}行程",
        destinations=list(cities),
        highlights=[
            f"考辨{cities[0]}古建筑营造法式",
            "寻味三晋本地特产与非遗风味",
            "遵照古建光影严选朝暮最佳游憩时刻",
        ],
        rationale=_mock_rationale(form, cities, day_cities),
        days=days,
    )


def _mock_rationale(form: ItineraryForm, cities: List[str], day_cities: List[str]) -> str:
    """兜底行程的「为何这样安排」——以古风说明三项选择如何影响编排。"""
    companion = form.companion or "独自悠游"
    pace = form.pace or "研学适中"
    themes = "、".join(form.themes) if form.themes else "古建营造、晋商文化、非遗民俗"

    companion_note = {
        "独自悠游": "孤身策杖，行止由心，故日程疏朗，留白以容随兴驻足",
        "双人同行": "二人偕行，步履相将，故朝暮各留一处对坐品茗之所",
        "亲子出游": "携幼同行，宜张弛有度，故半日访古、半日游憩，不使倦怠",
        "长者同行": "扶杖长者，宜缓不宜促，故择平缓通途，避登临之劳",
        "团队研学": "群贤毕至，宜有次第，故按古建法式、佛艺源流分日递进",
    }.get(companion, "循同行之宜，量力而排")

    pace_note = {
        "研学适中": "步趋中道，一日二处，既可细审法式，亦不致奔命",
        "深度慢游": "意在沉潜，故于要冲久驻，反复摩挲，不贪多而贵精",
        "紧凑打卡": "志在广览，故晨昏连缀，取其梗概，务使名迹不遗",
    }.get(pace, "依所择节奏铺陈日程")

    dwell = "、".join(f"{c}驻 {day_cities.count(c)} 日" for c in cities)
    return (
        f"此番编排，首重同行之宜：{companion}，{companion_note}。"
        f"次循寻古节奏：{pace}，{pace_note}。"
        f"复以三晋意趣为纲：所好者{themes}，故择点、命题皆向此归。"
        f"府邑之序则依路程顺次铺展（{dwell}），使舟车不虚、游观有节。"
    )


# ---------------------------------------------------------------- 结构规整


def _coerce_plan(plan_dict: dict, form: ItineraryForm, cities: List[str]) -> Itinerary:
    """把大模型返回的 JSON 规整为 Itinerary，并补齐缺失字段"""
    days: List[DayPlan] = []
    for d in plan_dict.get("days") or []:
        if not isinstance(d, dict):
            continue
        acts: List[Activity] = []
        legacy_foods: List[str] = []
        for a in d.get("activities") or []:
            if not isinstance(a, dict):
                continue
            # 旧格式把餐饮写在 activity.foodPairing；新格式只在日级 localSpecialties。
            # 这里把旧字段收集起来，稍后上移到日级，避免每时段重复显示同一批特产。
            legacy = str(a.get("foodPairing") or a.get("food") or "").strip()
            if legacy:
                legacy_foods.extend(x for x in re.split(r"[、,，/]", legacy) if x.strip())
            acts.append(
                Activity(
                    time=str(a.get("time", "")),
                    place=str(a.get("place", "")),
                    type=str(a.get("type", "")),
                    desc=str(a.get("desc", "")),
                    tips=str(a.get("tips", "")),
                )
            )
        specialties = str(d.get("localSpecialties") or d.get("specialties") or "").strip()
        if not specialties and legacy_foods:
            specialties = "、".join(dict.fromkeys(legacy_foods))
        days.append(
            DayPlan(
                theme=str(d.get("theme", "")),
                title=str(d.get("title", "")),
                totalHours=str(d.get("totalHours", "")),
                suggestedDeparture=str(d.get("suggestedDeparture", "")),
                localSpecialties=specialties,
                activities=acts,
            )
        )

    plan = Itinerary(
        title=str(plan_dict.get("title") or f"三晋文旅·{'、'.join(cities)}{form.days}日行程"),
        destinations=[str(x) for x in (plan_dict.get("destinations") or cities)],
        highlights=[str(x) for x in (plan_dict.get("highlights") or [])],
        rationale=str(plan_dict.get("rationale") or "").strip(),
        days=days,
    )
    if not plan.destinations:
        plan.destinations = list(cities)
    if not plan.highlights:
        plan.highlights = [f"遍览{'、'.join(cities)}人文与风味"]
    return plan


def _repair_structure(
    plan: Itinerary, db: Optional[Session], form: ItineraryForm, cities: List[str]
) -> Itinerary:
    """结构兜底：标题补城市名、日级特产补齐、天数对齐、理由补齐"""
    day_cities = allocate_days_to_cities(max(form.days, len(plan.days)), cities)

    for i, day in enumerate(plan.days):
        city_name = day_cities[i] if i < len(day_cities) else (cities[-1] if cities else "")
        if city_name and city_name not in (day.title or ""):
            day.title = f"{city_name}·第{i + 1}日{day.theme or '行驿'}"

    if len(plan.days) < form.days:
        filler = _build_mock_plan(db, form)
        plan.days.extend(filler.days[len(plan.days) : form.days])
    elif len(plan.days) > form.days:
        plan.days = plan.days[: form.days]

    # 日级特产兜底：缺失时从知识库补，保证「主轴」一定有本地特产可看
    _fill_local_specialties(plan, db, day_cities)
    # 「为何这样安排」兜底
    if not (plan.rationale or "").strip():
        plan.rationale = _mock_rationale(form, cities, day_cities)
    return plan


def _fill_local_specialties(
    plan: Itinerary, db: Optional[Session], day_cities: List[str]
) -> None:
    """为缺失 localSpecialties 的日补齐该府邑特产（按城市去重缓存，避免逐日查库）"""
    if all((d.localSpecialties or "").strip() for d in plan.days):
        return

    cache_by_city: Dict[str, str] = {}
    for i, day in enumerate(plan.days):
        if (day.localSpecialties or "").strip():
            continue
        city_name = day_cities[i] if i < len(day_cities) else ""
        if not city_name:
            continue
        if city_name not in cache_by_city:
            cache_by_city[city_name] = _city_specialties(db, city_name)
        day.localSpecialties = cache_by_city[city_name]


def _city_specialties(db: Optional[Session], city_name: str) -> str:
    """取某府邑的本地特产名（最多 3 样），取不到则返回空串"""
    if db is None or not city_name:
        return ""
    try:
        items = knowledge_service.list_items(db, city=city_name, kind="food")
    except Exception:
        return ""
    names = [getattr(x, "name", "") for x in items]
    names = [n for n in names if n]
    return "、".join(dict.fromkeys(names[:3]))


def _dedupe_days(plan: Itinerary) -> Itinerary:
    """清理同一天内重复的点位，避免模型凑数"""
    for day in plan.days:
        seen = set()
        unique: List[Activity] = []
        for act in day.activities:
            key = (act.place or "").strip()
            if key and key in seen:
                continue
            seen.add(key)
            unique.append(act)
        day.activities = unique
    return plan


def _theme_capacity(pool: List[Any], primary: str, theme_set: set) -> int:
    """该城可用的（主）主题点位数量 —— 决定「每日都要主题点位」是否现实。"""
    if primary:
        hits = [a for a in pool if primary in set(getattr(a, "tags", []) or [])]
        if hits:
            return len(hits)
    return len([a for a in pool if theme_set & set(getattr(a, "tags", []) or [])])


def _dedupe_across_days(
    plan: Itinerary, form: ItineraryForm, db: Optional[Session], bundle, cities: List[str]
) -> int:
    """跨日去重：同一点位不在多日重复出现（有候选可换时）。

    为什么需要：同城多日（如 1 城 4 日）时，模型倾向反复推荐同一批「名点」，
    实测 12 处跨日重复。`_dedupe_days` 只清理**同一天内**的重复，
    跨天重复需要在这里按知识库候选确定性地替换。

    优先级：**主题点位默认保留**（用户选的主题比去重重要），
    但仅当该城的主题点位**足够覆盖它的天数**时如此 ——
    若主题点位比天数还少（如阳泉只有 2 个红色点位却排 3 日），
    那么「每日都有主题点位」与「不重复」不可能同时成立，
    此时**优先去重**：宁可某天不带主题，也不要同一处反复出现。

    返回替换次数。
    """
    if not plan.days:
        return 0
    theme_list = [t for t in (form.themes or []) if t]
    primary = theme_list[0] if theme_list else ""
    theme_set = set(theme_list)

    by_city = {c.city_name: c for c in (bundle.cities if bundle else [])}

    # 平铺的 名称 → [item]，供 tags 查询
    pool_for_flat: Dict[str, List[Any]] = {}
    for c in (bundle.cities if bundle else []):
        for a in c.attractions:
            pool_for_flat.setdefault(a.name, []).append(a)

    def tags_of(name: str) -> set:
        items = pool_for_flat.get(name) or []
        return set(getattr(items[0], "tags", []) or []) if items else set()

    # 城市 → 有序候选（先用 bundle 里主题优先顺序，再从全量库补齐）
    pools: Dict[str, List[Any]] = {}

    def pool_for(city_name: str) -> List[Any]:
        if city_name in pools:
            return pools[city_name]
        items: List[Any] = []
        ctx = by_city.get(city_name)
        if ctx:
            items.extend(ctx.attractions)
        if db is not None:
            try:
                full = knowledge_service.list_items(
                    db, city=city_name, kind="attraction", limit=200
                )
                have = {getattr(a, "name", "") for a in items}
                items.extend(x for x in full if x.name not in have)
            except Exception:
                pass
        pools[city_name] = items
        return items

    day_cities = allocate_days_to_cities(len(plan.days), cities or _city_order(form))
    seen: set = set()
    fixed = 0

    for i, day in enumerate(plan.days):
        city_name = day_cities[i] if i < len(day_cities) else ""
        if not city_name:
            for name in by_city:
                if name and name in (day.title or ""):
                    city_name = name
                    break
        pool = pool_for(city_name) if city_name else []
        pool_names = [a.name for a in pool]

        # 该城主题点位是否足够覆盖它的天数 —— 不够时主题让位于去重
        days_in_city = day_cities.count(city_name) if city_name else 0
        capacity = _theme_capacity(pool, primary, theme_set) if primary else 0
        theme_surplus = capacity < days_in_city

        # 当日主主题点位数量
        theme_hits = sum(
            1 for act in day.activities if primary and primary in tags_of((act.place or "").strip())
        )

        for j, act in enumerate(day.activities):
            name = (act.place or "").strip()
            if not name:
                continue
            if name not in seen:
                seen.add(name)
                continue

            # 重复了：若它是当日唯一的主主题点位，且主题点位足以覆盖天数，则保留
            if (
                primary
                and not theme_surplus
                and theme_hits <= 1
                and primary in tags_of(name)
            ):
                continue

            # 候选必须同时避开「往期已用」与「当日其它活动占用」，
            # 否则会把重复从跨日变成同日（测试抓到过这个边界）
            taken = seen | {
                (a.place or "").strip() for k, a in enumerate(day.activities) if k != j
            }
            alt = next(
                (
                    p for p in pool_names
                    if p and p not in taken and primary and primary in tags_of(p)
                ),
                None,
            )
            if alt is None:
                alt = next((p for p in pool_names if p and p not in taken), None)
            if not alt:
                continue
            info = next((a for a in pool if a.name == alt), None)
            day.activities[j] = Activity(
                time=act.time or "14:00 - 17:00",
                place=alt,
                type=act.type or "文脉沉浸体验",
                desc=((getattr(info, "desc", "") or act.desc) or "")[:60],
                tips=((getattr(info, "tips", "") or act.tips) or "")[:40],
            )
            seen.add(alt)
            fixed += 1
            print(f"[跨日去重] 第{i + 1}日「{name}」重复，已替换为「{alt}」")
    return fixed


def _ensure_theme_coverage(
    plan: Itinerary, form: ItineraryForm, db: Optional[Session], bundle, cities: List[str]
) -> int:
    """代码兜底：保证**每日至少 1 个契合主主题**的点位，且尽量不跨日重复。

    为什么需要：提示词里已把主题景点排前并加 ★，但模型仍可能选成其它主题
    （实测「博物馆藏」线路一个博物馆都没排）。这与地理审计同一思路 ——
    **模型负责文采，代码负责底线**：主题是用户明确选过的，不该被忽视。

    为何以 `themes[0]` 为准则：多主题组合下（如「博物馆藏+古建营造」），
    若按「任一主题命中即算覆盖」，太原几乎每个古建都带「古建营造」标签，
    每天都会被判定为已覆盖，主主题反而永远补不上。

    **均衡而不只是补齐**：模型常把主题点位扎堆在两天（如第1、3日各 2 个），
    另一天无点可用只能重复，实测产生跨日重复。
    因此当没有未用的主题候选时，优先**从多占了一天借一个**，
    真正无点可借（主题点位少于天数）才允许重复。

    返回修正天数。
    """
    theme_list = [t for t in (form.themes or []) if t]
    if not theme_list or not plan.days:
        return 0
    primary = theme_list[0]
    theme_set = set(theme_list)

    # 城市 → 有序候选（bundle 内的主题优先顺序 + 全量库补齐，供「借点位」用）
    by_city = {c.city_name: c for c in (bundle.cities if bundle else [])}
    pools: Dict[str, List[Any]] = {}
    tag_of: Dict[str, set] = {}

    def ensure_pool(city_name: str) -> List[Any]:
        if city_name in pools:
            return pools[city_name]
        items: List[Any] = []
        ctx = by_city.get(city_name)
        if ctx:
            items.extend(ctx.attractions)
        if db is not None:
            try:
                full = knowledge_service.list_items(
                    db, city=city_name, kind="attraction", limit=200
                )
                have = {a.name for a in items}
                items.extend(x for x in full if x.name not in have)
            except Exception:
                pass
        for it in items:
            tag_of.setdefault(it.name, set(getattr(it, "tags", []) or []))
        pools[city_name] = items
        return items

    for c in (bundle.cities if bundle else []):
        for a in c.attractions:
            tag_of.setdefault(a.name, set(getattr(a, "tags", []) or []))

    day_cities = allocate_days_to_cities(len(plan.days), cities or _city_order(form))

    def norm(c: str) -> str:
        return (c or "").strip()

    def city_of_day(i: int, day) -> str:
        name = day_cities[i] if i < len(day_cities) else ""
        if name:
            return name
        for n in by_city:
            if n and n in (day.title or ""):
                return n
        return ""

    def is_theme(place: str, keys: set) -> bool:
        return bool(keys & tag_of.get(norm(place), set()))

    used = {norm(a.place) for d in plan.days for a in d.activities}
    fixed = 0

    for i, day in enumerate(plan.days):
        city_name = city_of_day(i, day)
        if not city_name:
            continue
        pool = ensure_pool(city_name)

        # 该日城市是否有契合主主题的候选；没有则退到「任一所选主题」
        matched = [a for a in pool if primary in set(getattr(a, "tags", []) or [])]
        enforce: set = {primary}
        if not matched:
            matched = [a for a in pool if theme_set & set(getattr(a, "tags", []) or [])]
            enforce = theme_set
        if not matched:
            continue

        if any(is_theme(a.place, enforce) for a in day.activities):
            continue
        if not day.activities:
            continue

        # ① 首选未用过的主题候选
        pick = next((m for m in matched if m.name not in used), None)

        # ② 没有未用的 —— 从「占了 2 个以上主题点位」的另一天借一个，
        #    并给那天补一个未用的非主题候选，保持每日都有主题点位且不重复
        if pick is None:
            for k, other in enumerate(plan.days):
                if k == i:
                    continue
                hits = [a for a in other.activities if is_theme(a.place, enforce)]
                if len(hits) < 2:
                    continue
                victim = hits[-1]
                donor_city = city_of_day(k, other)
                donor_pool = ensure_pool(donor_city) if donor_city else []
                alt = next(
                    (
                        p for p in donor_pool
                        if p.name not in used and not is_theme(p.name, enforce)
                    ),
                    None,
                )
                if alt is None:
                    continue
                v_idx = other.activities.index(victim)
                other.activities[v_idx] = Activity(
                    time=victim.time or "14:00 - 17:00",
                    place=alt.name,
                    type=victim.type or "文脉沉浸体验",
                    desc=(alt.desc or "")[:60],
                    tips=(alt.tips or "")[:40],
                )
                used.discard(norm(victim.place))
                used.add(alt.name)
                pick_name = norm(victim.place)
                print(
                    f"[行程主题] 第{k + 1}日主题点位偏多，"
                    f"已将「{pick_name}」调给第{i + 1}日，原位置补「{alt.name}」"
                )
                # 借来的点位直接填进当日
                day.activities[len(day.activities) - 1] = Activity(
                    time="14:00 - 17:00",
                    place=pick_name,
                    type="主题研习（应三晋意趣）",
                    desc="",
                    tips="",
                )
                used.add(pick_name)
                fixed += 1
                break
            else:
                # ③ 无可借之处。若主题点位本就少于该城天数，说明「每日都有主题点位」
                #    与「不重复」无法兼得 —— 宁缺勿重，交给去重阶段处理。
                days_in_city = day_cities.count(city_name)
                if _theme_capacity(pool, primary, theme_set) >= days_in_city:
                    pick = matched[0]
                else:
                    print(
                        f"[行程主题] {city_name}主题点位仅 "
                        f"{_theme_capacity(pool, primary, theme_set)} 处、不足 {days_in_city} 日，"
                        f"第{i + 1}日不再强求主题以避免重复"
                    )
                    pick = None

        if pick is not None:
            idx = len(day.activities) - 1
            old = day.activities[idx]
            day.activities[idx] = Activity(
                time=old.time or "14:00 - 17:00",
                place=pick.name,
                type="主题研习（应三晋意趣）",
                desc=(pick.desc or "")[:60],
                tips=(pick.tips or "")[:40],
            )
            used.discard(norm(old.place))
            used.add(pick.name)
            fixed += 1
            print(
                f"[行程主题] 第{i + 1}日原无「{'、'.join(sorted(enforce))}」点位，"
                f"已将「{old.place}」替换为「{pick.name}」"
            )
    return fixed


# ---------------------------------------------------------------- 生成主流程


async def _generate_with_llm(form: ItineraryForm, bundle, cities: List[str]) -> Itinerary:
    """接地生成行程。

    JSON 解析失败时追加一次「只输出 JSON」的纠正请求，而不是直接降级到兜底行程 ——
    多城行程输出较长，模型偶发夹带说明文字或截断。
    """
    prompt, _ = _build_prompt(form, bundle)
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": "你是三晋文旅行程规划专家，只输出符合要求的 JSON，且严格保证地理准确。",
        },
        {"role": "user", "content": prompt},
    ]
    content = await _chat_with_retry(messages)
    plan_dict = _extract_json(content)
    if plan_dict:
        return _coerce_plan(plan_dict, form, cities)

    # 纠正轮：把模型自己的输出带回，明确要求重新只输出 JSON
    print("[行程生成] 首轮返回无法解析为 JSON，发起一次纠正请求")
    messages.extend(
        [
            {"role": "assistant", "content": content[:2000]},
            {
                "role": "user",
                "content": (
                    "上面的回复无法被解析为 JSON。请重新输出**完整**的行程 JSON，"
                    "不要包含任何解释、前后缀或 Markdown 代码块标记，"
                    "直接以 { 开头、以 } 结尾。"
                ),
            },
        ]
    )
    retry_content = await _chat_with_retry(messages, attempts=2)
    plan_dict = _extract_json(retry_content)
    if not plan_dict:
        raise LLMError("两轮均无法解析大模型返回的 JSON")
    return _coerce_plan(plan_dict, form, cities)


async def _audit_with_llm(plan: Itinerary) -> List[Dict[str, Any]]:
    """第二道闸门：让模型以「审核员」身份复核地理归属"""
    try:
        content = await _chat_with_retry(
            [
                {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
                {"role": "user", "content": _build_audit_prompt(plan)},
            ],
            temperature=0.0,
            attempts=2,
        )
    except Exception as e:
        print(f"[行程审计] 审计调用失败，改由代码规则兜底：{e}")
        return []
    parsed = _extract_json(content) or {}
    issues = parsed.get("issues")
    return issues if isinstance(issues, list) else []


def _resolve_day_city_id(db: Session, day: DayPlan, plan_city_ids: List[str], index: int):
    matcher = knowledge_service.CityMatcher.from_db(db)
    for field in (day.title or "", day.theme or ""):
        ids = [i for i in matcher.find_ids(field) if i in plan_city_ids]
        if ids:
            return ids[0]
    if plan_city_ids:
        return plan_city_ids[index % len(plan_city_ids)]
    return None


def _normalize_food_suggestion(value: Any) -> str:
    """规整审计模型给出的替换建议。

    模型常写成「改为右玉羊肉」「建议换成朔州大烩菜」这类带前缀的整句，
    需要剥掉前后缀后才能与知识库菜名精确比对（否则会把合法建议误判为不可信）。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    # 去掉开头的动词/引导语
    text = re.sub(r"^(建议|可以|可|应|应当|建议改为|替换为|替换成|换为|换成|改为|改成|用|推荐)+", "", text)
    # 去掉「改为：xxx」「改为 xxx」残留的标点
    text = text.lstrip("：:，,。.、 　")
    # 只取第一项（模型可能写「右玉羊肉或朔州大烩菜」）
    text = re.split(r"[或/、，,；;（(]", text)[0].strip()
    return text


def _apply_audit_issues(db: Session, plan: Itinerary, raw_issues: List[Dict[str, Any]]) -> int:
    """把审计模型报告的问题按确定性规则修正（本地特产替换 / 抹去外地地名）"""
    if not raw_issues:
        return 0
    matcher = knowledge_service.CityMatcher.from_db(db)
    city_names = knowledge_service.city_name_map(db)
    contexts = knowledge_service._load_city_contexts(db)
    plan_city_ids = [
        cid for cid in (matcher.normalize_city(d) for d in plan.destinations) if cid
    ]

    fixed = 0
    used_replacements: set = set()
    for issue in raw_issues:
        if not isinstance(issue, dict):
            continue
        try:
            di = int(issue.get("day", 0)) - 1
            ai = int(issue.get("activity", 0))
        except (TypeError, ValueError):
            continue
        if not (0 <= di < len(plan.days)):
            continue
        day = plan.days[di]
        field = str(issue.get("field", ""))
        found = str(issue.get("found", "") or "")
        kind = str(issue.get("kind", ""))
        # 餐饮已上移到日级「主轴」，故 food_city_mismatch 一律按日级特产修正，
        # 不再依赖（且新格式也不再产生）activity 下标。
        is_day_food = kind == "food_city_mismatch" or field == "localSpecialties"

        day_city_id = _resolve_day_city_id(db, day, plan_city_ids, di)

        if is_day_food:
            if day_city_id is None:
                continue
            ctx = contexts.get(day_city_id)
            if not ctx or not ctx.foods:
                continue
            suggestion = _normalize_food_suggestion(issue.get("suggestion"))
            local_names = {f.name for f in ctx.foods}
            replacement = suggestion if suggestion in local_names else None
            if suggestion and replacement is None:
                # 审计模型常给出「听起来像本地」但知识库中不存在的菜名，一律不采信
                print(
                    f"[行程审计] 忽略未经知识库核实的替换建议「{suggestion}」"
                    f"（{city_names.get(day_city_id, day_city_id)}）"
                )
            if not replacement:
                replacement = knowledge_service._pick_local_food(
                    db, day_city_id, di, day.localSpecialties, used_replacements
                )
            if not replacement:
                continue
            if found and found in (day.localSpecialties or ""):
                day.localSpecialties = knowledge_service._replace_term(
                    day.localSpecialties, found, replacement
                )
            else:
                day.localSpecialties = replacement
            used_replacements.add(replacement)
            fixed += 1
            print(
                f"[行程审计] 第{di + 1}日特产「{found}」不属于"
                f"{city_names.get(day_city_id, day_city_id)}，已改为「{replacement}」"
            )
            continue

        # 以下为活动级问题，需要合法的 activity 下标
        if not (0 <= ai < len(day.activities)):
            continue
        act = day.activities[ai]
        if day_city_id is None:
            continue
        # 与校验器保持一致：活动自身点明其它已选目的地时，以活动为准
        act_city_id = day_city_id
        for f in (act.place or "", act.desc or ""):
            ids = [
                i
                for i in matcher.find_ids(f)
                if i in plan_city_ids and i != day_city_id
            ]
            if ids:
                act_city_id = ids[0]
                break
        day_city_id = act_city_id

        if kind == "foreign_city_mention" and field in ("place", "desc"):
            value = getattr(act, field) or ""
            foreign = [
                name
                for name in matcher.find_names(value)
                if matcher.normalize_city(name) not in plan_city_ids
                and name != city_names.get(day_city_id)
            ]
            if not foreign:
                continue
            cleaned = knowledge_service._clean_foreign_city_text(
                value, foreign, city_names.get(day_city_id, "")
            )
            if cleaned and cleaned != value:
                setattr(act, field, cleaned)
                fixed += 1
    return fixed


async def generate_itinerary(
    form: ItineraryForm,
    db: Optional[Session] = None,
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
    audit: Optional[bool] = None,
) -> Itinerary:
    """生成行程：缓存 → RAG 接地生成 → 审计复核 → 代码规则兜底"""
    # 先校验合理性：天数不足以为每处府邑留出至少一日时直接拒绝，
    # 避免「选 1 日游 4 城却只生成 1 城」这种静默丢弃目的地的情况。
    validate_plan_form(form)
    cities = _city_order(form)

    fingerprint = ""
    cache_key = ""
    if db is not None:
        try:
            fingerprint = knowledge_service.knowledge_fingerprint(db)
        except Exception:
            fingerprint = ""

    # ---------- 1. 缓存
    if db is not None and use_cache and not force_refresh and cache.active:
        cache_key = build_cache_key(form, fingerprint)
        cached = cache.get(db, cache_key)
        if cached:
            try:
                data = {k: v for k, v in cached.items() if not k.startswith("_")}
                plan = Itinerary(**data)
                plan.source = "cache"
                plan.cached = True
                plan.knowledge_fingerprint = fingerprint
                return _repair_structure(_dedupe_days(plan), db, form, cities)
            except Exception as e:
                print(f"[缓存] 命中但反序列化失败，改为重新生成：{e}")
                cache.invalidate(db, cache_key)

    # ---------- 2. 知识库检索（RAG）
    bundle = None
    if db is not None and settings.enable_rag:
        try:
            knowledge_service.ensure_knowledge_loaded(db)
            bundle = knowledge_service.retrieve_for_destinations(
                db, cities, form.themes, _need_by_city(form, cities)
            )
            fingerprint = bundle.fingerprint or fingerprint
        except Exception as e:
            print(f"[RAG] 检索失败，退化为无接地生成：{e}")
            bundle = None

    # ---------- 3. 生成
    source = "llm"
    if not settings.deepseek_api_key:
        plan = _build_mock_plan(db, form)
        source = "mock"
    else:
        try:
            plan = await _generate_with_llm(form, bundle, cities)
        except Exception as e:
            print(f"[行程生成] 大模型调用失败，降级到知识库兜底行程：{e}")
            plan = _build_mock_plan(db, form)
            source = "mock"

    plan = _dedupe_days(_repair_structure(plan, db, form, cities))

    # ---------- 3.4 主题兜底（代码规则）
    # 用户明确选了三晋意趣，模型可能不照做；这里确定性保证「每日至少 1 个主题点位」，
    # 并在模型把主题点位扎堆时做均衡（借给空缺的那天），避免为了主题而重复。
    if form.themes:
        _ensure_theme_coverage(plan, form, db, bundle, cities)

    # ---------- 3.5 跨日去重（代码规则）
    # 同城多日时模型会反复推荐同一批名点（实测 1 城 4 日全部重复）。
    # 放在主题兜底之后：此时主题点位已就位，去重只需清理非主题的重复，
    # 且会保留「当日唯一的主题点位」。
    _dedupe_across_days(plan, form, db, bundle, cities)

    # ---------- 4. 准确性审计（两个模型角色互相制衡）
    fix_count = 0
    if source == "llm":
        do_audit = settings.strict_geo_validation if audit is None else audit
        if do_audit:
            raw_issues = await _audit_with_llm(plan)
            if raw_issues and db is not None and settings.auto_fix_geo:
                fix_count = _apply_audit_issues(db, plan, raw_issues)

    # ---------- 5. 代码规则兜底（确定性，不依赖模型）
    if db is not None:
        try:
            report = knowledge_service.validate_plan_geo(db, plan)
            if report.issues:
                kinds: Dict[str, int] = {}
                for i in report.issues:
                    kinds[i.kind] = kinds.get(i.kind, 0) + 1
                print(
                    f"[地理校验] 检出 {len(report.issues)} 处问题"
                    f"（自动修正 {report.fixed_count} 处）：{kinds}"
                )
                fix_count += report.fixed_count
        except Exception as e:
            print(f"[地理校验] 校验失败（不影响返回）：{e}")

    plan.source = source
    plan.cached = False
    plan.knowledge_fingerprint = fingerprint
    plan.geo_fixed_count = fix_count

    # ---------- 6. 写回缓存
    if db is not None and cache.active and use_cache and plan.days:
        if not cache_key:
            cache_key = build_cache_key(form, fingerprint)
        cache.set(db, cache_key, plan, source=source, provider=settings.deepseek_model)
    return plan


# ---------------------------------------------------------------- 持久化


def _coerce_days(days_raw: Any) -> List[DayPlan]:
    """把数据库中的 days 字段解析为 DayPlan 列表，兼容三种历史格式：

    1. 规范格式（当前）：`[{day}, ...]`
    2. 字符串格式：`'{"title":...}'` —— 旧代码把整份行程 JSON 误存进 days 列
    3. 逐字符数组：`['{', '"', ...]` —— 旧代码 `model_dump_json()` 返回字符串，
       被当作可迭代对象写成了字符数组

    任一格式解析失败都不应让接口 500，最差情况返回空列表。
    """
    if isinstance(days_raw, str):
        # 可能是 JSON 字符串，也可能是逐字符拼接的内容
        try:
            parsed = json.loads(days_raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, (list, dict)):
            return _coerce_days(parsed)
        # 拼接逐字符数组
        return _coerce_days(list(days_raw))

    if isinstance(days_raw, dict):
        # 旧格式：整个行程对象 —— 从中取 days 字段
        nested = days_raw.get("days")
        return _coerce_days(nested) if nested is not None else []

    if not isinstance(days_raw, list):
        return []

    # 逐字符数组 → 先还原字符串
    if days_raw and all(isinstance(x, str) and len(x) <= 1 for x in days_raw):
        joined = "".join(days_raw)
        try:
            parsed = json.loads(joined)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(parsed, (list, dict)):
            return _coerce_days(parsed)
        return []

    out: List[DayPlan] = []
    for d in days_raw:
        if not isinstance(d, dict):
            continue
        try:
            out.append(DayPlan(**d))
        except Exception as e:  # 单日数据损坏不应影响整份行程
            print(f"[行程读取] 跳过损坏的日数据：{e}")
    return out


def _to_saved(model: ItineraryModel) -> SavedItinerary:
    try:
        days_raw = json.loads(model.days)
    except (json.JSONDecodeError, TypeError):
        days_raw = []
    try:
        destinations = json.loads(model.destinations)
    except (json.JSONDecodeError, TypeError):
        destinations = []
    try:
        highlights = json.loads(model.highlights)
    except (json.JSONDecodeError, TypeError):
        highlights = []
    return SavedItinerary(
        id=model.id,
        savedAt=(
            model.saved_at.strftime("%Y-%m-%d %H:%M:%S")
            if model.saved_at
            else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ),
        title=model.title,
        destinations=destinations if isinstance(destinations, list) else [],
        highlights=highlights if isinstance(highlights, list) else [],
        rationale=getattr(model, "rationale", "") or "",
        days=_coerce_days(days_raw),
    )


def save_itinerary(db: Session, plan: Itinerary, user_id: Optional[int] = None) -> SavedItinerary:
    """保存行程到数据库（user_id 为 None 时表示匿名保存，兼容旧逻辑）"""
    model = ItineraryModel(
        title=plan.title,
        destinations=json.dumps(plan.destinations, ensure_ascii=False),
        highlights=json.dumps(plan.highlights, ensure_ascii=False),
        rationale=plan.rationale or "",
        days=json.dumps([d.model_dump() for d in plan.days], ensure_ascii=False),
        user_id=user_id,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return _to_saved(model)


def list_itineraries(db: Session, user_id: Optional[int] = None) -> List[SavedItinerary]:
    """列出已保存行程（按保存时间倒序）。
    - user_id 不为 None 时仅返回该用户的行程
    - user_id 为 None 时返回全部（兼容旧逻辑/匿名）
    """
    query = db.query(ItineraryModel)
    if user_id is not None:
        query = query.filter(ItineraryModel.user_id == user_id)
    rows = query.order_by(ItineraryModel.saved_at.desc()).all()
    return [_to_saved(r) for r in rows]


def delete_itinerary(db: Session, itinerary_id: int, user_id: Optional[int] = None) -> bool:
    """删除指定行程。
    - user_id 不为 None 时，只能删除自己的行程（越权删除返回 False）
    """
    query = db.query(ItineraryModel).filter(ItineraryModel.id == itinerary_id)
    if user_id is not None:
        query = query.filter(ItineraryModel.user_id == user_id)
    row = query.first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True
