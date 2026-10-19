"""前端 TDZ 隐患检查

Vue SFC 的 `<script setup>` 整体是一个函数体，其中**顶层直接执行的语句**
（例如 `ensureSession()`）会在组件初始化时运行。若它们调用的函数读取了
在稍后才声明的 `let`/`const`，就会抛：

    ReferenceError: Cannot access 'x' before initialization

`vue-tsc` 与 `vite build` **都不会**报这类错误 —— 构建成功但页面白屏，
排查成本很高（本项目曾因此让「驿路谋士」整页丢失）。故单列此脚本。

检查方式：
1. 收集顶层 `let`/`const`/`var` 与 `function` 的声明位置
2. 找出顶层直接调用的函数
3. 若被调用函数体内引用了「声明位置晚于该调用」的变量 → 报告

用法：
    cd backend
    python scripts\\check_frontend_tdz.py
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
SRC = BACKEND_DIR.parent / "frontend" / "src"

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

_JS_KEYWORDS = {"if", "for", "while", "switch", "return", "await", "catch", "typeof"}


def _top_level_calls(lines: list) -> list:
    """返回 [(行号, 函数名)]，仅顶层（无缩进）的直接调用"""
    out = []
    for i, ln in enumerate(lines):
        if not ln.strip() or ln != ln.lstrip():
            continue
        if ln.lstrip().startswith(("//", "/*", "*", "}")):
            continue
        m = re.match(r"^([A-Za-z_$][\w$]*)\s*\(", ln)
        if m and m.group(1) not in _JS_KEYWORDS:
            out.append((i, m.group(1)))
    return out


def check_file(path: Path) -> list:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<script setup[^>]*>(.*?)</script>", text, re.DOTALL)
    if not m:
        return []
    lines = m.group(1).splitlines()

    decl_line: dict = {}
    fn_range: dict = {}
    for i, ln in enumerate(lines):
        mv = re.match(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)", ln)
        if mv:
            decl_line[mv.group(1)] = i
        mf = re.match(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", ln)
        if mf:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^(?:const|let|var|function|async function)\s", lines[j]):
                    end = j
                    break
            fn_range[mf.group(1)] = (i, end)

    problems = []
    for i, called in _top_level_calls(lines):
        rng = fn_range.get(called)
        if not rng:
            continue
        body = "\n".join(lines[rng[0] : rng[1]])
        for name, dline in decl_line.items():
            if dline > i and re.search(rf"\b{re.escape(name)}\b", body):
                problems.append(
                    f"{path.name}: 顶层调用 {called}()（第 {i + 1} 行）"
                    f"读取了其后声明的 {name}（第 {dline + 1} 行）"
                )
    return problems


def main() -> int:
    files = sorted(SRC.rglob("*.vue")) + sorted(SRC.rglob("*.ts"))
    if not files:
        print(f"✗ 未找到源码：{SRC}")
        return 1

    problems = []
    for p in files:
        problems.extend(check_file(p))

    print(f"扫描 {len(files)} 个文件（{SRC}）")
    if problems:
        print(f"\n发现 {len(problems)} 处 TDZ 隐患（会导致组件挂载失败、页面白屏）：")
        for t in problems:
            print("  ✗", t)
        print("\n修法：把相关 let/const 声明上移到调用之前，或把顶层调用移到文件末尾。")
        return 1
    print("\n✓ 未发现 TDZ 隐患")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
