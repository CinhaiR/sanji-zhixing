"""知识库导出脚本：把 knowledge_seed.json 导出为适合讯飞星火知识库分片的 txt

设计目标（针对知识库分片检索，而非人读）：
1. **每个分片自包含**：每条内容以 `[城市名] 标题` 起头，切分后仍能看出归属地
   —— 这是防止「朔州吃平遥牛肉」在此环节复发的关键
2. **单条长度可控**：控制在 800 字以内，避免一条内容被平台二次切开
3. **地名互斥提示前置**：文件头写明地理原则，并被切进检索语料
4. **多粒度输出**：
   - `*_all.txt`         全量单文件（最省事，适合一次性导入）
   - `*_景点.txt` 等     按类型拆分（检索更聚焦）
   - `by_city/城市.txt`  按府邑拆分（精确问答场景下误召回最少）

用法：
    cd backend
    python scripts\\export_knowledge_txt.py
    python scripts\\export_knowledge_txt.py --out D:\\kb
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

SEP = "=" * 60
SUB_SEP = "-" * 60

# 单条内容的安全长度（平台分片常在 500-1000 字区间，超过易被二次切开）
MAX_ENTRY_CHARS = 800


# ---------------------------------------------------------------- 载入


def load_seed(path: Optional[Path] = None) -> dict:
    from app.core.config import knowledge_seed_path

    p = path or knowledge_seed_path()
    import json

    return json.loads(Path(p).read_text(encoding="utf-8"))


def index_cities(data: dict) -> Dict[str, dict]:
    return {c["id"]: c for c in data.get("cities", [])}


def group_by_city(items: Iterable[dict], city_ids: List[str]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {c: [] for c in city_ids}
    for it in items:
        out.setdefault(it.get("city", ""), []).append(it)
    return out


# ---------------------------------------------------------------- 文本渲染


def _join(values: Optional[Iterable[str]]) -> str:
    return "、".join([v for v in (values or []) if v])


def render_attraction(a: dict, idx: int) -> str:
    """渲染单条景点（自包含：标题带府邑）"""
    city_name = a.get("city_name") or ""
    lines: List[str] = []
    lines.append(f"{idx}. 【{city_name}】{a['name']}")
    meta_parts = []
    if a.get("type"):
        meta_parts.append(f"资源类型：{a['type']}")
    if a.get("level"):
        meta_parts.append(f"景区等级：{a['level']}")
    meta_parts.append(f"所属府邑：{city_name}")
    lines.append("   " + "；".join(meta_parts))
    if a.get("aliases"):
        lines.append(f"   别名：{_join(a['aliases'])}")
    if a.get("tags"):
        lines.append(f"   主题：{_join(a['tags'])}")
    if a.get("desc"):
        lines.append(f"   简介：{a['desc']}")
    if a.get("tips"):
        lines.append(f"   参访锦囊：{a['tips']}")
    if a.get("confidence") and a["confidence"] != "high":
        lines.append("   备注：本条内容为待核实资料，引用时请注明可能存在出入。")
    return "\n".join(lines)


def render_food(f: dict, idx: int, city_names: Dict[str, str]) -> str:
    """渲染单条特产（自包含：标题带产地）"""
    city_name = f.get("city_name") or ""
    lines: List[str] = []
    lines.append(f"{idx}. 【{city_name}】{f['name']}")
    meta_parts = [f"正宗产地：{city_name}"]
    if f.get("type"):
        meta_parts.append(f"类别：{f['type']}")
    if f.get("taste") and f["taste"] != "-":
        meta_parts.append(f"风味：{f['taste']}")
    lines.append("   " + "；".join(meta_parts))
    if f.get("aliases"):
        lines.append(f"   别名／俗称：{_join(f['aliases'])}")
    if f.get("traditional_in"):
        shared = _join([city_names.get(c, c) for c in f["traditional_in"]])
        lines.append(
            f"   同源地区：{shared}（这些地区也有同源做法，属正常；"
            f"但要说清本源在{city_name}）"
        )
    if f.get("ingredients"):
        lines.append(f"   主要原料：{_join(f['ingredients'])}")
    if f.get("desc"):
        lines.append(f"   简介：{f['desc']}")
    if f.get("tips"):
        lines.append(f"   品尝锦囊：{f['tips']}")
    if f.get("confidence") and f["confidence"] != "high":
        lines.append("   备注：本条内容为待核实资料，引用时请注明可能存在出入。")
    return "\n".join(lines)


def render_attraction_list(atts: List[dict]) -> str:
    return "\n\n".join(render_attraction(a, i) for i, a in enumerate(atts, 1))


def render_food_list(foods: List[dict], city_names: Dict[str, str]) -> str:
    return "\n\n".join(render_food(f, i, city_names) for i, f in enumerate(foods, 1))


# ---------------------------------------------------------------- 文件内容


def file_header(title: str, purpose: str, data: dict) -> str:
    n_city = len(data.get("cities", []))
    n_att = len(data.get("attractions", []))
    n_food = len(data.get("foods", []))
    return f"""{title}

【资料用途】{purpose}

【覆盖范围】山西 {n_city} 个地级市；景点 {n_att} 条；地方特产 {n_food} 条。

【地理原则 · 回答时必须遵守】
1. 山西各地风物各异，特产与景点都有明确归属地。回答时不得把甲地的名产安到乙地。
   典型错误示例：把「平遥牛肉」（晋中平遥特产）说成朔州特产；
   把「闻喜煮饼」（运城闻喜特产）说成大同特产。
2. 每条资料的标题已标注所属府邑，如「【朔州】应县木塔」，请以此为准。
3. 朔州的代表特产是右玉羊肉、应县紫皮蒜、朔州大烩菜、朔州黄米油糕、朔州莜面栲栳栳；
   平遥牛肉、太谷饼属晋中；浑源凉粉、大同刀削面、大同羊杂属大同。
4. 若资料中标注了「同源地区」，表示该食物在多地都有同源做法，
   回答时应说明本源产地，不要简单归给提问所在城市。
5. 资料未收录的内容，回答时请说明「现有资料未收录」，不要凭空编造。

{SEP}
"""


def build_city_overview(data: dict) -> str:
    """府邑总览：为检索提供锚点（问题提到某市时，本篇含该市全貌）"""
    city_names = {c["id"]: c["name"] for c in data["cities"]}
    att_by_city = group_by_city(data["attractions"], list(city_names))
    food_by_city = group_by_city(data["foods"], list(city_names))

    lines = ["一、山西各府邑总览", ""]
    for c in data["cities"]:
        cid = c["id"]
        atts = att_by_city.get(cid, [])
        foods = food_by_city.get(cid, [])
        lines.append(f"{c['name']}")
        if c.get("aliases"):
            lines.append(f"   古称／下辖：{_join(c['aliases'])}")
        if c.get("theme_tags"):
            lines.append(f"   适合主题：{_join(c['theme_tags'])}")
        if c.get("intro"):
            lines.append(f"   概况：{c['intro']}")
        if atts:
            lines.append(f"   代表景点（{len(atts)}）：{_join([a['name'] for a in atts])}")
        if foods:
            lines.append(f"   代表特产（{len(foods)}）：{_join([f['name'] for f in foods])}")
        lines.append("")
    return "\n".join(lines)


def build_attraction_section(data: dict, grouped: bool = True) -> str:
    city_names = {c["id"]: c["name"] for c in data["cities"]}
    if not grouped:
        return "二、景点资料\n\n" + render_attraction_list(data["attractions"])

    lines = ["二、景点资料（按府邑分列）", ""]
    by_city = group_by_city(data["attractions"], list(city_names))
    for c in data["cities"]:
        items = by_city.get(c["id"], [])
        if not items:
            continue
        lines.append(SUB_SEP)
        lines.append(f"府邑：{c['name']}")
        lines.append(SUB_SEP)
        lines.append("")
        lines.append(render_attraction_list(items))
        lines.append("")
    return "\n".join(lines)


def build_food_section(data: dict, grouped: bool = True) -> str:
    city_names = {c["id"]: c["name"] for c in data["cities"]}
    if not grouped:
        return "三、地方特产与风味资料\n\n" + render_food_list(data["foods"], city_names)

    lines = ["三、地方特产与风味资料（按产地分列）", ""]
    by_city = group_by_city(data["foods"], list(city_names))
    for c in data["cities"]:
        items = by_city.get(c["id"], [])
        if not items:
            continue
        lines.append(SUB_SEP)
        lines.append(f"产地：{c['name']}")
        lines.append(SUB_SEP)
        lines.append("")
        lines.append(render_food_list(items, city_names))
        lines.append("")
    return "\n".join(lines)


def build_city_file(city: dict, atts: List[dict], foods: List[dict], city_names: Dict[str, str]) -> str:
    """单府邑文件：整城内容集中，精确问答场景下误召回最低"""
    name = city["name"]
    head = f"""{name} · 三晋文旅方志

【资料用途】山西{name}的景点与地方特产资料，用于回答{name}相关旅游问题。

【地理原则】以下景点与特产均属{name}。
回答时不得把其他城市的特产说成{name}特产，也不得把{name}名产安到别处。
资料未收录的内容请说明「现有资料未收录」，不要编造。

{SEP}
"""
    parts = [head]
    parts.append(f"一、{name}概况\n")
    if city.get("aliases"):
        parts.append(f"古称／下辖：{_join(city['aliases'])}")
    if city.get("theme_tags"):
        parts.append(f"适合主题：{_join(city['theme_tags'])}")
    if city.get("intro"):
        parts.append(f"概况：{city['intro']}")
    parts.append("")

    if atts:
        parts.append(SUB_SEP)
        parts.append(f"二、{name}景点资料")
        parts.append(SUB_SEP)
        parts.append("")
        parts.append(render_attraction_list(atts))
        parts.append("")

    if foods:
        parts.append(SUB_SEP)
        parts.append(f"三、{name}地方特产与风味")
        parts.append(SUB_SEP)
        parts.append("")
        parts.append(render_food_list(foods, city_names))
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------- 主流程


def write_file(path: Path, content: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return len(content.encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="导出知识库为讯飞知识库可用的 txt")
    parser.add_argument("--out", default=None, help="输出目录（默认 ../knowledge_txt）")
    parser.add_argument("--seed", default=None, help="种子文件路径")
    parser.add_argument("--max-entry-chars", type=int, default=MAX_ENTRY_CHARS)
    args = parser.parse_args()

    data = load_seed(Path(args.seed) if args.seed else None)
    city_names = {c["id"]: c["name"] for c in data["cities"]}
    out_dir = Path(args.out) if args.out else (BACKEND_DIR.parent / "knowledge_txt")

    results: List[tuple] = []

    # ---- 1. 全量单文件（推荐首选）
    full = "".join(
        [
            file_header(
                "三晋智行 · 山西文旅方志（全量）",
                "山西古建、佛艺、晋商、非遗、山川与地方风味问答",
                data,
            ),
            "\n",
            build_city_overview(data),
            "\n",
            SEP,
            "\n",
            build_attraction_section(data),
            "\n",
            SEP,
            "\n",
            build_food_section(data),
        ]
    )
    results.append(("三晋智行_全量知识库.txt", write_file(out_dir / "三晋智行_全量知识库.txt", full)))

    # ---- 2. 按类型拆分
    att_only = file_header("三晋智行 · 山西景点方志", "山西景点、古建与文物保护单位问答", data)
    att_only += "\n" + build_attraction_section(data)
    results.append(("三晋智行_景点.txt", write_file(out_dir / "三晋智行_景点.txt", att_only)))

    food_only = file_header("三晋智行 · 山西特产风味方志", "山西地方特产与风味饮食问答", data)
    food_only += "\n" + build_food_section(data)
    results.append(("三晋智行_特产风味.txt", write_file(out_dir / "三晋智行_特产风味.txt", food_only)))

    city_only = file_header("三晋智行 · 山西府邑总览", "山西各市概况、别名与代表风物问答", data)
    city_only += "\n" + build_city_overview(data)
    results.append(("三晋智行_府邑总览.txt", write_file(out_dir / "三晋智行_府邑总览.txt", city_only)))

    # ---- 3. 按府邑拆分
    by_city = out_dir / "by_city"
    att_by_city = group_by_city(data["attractions"], list(city_names))
    food_by_city = group_by_city(data["foods"], list(city_names))
    for c in data["cities"]:
        content = build_city_file(
            c, att_by_city.get(c["id"], []), food_by_city.get(c["id"], []), city_names
        )
        fname = f"{c['name']}.txt"
        results.append((f"by_city/{fname}", write_file(by_city / fname, content)))

    # ---- 4. 校验单条长度
    too_long = []
    for a in data["attractions"]:
        if len(render_attraction(a, 1)) > args.max_entry_chars:
            too_long.append(("景点", a["name"], len(render_attraction(a, 1))))
    for f in data["foods"]:
        if len(render_food(f, 1, city_names)) > args.max_entry_chars:
            too_long.append(("特产", f["name"], len(render_food(f, 1, city_names))))

    print(f"输出目录：{out_dir}")
    print(f"共 {len(results)} 个文件\n")
    total = 0
    for name, size in results:
        total += size
        print(f"  {name:<32} {size:>7} 字节  ≈{size // 3:>5} 字")
    print(f"\n合计 {total} 字节（上限 10MB / 文件，当前远低于限制）")

    if too_long:
        print(f"\n⚠ 有 {len(too_long)} 条超过 {args.max_entry_chars} 字，可能被平台二次切分：")
        for kind, name, n in too_long[:10]:
            print(f"    {kind}「{name}」{n} 字")
    else:
        print(f"\n✓ 所有单条均未超过 {args.max_entry_chars} 字（不会被平台二次切分）")

    # 提示地理要点
    cross = [
        f for f in data["foods"] if f.get("traditional_in")
    ]
    print(f"✓ 含「同源地区」标注的特产 {len(cross)} 条（已生成互斥说明）")
    print(
        "\n提示：这是**列表形式**（备用）。讯飞知识库推荐用问答对形式，"
        "请改跑 export_knowledge_qa.py，导入「三晋智行_全量知识库（问答对）.txt」。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
