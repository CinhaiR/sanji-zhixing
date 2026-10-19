"""知识库导入脚本

用法：
    cd backend
    python scripts/import_knowledge.py                 # 校验并导入
    python scripts/import_knowledge.py --check         # 仅校验，不写库
    python scripts/import_knowledge.py --file other.json

编辑 `backend/data/knowledge_seed.json` 增加景点 / 特产内容后运行本脚本即可生效。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

# 允许直接以 `python scripts/import_knowledge.py` 运行
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Windows 控制台默认 GBK，强制 UTF-8 以免中文报错
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


def validate(data: dict) -> tuple[list[str], list[str]]:
    """校验种子数据，返回 (errors, warnings)"""
    errors: list[str] = []
    warns: list[str] = []

    cities = data.get("cities") or []
    attractions = data.get("attractions") or []
    foods = data.get("foods") or []
    themes = set(data.get("theme_tags") or [])

    if not cities:
        errors.append("cities 为空")
        return errors, warns

    city_ids: set[str] = set()
    for c in cities:
        cid = c.get("id")
        if not cid:
            errors.append(f"城市缺少 id：{c.get('name')}")
            continue
        if cid in city_ids:
            errors.append(f"城市 id 重复：{cid}")
        city_ids.add(cid)
        if not c.get("name"):
            errors.append(f"城市 {cid} 缺少 name")
        for t in c.get("theme_tags") or []:
            if themes and t not in themes:
                errors.append(f"城市 {cid} 主题标签非法：{t}")

    att_by_city: dict[str, list[str]] = defaultdict(list)
    seen_att: set[str] = set()
    for a in attractions:
        name = a.get("name")
        if not name:
            errors.append("存在缺少 name 的景点")
            continue
        if name in seen_att:
            errors.append(f"景点重名：{name}")
        seen_att.add(name)
        if a.get("city") not in city_ids:
            errors.append(f"景点「{name}」引用未知城市：{a.get('city')}")
        if not a.get("desc"):
            errors.append(f"景点「{name}」缺少 desc")
        att_by_city[a.get("city")].append(name)
        for t in a.get("tags") or []:
            if themes and t not in themes:
                errors.append(f"景点「{name}」标签非法：{t}")

    food_by_city: dict[str, list[str]] = defaultdict(list)
    seen_food: set[str] = set()
    for f in foods:
        name = f.get("name")
        if not name:
            errors.append("存在缺少 name 的特产")
            continue
        if name in seen_food:
            errors.append(f"特产重名：{name}")
        seen_food.add(name)
        if f.get("city") not in city_ids:
            errors.append(f"特产「{name}」引用未知城市：{f.get('city')}")
        if not f.get("desc"):
            errors.append(f"特产「{name}」缺少 desc")
        for t in f.get("traditional_in") or []:
            if t not in city_ids:
                errors.append(f"特产「{name}」traditional_in 未知城市：{t}")
            if t == f.get("city"):
                warns.append(f"特产「{name}」的 traditional_in 含自身 city，冗余")
        food_by_city[f.get("city")].append(name)

    # 覆盖度：每城至少 1 景点 + 2 特产，否则行程兜底时无法落地
    for c in cities:
        cid = c.get("id")
        if len(att_by_city.get(cid, [])) < 1:
            errors.append(f"城市「{c.get('name')}」没有任何景点")
        if len(food_by_city.get(cid, [])) < 2:
            errors.append(
                f"城市「{c.get('name')}」特产不足 2 个（实际 {len(food_by_city.get(cid, []))}）"
            )

    # 别名冲突（跨城同源属正常，仅提示）
    alias_owner: dict[str, str] = {}
    for items in (attractions, foods):
        for it in items:
            for term in [it.get("name"), *(it.get("aliases") or [])]:
                if not term:
                    continue
                prev = alias_owner.get(term)
                if prev and prev != it.get("name"):
                    warns.append(f"别名「{term}」同时指向「{prev}」与「{it.get('name')}」")
                alias_owner[term] = it.get("name")

    return errors, warns


def main() -> int:
    parser = argparse.ArgumentParser(description="导入三晋智行知识库")
    parser.add_argument("--file", default=None, help="种子文件路径（默认 data/knowledge_seed.json）")
    parser.add_argument("--check", action="store_true", help="仅校验，不写入数据库")
    parser.add_argument("--quiet", action="store_true", help="减少输出")
    args = parser.parse_args()

    from app.core.config import db_path, knowledge_seed_path

    path = Path(args.file) if args.file else knowledge_seed_path()
    if not path.exists():
        print(f"✗ 种子文件不存在：{path}")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    errors, warns = validate(data)

    cities = data.get("cities") or []
    attractions = data.get("attractions") or []
    foods = data.get("foods") or []

    print(f"种子文件：{path}")
    print(f"城市 {len(cities)}｜景点 {len(attractions)}｜特产 {len(foods)}")

    if not args.quiet:
        att_by_city: dict[str, int] = defaultdict(int)
        food_by_city: dict[str, int] = defaultdict(int)
        for a in attractions:
            att_by_city[a.get("city")] += 1
        for f in foods:
            food_by_city[f.get("city")] += 1
        for c in cities:
            print(
                f"  {c.get('name'):<4} 景点 {att_by_city.get(c.get('id'), 0)}"
                f"｜特产 {food_by_city.get(c.get('id'), 0)}"
            )

    if warns:
        print(f"\n提示 {len(warns)} 条：")
        for w in warns[:20]:
            print(f"  ! {w}")
        if len(warns) > 20:
            print(f"  ... 另有 {len(warns) - 20} 条")

    if errors:
        print(f"\n✗ 校验失败，共 {len(errors)} 个错误：")
        for e in errors[:40]:
            print(f"  - {e}")
        if len(errors) > 40:
            print(f"  ... 另有 {len(errors) - 40} 个错误")
        return 1

    print("\n✓ 校验通过")
    if args.check:
        print("（--check 模式，未写入数据库）")
        return 0

    from app.db.database import Base, SessionLocal, engine
    from app.models import itinerary as _itinerary_model  # noqa: F401
    from app.models import knowledge as _knowledge_model  # noqa: F401
    from app.services import knowledge_service

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = knowledge_service.load_seed(db, path)
    finally:
        db.close()

    print(f"✓ 已导入数据库：{db_path()}")
    print(
        f"  城市 {result.cities}｜景点 {result.attractions}｜特产 {result.foods}"
        f"｜内容指纹 {result.fingerprint}"
    )
    print("  提示：内容指纹变化后，行程生成缓存会自动失效，无需手动清理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
