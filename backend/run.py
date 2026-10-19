"""一键启动入口

在 PyCharm 里直接运行本文件即可，无需再开终端跑 `npm run dev`：

    python backend/run.py

它会启动 FastAPI，并**顺带托管前端构建产物**（frontend/dist），
因此访问 http://localhost:8000/ 就是完整站点，前后端同一端口。

改前端代码后需重新构建一次（后端会提示）：
    cd frontend && npm.cmd run build
"""
from __future__ import annotations

import sys
from pathlib import Path

# 保证无论从哪个目录运行都能 import app 包
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def main() -> None:
    import os

    # 面板由本脚本在启动前打印，关掉中间件里的重复打印
    os.environ["SJX_BANNER_PRINTED"] = "1"

    import uvicorn

    from app.core.config import (
        _force_utf8_stdout,
        db_path,
        frontend_built,
        frontend_url,
        settings,
        startup_banner,
    )

    _force_utf8_stdout()

    host = settings.app_host
    port = settings.app_port

    # 面板里的地址用 127.0.0.1 展示（0.0.0.0 无法直接打开）
    print()
    print(startup_banner(host=f"{display_host_for_banner()}:{port}"))
    print(f"\n  数据库：{db_path()}")
    if not frontend_built():
        print("  ⚠ 未找到前端构建产物，页面打不开。请先执行：")
        print("      cd frontend && npm.cmd run build")
    print(flush=True)

    # reload=True 便于开发；如需关掉可传 --no-reload
    reload = "--no-reload" not in sys.argv
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[str(_BACKEND_DIR)] if reload else None,
    )


def display_host_for_banner() -> str:
    from app.core.config import display_host

    return display_host()


if __name__ == "__main__":
    main()
