"""知识库服务：装载、检索与地理校验

分层：
1. 装载    —— 从 `data/knowledge_seed.json` 导入 `kb_cities` / `kb_items`，并重建 FTS5 索引
2. 检索    —— 按用户所选府邑做结构化召回；FTS5 提供全文检索（供知识库页面与跨城查询）
3. 校验    —— 检测并修正「朔州吃平遥牛肉」这类地理错配，这是提升准确性的关键闸门
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.config import knowledge_seed_path, settings
from app.models.knowledge import CityModel, KnowledgeItemModel
from app.schemas.knowledge import (
    CityContext,
    CityInfo,
    GeoIssue,
    GeoReport,
    KnowledgeItem,
    KnowledgeStats,
    ReloadResult,
    RetrievalBundle,
)

FTS_TABLE = "kb_items_fts"
KIND_ATTRACTION = "attraction"
KIND_FOOD = "food"

# ---------------------------------------------------------------- 工具


def _loads(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _dumps(v: Iterable[str]) -> str:
    return json.dumps(list(v), ensure_ascii=False)


def _row_to_item(row: KnowledgeItemModel, city_names: Dict[str, str]) -> KnowledgeItem:
    return KnowledgeItem(
        id=row.id,
        kind=row.kind,
        name=row.name,
        aliases=_loads(row.aliases),
        city=row.city,
        city_name=row.city_name or city_names.get(row.city, row.city),
        traditional_in=_loads(row.traditional_in),
        traditional_in_names=[city_names.get(c, c) for c in _loads(row.traditional_in)],
        item_type=row.item_type or "",
        level=row.level,
        tags=_loads(row.tags),
        desc=row.desc or "",
        tips=row.tips or "",
        taste=row.taste,
        ingredients=_loads(row.ingredients),
        confidence=row.confidence or "high",
        source=row.source or "seed",
    )


def city_name_map(db: Session) -> Dict[str, str]:
    return {c.id: c.name for c in db.scalars(select(CityModel)).all()}


# ---------------------------------------------------------------- 城市名匹配


class CityMatcher:
    """城市别名匹配器：最长匹配优先，避免「晋中」被误判为「晋城」等"""

    def __init__(self, entries: Sequence[Tuple[str, str, str]]):
        """entries: (alias, city_id, city_name)，按别名长度降序传入"""
        self.entries = sorted(entries, key=lambda e: -len(e[0]))

    @classmethod
    def from_db(cls, db: Session) -> "CityMatcher":
        entries: List[Tuple[str, str, str]] = []
        for c in db.scalars(select(CityModel)).all():
            entries.append((c.name, c.id, c.name))
            for a in _loads(c.aliases):
                if a and a != c.name:
                    entries.append((a, c.id, c.name))
        # 去重（同一别名同一城市只留一条）
        seen = set()
        uniq = []
        for e in entries:
            k = (e[0], e[1])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(e)
        return cls(uniq)

    def find_all(self, text_value: str) -> List[Tuple[int, int, str, str, str]]:
        """返回所有命中：[(start, end, city_id, city_name, alias)]，按位置排序"""
        if not text_value:
            return []
        hits: List[Tuple[int, int, str, str, str]] = []
        consumed = [False] * len(text_value)
        for alias, cid, cname in self.entries:
            if not alias:
                continue
            start = 0
            while True:
                i = text_value.find(alias, start)
                if i == -1:
                    break
                end = i + len(alias)
                if not any(consumed[i:end]):
                    for k in range(i, end):
                        consumed[k] = True
                    hits.append((i, end, cid, cname, alias))
                start = i + 1
        hits.sort(key=lambda h: h[0])
        return hits

    def find_ids(self, text_value: str) -> List[str]:
        """去重后的城市 id，按出现顺序"""
        out: List[str] = []
        for _, _, cid, _, _ in self.find_all(text_value):
            if cid not in out:
                out.append(cid)
        return out

    def find_names(self, text_value: str) -> List[str]:
        out: List[str] = []
        for _, _, _, cname, _ in self.find_all(text_value):
            if cname not in out:
                out.append(cname)
        return out

    def normalize_city(self, name: str) -> Optional[str]:
        """把用户输入的府邑名归一到知识库 city id"""
        name = (name or "").strip()
        if not name:
            return None
        for alias, cid, _ in self.entries:
            if alias == name:
                return cid
        for alias, cid, _ in self.entries:
            if len(alias) >= 2 and (alias in name or name in alias):
                return cid
        ids = self.find_ids(name)
        return ids[0] if ids else None


# ---------------------------------------------------------------- 装载


def _clear_kb(db: Session) -> None:
    db.execute(delete(KnowledgeItemModel))
    db.execute(delete(CityModel))
    db.commit()


def _ensure_fts(db: Session) -> bool:
    """建立 FTS5 虚拟表；不支持时返回 False（检索会退化为 LIKE）"""
    try:
        db.execute(
            text(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
                "name, aliases, desc, tips, city_name, tags, "
                "item_id UNINDEXED, kind UNINDEXED, tokenize='unicode61')"
            )
        )
        db.commit()
        return True
    except Exception as e:  # pragma: no cover - 取决于 sqlite 编译选项
        db.rollback()
        print(f"[知识库] FTS5 不可用，检索退化为关键词匹配：{e}")
        return False


def _rebuild_fts(db: Session) -> None:
    _ensure_fts(db)
    try:
        db.execute(text(f"DELETE FROM {FTS_TABLE}"))
    except Exception:
        db.rollback()
        return
    rows = db.execute(
        text(
            "SELECT id, kind, name, aliases, desc, tips, city_name, tags "
            f"FROM kb_items"
        )
    ).all()
    for r in rows:
        db.execute(
            text(
                f"INSERT INTO {FTS_TABLE} (name, aliases, desc, tips, city_name, tags, item_id, kind) "
                "VALUES (:name, :aliases, :desc, :tips, :city_name, :tags, :item_id, :kind)"
            ),
            {
                "name": r[2],
                "aliases": " ".join(_loads(r[3])),
                "desc": r[4] or "",
                "tips": r[5] or "",
                "city_name": r[6] or "",
                "tags": " ".join(_loads(r[7])),
                "item_id": r[0],
                "kind": r[1],
            },
        )
    db.commit()


def load_seed(db: Session, seed_file: Optional[Path] = None) -> ReloadResult:
    """从种子 JSON 导入知识库。

    - `source=seed` 的条目按文件内容全量重建
    - `source=manual` 的条目（通过 API 手工新增）会被保留，不会被种子覆盖
    """
    path = seed_file or knowledge_seed_path()
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    keep_manual = db.scalars(
        select(KnowledgeItemModel).where(KnowledgeItemModel.source == "manual")
    ).all()
    manual_rows = [
        {
            "kind": m.kind,
            "name": m.name,
            "aliases": m.aliases,
            "city": m.city,
            "city_name": m.city_name,
            "traditional_in": m.traditional_in,
            "item_type": m.item_type,
            "level": m.level,
            "tags": m.tags,
            "desc": m.desc,
            "tips": m.tips,
            "taste": m.taste,
            "ingredients": m.ingredients,
            "confidence": m.confidence,
        }
        for m in keep_manual
    ]

    _clear_kb(db)

    for i, c in enumerate(data.get("cities", [])):
        db.add(
            CityModel(
                id=c["id"],
                name=c["name"],
                aliases=_dumps(c.get("aliases", [])),
                theme_tags=_dumps(c.get("theme_tags", [])),
                intro=c.get("intro", ""),
                sort_order=i,
            )
        )
    db.commit()

    for a in data.get("attractions", []):
        db.add(
            KnowledgeItemModel(
                kind=KIND_ATTRACTION,
                name=a["name"],
                aliases=_dumps(a.get("aliases", [])),
                city=a["city"],
                city_name=a.get("city_name", ""),
                traditional_in="[]",
                item_type=a.get("type", ""),
                level=a.get("level"),
                tags=_dumps(a.get("tags", [])),
                desc=a.get("desc", ""),
                tips=a.get("tips", ""),
                taste=None,
                ingredients="[]",
                confidence=a.get("confidence", "high"),
                source="seed",
            )
        )
    for f in data.get("foods", []):
        db.add(
            KnowledgeItemModel(
                kind=KIND_FOOD,
                name=f["name"],
                aliases=_dumps(f.get("aliases", [])),
                city=f["city"],
                city_name=f.get("city_name", ""),
                traditional_in=_dumps(f.get("traditional_in", [])),
                item_type=f.get("type", ""),
                level=None,
                tags=_dumps(f.get("tags", []) or []),
                desc=f.get("desc", ""),
                tips=f.get("tips", ""),
                taste=f.get("taste"),
                ingredients=_dumps(f.get("ingredients", [])),
                confidence=f.get("confidence", "high"),
                source="seed",
            )
        )
    db.commit()

    # 回填手工条目（保留人工添加的内容）
    for m in manual_rows:
        db.add(KnowledgeItemModel(**m))
    db.commit()

    _rebuild_fts(db)
    invalidate_caches()
    stats = get_stats(db)
    return ReloadResult(
        cities=stats.cities,
        attractions=stats.attractions,
        foods=stats.foods,
        fingerprint=stats.knowledge_fingerprint,
        seed_file=str(path),
    )


def ensure_knowledge_loaded(db: Session) -> None:
    """启动/首次访问时确保知识库已导入"""
    count = db.scalar(select(func.count()).select_from(KnowledgeItemModel)) or 0
    if count == 0:
        try:
            load_seed(db)
            print("[知识库] 已从种子文件导入")
        except Exception as e:  # pragma: no cover
            print(f"[知识库] 导入失败：{e}")


# ---------------------------------------------------------------- 指纹与缓存

_FINGERPRINT: Optional[str] = None
_CITY_CONTEXTS: Optional[Dict[str, CityContext]] = None
_FOOD_INDEX: Optional[Dict[str, object]] = None


def invalidate_caches() -> None:
    global _FINGERPRINT, _CITY_CONTEXTS, _FOOD_INDEX
    _FINGERPRINT = None
    _CITY_CONTEXTS = None
    _FOOD_INDEX = None


def knowledge_fingerprint(db: Session) -> str:
    """知识库内容指纹：行程缓存据此自动失效"""
    global _FINGERPRINT
    if _FINGERPRINT is not None:
        return _FINGERPRINT
    rows = db.execute(
        text("SELECT COUNT(*), COALESCE(SUM(LENGTH(name) + LENGTH(desc)), 0) FROM kb_items")
    ).one()
    raw = f"{rows[0]}:{rows[1]}"
    import hashlib

    _FINGERPRINT = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return _FINGERPRINT


# ---------------------------------------------------------------- 查询 API


def get_cities(db: Session) -> List[CityInfo]:
    city_names = city_name_map(db)
    agg = db.execute(
        text("SELECT city, kind, COUNT(*) as n FROM kb_items GROUP BY city, kind")
    ).all()
    table: Dict[str, Dict[str, int]] = {}
    for city, kind, n in agg:
        table.setdefault(city, {})[kind] = n
    out: List[CityInfo] = []
    for c in db.scalars(select(CityModel).order_by(CityModel.sort_order)).all():
        out.append(
            CityInfo(
                id=c.id,
                name=city_names.get(c.id, c.name),
                aliases=_loads(c.aliases),
                theme_tags=_loads(c.theme_tags),
                intro=c.intro or "",
                attraction_count=table.get(c.id, {}).get(KIND_ATTRACTION, 0),
                food_count=table.get(c.id, {}).get(KIND_FOOD, 0),
            )
        )
    return out


def get_stats(db: Session) -> KnowledgeStats:
    rows = db.execute(text("SELECT kind, COUNT(*) FROM kb_items GROUP BY kind")).all()
    by_kind = {k: n for k, n in rows}
    per_city = db.execute(
        text(
            "SELECT city, city_name, "
            "SUM(CASE WHEN kind='attraction' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN kind='food' THEN 1 ELSE 0 END) "
            "FROM kb_items GROUP BY city, city_name ORDER BY city"
        )
    ).all()
    return KnowledgeStats(
        cities=db.scalar(select(func.count()).select_from(CityModel)) or 0,
        attractions=by_kind.get(KIND_ATTRACTION, 0),
        foods=by_kind.get(KIND_FOOD, 0),
        total=sum(by_kind.values()),
        by_city=[
            {
                "city": r[0],
                "city_name": r[1],
                "attractions": r[2] or 0,
                "foods": r[3] or 0,
            }
            for r in per_city
        ],
        knowledge_fingerprint=knowledge_fingerprint(db),
    )


def _fts_query(db: Session, q: str, limit: int) -> List[int]:
    """FTS5 前缀检索，返回 item id 列表（按相关度）"""
    tokens = [t for t in re.split(r"[\s,，、。；;:：/|]+", q.strip()) if t]
    if not tokens:
        return []
    try:
        match = " AND ".join(f'"{t}"*' for t in tokens)
        rows = db.execute(
            text(
                f"SELECT item_id FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :m "
                "ORDER BY rank LIMIT :lim"
            ),
            {"m": match, "lim": limit},
        ).all()
        return [r[0] for r in rows]
    except Exception:
        db.rollback()
        return []


def _like_query(db: Session, q: str, limit: int) -> List[int]:
    like = f"%{q.strip()}%"
    rows = db.execute(
        text(
            "SELECT id FROM kb_items WHERE name LIKE :q OR aliases LIKE :q "
            "OR desc LIKE :q OR city_name LIKE :q LIMIT :lim"
        ),
        {"q": like, "lim": limit},
    ).all()
    return [r[0] for r in rows]


def search_items(
    db: Session,
    q: str,
    kind: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = 20,
) -> List[KnowledgeItem]:
    """全文检索知识条目：优先 FTS5，结果不足时用 LIKE 兜底"""
    ids = _fts_query(db, q, limit * 2)
    if len(ids) < limit:
        for i in _like_query(db, q, limit * 2):
            if i not in ids:
                ids.append(i)
    if not ids:
        return []
    city_names = city_name_map(db)
    stmt = select(KnowledgeItemModel).where(KnowledgeItemModel.id.in_(ids))
    if kind:
        stmt = stmt.where(KnowledgeItemModel.kind == kind)
    if city:
        matcher = CityMatcher.from_db(db)
        cid = matcher.normalize_city(city) or city
        stmt = stmt.where(KnowledgeItemModel.city == cid)
    rows = {r.id: r for r in db.scalars(stmt).all()}
    return [_row_to_item(rows[i], city_names) for i in ids if i in rows][:limit]


def list_items(
    db: Session,
    kind: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[KnowledgeItem]:
    city_names = city_name_map(db)
    stmt = select(KnowledgeItemModel)
    if kind:
        stmt = stmt.where(KnowledgeItemModel.kind == kind)
    if city:
        matcher = CityMatcher.from_db(db)
        cid = matcher.normalize_city(city) or city
        stmt = stmt.where(KnowledgeItemModel.city == cid)
    stmt = stmt.order_by(KnowledgeItemModel.city, KnowledgeItemModel.kind, KnowledgeItemModel.id)
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    return [_row_to_item(r, city_names) for r in rows]


def get_item(db: Session, item_id: int) -> Optional[KnowledgeItem]:
    row = db.get(KnowledgeItemModel, item_id)
    if not row:
        return None
    return _row_to_item(row, city_name_map(db))


def add_item(db: Session, item: KnowledgeItem) -> KnowledgeItem:
    row = KnowledgeItemModel(
        kind=item.kind,
        name=item.name,
        aliases=_dumps(item.aliases),
        city=item.city,
        city_name=item.city_name,
        traditional_in=_dumps(item.traditional_in),
        item_type=item.item_type,
        level=item.level,
        tags=_dumps(item.tags),
        desc=item.desc,
        tips=item.tips,
        taste=item.taste,
        ingredients=_dumps(item.ingredients),
        confidence=item.confidence,
        source="manual",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _rebuild_fts(db)
    invalidate_caches()
    return _row_to_item(row, city_name_map(db))


def delete_item(db: Session, item_id: int) -> bool:
    row = db.get(KnowledgeItemModel, item_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    _rebuild_fts(db)
    invalidate_caches()
    return True


# ---------------------------------------------------------------- RAG 检索


def _load_city_contexts(db: Session) -> Dict[str, CityContext]:
    global _CITY_CONTEXTS
    if _CITY_CONTEXTS is not None:
        return _CITY_CONTEXTS
    city_names = city_name_map(db)
    contexts: Dict[str, CityContext] = {}
    for c in db.scalars(select(CityModel).order_by(CityModel.sort_order)).all():
        contexts[c.id] = CityContext(
            city_id=c.id, city_name=c.name, intro=c.intro or ""
        )
    for r in db.scalars(select(KnowledgeItemModel)).all():
        ctx = contexts.get(r.city)
        if not ctx:
            continue
        item = _row_to_item(r, city_names)
        if r.kind == KIND_ATTRACTION:
            ctx.attractions.append(item)
        elif r.kind == KIND_FOOD:
            ctx.foods.append(item)
    _CITY_CONTEXTS = contexts
    return contexts


def _prioritize_by_theme(
    items: List[KnowledgeItem], themes: Optional[Sequence[str]], limit: int
) -> List[KnowledgeItem]:
    """按主题优先截断：契合主题的条目**不会被检索上限截掉**。

    背景：单城景点数可能远超检索上限（如太原 12 个、上限 6）。
    旧实现直接 `ctx.attractions[:limit]` 按 id 取前 N 个，
    导致后加入的条目（博物馆、红色旧址等）永远进不了提示词 ——
    于是「博物馆藏」主题的线路一个博物馆都排不出来。

    **主次之分很重要**：`themes[0]` 视为主主题（各条线路的定义性主题都放首位），
    优先占名额；其余为次要偏好。否则「博物馆藏+古建营造」这种组合里，
    几乎每个古建都算命中，主主题反而被挤掉。
    """
    theme_list = [t for t in (themes or []) if t]
    if not theme_list:
        return items[:limit]

    primary = theme_list[0]
    theme_set = set(theme_list)

    matched = [i for i in items if primary in set(i.tags or [])]
    secondary = [
        i for i in items
        if i not in matched and (theme_set & set(i.tags or []))
    ]
    others = [i for i in items if i not in matched and i not in secondary]

    # 主主题条目优先占位，但给非主题条目留至少 1 个名额，避免视野过窄
    reserve_for_others = 1 if (others and matched) else 0
    picked = matched[: max(0, limit - reserve_for_others)]
    for pool in (secondary, others):
        if len(picked) >= limit:
            break
        picked += pool[: limit - len(picked)]
    return picked[:limit]


def retrieve_for_destinations(
    db: Session,
    destinations: Sequence[str],
    themes: Optional[Sequence[str]] = None,
    need_by_city: Optional[Dict[str, int]] = None,
) -> RetrievalBundle:
    """按所选府邑召回知识上下文（自建 RAG 的结构化召回层）。

    - 匹配成功：返回该城市的景点与特产
    - 传入 `themes` 时**主题优先**，保证契合主题的条目不被检索上限截掉
    - 传入 `need_by_city` 时按需扩容：同城多日需要更多候选，
      否则「1 城 4 日」这类行程只能反复用同一批点位（实测跨日重复严重）
    - 匹配失败：记入 unmatched_destinations，并按原始字符串做一次全文检索兜底
    """
    matcher = CityMatcher.from_db(db)
    contexts = _load_city_contexts(db)
    fingerprint = knowledge_fingerprint(db)

    matched_ids: List[str] = []
    unmatched: List[str] = []
    for d in destinations:
        cid = matcher.normalize_city(d)
        if cid and cid in contexts:
            if cid not in matched_ids:
                matched_ids.append(cid)
        else:
            unmatched.append(d)

    result: List[CityContext] = []
    for cid in matched_ids:
        ctx = contexts[cid]
        limit = settings.retrieval_max_attractions
        need = (need_by_city or {}).get(ctx.city_name)
        if need:
            # 不超过该城实际条目数，也不低于默认上限
            limit = max(limit, min(len(ctx.attractions), int(need)))
        result.append(
            CityContext(
                city_id=ctx.city_id,
                city_name=ctx.city_name,
                intro=ctx.intro,
                attractions=_prioritize_by_theme(ctx.attractions, themes, limit),
                foods=ctx.foods[: settings.retrieval_max_foods],
            )
        )

    # 未匹配地名：用全文检索兜底，尽力给出可用素材
    for raw in unmatched:
        hits = search_items(db, raw, limit=4)
        if not hits:
            continue
        for h in hits:
            ctx = contexts.get(h.city)
            if not ctx:
                continue
            if any(c.city_id == ctx.city_id for c in result):
                continue
            result.append(
                CityContext(
                    city_id=ctx.city_id,
                    city_name=ctx.city_name,
                    intro=ctx.intro,
                    attractions=[i for i in ctx.attractions if i.id == h.id]
                    or ctx.attractions[:2],
                    foods=ctx.foods[:4],
                )
            )

    return RetrievalBundle(
        cities=result,
        unmatched_destinations=unmatched,
        fingerprint=fingerprint,
        used_rag=True,
    )


def build_prompt_context(bundle: RetrievalBundle) -> str:
    """把召回结果渲染为提示词片段（含可引用编号，便于模型引用与追溯）"""
    if not bundle.cities:
        return ""
    lines: List[str] = []
    for ctx in bundle.cities:
        lines.append(f"### {ctx.city_name}")
        if ctx.intro:
            lines.append(f"- 概况：{ctx.intro}")
        if ctx.attractions:
            lines.append("- 可用景点（只能从中选择）：")
            for a in ctx.attractions:
                meta = "／".join(x for x in [a.item_type, a.level or ""] if x)
                lines.append(f"  · {a.name}（{meta}）：{a.desc}")
        if ctx.foods:
            lines.append("- 本地特产／餐食（只能从中选择）：")
            for f in ctx.foods:
                share = (
                    f"；同源地区：{'、'.join(f.traditional_in_names)}"
                    if f.traditional_in_names
                    else "；仅限本地"
                )
                lines.append(f"  · {f.name}（{f.item_type}，{f.taste or '-'}）：{f.desc}{share}")
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- 地理校验

# 允许出现在任意城市的通用佐食（调味/主食，无地标属性）
NEUTRAL_FOOD_TERMS = (
    "老陈醋",
    "陈醋",
    "醋",
    "面食",
    "小米",
    "黄米",
    "莜面",
    "烧饼",
    "馒头",
    "米饭",
    "清茶",
)


class FoodIndex:
    """特产索引：名称/别名 → 归属城市集合，用于错配检测"""

    def __init__(self, entries: Sequence[Tuple[str, str, List[str], str]]):
        """entries: (term, term_city, allowed_city_ids, canonical_name)，按词长降序"""
        self.entries = sorted(entries, key=lambda e: -len(e[0]))

    @classmethod
    def from_db(cls, db: Session) -> "FoodIndex":
        entries: List[Tuple[str, str, List[str], str]] = []
        for r in db.scalars(
            select(KnowledgeItemModel).where(KnowledgeItemModel.kind == KIND_FOOD)
        ).all():
            allowed = [r.city] + _loads(r.traditional_in)
            terms = [r.name] + _loads(r.aliases)
            for t in terms:
                if not t or len(t) < 2:
                    continue
                entries.append((t, r.city, allowed, r.name))
        return cls(entries)

    def find_all(self, value: str) -> List[Tuple[int, int, str, List[str], str]]:
        """返回 [(start, end, term, allowed_city_ids, canonical_name)]"""
        if not value:
            return []
        hits: List[Tuple[int, int, str, List[str], str]] = []
        consumed = [False] * len(value)
        for term, term_city, allowed, canonical in self.entries:
            start = 0
            while True:
                i = value.find(term, start)
                if i == -1:
                    break
                end = i + len(term)
                if not any(consumed[i:end]):
                    for k in range(i, end):
                        consumed[k] = True
                    hits.append((i, end, term, allowed, canonical))
                start = i + 1
        hits.sort(key=lambda h: h[0])
        return hits


def _food_index(db: Session) -> FoodIndex:
    global _FOOD_INDEX
    if _FOOD_INDEX is None:
        _FOOD_INDEX = FoodIndex.from_db(db)  # type: ignore[assignment]
    return _FOOD_INDEX  # type: ignore[return-value]


def validate_plan_geo(db: Session, plan, fix: Optional[bool] = None) -> GeoReport:
    """校验整份行程的地理准确性，并（可选）自动修正。

    检测三类问题：
    1. food_city_mismatch     —— 餐食里出现不属于当日城市的特产（平遥牛肉出现在朔州）
    2. foreign_city_mention   —— 点位/简介里提到用户未选择的外地地名
    3. unknown_food_city      —— 餐食提到知识库中不存在的特产（无法判定归属，仅提示）
    """
    if not settings.strict_geo_validation:
        return GeoReport(ok=True)
    do_fix = settings.auto_fix_geo if fix is None else fix

    matcher = CityMatcher.from_db(db)
    food_idx = _food_index(db)
    city_names = city_name_map(db)
    allowed_ids = [c.city_id for c in _load_city_contexts(db).values()]
    plan_city_ids = [
        cid for cid in (matcher.normalize_city(d) for d in plan.destinations) if cid
    ]

    issues: List[GeoIssue] = []
    checked_days = 0
    checked_activities = 0

    for di, day in enumerate(plan.days):
        checked_days += 1
        # 当日归属城市：优先从 title/theme 判定，其次按序轮转 destinations
        day_city_id = None
        for field in (day.title or "", day.theme or ""):
            ids = [i for i in matcher.find_ids(field) if i in plan_city_ids]
            if ids:
                day_city_id = ids[0]
                break
        if day_city_id is None and plan_city_ids:
            day_city_id = plan_city_ids[di % len(plan_city_ids)]
        if day_city_id is None:
            continue
        day_city_name = city_names.get(day_city_id, day_city_id)

        for ai, act in enumerate(day.activities):
            checked_activities += 1

            # 活动级城市：若本活动自身文本已点明某个「已选目的地」城市，
            # 则以它为准（模型可能把跨城连线写在同一天里，不应误报）
            act_city_id = day_city_id
            for field in (act.place or "", act.desc or ""):
                ids = [
                    i
                    for i in matcher.find_ids(field)
                    if i in plan_city_ids and i != day_city_id
                ]
                if ids:
                    act_city_id = ids[0]
                    break
            act_city_name = city_names.get(act_city_id, day_city_name)

            # --- 1. 餐食地理错配
            food_text = act.foodPairing or ""
            fix_salt = 0
            used_replacements: set = set()
            for start, end, term, allowed, canonical in food_idx.find_all(food_text):
                if term in NEUTRAL_FOOD_TERMS:
                    continue
                if act_city_id in allowed:
                    continue
                # 前缀感知：吸收「应县凉粉」这类产地前缀
                new_start, full_term = _expand_food_span(
                    food_text, start, term, matcher, act_city_id
                )
                if new_start == -1:
                    # 前缀地名属于当日城市 → 泛化别名误报，跳过
                    continue
                if new_start != start:
                    term = full_term
                found_names = [city_names.get(c, c) for c in allowed]
                replacement = None
                if do_fix:
                    replacement = _pick_local_food(
                        db, act_city_id, ai + fix_salt, food_text, used_replacements
                    )
                    if replacement:
                        act.foodPairing = _replace_term(food_text, term, replacement)
                        if act.foodPairing == food_text:
                            act.foodPairing = _replace_term(food_text, canonical, replacement)
                        food_text = act.foodPairing
                        used_replacements.add(replacement)
                        fix_salt += 1
                issues.append(
                    GeoIssue(
                        scope="activity",
                        day_index=di,
                        activity_index=ai,
                        field="foodPairing",
                        kind="food_city_mismatch",
                        detail=(
                            f"第{di + 1}日（{act_city_name}）餐食出现「{canonical}」，"
                            f"其正宗产地为 {'、'.join(found_names)}"
                        ),
                        found=canonical,
                        expected_city=act_city_name,
                        found_cities=found_names,
                        replacement=replacement,
                        fixed=bool(replacement),
                    )
                )

            # --- 2. 外地地名
            for field_name in ("place", "desc"):
                value = getattr(act, field_name) or ""
                foreign = [
                    n
                    for n in matcher.find_names(value)
                    if n not in (act_city_name,)
                    and matcher.normalize_city(n) not in plan_city_ids
                ]
                if not foreign:
                    continue
                cleaned_text = value
                if do_fix:
                    # 确定性修正：抹去外地地名并修复残缺的方位短语
                    cleaned_text = _clean_foreign_city_text(
                        value, foreign, act_city_name
                    )
                    if cleaned_text and cleaned_text != value:
                        setattr(act, field_name, cleaned_text)
                issues.append(
                    GeoIssue(
                        scope="activity",
                        day_index=di,
                        activity_index=ai,
                        field=field_name,
                        kind="foreign_city_mention",
                        detail=(
                            f"第{di + 1}日（{act_city_name}）的"
                            f"{'点位' if field_name == 'place' else '简介'}提到未选择的地名："
                            f"{'、'.join(foreign)}"
                        ),
                        found="、".join(foreign),
                        expected_city=act_city_name,
                        found_cities=foreign,
                        fixed=bool(do_fix and cleaned_text != value),
                    )
                )

    fixed = sum(1 for i in issues if i.fixed)
    return GeoReport(
        checked_days=checked_days,
        checked_activities=checked_activities,
        issues=issues,
        fixed_count=fixed,
        ok=len(issues) == 0,
    )


def _replace_term(value: str, term: str, replacement: str) -> str:
    """把文本中的错配特产替换为本地特产（首次出现处替换）"""
    idx = value.find(term)
    if idx == -1:
        return value
    return f"{value[:idx]}{replacement}{value[idx + len(term):]}"


def _expand_food_span(
    value: str, start: int, term: str, matcher: "CityMatcher", day_city_id: Optional[str]
) -> Tuple[int, str]:
    """向左扩张匹配范围，吸收特产的「归属地前缀」。

    中文菜名常带产地前缀（应县凉粉、壶关羊汤、平遥牛肉）。别名表里可能同时存在
    泛化别名（如「凉粉」），若只按泛化别名切分，会切在词中间并留下残片。

    返回 (吸收前缀后的起始位置, 含前缀的完整片段)，若判定为误报则返回 (-1, term)。

    例：文本「应县凉粉」，别名命中「凉粉」→ start=2；
        若前面是地名且不属于当日城市，则扩张为「应县凉粉」；
        若前面是当日城市的地名，说明首尾同属当日城市，属泛化别名误报。
    """
    if start <= 0:
        return start, term
    prefix = value[:start]
    for alias, cid, cname in matcher.entries:
        if not prefix.endswith(alias):
            continue
        if cid == day_city_id:
            return -1, term
        return start - len(alias), alias + term
    return start, term


# 「位于」「地处」等方位词后直接接标点，说明地名被抹掉后句子残缺
_EMPTY_LOCATIVE_RE = re.compile(r"(位于|地处|坐落于|毗邻|隶属|属于)\s*(?=[，,。；;、]|$)")


def _clean_foreign_city_text(value: str, foreign: List[str], replacement_city: str) -> str:
    """抹去文本中的外地地名，并修复因此残缺的方位短语"""
    cleaned = value
    for name in foreign:
        if not name or name not in cleaned:
            continue
        # 「位于晋中」→「位于朔州」，保留句子通顺
        if re.search(rf"(位于|地处|坐落于|毗邻|隶属|属于){re.escape(name)}", cleaned):
            cleaned = re.sub(
                rf"(位于|地处|坐落于|毗邻|隶属|属于){re.escape(name)}",
                rf"\g<1>{replacement_city}",
                cleaned,
            )
        else:
            cleaned = cleaned.replace(name, "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    # 「位于，」这类残缺补上本地名
    if _EMPTY_LOCATIVE_RE.search(cleaned):
        cleaned = _EMPTY_LOCATIVE_RE.sub(rf"\g<1>{replacement_city}", cleaned)
    return cleaned.strip(" ·，,、。")


def _pick_local_food(
    db: Session,
    city_id: str,
    salt: int,
    avoid_text: str,
    avoid_names: Optional[set] = None,
) -> Optional[str]:
    """为指定城市挑一个本地特产作为替换项。

    优先选择尚未在文本中出现的特产，避免替换后与已有内容重复。
    """
    ctx = _load_city_contexts(db).get(city_id)
    if not ctx or not ctx.foods:
        return None
    names = [f.name for f in ctx.foods]
    avoid_names = set(avoid_names or set())
    # 同时避开文本里已经出现过的本城特产，避免替换后堆叠重复
    avoid_names |= {n for n in names if n in (avoid_text or "")}
    fresh = [n for n in names if n not in avoid_names]
    candidates = fresh or names
    return candidates[salt % len(candidates)]


def validate_text(
    db: Session, value: str, city: Optional[str] = None
) -> Tuple[List[GeoIssue], List[str]]:
    """校验任意文本的地理错配（供知识库页面自查与 Agent 复用）。

    与 `validate_plan_geo` 使用同一套判定规则（含前缀感知），保证结果一致。
    指定 city 时按该城市判定；否则使用文本中出现的首个城市。
    """
    matcher = CityMatcher.from_db(db)
    food_idx = _food_index(db)
    city_names = city_name_map(db)
    detected_names = matcher.find_names(value)
    detected_ids = matcher.find_ids(value)

    target_id: Optional[str]
    if city:
        target_id = matcher.normalize_city(city)
    else:
        target_id = detected_ids[0] if detected_ids else None
    target_name = city_names.get(target_id or "", city or "")

    issues: List[GeoIssue] = []
    for start, _end, term, allowed, canonical in food_idx.find_all(value):
        if term in NEUTRAL_FOOD_TERMS:
            continue
        if target_id is None:
            continue
        if target_id in allowed:
            continue
        # 与行程校验保持一致的前缀感知，避免「应县凉粉」这类误报
        new_start, full_term = _expand_food_span(value, start, term, matcher, target_id)
        if new_start == -1:
            continue
        if new_start != start:
            term = full_term
        found_names = [city_names.get(c, c) for c in allowed]
        issues.append(
            GeoIssue(
                scope="activity",
                day_index=0,
                field="text",
                kind="food_city_mismatch",
                detail=(
                    f"「{canonical}」的正宗产地为 {'、'.join(found_names)}，"
                    f"与文本所指城市（{target_name}）不符"
                ),
                found=canonical,
                expected_city=target_name,
                found_cities=found_names,
            )
        )
    # 文本提到的城市若不属于该特产产地，也可能存在跨城描述，交由调用方判断
    return issues, detected_names
