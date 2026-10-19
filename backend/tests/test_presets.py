"""典藏线路数据与接口测试。

线路是人工策展内容，最容易出的错是「改数据时不小心破坏约束」，
所以这里把关键约束固化成测试：
  1. id 不重复、必填字段齐全
  2. `days >= len(cities)` —— 与行程生成的合理性规则一致，
     否则前端「一键套用」会被后端 400 拒绝（用户体验断裂）
  3. 府邑名必须是真实存在的府邑
  4. `highlights` 必须都能在知识库中查到，且属于该线路的府邑
  5. 接口行为：列表、按主题筛选、单条、404
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import preset_service

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def seed() -> dict:
    path = BACKEND_DIR / "data" / "knowledge_seed.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 数据约束


def test_routes_not_empty():
    assert len(preset_service.ALL_ROUTES) >= 15


def test_route_ids_unique():
    ids = [r["id"] for r in preset_service.ALL_ROUTES]
    assert len(ids) == len(set(ids)), "线路 id 有重复"


def test_required_fields_present():
    for r in preset_service.ALL_ROUTES:
        for key in ("id", "name", "route", "tagline", "why", "category", "cities", "days"):
            assert str(r.get(key) or "").strip(), f"{r.get('id')} 缺字段 {key}"


def test_days_cover_all_cities():
    """天数必须 >= 府邑数，否则套用后会被行程生成拒绝。"""
    for r in preset_service.ALL_ROUTES:
        assert r["days"] >= len(r["cities"]), (
            f"{r['name']}：{r['days']} 日 < {len(r['cities'])} 处府邑"
        )


def test_days_within_slider_range():
    """前端天数滑块是 1-10，线路天数必须在同一区间内，否则预填后无法对齐。"""
    for r in preset_service.ALL_ROUTES:
        assert 1 <= r["days"] <= 10, f"{r['name']} 天数 {r['days']} 超出滑块范围"


def test_cities_are_real(seed: dict):
    names = {c["name"] for c in seed["cities"]}
    for r in preset_service.ALL_ROUTES:
        unknown = [c for c in r["cities"] if c not in names]
        assert not unknown, f"{r['name']} 含未知府邑 {unknown}"


def test_validate_routes_reports_no_problem(seed: dict):
    names = {c["name"] for c in seed["cities"]}
    assert preset_service.validate_routes(names) == []


def test_highlights_exist_in_knowledge_base(seed: dict):
    """看点点位必须真实存在 —— 否则一键套用后生成的行程与宣传不符。"""
    city_of = {a["name"]: a["city_name"] for a in seed["attractions"]}
    for r in preset_service.ALL_ROUTES:
        for h in r["highlights"]:
            assert h in city_of, f"{r['name']} 的看点在知识库中不存在：{h}"


def test_highlights_belong_to_route_cities(seed: dict):
    """看点必须属于该线路的府邑（红色线路尤其容易写错归属）。"""
    city_of = {a["name"]: a["city_name"] for a in seed["attractions"]}
    for r in preset_service.ALL_ROUTES:
        for h in r["highlights"]:
            if h in city_of:
                assert city_of[h] in r["cities"], (
                    f"{r['name']} 的看点「{h}」属 {city_of[h]}，不在线路府邑内"
                )


def test_why_is_substantial():
    """「为何选这条路线」是核心卖点，不能写成一两句敷衍话。"""
    for r in preset_service.ALL_ROUTES:
        assert len(r["why"]) >= 60, f"{r['name']} 的 why 过短（{len(r['why'])} 字）"


def test_themes_are_known(seed: dict):
    known = set(seed["theme_tags"])
    for r in preset_service.ALL_ROUTES:
        bad = [t for t in r["themes"] if t not in known]
        assert not bad, f"{r['name']} 含未知主题 {bad}"


def test_red_routes_marked_official():
    """红色线路来自省文旅厅，应带「官方线路」角标并标注出处。"""
    for r in preset_service.RED_ROUTES:
        assert r.get("badge") == "官方线路"
        assert "文旅厅" in r.get("source", "")


# ---------------------------------------------------------------- 接口


def test_list_endpoint(client: TestClient):
    res = client.get("/api/presets")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == len(preset_service.ALL_ROUTES)
    assert data["categories"][0] == "全部"
    assert data["routes"][0]["id"]


def test_filter_by_category(client: TestClient):
    res = client.get("/api/presets", params={"category": "红色记忆"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == len(preset_service.RED_ROUTES)
    assert all(r["category"] == "红色记忆" for r in data["routes"])


def test_get_single_route(client: TestClient):
    first = preset_service.ALL_ROUTES[0]
    res = client.get(f"/api/presets/{first['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == first["id"]
    assert res.json()["why"]


def test_unknown_route_returns_404(client: TestClient):
    res = client.get("/api/presets/definitely-not-a-route")
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "preset_not_found"
