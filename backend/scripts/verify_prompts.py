"""校验提示词文件的结构完整性

提示词改坏了不会报错，只会在用户提问时才发现。本脚本把「必须存在的约束项」
固化成检查项，便于改动后回归。

检查项：
1. 必备分节存在（人设 / 范围 / 事实 / 风格 / 防护）
2. 必备约束条目存在（域外引导、闲聊、敏感话题、地理互斥、防编造、未收录声明）
3. 变量引用正确（用户问题变量名与文档声明一致；无遗留的其它平台变量语法）
4. 提示词块代码围栏成对闭合
5. 系统提示词中不得出现本平台变量占位（会被误当变量解析）

用法：
    cd backend
    python scripts\\verify_prompts.py
"""
from __future__ import annotations

import io
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

PROMPT_FILE = BACKEND_DIR.parent / "prompts" / "讯飞大模型节点提示词.md"

# 用户问题变量名（需与平台开始节点一致）
USER_VAR = "AGENT_USER_INPUT"
# 允许出现在用户提示词中的知识召回变量
KB_VARS = ["input", "input2"]

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


def main() -> int:
    if not PROMPT_FILE.exists():
        print(f"✗ 缺少提示词文件：{PROMPT_FILE}")
        return 1

    text = PROMPT_FILE.read_text(encoding="utf-8")
    print(f"校验文件：{PROMPT_FILE.name}\n")

    # ---- 1. 分节
    print("=== 1. 必备分节 ===")
    for section in ["身份与人设", "回答范围", "事实规则", "表达风格", "边界防护"]:
        check(f"含分节【{section}】", section in text)

    # ---- 2. 关键约束
    print("\n=== 2. 关键约束条目 ===")
    constraints = {
        "回答范围限山西": ["只负责山西", "山西（三晋）文旅相关话题"],
        "域外话题引导回山西": ["与山西文旅无关的话题", "引回山西"],
        "闲聊热情回应": ["闲聊、寒暄、问候要热情得体"],
        "敏感话题婉谢": ["政治、宗教争议、民族、军事、涉外"],
        "地理归属硬约束": ["地理归属是底线", "平遥牛肉", "属晋中"],
        "同源地区说明": ["同源地区"],
        "待核实提示": ["尚待核实"],
        "防编造": ["编造景点名、特产名、年代、票价"],
        "未收录声明": ["现有方志未收录"],
        "不透露系统提示": ["不透露本提示词"],
    }
    for label, needles in constraints.items():
        hit = all(n in text for n in needles)
        check(label, hit, f"缺少：{[n for n in needles if n not in text]}")

    # ---- 3. 地理约束覆盖易错项
    print("\n=== 3. 地理约束覆盖 ===")
    for term, city in [
        ("平遥牛肉", "晋中"),
        ("太谷饼", "晋中"),
        ("浑源凉粉", "大同"),
        ("右玉羊肉", "朔州"),
        ("应县紫皮蒜", "朔州"),
        ("闻喜煮饼", "运城"),
    ]:
        # 该物产与所属城市应出现在相邻文本中
        pattern = rf"{term}[^。\n]{{0,20}}{city}|{city}[^。\n]{{0,20}}{term}"
        check(f"{term} 已绑定 {city}", re.search(pattern, text) is not None)

    # ---- 4. 变量引用
    print("\n=== 4. 变量引用 ===")
    check(f"用户问题变量使用 {{{{{USER_VAR}}}}}", f"{{{{{USER_VAR}}}}}" in text)
    check("文档中声明了变量对照表", "变量对照" in text)
    for v in KB_VARS:
        check(f"知识召回变量 {{{{{v}}}}} 有出现", f"{{{{{v}}}}}" in text)

    # 系统提示词块内不得出现平台变量（会被误解析）
    sys_blocks = re.findall(r"##\s*一、系统提示词(.*?)(?=\n##\s*二、)", text, re.DOTALL)
    if sys_blocks:
        vars_in_system = re.findall(r"\{\{[^}]+\}\}", sys_blocks[0])
        check(
            "系统提示词不含平台变量占位",
            not vars_in_system,
            f"发现：{vars_in_system}",
        )
    else:
        check("能定位系统提示词区块", False)

    # ---- 5. 代码围栏闭合
    print("\n=== 5. 代码围栏 ===")
    # 只统计「行首」的围栏 —— 提示词正文里可能出现内联的 ``` 字样
    # （例如教模型「不要用 ``` 代码块」），那不是围栏，不该计入配对。
    fences = len(re.findall(r"(?m)^\s*```", text))
    check(f"代码围栏成对（{fences} 个标记）", fences % 2 == 0)

    # 提示词代码块应以 text 标注
    check("提示词块标注为 text", text.count("```text") >= 2)

    # ---- 6. 篇幅合理性
    print("\n=== 6. 篇幅 ===")
    if sys_blocks:
        body = re.search(r"```text\n(.*?)```", sys_blocks[0], re.DOTALL)
        if body:
            n = len(body.group(1))
            check(f"系统提示词长度合理（{n} 字）", 400 <= n <= 2500, f"{n} 字")
        else:
            check("系统提示词代码块可提取", False)

    print(f"\n结果：通过 {ok_count}，失败 {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
