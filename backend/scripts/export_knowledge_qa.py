"""知识库导出（问答对形式）：把 knowledge_seed.json 导出为「问：…／答：…」的 txt

为什么用问答对：
1. 讯飞知识库检索结果的 `answerSource` 分三类 —— `qa`（问答对，100% 准确）、
   `spark`、`doc`（文档问答，不是 100% 准确）。问答对形式最接近 qa 通路。
2. 分片后每个片段就是「一个问题 + 一个完整答案」，检索时问句与用户提问直接对齐，
   不会像长文档那样把两条不同城市的内容切进同一片。
3. 一条问答对自包含，即使只召回一条也能回答完整。

格式约定（便于平台自动分段）：
    <文件头：回答原则，会被切进检索语料>
    ====
    一、山西总览
    ----
    问：山西有哪些地级市？
    答：……

    问：大同有哪些景点？
    答：……
    ====

设计要点：
- 每个「答：」都在首句重复归属地（如「朔州的景点有……」），确保切开也不丢地理信息
- 单条问答对 < 600 字，不会被平台二次切分
- 同一答案的多种问法合并为一个问答对，避免语料重复

用法：
    cd backend
    python scripts\\export_knowledge_qa.py
    python scripts\\export_knowledge_qa.py --out D:\\kb
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

SEP = "=" * 60
SUB_SEP = "-" * 60
MAX_QA_CHARS = 600  # 单条问答对安全长度


# ---------------------------------------------------------------- 工具


def load_seed(path: Optional[Path] = None) -> dict:
    import json as _json

    from app.core.config import knowledge_seed_path

    p = path or knowledge_seed_path()
    return _json.loads(Path(p).read_text(encoding="utf-8"))


def join(values) -> str:
    return "、".join([v for v in (values or []) if v])


def qa(questions: List[str], answer: str) -> str:
    """渲染一个问答对；多个等价问法用「／」并列在同一行问句里"""
    q = "／".join(questions)
    return f"问：{q}\n答：{answer.strip()}"


def dedup_questions(questions: List[str]) -> List[str]:
    seen = set()
    out = []
    for q in questions:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def real_aliases(name: str, aliases) -> List[str]:
    """过滤掉与正名重复（或互为子串）的别名，避免「又称平遥牛肉」这种自指句"""
    out = []
    for a in aliases or []:
        if not a or a == name:
            continue
        if a in name or name in a:
            continue
        if a not in out:
            out.append(a)
    return out


# ---------------------------------------------------------------- 答案构造


def answer_city_attractions(city: dict, atts: List[dict]) -> str:
    """「X 有哪些景点？」"""
    name = city["name"]
    if not atts:
        return f"现有方志未收录{name}的景点资料。"
    names = "、".join(f"{a['name']}" for a in atts)
    parts = [f"{name}的代表景点有{len(atts)}处：{names}。"]
    # 附一句概括，帮助模型理解这批景点的共同定位
    themes = [t for a in atts for t in (a.get("tags") or [])]
    if themes:
        top = []
        for t in themes:
            if t not in top:
                top.append(t)
        parts.append(f"整体以{'、'.join(top[:3])}见长。")
    if city.get("intro"):
        parts.append(city["intro"])
    return "".join(parts)


def answer_city_foods(city: dict, foods: List[dict]) -> str:
    """「X 有什么特产／好吃的？」"""
    name = city["name"]
    if not foods:
        return f"现有方志未收录{name}的地方特产资料。"
    names = "、".join(f["name"] for f in foods)
    parts = [f"{name}的地方特产有{len(foods)}种：{names}。"]
    thumbs = [f"{f['name']}（{f['taste']}）" for f in foods[:3] if f.get("taste") and f["taste"] != "-"]
    if thumbs:
        parts.append(f"其中{'、'.join(thumbs)}最具代表性。")
    return "".join(parts)


def answer_attraction(a: dict) -> str:
    """单个景点：把结构化字段拼成一段完整叙述"""
    name = a["name"]
    city = a.get("city_name") or ""
    seg: List[str] = []
    meta = []
    if a.get("type"):
        meta.append(a["type"])
    if a.get("level"):
        meta.append(f"{a['level']}级景区")
    head = f"{name}位于山西{city}"
    if meta:
        head += f"，是{'、'.join(meta)}"
    head += "。"
    seg.append(head)
    aliases = real_aliases(name, a.get("aliases"))
    if aliases:
        seg.append(f"又称{join(aliases)}。")
    if a.get("desc"):
        seg.append(a["desc"])
    if a.get("tips"):
        seg.append(f"参访提示：{a['tips']}")
    if a.get("confidence") and a["confidence"] != "high":
        seg.append("（此条资料尚待核实，引用时请注意可能有出入。）")
    return "".join(seg)


def answer_food(f: dict, city_names: Dict[str, str]) -> str:
    """单个特产：突出正宗产地，这是防错配的关键句"""
    name = f["name"]
    city = f.get("city_name") or ""
    seg: List[str] = []
    parts = [f"{name}是山西{city}的特产"]
    if f.get("type"):
        parts.append(f"，属{f['type']}")
    if f.get("taste") and f["taste"] != "-":
        parts.append(f"，风味{f['taste']}")
    seg.append("".join(parts) + "。")
    aliases = real_aliases(name, f.get("aliases"))
    if aliases:
        seg.append(f"又称{join(aliases)}。")
    if f.get("ingredients"):
        seg.append(f"主要原料为{join(f['ingredients'])}。")
    if f.get("desc"):
        seg.append(f["desc"])
    if f.get("traditional_in"):
        shared = join([city_names.get(c, c) for c in f["traditional_in"]])
        seg.append(
            f"需要注意的是，{shared}也有同源做法，但其正宗产地是{city}。"
        )
    if f.get("tips"):
        seg.append(f"品尝提示：{f['tips']}")
    if f.get("confidence") and f["confidence"] != "high":
        seg.append("（此条资料尚待核实，引用时请注意可能有出入。）")
    return "".join(seg)


def answer_city_overview(city: dict, atts: List[dict], foods: List[dict]) -> str:
    name = city["name"]
    seg = []
    if city.get("intro"):
        seg.append(city["intro"])
    if city.get("aliases"):
        seg.append(f"古称或下辖地区包括{join(city['aliases'])}。")
    if atts:
        seg.append(f"代表景点有{'、'.join(a['name'] for a in atts)}。")
    if foods:
        seg.append(f"代表特产有{'、'.join(f['name'] for f in foods)}。")
    if city.get("theme_tags"):
        seg.append(f"适合{'、'.join(city['theme_tags'])}主题的旅行。")
    return "".join(seg)


# ---------------------------------------------------------------- 文件组装


def file_header(data: dict) -> str:
    n_city = len(data.get("cities", []))
    n_att = len(data.get("attractions", []))
    n_food = len(data.get("foods", []))
    return f"""三晋智行 · 山西文旅问答方志

【资料性质】本文件为问答对（QA）形式的知识资料，用于回答山西旅游相关问题。

【覆盖范围】山西 {n_city} 个地级市；景点 {n_att} 处；地方特产 {n_food} 种。

【回答原则 · 必须遵守】
1. 地理归属是底线。山西各地风物各异，绝不可把甲地的名产说成乙地所有。
   典型错误：把「平遥牛肉」（晋中平遥特产）说成朔州特产；
   把「闻喜煮饼」（运城闻喜特产）说成大同特产。
2. 每个答案的首句都已点明归属地，请以答案原文为准，不要自行推断。
3. 资料未收录的内容，请回答「现有方志未收录」，不要编造具体事实。
4. 标注「同源地区」的食物，要说明其正宗产地，并指出多地都有同源做法。
5. 涉及景点时，一并给出参访提示（预约、拍摄、禁忌等）。
6. 标注「尚待核实」的内容，回答时提示用户该信息可能存疑。

{SEP}
"""


def build_overview_section(data: dict, city_names: Dict[str, str]) -> str:
    att_by = group_by(data["attractions"])
    food_by = group_by(data["foods"])
    cities = data["cities"]

    blocks: List[str] = ["一、山西与各府邑总览", ""]

    blocks.append(
        qa(
            ["山西有哪些地级市？", "山西有哪些城市值得旅游？"],
            "山西共 11 个地级市："
            + "、".join(c["name"] for c in cities)
            + "。其中大同以云冈石窟与辽金古建见长，朔州以应县木塔与广武长城著称，"
            "忻州有五台山与唐代木构，太原是省会与晋祠所在，"
            "晋中是晋商故里与平遥古城所在地，吕梁有碛口古镇，阳泉有娘子关，"
            "长治与晋城古建彩塑冠绝，临汾有广胜寺与壶口瀑布，运城有解州关帝庙与永乐宫。",
        )
    )
    blocks.append("")

    for c in cities:
        cid = c["id"]
        blocks.append(
            qa(
                [f"{c['name']}是个怎样的地方？", f"{c['name']}有什么特色？"],
                answer_city_overview(c, att_by.get(cid, []), food_by.get(cid, [])),
            )
        )
        blocks.append("")
    return "\n".join(blocks)


def build_attraction_section(data: dict) -> str:
    att_by = group_by(data["attractions"])
    cities = data["cities"]
    blocks: List[str] = ["二、景点问答（按府邑分列）", ""]

    for c in cities:
        cid = c["id"]
        atts = att_by.get(cid, [])
        if not atts:
            continue
        blocks.append(SUB_SEP)
        blocks.append(f"府邑：{c['name']}")
        blocks.append(SUB_SEP)
        blocks.append("")
        # 汇总问
        blocks.append(
            qa(
                dedup_questions(
                    [
                        f"{c['name']}有哪些景点？",
                        f"{c['name']}有什么好玩的地方？",
                        f"{c['name']}必去的景点有哪些？",
                    ]
                ),
                answer_city_attractions(c, atts),
            )
        )
        blocks.append("")
        # 逐条问
        for a in atts:
            name = a["name"]
            aliases = [x for x in (a.get("aliases") or []) if x != name]
            qs = [f"{name}在哪里？", f"{name}是什么地方？", f"{name}值得去吗？"]
            if aliases:
                qs.append(f"{aliases[0]}是哪里？")
            blocks.append(qa(dedup_questions(qs), answer_attraction(a)))
            blocks.append("")
    return "\n".join(blocks)


def build_food_section(data: dict, city_names: Dict[str, str]) -> str:
    food_by = group_by(data["foods"])
    cities = data["cities"]
    blocks: List[str] = ["三、地方特产与风味问答（按产地分列）", ""]

    for c in cities:
        cid = c["id"]
        foods = food_by.get(cid, [])
        if not foods:
            continue
        blocks.append(SUB_SEP)
        blocks.append(f"产地：{c['name']}")
        blocks.append(SUB_SEP)
        blocks.append("")
        blocks.append(
            qa(
                dedup_questions(
                    [
                        f"{c['name']}有什么特产？",
                        f"{c['name']}有什么好吃的？",
                        f"{c['name']}的美食有哪些？",
                        f"去{c['name']}吃什么？",
                    ]
                ),
                answer_city_foods(c, foods),
            )
        )
        blocks.append("")
        for f in foods:
            name = f["name"]
            aliases = [x for x in (f.get("aliases") or []) if x != name]
            qs = [f"{name}是哪里的特产？", f"{name}是什么？"]
            if aliases:
                qs.append(f"{aliases[0]}是什么？")
            blocks.append(qa(dedup_questions(qs), answer_food(f, city_names)))
            blocks.append("")
    return "\n".join(blocks)


def group_by(items: List[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for it in items:
        out.setdefault(it.get("city", ""), []).append(it)
    return out


def build_cross_city_section(data: dict, city_names: Dict[str, str]) -> str:
    """跨城对比与易混淆专问：直接锚定历史错配点

    干扰项不用「按顺序取前三个城市」，而是显式指定**真正容易被搞混**的城市，
    否则会出现「平遥牛肉不是大同、朔州、忻州的特产」里偏偏漏掉最常见误区的尴尬。
    """
    blocks: List[str] = ["四、易混淆食物产地专问（重要）", ""]

    # (食物名, [最容易混淆成哪些城市])
    confusions: List[Tuple[str, List[str]]] = [
        ("平遥牛肉", ["朔州", "大同"]),
        ("太谷饼", ["朔州", "大同"]),
        ("浑源凉粉", ["朔州", "太原"]),
        ("大同刀削面", ["朔州", "太原"]),
        ("大同羊杂", ["朔州", "太原"]),
        ("右玉羊肉", ["大同", "忻州"]),
        ("应县紫皮蒜", ["大同", "忻州"]),
        ("运城闻喜煮饼", ["大同", "临汾"]),
    ]
    foods_by_name = {f["name"]: f for f in data["foods"]}
    for name, distractors in confusions:
        f = foods_by_name.get(name)
        if not f:
            continue
        correct = f.get("city_name") or ""
        # 只保留确实不是产地的干扰项，避免自相矛盾
        distractors = [d for d in distractors if d != correct]
        wrong = "、".join(distractors[:2])
        ans = (
            f"{name}是山西{correct}的特产，不是{wrong}的特产。"
            f"{f.get('desc','')}"
        )
        qs = [f"{name}是哪里的特产？"]
        if distractors:
            qs.append(f"{name}是{distractors[0]}的特产吗？")
        blocks.append(qa(dedup_questions(qs), ans))
        blocks.append("")
    return "\n".join(blocks)


def write_file(path: Path, content: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return len(content.encode("utf-8"))


# ---------------------------------------------------------------- 主流程


def main() -> int:
    parser = argparse.ArgumentParser(description="导出知识库为问答对形式 txt")
    parser.add_argument("--out", default=None, help="输出目录（默认 ../knowledge_txt）")
    parser.add_argument("--seed", default=None)
    args = parser.parse_args()

    data = load_seed(Path(args.seed) if args.seed else None)
    city_names = {c["id"]: c["name"] for c in data["cities"]}
    out_dir = Path(args.out) if args.out else (BACKEND_DIR.parent / "knowledge_txt")

    parts = [
        file_header(data),
        "\n",
        build_overview_section(data, city_names),
        "\n",
        SEP,
        "\n",
        build_attraction_section(data),
        "\n",
        SEP,
        "\n",
        build_food_section(data, city_names),
        "\n",
        SEP,
        "\n",
        build_cross_city_section(data, city_names),
    ]
    full = "\n".join(parts)

    name = "三晋智行_全量知识库（问答对）.txt"
    size = write_file(out_dir / name, full)

    # 同时输出按类型拆分的问答对文件
    att_qa = file_header(data) + "\n" + build_attraction_section(data)
    att_size = write_file(out_dir / "三晋智行_景点问答对.txt", att_qa)

    food_qa = file_header(data) + "\n" + build_food_section(data, city_names)
    food_size = write_file(out_dir / "三晋智行_特产问答对.txt", food_qa)

    # 统计
    pairs = full.count("问：")
    blocks = [b for b in full.split("\n\n") if b.strip().startswith("问：")]
    too_long = []
    for b in blocks:
        if len(b) > MAX_QA_CHARS:
            q = b.splitlines()[0][:30]
            too_long.append((q, len(b)))

    print(f"输出目录：{out_dir}\n")
    print(f"  {name:<40} {size:>7} 字节  ≈{size // 3:>6} 字  问答对 {pairs} 条")
    print(f"  {'三晋智行_景点问答对.txt':<40} {att_size:>7} 字节")
    print(f"  {'三晋智行_特产问答对.txt':<40} {food_size:>7} 字节")

    if too_long:
        print(f"\n⚠ {len(too_long)} 条问答对超过 {MAX_QA_CHARS} 字，可能被二次切分：")
        for q, n in too_long[:10]:
            print(f"    {q}… {n} 字")
    else:
        print(f"\n✓ 所有问答对均 < {MAX_QA_CHARS} 字（不会被平台二次切分）")
    print(f"✓ 共 {pairs} 条问答对")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
