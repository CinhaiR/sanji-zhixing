"""前端自动构建服务

监听 `frontend/src` 等源文件，改动后自动执行 `npm run build`，
让「后端托管前端」这一模式下也能改完即刷新，不必手动敲构建命令。

设计要点：
- 用 `watchfiles`（uvicorn 的既有依赖，无需新增包）
- **防抖**：编辑器保存常常连续触发多次事件，合并到一次构建
- **串行**：构建期间再有改动只标记「待办」，不并发起多个 npm 进程
- 任何失败都不影响后端自身运行
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

from app.core.config import BACKEND_DIR, frontend_dist_path, settings

# 需要监听的前端源文件（相对 frontend 目录）
_WATCH_FILES = [
    "index.html",
    "package.json",
    "vite.config.ts",
    "tailwind.config.js",
    "postcss.config.js",
    "tsconfig.json",
    "tsconfig.node.json",
]
# 需要监听的目录（相对 frontend 目录）
_WATCH_DIRS = ["src"]
# 忽略的路径片段
_IGNORE = ("node_modules", "/dist/", "\\dist\\", ".tsbuildinfo")


def frontend_dir() -> Path:
    return BACKEND_DIR.parent / "frontend"


def _npm_command() -> Optional[List[str]]:
    """定位 npm 命令。Windows 下必须用 npm.cmd（npm.ps1 受执行策略限制）。"""
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        return None
    return [npm, "run", "build"]


async def _run_build(verbose: bool = False) -> tuple[bool, str]:
    """执行一次前端构建，返回 (是否成功, 输出摘要)"""
    cmd = _npm_command()
    if not cmd:
        return False, "未找到 npm（请确认已安装 Node.js 并加入 PATH）"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(frontend_dir()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
    except FileNotFoundError:
        return False, "无法启动 npm（路径或权限问题）"
    except Exception as e:  # pragma: no cover
        return False, f"构建进程异常：{type(e).__name__}: {e}"

    text = (out or b"").decode("utf-8", "replace")
    return proc.returncode == 0, text


def _summarize(output: str) -> str:
    """从构建输出里挑出关键行，避免刷屏"""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    keep = []
    for ln in lines:
        low = ln.lower()
        if "error" in low or "built in" in low or "modules transformed" in low:
            keep.append(ln)
    return " | ".join(keep[-3:]) if keep else (lines[-1] if lines else "")


async def watch_frontend() -> None:
    """持续监听前端源文件并在改动后重建（协程，交给 asyncio.create_task）"""
    try:
        from watchfiles import awatch
    except ImportError:  # pragma: no cover
        print("[前端监听] 未安装 watchfiles，跳过自动构建", flush=True)
        return

    fe = frontend_dir()
    if not fe.exists():
        print(f"[前端监听] 未找到前端目录：{fe}", flush=True)
        return

    paths = [fe / d for d in _WATCH_DIRS] + [fe / f for f in _WATCH_FILES]
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("[前端监听] 无可监听路径，跳过", flush=True)
        return

    debounce = max(0.3, settings.frontend_watch_debounce)
    print(
        f"[前端监听] 已启动：改动 {fe.name}/src 等文件后自动构建"
        f"（防抖 {debounce:g}s）",
        flush=True,
    )

    while True:
        try:
            # uvicorn reload 模式下，父进程不执行 lifespan，因此只有 worker
            # 会走到这里，不会出现两份监听导致的重复构建（已实测确认）。
            async for changes in awatch(
                *paths,
                debounce=int(debounce * 1000),
                step=100,
                recursive=True,
            ):
                # 过滤掉 dist/node_modules 等无关变更
                relevant = [
                    (_, p) for _, p in changes if not any(s in p for s in _IGNORE)
                ]
                if not relevant:
                    continue

                changed = Path(relevant[0][1]).name
                print(
                    f"\n[前端监听] 检测到改动（{changed}"
                    + (f" 等 {len(relevant)} 项" if len(relevant) > 1 else "")
                    + "），开始构建 ……",
                    flush=True,
                )
                t0 = time.time()
                ok, output = await _run_build()
                dt = time.time() - t0
                if ok:
                    print(
                        f"[前端监听] ✓ 构建完成（{dt:.1f}s）：{_summarize(output)}"
                        "\n            刷新浏览器即可看到改动",
                        flush=True,
                    )
                else:
                    print(
                        f"[前端监听] ✗ 构建失败（{dt:.1f}s）：{_summarize(output)}",
                        flush=True,
                    )
                    tail = "\n".join(output.splitlines()[-12:])
                    print(tail, flush=True)
        except asyncio.CancelledError:
            print("[前端监听] 已停止", flush=True)
            raise
        except Exception as e:  # pragma: no cover
            print(f"[前端监听] 监听异常，5 秒后重试：{type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(5)


def start_watcher() -> Optional[asyncio.Task]:
    """按配置启动监听任务；未启用或未托管前端时返回 None"""
    if not settings.auto_build_frontend:
        return None
    if not settings.serve_frontend:
        # 未托管前端说明用的是 Vite 开发服务器，它自带热更新
        print("[前端监听] 未启用后端托管，无需自动构建（Vite 已提供热更新）", flush=True)
        return None
    try:
        return asyncio.create_task(watch_frontend())
    except RuntimeError:  # 无运行中的事件循环
        return None
