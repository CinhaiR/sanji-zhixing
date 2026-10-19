"""行程保存接口的守卫测试。

背景（用户报告的 bug）：在「行驿日程」页未定制行程时，仍能把内容存进
「我的通关文牒」。前端此前会渲染一个标题为「待定制行程」的占位对象
（`days: []`），页面上摆着「💾 保存此案」按钮，看起来可以保存。

本文件守两件事：
1. **空行程必须被接口拒绝** —— 即使前端守卫被绕过，也不会产生一条空文牒。
2. **正常行程必须仍能保存** —— 修复不能误伤正常流程。

配套的前端改动：
- `ScheduleView.vue` 用 `v-if="hasPlan"` 隐藏整块摘要/操作区，无行程时不存在保存入口；
- `stores/itinerary.ts` 的 `save()` 保留 `days.length === 0` 早退；
- `ScheduleView.vue` 用 `computed` 跟踪 `store.currentPlan`（原来 `const plan = store.currentPlan`
  只捕获旧对象引用，导致「载入此案 / 删除此案」点了没反应）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def make_plan(days: int = 1) -> dict:
    return {
        "title": "三晋智行·太原1日测试行程",
        "destinations": ["太原"],
        "highlights": ["晋祠"],
        "rationale": "测试用说明",
        "days": [
            {
                "theme": "古建营造",
                "title": f"太原·第{i + 1}日晋祠",
                "totalHours": "5",
                "suggestedDeparture": "09:00",
                "localSpecialties": "太原头脑",
                "activities": [
                    {
                        "time": "09:00 - 12:00",
                        "place": "晋祠",
                        "type": "参访",
                        "desc": "宋代建筑实例",
                        "tips": "宜早到",
                    }
                ],
            }
            for i in range(days)
        ],
    }


def empty_plan() -> dict:
    """前端未定制行程时展示的占位对象"""
    return {"title": "待定制行程", "destinations": [], "highlights": [], "days": []}


# ---------------------------------------------------------------- 空行程必须被拒


def test_empty_plan_rejected(client: TestClient):
    r = client.post("/api/itinerary/save", json=empty_plan())
    assert r.status_code == 400, f"空行程应被拒绝，实际 {r.status_code}"
    assert r.json()["detail"]["code"] == "empty_itinerary"


def test_empty_plan_does_not_persist(client: TestClient):
    """被拒之后不应在列表里留下任何记录。"""
    before = len(client.get("/api/itinerary/list").json())
    client.post("/api/itinerary/save", json=empty_plan())
    after = client.get("/api/itinerary/list").json()
    assert len(after) == before, f"空行程被写入了：{after}"


def test_plan_with_days_but_empty_activities_is_allowed(client: TestClient):
    """有 days 就算有内容（哪怕当日活动为空）—— 守卫只看 days 是否为空，
    避免把「用户手工清空了某天活动」误判成未定制。"""
    plan = make_plan(days=1)
    plan["days"][0]["activities"] = []
    r = client.post("/api/itinerary/save", json=plan)
    assert r.status_code in (200, 201), r.text
    client.delete(f"/api/itinerary/{r.json()['id']}")


# ---------------------------------------------------------------- 正常流程不受影响


@pytest.mark.parametrize("days", [1, 3])
def test_valid_plan_can_be_saved(client: TestClient, days: int):
    r = client.post("/api/itinerary/save", json=make_plan(days))
    assert r.status_code in (200, 201), r.text
    saved = r.json()
    assert saved["id"]
    assert len(saved["days"]) == days

    listed = client.get("/api/itinerary/list").json()
    assert any(x["id"] == saved["id"] for x in listed), "保存后应出现在通关文牒列表里"

    client.delete(f"/api/itinerary/{saved['id']}")
    assert not any(
        x["id"] == saved["id"] for x in client.get("/api/itinerary/list").json()
    ), "删除后不应仍在列表里"


def test_saved_plan_keeps_days_shape(client: TestClient):
    """列表接口必须带回 days —— 前端「载入此案」依赖它。

    若列表只返回摘要，`store.restore()` 会拿到 undefined，
    载入后页面仍是空的（正是本次一并修掉的那类问题）。
    """
    r = client.post("/api/itinerary/save", json=make_plan(2))
    saved_id = r.json()["id"]
    try:
        item = next(x for x in client.get("/api/itinerary/list").json() if x["id"] == saved_id)
        assert "days" in item and len(item["days"]) == 2
        day = item["days"][0]
        for key in ("title", "activities", "localSpecialties"):
            assert key in day, f"days[0] 缺少字段 {key}（前端将无法渲染）"
    finally:
        client.delete(f"/api/itinerary/{saved_id}")
