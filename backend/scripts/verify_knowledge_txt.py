"""校验导出的 txt：确保地理信息未在导出环节发生串味

检查项：
1. 每个「产地：X」小节内，只出现该城市的特产
2. 每个「府邑：X」小节内，只出现该城市的景点
3. by_city 文件中不得混入其它城市的条目
4. 每条内容都有归属地标注（【城市】前缀）
5. 单条长度未超阈值

用法：
    cd backend
    python scripts\\verify_knowledge_txt.py
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

from app.core.config import knowledge_seed_path  # noqa: E402

OUT_DIR = BACKEND_DIR.parent / "knowledge_txt"
SUB_SEP = "-" * 60

ok_count = 0
fail_count = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  ✓ {label}")
    else:
        fail_count += 1
        print(f"  ✗ {label} {extra}")


def load() -> dict:
    return json.loads(knowledge_seed_path().read_text(encoding="utf-8"))


def sections(text: str, marker: str) -> dict:
    """切出「marker：城市」小节 → 正文"""
    out = {}
    parts = text.split(SUB_SEP)
    for i, p in enumerate(parts):
        m = re.search(rf"^{marker}：(\S+)\s*$", p.strip(), re.MULTILINE)
        if m and i + 1 < len(parts):
            out[m.group(1)] = parts[i + 1]
    return out


def main() -> int:
    data = load()
    city_names = {c["id"]: c["name"] for c in data["cities"]}
    food_owner = {}
    for f in data["foods"]:
        for term in [f["name"], *(f.get("aliases") or [])]:
            food_owner.setdefault(term, set()).add(f["city"])
    att_owner = {}
    for a in data["attractions"]:
        for term in [a["name"], *(a.get("aliases") or [])]:
            att_owner.setdefault(term, set()).add(a["city"])

    full_path = OUT_DIR / "三晋智行_全量知识库.txt"
    print(f"校验目录：{OUT_DIR}\n")

    if not full_path.exists():
        print(f"✗ 缺少 {full_path.name}，请先运行 export_knowledge_txt.py")
        return 1

    full = full_path.read_text(encoding="utf-8")

    print("=== 1. 全量文件：特产小节归属正确性 ===")
    food_secs = sections(full, "产地")
    check(f"切出 {len(food_secs)} 个产地小节（应为 11）", len(food_secs) == 11, str(len(food_secs)))
    for cname, body in food_secs.items():
        cid = next((k for k, v in city_names.items() if v == cname), None)
        if not cid:
            check(f"产地小节「{cname}」可识别", False)
            continue
        # 本节标题行里出现的所有特产名，必须归属本城
        bad = []
        for term, owners in food_owner.items():
            if len(term) < 2:
                continue
            # 只看条目标题行「N. 【城】名」
            for line in body.splitlines():
                m = re.match(r"^\s*\d+\.\s*【(.+?)】(.+)$", line)
                if not m:
                    continue
                item_name = m.group(2).strip()
                if item_name == term and cid not in owners:
                    bad.append(f"{term}→{sorted(owners)}")
        check(f"「{cname}」小节无外城特产", not bad, str(bad))

    print("\n=== 2. 全量文件：景点小节归属正确性 ===")
    att_secs = sections(full, "府邑")
    check(f"切出 {len(att_secs)} 个府邑景点小节", len(att_secs) == 11, str(len(att_secs)))
    for cname, body in att_secs.items():
        cid = next((k for k, v in city_names.items() if v == cname), None)
        bad = []
        for line in body.splitlines():
            m = re.match(r"^\s*\d+\.\s*【(.+?)】(.+)$", line)
            if not m:
                continue
            item_name = m.group(2).strip()
            owners = att_owner.get(item_name)
            if owners and cid not in owners:
                bad.append(f"{item_name}→{sorted(owners)}")
        check(f"「{cname}」小节无外城景点", not bad, str(bad))

    print("\n=== 3. 关键互斥：朔州小节不得出现平遥牛肉 ===")
    sz = food_secs.get("朔州", "")
    check("朔州特产小节不含「平遥牛肉」", "平遥牛肉" not in sz)
    check("朔州特产小节含「右玉羊肉」", "右玉羊肉" in sz)
    jz = food_secs.get("晋中", "")
    check("晋中特产小节含「平遥牛肉」", "平遥牛肉" in jz)
    dt = food_secs.get("大同", "")
    check("大同特产小节含「浑源凉粉」", "浑源凉粉" in dt)
    check("朔州特产小节不含「浑源凉粉」", "浑源凉粉" not in sz)

    print("\n=== 4. by_city 分文件不串味 ===")
    by_city_dir = OUT_DIR / "by_city"
    for c in data["cities"]:
        p = by_city_dir / f"{c['name']}.txt"
        if not p.exists():
            check(f"存在 by_city/{c['name']}.txt", False)
            continue
        body = p.read_text(encoding="utf-8")
        # 该文件里出现的景点/特产条目，其归属必须是本城
        bad = []
        for line in body.splitlines():
            m = re.match(r"^\s*\d+\.\s*【(.+?)】(.+)$", line)
            if not m:
                continue
            item_name = m.group(2).strip()
            owners = food_owner.get(item_name) or att_owner.get(item_name)
            if owners and c["id"] not in owners:
                bad.append(item_name)
        check(f"by_city/{c['name']}.txt 条目归属本城", not bad, str(bad))

    print("\n=== 5. 每条内容均有归属地标注 ===")
    # 只统计真正的资料条目：形如「N. 【府邑】名称」；
    # 文件头部的「地理原则」也用了 1./2. 编号，需排除
    labeled = re.findall(r"^\s*\d+\.\s*【(.+?)】(.+)$", full, re.MULTILINE)
    expected = len(data["attractions"]) + len(data["foods"])
    check(
        f"{len(labeled)} 条带【府邑】标注（应为 {expected}）",
        len(labeled) == expected,
        f"实际 {len(labeled)}",
    )
    # 反向确认：不存在没有【府邑】前缀的资料条目
    all_numbered = re.findall(r"^\s*\d+\.\s*(.+)$", full, re.MULTILINE)
    unlabeled = [
        e for e in all_numbered if not re.match(r"^【.+?】", e) and not e.startswith("典型错误示例")
    ]
    # 头部「地理原则」的编号行以中文说明开头，长度较长且不含【】
    header_like = [e for e in unlabeled if e.endswith(("。", "；", "，"))]
    check(
        "无资料条目缺少【府邑】前缀",
        len(unlabeled) == len(header_like),
        f"可疑：{[e for e in unlabeled if e not in header_like][:5]}",
    )

    print("\n=== 6. 单条长度 ===")
    blocks = re.split(r"\n(?=\s*\d+\.\s*【)", full)
    too_long = []
    for b in blocks:
        m = re.match(r"^\s*\d+\.\s*【(.+?)】(.+)", b)
        if m and len(b.strip()) > 800:
            too_long.append((m.group(2).strip(), len(b.strip())))
    check("无单条超过 800 字", not too_long, str(too_long[:5]))

    print("\n=== 7. 文件规模 ===")
    for p in sorted(OUT_DIR.rglob("*.txt")):
        size = p.stat().st_size
        check(f"{p.relative_to(OUT_DIR)} < 10MB", size < 10 * 1024 * 1024, f"{size}")

    print(f"\n结果：通过 {ok_count}，失败 {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
