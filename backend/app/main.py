"""三晋智行 后端入口"""
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 将 backend 目录加入 sys.path 最前面，确保本地 app 包优先于 site-packages 中的同名包
# 这样既支持 `python -m uvicorn app.main:app`，也支持直接运行 `python app/main.py`
_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import agent, auth, itinerary, knowledge, preset
from app.core.config import (
    _force_utf8_stdout,
    cors_origin_list,
    db_path,
    display_host,
    frontend_built,
    frontend_dist_path,
    settings,
    startup_banner,
)
from app.db.database import Base, SessionLocal, engine, ensure_additive_columns
from app.services.auth_service import get_current_user_optional

# 尽早统一标准输出编码，避免中文/制表符在 GBK 控制台报错
_force_utf8_stdout()

# 注册全部 ORM 模型，确保建表覆盖知识库与缓存表
from app.models import itinerary as _itinerary_model  # noqa: F401
from app.models import knowledge as _knowledge_model  # noqa: F401
from app.models import user as _user_model  # noqa: F401
from app.services import frontend_builder, knowledge_service


def _describe_agent() -> str:
    provider = (settings.agent_provider or "deepseek").lower()
    if provider == "deepseek":
        if settings.deepseek_api_key:
            from app.services import workflow_sim

            workflow_sim.index.load()
            return (
                f"本地工作流（{settings.agent_deepseek_model} + 知识库 "
                f"{workflow_sim.index.size} 条问答对）"
            )
        return "未配置 DEEPSEEK_API_KEY，将本地降级"
    return (
        "讯飞工作流（已配置凭据）"
        if settings.xingchen_api_key
        else "讯飞工作流（未配置凭据，将本地降级）"
    )


def _print_startup_panel(host: str) -> None:
    # 面板含中文与制表符，Windows GBK 控制台会编码失败。
    # 这里一并兜底：即使打印出问题也不该影响正在处理的那个请求。
    try:
        print()
        print(startup_banner(host=host))
        print(f"\n  数据库：{db_path()}")
        print(f"  智能体对话：{_describe_agent()}")
        print(flush=True)
    except Exception as e:  # pragma: no cover
        print(f"[启动] 打印信息面板失败（已忽略）：{e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 开发环境建表
    Base.metadata.create_all(bind=engine)
    # 建表不会给已有表加列，这里补上后加的列（幂等）
    ensure_additive_columns()
    # 首次启动时自动导入知识库种子数据（已导入则跳过）
    db = SessionLocal()
    try:
        knowledge_service.ensure_knowledge_loaded(db)
    except Exception as e:  # pragma: no cover
        print(f"[启动] 知识库初始化失败（不影响服务启动）：{e}")
    try:
        from app.services.auth_service import ensure_default_admin

        ensure_default_admin(db)
    except Exception as e:  # pragma: no cover
        print(f"[启动] 默认管理员创建失败（不影响服务启动）：{e}")
    finally:
        db.close()

    # 前端自动构建：监听源码改动并重建（配合后端托管前端使用）
    watcher = frontend_builder.start_watcher()

    # 面板通常已由启动脚本（run.py）提前打印；若直接以 uvicorn 命令启动，
    # 则延后到首个请求时打印 —— 那时才能从 Host 头拿到真实端口。
    import os

    if not os.environ.get("SJX_BANNER_PRINTED"):
        print("\n  服务已启动，等待首个请求以打印访问地址……", flush=True)
    try:
        yield
    finally:
        if watcher is not None:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            except Exception:
                pass


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="三晋智行后端：RAG 知识库 + 大模型行程定制",
    lifespan=lifespan,
)

# CORS：来源由 CORS_ORIGINS 配置（逗号分隔）。
# 前后端同源时不生效；分开部署或换端口时靠它放行。
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.url_announced = False

# 无需登录即可访问的路径前缀（API 层）
_PUBLIC_API_PREFIXES = (
    "/api/auth/",       # 注册、登录、验证码
    "/api/health",      # 探活
    "/api/build",       # 前端构建标识
)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    """全局认证中间件：除白名单外，所有 /api/* 请求必须携带有效 token。

    放行规则：
    - 非 /api 开头（前端静态资源、SPA 回退）→ 放行
    - /api/auth/*、/api/health、/api/build → 放行
    - OPTIONS 预检请求 → 放行
    其余 /api/* → 校验 Authorization: Bearer <token>，无效返回 401。
    """
    path = request.url.path
    method = request.method

    # 非 API 路径放行（前端静态资源）
    if not path.startswith("/api/"):
        return await call_next(request)

    # 白名单 API 放行
    if any(path.startswith(p) for p in _PUBLIC_API_PREFIXES):
        return await call_next(request)

    # CORS 预检放行
    if method == "OPTIONS":
        return await call_next(request)

    # 校验 token
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    if not token:
        return JSONResponse(
            status_code=401,
            content={"detail": "未登录或登录已过期"},
        )

    # 复用依赖函数的解析逻辑（需要手动构造 db session）
    from app.services.auth_service import _decode_token_to_user

    db = SessionLocal()
    try:
        user = _decode_token_to_user(token, db)
    finally:
        db.close()

    if user is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "未登录或登录已过期"},
        )
    return await call_next(request)


@app.middleware("http")
async def announce_url_once(request, call_next):
    """首个请求时打印启动面板（此时可拿到真实 Host，含命令行覆盖的端口）

    若启动脚本（run.py）已提前打印过面板，则跳过，避免重复。
    """
    if not app.state.url_announced:
        app.state.url_announced = True
        import os

        if not os.environ.get("SJX_BANNER_PRINTED"):
            host = request.headers.get("host") or f"{display_host()}:{settings.app_port}"
            _print_startup_panel(host)
    return await call_next(request)


# 注册路由
app.include_router(auth.router, prefix="/api")
app.include_router(itinerary.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(preset.router, prefix="/api")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": app.version,
        "rag": settings.enable_rag,
        "cache": settings.cache_backend if settings.enable_generation_cache else "off",
        "frontend_served": bool(settings.serve_frontend and frontend_built()),
    }


def _frontend_build_id() -> str:
    """前端构建标识：index.html 的 mtime+size。

    每次 `npm run build` 都会重写 index.html（chunk 文件名带哈希），
    因此这个值会随之改变，可作为「有没有新版本」的判据。
    """
    index = frontend_dist_path() / "index.html"
    if not index.exists():
        return ""
    st = index.stat()
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


@app.get("/api/build")
def build_info():
    """供页面轮询「是否有新版本」。

    为什么需要：前端是构建产物，**已经打开的标签页**里跑的是旧 JS。
    改动重建后，旧页面不会自己变新 —— 用户必须手动刷新才能看到新内容
    （例如新加的景点百科超链接）。页面轮询这个接口，发现变化就自动更新，
    从而不再需要手动刷新。

    刻意做得极轻：只 stat 一个文件，不读盘、不查库。
    """
    return {
        "build": _frontend_build_id(),
        "frontend_served": bool(settings.serve_frontend and frontend_built()),
    }


# ---------------------------------------------------------------- 前端静态托管
# 打开后只需运行后端即可访问整个站点（http://host:port/），无需再跑 npm run dev。
# 注意：静态挂载必须放在所有 API 路由之后，否则会抢走 /api 前缀。
_FRONTEND_MOUNTED = False

if settings.serve_frontend and frontend_built():
    _dist = frontend_dist_path()
    # 带哈希文件名的资源，可放心长期缓存
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")
    _FRONTEND_MOUNTED = True


_INDEX_CACHE_CONTROL = "no-cache, must-revalidate"
_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _index_html_response(request) -> Optional[Response]:
    """前端入口 index.html 的响应（带 ETag，未变返回 304）。

    为什么要自己做 304：Starlette 的 304 逻辑在 `StaticFiles` 里，
    `FileResponse` 本身不做 `If-None-Match` 判断，会每次都重发正文。
    index.html 虽小，但每次页面加载都重发没有意义。

    为什么每次读盘、不做内存缓存：前端改动后会自动重新构建，
    内存缓存会让服务器继续吐旧的 index.html —— 那正是本次要修的问题。
    """
    index = frontend_dist_path() / "index.html"
    if not index.exists():
        return None
    stat = index.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    headers = {"Cache-Control": _INDEX_CACHE_CONTROL, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=index.read_bytes(), media_type="text/html", headers=headers)


@app.middleware("http")
async def spa_fallback(request, call_next):
    """SPA 路由回退 + 前端产物缓存策略。

    回退：只处理 404 且非 /api、非 /docs 等后端路径的请求，
    避免把接口 404 误变成一份 HTML。

    缓存策略（此前两者都没有 Cache-Control，导致「要刷新一下才生效」）：
    - **index.html 必须每次校验**。它是前端入口，引用的是带哈希的 chunk 文件名；
      若浏览器按启发式缓存了旧 index.html，重建后仍会去加载**已被构建删除的旧 chunk**，
      表现为「页面能打开，但内容是旧的，按 F5 才更新」。
      带 ETag 的 no-cache 未变时返回 304，在 3 Mbps 的服务器上开销极小。
    - **/assets/* 可长期缓存**。文件名内含内容哈希，内容永不改变，
      重建只会换新文件名，不存在取到旧内容的可能。
    """
    response = await call_next(request)
    path = request.url.path

    # ---- SPA 路由回退
    if (
        _FRONTEND_MOUNTED
        and response.status_code == 404
        and request.method == "GET"
        and not path.startswith(("/api", "/docs", "/redoc", "/openapi", "/assets"))
    ):
        fallback = _index_html_response(request)
        if fallback is not None:
            return fallback

    # ---- 缓存策略（按内容类型判定，兼容直接访问 / 与 /knowledge 两种情况）
    ctype = response.headers.get("content-type", "")
    if "text/html" in ctype:
        response.headers["Cache-Control"] = _INDEX_CACHE_CONTROL
    elif path.startswith("/assets/"):
        response.headers["Cache-Control"] = _ASSET_CACHE_CONTROL

    return response


if __name__ == "__main__":
    import uvicorn

    port = settings.app_port
    print(f"  访问地址：http://{display_host()}:{port}")
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=port,
        reload=True,
    )
