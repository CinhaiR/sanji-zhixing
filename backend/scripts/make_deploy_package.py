"""生成阿里云部署包

用法：
    cd backend
    python scripts\\make_deploy_package.py

产出：项目根目录下 sjx-deploy.zip
内含后端源码、前端构建产物、知识库与提示词；
**不含** .env、本地数据库、测试与缓存（密钥需单独上传，见 DEPLOY.md）。
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

# 需要打包的内容：(源路径, 包内前缀)
INCLUDES = [
    (BACKEND_DIR / "app", "backend/app"),
    (BACKEND_DIR / "data", "backend/data"),
    (BACKEND_DIR / "requirements.txt", "backend/requirements.txt"),
    # 一键启动入口：文档与手册都让用户在服务器上跑 `python run.py`
    (BACKEND_DIR / "run.py", "backend/run.py"),
    # 配置模板（不含真实密钥），服务器上照着写 .env 用
    (BACKEND_DIR / ".env.example", "backend/.env.example"),
    # 运维脚本：手册里让在服务器上跑
    #   python scripts\import_knowledge.py    （更新知识库）
    #   python scripts\export_knowledge_qa.py （重新导出问答库）
    #   python scripts\verify_*.py            （校验）
    # 早先漏了这一项，服务器上根本没有 scripts\ 目录，这些命令无从执行。
    (BACKEND_DIR / "scripts", "backend/scripts"),
    (ROOT / "frontend" / "dist", "frontend/dist"),
    (ROOT / "prompts", "prompts"),
    # 服务器侧 .env 权限加固脚本，部署后直接在 C:\sanjin 下运行
    (ROOT / "scripts" / "harden_env.ps1", "scripts/harden_env.ps1"),
]

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "node_modules"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".db")


def should_skip(p: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in p.parts):
        return True
    if p.suffix in EXCLUDE_SUFFIX:
        return True
    if ".bak-" in p.name:
        return True
    # harden_env.ps1 生成的 ACL 备份（.env.acl.bak 等）：属于本机产物，
    # 内含 SID 信息，没有上传价值，且不该混进部署包
    if p.name.endswith(".acl.bak"):
        return True
    return False


def active_kb_file() -> Path:
    """对话实际使用的知识库文件（由 AGENT_KB_FILE 决定）"""
    from app.core.config import knowledge_seed_path  # noqa: F401  (确保 app 包可导入)
    from app.core.config import settings

    p = Path(settings.agent_kb_file)
    return p if p.is_absolute() else (ROOT / p)


def main() -> int:
    # 只打包对话真正用到的那一个知识库文件：
    # knowledge_txt/ 下还有列表形式的备用导出，线上用不到，徒增体积与困惑。
    kb = active_kb_file()
    if not kb.exists():
        print(f"✗ 找不到对话知识库：{kb}")
        print("  可先执行：python scripts\\export_knowledge_qa.py")
        return 1

    includes = INCLUDES + [(kb, f"knowledge_txt/{kb.name}")]

    missing = [str(src) for src, _ in includes if not src.exists()]
    if missing:
        print("✗ 以下路径不存在，无法打包：")
        for m in missing:
            print("   ", m)
        if any("dist" in m for m in missing):
            print("\n提示：前端尚未构建，请先执行：")
            print("    cd frontend && npm.cmd run build")
        return 1

    # ---- requirements.txt 必须纯 ASCII ----------------------------------------
    # pip 解析 requirements 文件时用**系统首选编码**（中文 Windows = cp936），
    # 而不是 UTF-8。所以「无 BOM 的 UTF-8 + 中文注释」会让服务器上 pip 直接抛
    # UnicodeDecodeError（traceback 落在 pip/_internal/req/req_file.py 的 auto_decode）。
    # 本机 locale 同样是 cp936，因此这里能拦住；注释请一律用英文。
    req = BACKEND_DIR / "requirements.txt"
    if req.exists():
        raw = req.read_bytes()
        offenders = [
            (i + 1, line)
            for i, line in enumerate(raw.decode("utf-8", "replace").splitlines())
            if any(ord(ch) > 127 for ch in line)
        ]
        if offenders:
            print("✗ requirements.txt 含非 ASCII 字符 —— 服务器上 pip 会解析失败：")
            for n, line in offenders:
                print(f"    行{n}: {line}")
            print("\n  pip 用的是系统首选编码（中文 Windows 为 cp936），不是 UTF-8。")
            print("  请把注释改成英文（或删除），保持该文件纯 ASCII。")
            return 1

    out = ROOT / "sjx-deploy.zip"
    if out.exists():
        out.unlink()

    files = 0
    total = 0
    skipped_db: list = []

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, prefix in includes:
            if src.is_file():
                zf.write(src, prefix)
                files += 1
                total += src.stat().st_size
                continue
            for p in sorted(src.rglob("*")):
                if not p.is_file() or should_skip(p):
                    continue
                if p.suffix == ".db" or ".db.bak-" in p.name:
                    skipped_db.append(str(p.relative_to(ROOT)))
                    continue
                rel = p.relative_to(src)
                zf.write(p, f"{prefix}/{rel.as_posix()}")
                files += 1
                total += p.stat().st_size

    size = out.stat().st_size
    print(f"✓ 已生成：{out}")
    print(f"  文件数 {files}｜未压缩 {total / 1024:.0f} KB｜压缩后 {size / 1024:.0f} KB")
    if skipped_db:
        print("\n  已排除的本地数据库（不上传）：")
        for d in skipped_db:
            print("   ", d)

    print("\n关键内容自检：")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        checks = [
            (f"knowledge_txt/{kb.name}", "对话知识库"),
            ("backend/app/main.py", "后端入口"),
            ("backend/run.py", "一键启动脚本"),
            ("backend/requirements.txt", "依赖清单"),
            ("backend/.env.example", "配置模板"),
            ("backend/data/knowledge_seed.json", "行程知识库种子"),
            ("backend/scripts/import_knowledge.py", "知识库导入脚本（服务器上更新方志用）"),
            ("frontend/dist/index.html", "前端首页"),
            ("prompts/讯飞大模型节点提示词.md", "对话提示词"),
            ("scripts/harden_env.ps1", "服务器密钥权限加固脚本"),
        ]
        for path, label in checks:
            hit = any(n == path for n in names)
            print(f"  {'✓' if hit else '✗'} {label}（{path}）")

        assets = [n for n in names if n.startswith("frontend/dist/assets/")]
        print(f"  {'✓' if assets else '✗'} 前端静态资源（{len(assets)} 个文件）")

        others = [
            n
            for n in names
            if n.startswith("knowledge_txt/") and n != f"knowledge_txt/{kb.name}"
        ]
        print(f"  ✓ knowledge_txt 仅含 1 个文件（排除的备用导出 {len(others)} 个）")

        if any(n.endswith(".env") for n in names):
            print("  ⚠ 包内含 .env，请确认是否要连同密钥一起上传")
        else:
            print("  ✓ 未包含 .env（密钥需单独上传，见 DEPLOY.md 3.3）")

        if any(n.endswith(".acl.bak") for n in names):
            print("  ⚠ 包内含 ACL 备份文件（.acl.bak），本机产物不应上传")
        else:
            print("  ✓ 未包含 ACL 备份文件")

        key_hits = []
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if key:
            for n in names:
                try:
                    if key in zf.read(n).decode("utf-8", "ignore"):
                        key_hits.append(n)
                except Exception:
                    pass
            if key_hits:
                print(f"  ✗ 包内明文出现当前 DeepSeek Key: {key_hits}")
            else:
                print("  ✓ 包内未出现当前 DeepSeek Key 明文")
        else:
            print("  · 未设置环境变量 DEEPSEEK_API_KEY，跳过密钥明文扫描")

    print("\n下一步：上传到 ECS 的 C:\\sanjin 解压，详见 DEPLOY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
