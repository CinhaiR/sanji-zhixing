"""前端托管与缓存策略测试。

背景：此前 SPA 的 `index.html` 与 `/assets/*` **都没有 Cache-Control**，
浏览器按 `Last-Modified` 做启发式缓存，重建后仍加载**已被构建删除的旧 chunk** ——
表现为「页面能打开，但内容是旧的，按 F5 刷新才更新」（用户实际遇到：
三晋方志不显示超链接，刷新后才出现）。

这里的断言都是为了不让这个问题复发。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import frontend_dist_path
from app.main import app

DIST = frontend_dist_path()


@pytest.fixture(scope="module")
def first_asset() -> str:
    assets = DIST / "assets"
    if not assets.exists():
        pytest.skip("前端未构建")
    files = sorted(assets.glob("*.js")) + sorted(assets.glob("*.css"))
    if not files:
        pytest.skip("前端未构建")
    return f"/assets/{files[0].name}"


def _skip_if_no_build(client: TestClient):
    r = client.get("/")
    if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
        pytest.skip("前端未构建或未开启托管")


def test_html_must_revalidate(client: TestClient):
    """前端壳必须每次向服务器校验，否则重建后浏览器还在用旧页面。"""
    _skip_if_no_build(client)
    for path in ("/", "/knowledge", "/preset"):
        r = client.get(path)
        cc = r.headers.get("cache-control", "")
        assert "no-cache" in cc, f"{path} 的 Cache-Control 应为 no-cache，实际：{cc!r}"


def test_html_supports_304(client: TestClient):
    """带 ETag 的 no-cache 在内容未变时应返回 304，避免每次重发正文。"""
    _skip_if_no_build(client)
    r1 = client.get("/")
    etag = r1.headers.get("etag")
    assert etag, "index.html 应带 ETag"
    r2 = client.get("/", headers={"If-None-Match": etag})
    assert r2.status_code == 304, f"内容未变应返回 304，实际 {r2.status_code}"


def test_hashed_assets_are_immutable(client: TestClient, first_asset: str):
    """带内容哈希的资源可长期缓存（文件名变了才会换 URL）。"""
    r = client.get(first_asset)
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "immutable" in cc and "max-age" in cc, f"资源缓存策略不对：{cc!r}"


def test_missing_asset_is_404_not_html(client: TestClient):
    """缺失的 chunk 必须 404 —— 若被 SPA 回退成 HTML，
    浏览器会把 HTML 当 JS 解析，报错信息会非常难查。"""
    _skip_if_no_build(client)
    r = client.get("/assets/definitely-missing-chunk.js")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


def test_api_404_stays_json(client: TestClient):
    """接口 404 不能被 SPA 回退成 HTML。"""
    r = client.get("/api/definitely-not-an-endpoint")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


def test_api_responses_are_not_cached(client: TestClient):
    """接口不应带长缓存头，避免前端拿到过期数据。"""
    r = client.get("/api/health")
    cc = r.headers.get("cache-control", "")
    assert "immutable" not in cc
    assert "max-age=31536000" not in cc


def test_index_html_references_existing_assets():
    """入口引用的资源必须都在 dist 里 —— 缺一个就是白屏。"""
    index = DIST / "index.html"
    if not index.exists():
        pytest.skip("前端未构建")
    html = index.read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
    assert refs, "index.html 未引用任何资源"
    missing = [r for r in refs if not (DIST / r.lstrip("/")).exists()]
    assert not missing, f"index.html 引用了不存在的资源：{missing}"


# ---------------------------------------------------------------- 版本自动更新


def test_build_endpoint(client: TestClient):
    """`/api/build` 供页面轮询「是否有新版本」。"""
    r = client.get("/api/build")
    assert r.status_code == 200
    data = r.json()
    assert "build" in data and "frontend_served" in data
    if not (DIST / "index.html").exists():
        assert data["build"] == ""
        return
    assert data["build"], "已构建时应返回非空 build 标识"
    assert re.fullmatch(r"[0-9a-f]+-[0-9a-f]+", data["build"]), data["build"]


def test_build_id_matches_index_etag(client: TestClient):
    """build 标识与 index.html 的 ETag 同源，两个机制不能各说各话。"""
    r = client.get("/api/build")
    if not r.json().get("build"):
        pytest.skip("前端未构建")
    etag = client.get("/").headers.get("etag", "")
    assert etag.strip('"') == r.json()["build"], (
        f"/api/build 与 index.html 的 ETag 不一致：{r.json()['build']} vs {etag}"
    )


def test_build_id_changes_when_index_html_changes():
    """构建标识必须能反映 index.html 的变化，否则自动更新永远不会触发。"""
    index = DIST / "index.html"
    if not index.exists():
        pytest.skip("前端未构建")
    from app.main import _frontend_build_id

    before = _frontend_build_id()
    st = index.stat()
    try:
        # 写入相同内容但改变 mtime（模拟重新构建）
        index.write_bytes(index.read_bytes() + b"\n")
        after = _frontend_build_id()
        assert after != before, "index.html 变化后 build 标识未变，自动更新会失效"
    finally:
        # 还原内容与时间戳，避免影响正在运行的页面
        import os

        index.write_bytes(index.read_bytes()[:-1])
        os.utime(index, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert _frontend_build_id() == before, "测试未正确还原 index.html"

