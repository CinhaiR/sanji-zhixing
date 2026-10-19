"""全局配置"""
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

# backend 目录，用于把相对路径配置解析为绝对路径
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    app_name: str = "三晋智行后端"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    # 前端开发服务器地址（仅用于启动时打印访问入口，不影响 CORS）
    frontend_url: str = "http://localhost:5173"
    # 是否由后端直接托管前端构建产物（frontend/dist）。
    # 打开后只需运行后端，访问 http://localhost:8000 即可用整个站点，
    # 无需再单独跑 `npm run dev`。改前端代码后需重新 `npm run build`。
    serve_frontend: bool = True
    # 前端构建产物目录（相对项目根目录）
    frontend_dist: str = "frontend/dist"
    # 监听前端源码改动并自动重新构建（配合 SERVE_FRONTEND 使用）
    auto_build_frontend: bool = True
    # 改动防抖秒数：编辑器一次保存常触发多个事件，合并成一次构建
    frontend_watch_debounce: float = 1.0

    database_url: str = "sqlite:///./sanji_zhixing.db"

    # CORS 允许的来源，逗号分隔。
    # 默认含本地开发地址；部署后把公网地址/域名追加进来。前后端同源时其实用不到，
    # 但一旦分开部署或换端口，缺了它会被浏览器全量拦截。
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # DeepSeek 大模型配置（行程智能生成）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_timeout: int = 180
    # 推理模型的 reasoning token 与正文共享该预算，过小会导致正文被截断为空
    deepseek_max_tokens: int = 8000
    llm_max_retries: int = 2
    llm_temperature: float = 0.7

    # Coze 工作流配置（旧方案，已改用讯飞星辰）
    coze_api_key: str = ""
    coze_bot_id: str = ""
    coze_base_url: str = "https://api.coze.cn"

    # JWT 认证配置
    # 生产环境务必通过 .env 设置一个长随机字符串，否则任何人都能伪造 token
    jwt_secret_key: str = "sanji-zhixing-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    # token 有效期（分钟），默认 1 天
    jwt_expire_minutes: int = 60 * 24

    # 默认管理员账号（首次启动时自动创建，已存在则跳过）
    # 生产环境务必通过 .env 修改默认密码
    admin_username: str = "admin"
    # 仅作占位：没有任何配置时也不允许用一个「像样的密码」当默认值，
    # 避免默认口令随公开版本库扩散。首次部署必须在 .env 中设置 ADMIN_PASSWORD。
    admin_password: str = "change-me-on-first-run"

    # 讯飞星辰智能体工作流（备用方案，需平台侧授权后可用）
    # 控制台：智能体 API → 服务接口认证信息
    xingchen_api_key: str = ""
    xingchen_api_secret: str = ""
    xingchen_flow_id: str = ""
    xingchen_base_url: str = "https://xingchen-api.xf-yun.com/workflow/v1"
    xingchen_timeout: int = 120
    # 工作流开始节点里承载用户问题的参数名
    xingchen_input_key: str = "AGENT_USER_INPUT"
    # 是否把多轮历史传给工作流（工作流的「对话历史」开关打开时才有意义）
    xingchen_send_history: bool = True
    xingchen_history_rounds: int = 4

    # 驿路谋士对话链路：deepseek（本地模拟工作流）/ xingchen（讯飞工作流）
    agent_provider: str = "deepseek"

    # 本地工作流（DeepSeek + 知识库问答检索）
    # 对话用模型：推理模型会先输出思维链、首字延迟高，对话场景改用非推理模型
    agent_deepseek_model: str = "deepseek-chat"
    agent_timeout: int = 120
    agent_temperature: float = 0.3
    agent_max_tokens: int = 2000
    # 知识库问答对文件（相对项目根目录）
    agent_kb_file: str = "knowledge_txt/三晋智行_全量知识库（问答对）.txt"
    # 提示词文件（相对项目根目录）
    agent_prompt_file: str = "prompts/讯飞大模型节点提示词.md"
    # 每次检索注入提示词的问答对条数
    agent_retrieval_top: int = 12

    # 行程生成缓存
    enable_generation_cache: bool = True
    cache_ttl_seconds: int = 86400
    cache_backend: str = "db"  # db / memory / off

    # RAG 知识库
    enable_rag: bool = True
    knowledge_seed_file: str = "data/knowledge_seed.json"
    retrieval_max_attractions: int = 6
    retrieval_max_foods: int = 7
    # 检索时是否为「不确定的城市名」额外召回跨城同源特产
    retrieval_include_shared_foods: bool = True

    # 行程生成结果的地理准确性校验
    strict_geo_validation: bool = True
    # 命中错配时是否自动用本地特产替换（False 则仅标记不修改）
    auto_fix_geo: bool = True

    class Config:
        # 用绝对路径：`env_file=".env"` 是相对「当前工作目录」解析的，
        # 一旦从别的目录启动（如 IDE 直接跑 app/main.py）就读不到 .env，
        # 表现为「明明填了 Key 却报未配置」。
        env_file = str(BACKEND_DIR / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()


def db_path() -> str:
    """把 sqlite 相对路径解析为 backend 目录下的绝对路径。

    避免在项目根目录或 backend/app 下启动时另建一份空库。
    """
    url = settings.database_url
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix) :]
        if raw == ":memory:":
            return raw
        p = Path(raw)
        if not p.is_absolute():
            p = (BACKEND_DIR / raw).resolve()
        return str(p)
    return url


def resolved_database_url() -> str:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        return f"sqlite:///{db_path()}"
    return url


def knowledge_seed_path() -> Path:
    p = Path(settings.knowledge_seed_file)
    return p if p.is_absolute() else (BACKEND_DIR / p)


def cors_origin_list() -> list:
    """解析 CORS 来源配置（逗号分隔），并顺带允许无端口的纯域名写法"""
    raw = settings.cors_origins or ""
    out = []
    for item in raw.split(","):
        v = item.strip().rstrip("/")
        if v and v not in out:
            out.append(v)
    return out


# 监听 0.0.0.0 时，浏览器需要用 127.0.0.1 才能打开
_DISPLAY_HOSTS = {"0.0.0.0": "127.0.0.1", "::": "127.0.0.1", "": "127.0.0.1"}


def display_host() -> str:
    return _DISPLAY_HOSTS.get(settings.app_host, settings.app_host)


def backend_url(host: Optional[str] = None) -> str:
    """后端访问地址。

    host 显式传入时以它为准（形如 `127.0.0.1:8014`）—— 因为 `uvicorn --port`
    会覆盖 .env 里的 APP_PORT，只按配置值打印会把用户引到错端口。
    """
    if host:
        return f"http://{host}"
    return f"http://{display_host()}:{settings.app_port}"


def frontend_url() -> str:
    return settings.frontend_url.rstrip("/")


def frontend_dist_path() -> Path:
    """前端构建产物目录（frontend/dist）"""
    p = Path(settings.frontend_dist)
    if not p.is_absolute():
        p = BACKEND_DIR.parent / p
    return p


def frontend_built() -> bool:
    return (frontend_dist_path() / "index.html").exists()


def _disp_width(text: str) -> int:
    """终端显示宽度：中文/全角字符占 2 列，其余占 1 列"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _force_utf8_stdout() -> None:
    """让标准输出以 UTF-8 编码。

    Windows 控制台默认 GBK，打印面板里的「▶」「│」等字符会抛
    UnicodeEncodeError —— 而面板打印发生在请求处理途中，异常会直接
    把请求变成 500。这里统一改成 UTF-8（errors=replace 兜底）。
    """
    import sys

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _supports_unicode() -> bool:
    """当前 stdout 能否安全输出制表符（GBK 控制台不能）"""
    import sys

    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


def startup_banner(host: Optional[str] = None) -> str:
    """启动信息面板：把该访问的地址直接列出来，省得去翻文档。

    控制台编码不是 UTF-8 时自动降级为纯 ASCII 边框，避免打印失败。
    """
    be = backend_url(host)
    served = bool(settings.serve_frontend and frontend_built())
    width = 58
    uni = _supports_unicode()

    # 宽字符用 1 个字符占位，ASCII 下也能对齐
    mark = "▶" if uni else "*"
    tl, tr, bl, br, hz, vt = (
        ("┌", "┐", "└", "┘", "─", "│") if uni else ("+", "+", "+", "+", "-", "|")
    )

    def line(text: str = "") -> str:
        shown = f"  {text}"
        return vt + shown + " " * max(0, width - _disp_width(shown)) + vt

    top = tl + hz * width + tr
    bottom = bl + hz * width + br

    if served:
        # 后端已托管前端：一个地址搞定，无需再开第二个终端
        rows = [
            top,
            line(f"{settings.app_name}"),
            line(),
            line(f"{mark} 打开这个地址即可使用（前后端同一端口）"),
            line(f"    {be}/"),
            line(),
            line(f"{mark} 其它入口"),
            line(f"    {be}/docs         接口文档"),
            line(f"    {be}/api/health   探活"),
            line(),
            line(f"{mark} 提示"),
            line("    已托管前端构建产物，无需再运行 npm run dev"),
        ]
        if settings.auto_build_frontend:
            rows.append(line("    改前端代码会自动重新构建，刷新浏览器即可"))
        else:
            rows.append(line("    改前端代码后需手动：cd frontend && npm.cmd run build"))
        rows.append(bottom)
    else:
        rows = [
            top,
            line(f"{settings.app_name}"),
            line(),
            line(f"{mark} 前端界面（用浏览器打开这个）"),
            line(f"    {frontend_url()}"),
            line(),
            line(f"{mark} 后端接口"),
            line(f"    {be}/docs         接口文档"),
            line(f"    {be}/api/health   探活"),
            line(),
            line(f"{mark} 启动提示"),
            line("    前端需另开一个终端：cd frontend && npm.cmd run dev"),
            line("    或先构建一次以由后端托管：npm.cmd run build"),
            bottom,
        ]
    return "\n".join(rows)
