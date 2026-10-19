"""校验问答对形式的导出文件

检查项：
1. 结构完整：每个「问：」紧接一个非空「答：」，无孤立问句、无悬空答案
2. 问答对边界清晰：以空行分隔，便于平台按段落分片
3. 长度可控：单条问答对 < 600 字，不会被二次切分
4. 地理互斥：朔州相关问答对中不得出现平遥牛肉等外地名产
5. 无自指别名：不得出现「又称平遥牛肉」这类与正名重复的表述
6. 覆盖度：每个府邑都有景点汇总问、特产汇总问

用法：
    cd backend
    python scripts\\verify_knowledge_qa.py
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
QA_FILES = [
    "三晋智行_全量知识库（问答对）.txt",
    "三晋智行_景点问答对.txt",
    "三晋智行_特产问答对.txt",
]
MAX_QA_CHARS = 600

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


def parse_pairs(text: str) -> tuple:
    """返回 (问答对列表, 结构错误列表)"""
    pairs = []
    errors = []
    # 按空行切块，块内应恰好是「问：…」+「答：…」
    blocks = re.split(r"\n\s*\n", text)
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        if b.startswith("问："):
            lines = [l for l in b.splitlines() if l.strip()]
            if len(lines) < 2 or not lines[1].startswith("答："):
                errors.append(f"问句缺少答案：{lines[0][:40]}")
                continue
            q = lines[0][len("问：") :]
            a = lines[1][len("答：") :]
            if not a.strip():
                errors.append(f"答案为空：{q[:40]}")
                continue
            pairs.append((q, a, b))
        elif b.startswith("答："):
            errors.append(f"出现孤立答案：{b[:40]}")
    return pairs, errors


def main() -> int:
    data = json.loads(knowledge_seed_path().read_text(encoding="utf-8"))
    city_names = [c["name"] for c in data["cities"]]
    full_path = OUT_DIR / QA_FILES[0]

    if not full_path.exists():
        print(f"✗ 缺少 {full_path.name}，请先运行 export_knowledge_qa.py")
        return 1

    print(f"校验目录：{OUT_DIR}\n")

    for fname in QA_FILES:
        p = OUT_DIR / fname
        print(f"=== {fname} ===")
        if not p.exists():
            check("文件存在", False)
            continue
        text = p.read_text(encoding="utf-8")
        pairs, errors = parse_pairs(text)

        check(f"解析出 {len(pairs)} 条问答对", len(pairs) > 0)
        check("无结构错误（孤立问句／空答案）", not errors, str(errors[:3]))

        # 每个问句都应有答案且答案不是占位
        placeholder = [q for q, a, _ in pairs if len(a.strip()) < 4]
        check("无过短答案", not placeholder, str(placeholder[:3]))

        # 长度
        too_long = [(q[:24], len(b)) for q, a, b in pairs if len(b) > MAX_QA_CHARS]
        check(f"全部问答对 < {MAX_QA_CHARS} 字", not too_long, str(too_long[:3]))

        # 自指别名：用与导出脚本一致的判定口径校验
        # （不能用粗暴的子串双向匹配 —— 「右玉炖羊肉」包含「右玉羊肉」，
        #   但它是正当别名；「双塔寺」也不包含于「永祚寺双塔」却完全正当）
        self_ref = []
        alias_pool = {}
        for item in data["attractions"] + data["foods"]:
            alias_pool[item["name"]] = set(
                a for a in (item.get("aliases") or []) if a
            )
        for q, a, _ in pairs:
            for m in re.finditer(r"又称([^。]+)", a):
                for term in re.split(r"[、，]", m.group(1)):
                    term = term.strip()
                    if not term:
                        continue
                    # 若该别名正好等于问句中的正名，才是真自指
                    if re.match(rf"^{re.escape(term)}(是|位于|在哪)", q):
                        self_ref.append(f"{q[:24]} → 又称{term}（与正名相同）")
                    # 或该别名与正名完全相等
                    elif term in alias_pool and any(
                        term == name for name in alias_pool if name in q
                    ):
                        self_ref.append(f"{q[:24]} → 又称{term}（与正名相同）")
        check("无自指别名（又称X 而 X 即问句主语）", not self_ref, str(self_ref[:3]))
        print()

    # ---- 地理互斥（针对全量文件）
    print("=== 地理互斥检查 ===")
    text = full_path.read_text(encoding="utf-8")
    pairs, _ = parse_pairs(text)
    # 找出问句或答案中明确指向朔州的问答对，检查是否混入外地名产
    foreign_in_sz = []
    for q, a, _ in pairs:
        blob = q + a
        if "朔州" not in blob:
            continue
        # 该问答对若同时给出这些外地名产且未声明「不是朔州」，视为串味
        for bad in ("平遥牛肉", "太谷饼", "浑源凉粉"):
            if bad in blob and f"不是" not in blob and "同源" not in blob:
                foreign_in_sz.append(f"{q[:24]} 含 {bad}")
            elif bad in blob and "不是" in blob:
                pass  # 明确否定的表述是正确的
    check("朔州相关问答对未串入外地名产", not foreign_in_sz, str(foreign_in_sz[:3]))

    # 反向：平遥牛肉必须明确归属晋中
    jz = [a for q, a, _ in pairs if "平遥牛肉" in q and "哪里" in q]
    check(
        "平遥牛肉明确归属晋中",
        bool(jz) and all("晋中" in a for a in jz),
        str(jz[:1]),
    )

    # ---- 覆盖度
    print("\n=== 覆盖度检查 ===")
    missing = []
    for name in city_names:
        has_att = any(f"{name}有哪些景点" in q or f"{name}有什么好玩" in q for q, _, _ in pairs)
        has_food = any(f"{name}有什么特产" in q or f"{name}有什么好吃" in q for q, _, _ in pairs)
        if not has_att:
            missing.append(f"{name}缺景点汇总问")
        if not has_food:
            missing.append(f"{name}缺特产汇总问")
    check(f"{len(city_names)} 个府邑均有景点/特产汇总问", not missing, str(missing[:5]))

    # ---- 规模
    print("\n=== 文件规模 ===")
    for fname in QA_FILES:
        p = OUT_DIR / fname
        if p.exists():
            size = p.stat().st_size
            check(f"{fname} < 10MB", size < 10 * 1024 * 1024, f"{size}")

    print(f"\n结果：通过 {ok_count}，失败 {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
