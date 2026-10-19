"""API 冒烟测试

覆盖知识库接口、缓存接口、行程列表、参数校验、知识库重载，
**不调用大模型**（避免消耗额度，也不受网络波动影响）。

运行：
    cd backend
    python scripts\\smoke_api.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.auth_service import create_access_token  # noqa: E402

ok = 0
fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {label}")
    else:
        fail += 1
        print(f"  ✗ {label} {extra}")


# 全局认证中间件要求 /api/* 携带有效 JWT（白名单只有 /api/auth/*、/api/health、/api/build）。
# 这里直接签发一个管理员 token 注入请求头 —— 不查库、不发真实登录请求，
# 因此冒烟脚本仍可脱库运行。缺了这一步，除健康检查外全部接口都会 401。
_TOKEN = create_access_token(user_id=1, username="smoke", is_admin=True)
AUTH = {"Authorization": f"Bearer {_TOKEN}"}

with TestClient(app, headers=AUTH) as client:
    print("=== 健康检查 ===")
    r = client.get("/api/health")
    check("GET /api/health", r.status_code == 200, r.text[:200])
    print("   ", r.json())

    print("\n=== 鉴权 ===")
    r = client.get("/api/knowledge/cities", headers={"Authorization": ""})
    check("未带 token 访问受保护接口返回 401", r.status_code == 401, f"实际 {r.status_code}")
    r = client.get("/api/knowledge/cities", headers=AUTH)
    check("带 token 可正常访问", r.status_code == 200, f"实际 {r.status_code}")

    print("\n=== 知识库接口 ===")
    r = client.get("/api/knowledge/cities")
    cities = r.json()
    check("GET /api/knowledge/cities", r.status_code == 200 and len(cities) == 11, str(len(cities)))
    print("    府邑：", "、".join(c["name"] for c in cities))

    r = client.get("/api/knowledge/stats")
    check("GET /api/knowledge/stats", r.status_code == 200, r.text[:200])
    st = r.json()
    print(f"    统计：城市{st['cities']} 景点{st['attractions']} 特产{st['foods']} 指纹{st['knowledge_fingerprint']}")

    r = client.get("/api/knowledge/search", params={"q": "木塔"})
    check("GET /api/knowledge/search?q=木塔", r.status_code == 200 and r.json()["total"] > 0, r.text[:200])
    print("    命中：", [i["name"] for i in r.json()["items"]])

    r = client.get("/api/knowledge/search", params={"q": "牛肉", "city": "晋中"})
    items = r.json()["items"]
    check("GET search 带 city 过滤", all(i["city"] == "jinzhong" for i in items), str(items))

    r = client.get("/api/knowledge/items", params={"city": "朔州"})
    check("GET /api/knowledge/items?city=朔州", r.status_code == 200 and len(r.json()) >= 7, r.text[:150])
    print(f"    朔州条目 {len(r.json())} 条：", [i["name"] for i in r.json()])

    r = client.get("/api/knowledge/items", params={"kind": "food", "city": "shuozhou"})
    check("items kind+city", all(i["kind"] == "food" for i in r.json()))

    first_id = client.get("/api/knowledge/items", params={"limit": 1}).json()[0]["id"]
    r = client.get(f"/api/knowledge/items/{first_id}")
    check(f"GET /api/knowledge/items/{first_id}", r.status_code == 200, r.text[:150])

    r = client.get("/api/knowledge/items/999999")
    check("GET 不存在条目返回 404", r.status_code == 404, str(r.status_code))

    r = client.get("/api/knowledge/validate", params={"text": "在朔州吃平遥牛肉", "city": "朔州"})
    check("GET /api/knowledge/validate 检出错配", r.status_code == 200 and len(r.json()["issues"]) > 0, r.text[:200])
    for i in r.json()["issues"]:
        print("    ", i["kind"], "|", i["detail"])

    r = client.get("/api/knowledge/validate", params={"text": "在朔州吃右玉羊肉", "city": "朔州"})
    check("validate 正确内容不报错", r.json()["issues"] == [], r.text[:200])

    print("\n=== 缓存接口 ===")
    r = client.get("/api/itinerary/cache/stats")
    check("GET cache/stats", r.status_code == 200, r.text[:200])
    print("   ", r.json())

    print("\n=== 行程接口（mock 路径，不调用大模型）===")
    r = client.get("/api/itinerary/list")
    check("GET /api/itinerary/list", r.status_code == 200, r.text[:150])
    print(f"    已保存行程 {len(r.json())} 条")

    print("\n=== 参数校验 ===")
    r = client.post("/api/itinerary/generate", json={"destinations": [], "days": 3})
    check("空目的地应 422", r.status_code == 422, str(r.status_code))
    r = client.post("/api/itinerary/generate", json={"destinations": ["朔州"], "days": 99})
    check("天数超限应 422", r.status_code == 422, str(r.status_code))

    print("\n=== 知识库重载 ===")
    r = client.post("/api/knowledge/reload")
    check("POST /api/knowledge/reload", r.status_code == 200, r.text[:250])
    if r.status_code == 200:
        print("   ", r.json())

    print("\n=== 智能体接口 ===")
    r = client.get("/api/agent/status")
    check("GET /api/agent/status", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        st = r.json()
        print("    已配置工作流：", st["configured"], "| provider:", st["provider"])
        print("    说明：", st["message"])
        # 状态接口不得泄露凭据本身：
        # flow_id / input_key 是参数名与工作流标识（非密钥），可公开；
        # 但 api_key / api_secret 及其值绝不能出现。
        body_text = r.text
        check("status 不含 api_key 相关字段", "api_key" not in body_text.lower(), body_text[:200])
        check("status 不含 secret 相关字段", "secret" not in body_text.lower(), body_text[:200])

    # 对话：会真实调用讯飞（若已配置）。无论成功或降级，都必须返回 200 且有 reply
    r = client.post("/api/agent/chat", json={"message": "你好", "history": []})
    check("POST /api/agent/chat 返回 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        body = r.json()
        check("响应含非空 reply", bool(body.get("reply")))
        print("    source:", body.get("source"), "| error:", body.get("error"))
        print("    reply:", (body.get("reply") or "")[:120])

    print("\n=== 参数校验（智能体）===")
    r = client.post("/api/agent/chat", json={"history": []})
    check("缺 message 应 422", r.status_code == 422, str(r.status_code))
    r = client.post("/api/agent/chat", json={"message": "hi", "history": [{"role": "bad", "content": "x"}]})
    check("非法 role 应 422", r.status_code == 422, str(r.status_code))

    print("\n=== 流式对话 SSE ===")
    r = client.post("/api/agent/chat/stream", json={"message": "朔州有什么特产？", "history": []})
    check("POST /api/agent/chat/stream 返回 200", r.status_code == 200, str(r.status_code))
    ctype = r.headers.get("content-type", "")
    check("content-type 为 text/event-stream", "text/event-stream" in ctype, ctype)
    body = r.text
    check("包含 delta 事件", "event: delta" in body, body[:120])
    check("包含 done 事件", "event: done" in body, body[:120])
    # done 的 JSON 载荷应是完整回答，非空
    import json as _json

    done_text = ""
    for line in body.splitlines():
        if line.startswith("data:") and "event" not in line:
            continue
    # 简单取最后一个 done 的 data 行
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "event: done" and i + 1 < len(lines):
            dl = lines[i + 1]
            if dl.startswith("data:"):
                try:
                    done_text = _json.loads(dl[5:].strip())
                except Exception:
                    pass
    check("done 载荷为非空文本", bool(done_text and len(done_text) > 10), repr(done_text[:80]))
    print(f"    done 文本长度：{len(done_text)} 字")

    r = client.post("/api/agent/chat/stream", json={"message": "   ", "history": []})
    check("空消息走 error 分支", "event: error" in r.text, r.text[:120])

print(f"\n结果：通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
