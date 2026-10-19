"""部署产物约束测试。

这里守的都是「在本机能跑、到服务器上才炸」的坑 —— 单靠人记很容易复发。
"""
from __future__ import annotations

import locale
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent


def test_requirements_is_pure_ascii():
    """`requirements.txt` 必须是纯 ASCII。

    坑的由来：pip 解析 requirements 文件时用的是**系统首选编码**
    （中文 Windows 为 cp936），而不是 UTF-8。若文件是「无 BOM 的 UTF-8 + 中文注释」，
    服务器上 `pip install -r requirements.txt` 会在解析阶段就抛
    `UnicodeDecodeError`，traceback 落在 `pip/_internal/req/req_file.py` 的 `auto_decode`，
    看起来像是「下载依赖报错」，其实与网络无关。

    本机 locale 也是 cp936，所以这个断言在本机就有意义。
    """
    req = BACKEND / "requirements.txt"
    assert req.exists(), "找不到 backend/requirements.txt"
    raw = req.read_bytes()

    offenders = [
        (i + 1, line)
        for i, line in enumerate(raw.decode("utf-8", "replace").splitlines())
        if any(ord(ch) > 127 for ch in line)
    ]
    assert not offenders, (
        f"requirements.txt 含非 ASCII 字符，服务器上 pip 会解析失败：{offenders}"
        "（注释请用英文）"
    )


def test_requirements_decodes_under_system_encoding():
    """按系统首选编码（服务器上就是它）也必须能解码。"""
    raw = (BACKEND / "requirements.txt").read_bytes()
    enc = locale.getpreferredencoding(False)
    try:
        raw.decode(enc)
    except UnicodeDecodeError as e:  # pragma: no cover - 触发即说明上一条测试也挂了
        pytest.fail(f"无法用系统编码 {enc} 解码 requirements.txt：{e}")


def test_requirements_lists_auth_dependencies():
    """认证所需依赖必须在清单里，否则服务器装完仍起不来。"""
    text = (BACKEND / "requirements.txt").read_text(encoding="utf-8").lower()
    for pkg in ("python-jose", "bcrypt", "pillow"):
        assert pkg in text, f"requirements.txt 缺少认证依赖：{pkg}"


def test_key_runtime_files_are_ascii_safe_for_tools():
    """`.env.example` 会被 python-dotenv 以 UTF-8 显式读取（中文无妨），
    但 requirements/pip 那条路不行 —— 这里记录差异，避免以后又把注释加回去。
    """
    # .env.example 允许中文（dotenv 显式 UTF-8）
    env_example = BACKEND / ".env.example"
    if env_example.exists():
        env_example.read_text(encoding="utf-8")  # 能读即通过
