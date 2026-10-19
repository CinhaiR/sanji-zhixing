"""驿路谋士对话链路诊断

按当前 AGENT_PROVIDER 逐层检查：

- **deepseek**（本地工作流）：配置 → 知识库加载 → 检索 → 提示词装配 → 调用模型
- **xingchen**（讯飞工作流）：配置 → 鉴权 → flow_id → 发布状态 → 授权 → 内容

用法：
    cd backend
    python scripts\\diagnose_agent.py
    python scripts\\diagnose_agent.py --message "朔州有什么特产？"
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.schemas.agent import ChatRequest  # noqa: E402

# 错误码 → 排查建议
HINTS = {
    401: "鉴权失败：核对 API Key / API Secret（注意区分「星火大模型」与「智能体」两套 Key）",
    460: "请求参数不合法：parameters 为必填，且键名须与工作流开始节点参数名一致",
    20201: "flow_id 不存在：核对智能体 API 页面上的 API Flowid",
    20204: "工作流未发布：在平台发布后再调用",
    20207: "工作流处于草稿状态：在平台发布后再调用",
    20373: "应用未被授权调用该工作流，或额度已用尽："
           "在控制台确认该 APPID 已开通「智能体/工作流」服务（与星火大模型是两项独立服务）",
    20900: "服务未授权或授权已到期",
    20902: "QPS 秒级流控超限",
    20903: "并发路数超限",
    23300: "工作流内大模型节点执行失败（检查节点提示词是否为空、模型是否授权）",
    23800: "工作流内问答节点执行失败",
    20500: "工作流内知识库请求异常",
    20501: "工作流内知识库节点执行异常",
    20804: "工作流输出超时",
    20805: "工作流输出失败（OpenAPI output error）：重点检查——"
           "① 结束节点是否已连接且返回变量与协议声明一致；"
           "② 开始节点的输入参数名是否与请求中的参数名一致；"
           "③ 各节点（尤其大模型节点）提示词是否为空。"
           "建议先用平台内工作流卡片的「对话」按钮直接调试",
}

GENERIC_20805_HINT = (
    "工作流未进入执行（executed_time=0、tokens=0），通常是编排或发布状态问题，"
    "而非参数问题。请用平台内「对话」按钮验证工作流自身能否跑通。"
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="诊断驿路谋士对话链路")
    parser.add_argument("--message", default="你好")
    parser.add_argument("--stream", action="store_true", help="使用流式请求（仅 xingchen）")
    args = parser.parse_args()

    from app.services import agent_service

    provider = agent_service.active_provider()

    print("=" * 70)
    print(f"驿路谋士对话链路诊断  ·  provider = {provider}")
    print("=" * 70)

    # ---------------- deepseek 本地工作流
    if provider == "deepseek":
        from app.services import workflow_sim

        workflow_sim.index.load(force=True)
        print(f"  对话模型    : {settings.agent_deepseek_model}")
        print(f"  API_KEY     : "
              + (f"{settings.deepseek_api_key[:6]}…{settings.deepseek_api_key[-4:]}"
                 if settings.deepseek_api_key else "（未配置）"))
        print(f"  知识库文件  : {settings.agent_kb_file}")
        print(f"  问答对      : {workflow_sim.index.size} 条 / {len(workflow_sim.index.cities)} 府邑")
        print(f"  提示词文件  : {settings.agent_prompt_file}")
        sysp, _ = workflow_sim.load_prompts()
        print(f"  系统提示词  : {len(sysp)} 字")
        print()

        if not settings.deepseek_api_key:
            print("✗ 未配置 DEEPSEEK_API_KEY，对话将降级")
            return 1

        print(f"① 接收输入：{args.message!r}")
        pairs, cities = workflow_sim.retrieve(args.message)
        print(f"② 知识库检索：命中府邑={cities or '无'}，召回 {len(pairs)} 条")
        for p in pairs[:5]:
            print(f"     [{p.header or '-'}/{p.kind}] {p.questions[0][:30]}")
        prompt = workflow_sim.build_user_prompt(args.message, pairs)
        print(f"   装配后用户提示词 {len(prompt)} 字")
        print(f"③ 调用 DeepSeek（{settings.agent_deepseek_model}）…")

        res = await agent_service.chat_with_agent(
            ChatRequest(message=args.message, history=[])
        )
        if res.source == "fallback":
            print(f"\n✗ 未接通：{res.error}")
            print(res.reply)
            return 1
        print(f"\n✓ 接通成功（source={res.source}）\n")
        print("-" * 70)
        print(res.reply)
        print("-" * 70)
        return 0

    # ---------------- xingchen 工作流
    print(f"  BASE_URL  : {settings.xingchen_base_url}")
    print(f"  FLOW_ID   : {settings.xingchen_flow_id or '（未配置）'}")
    print(f"  API_KEY   : "
          + (f"{settings.xingchen_api_key[:6]}…{settings.xingchen_api_key[-4:]}"
             if settings.xingchen_api_key else "（未配置）"))
    print(f"  INPUT_KEY : {settings.xingchen_input_key}")
    print()

    if not (settings.xingchen_api_key and settings.xingchen_api_secret and settings.xingchen_flow_id):
        print("✗ 配置不全，无法测试。请在 .env 中补齐 XINGCHEN_API_KEY / API_SECRET / FLOW_ID")
        return 1

    url = f"{settings.xingchen_base_url.rstrip('/')}/chat/completions"
    payload = {
        "flow_id": settings.xingchen_flow_id,
        "uid": "sanji-zhixing",
        "parameters": {settings.xingchen_input_key: args.message},
        "stream": args.stream,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {settings.xingchen_api_key}:{settings.xingchen_api_secret}",
    }

    print(f"请求：POST {url}")
    print(f"      参数 {settings.xingchen_input_key} = {args.message!r}")
    print()

    try:
        async with httpx.AsyncClient(timeout=settings.xingchen_timeout) as c:
            r = await c.post(url, headers=headers, json=payload)
    except Exception as e:
        print(f"✗ 网络异常：{type(e).__name__}: {e}")
        return 1

    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"响应：{r.text[:400]}")
        print(f"\n提示：{HINTS.get(r.status_code, '请核对接口地址与凭据')}")
        return 1

    data = r.json()
    code = int(data.get("code", -1))
    print(f"code = {code} | message = {data.get('message')}")

    # 节点级诊断
    step = (data.get("workflow_step") or {}).get("node") or {}
    if step:
        print("\n工作流节点诊断：")
        print(f"  node          : {step.get('id')}")
        print(f"  finish_reason : {step.get('finish_reason')}")
        print(f"  executed_time : {step.get('executed_time')}")
        print(f"  inputs        : {json.dumps(step.get('inputs'), ensure_ascii=False)}")
        print(f"  outputs       : {json.dumps(step.get('outputs'), ensure_ascii=False)[:200]}")
        print(f"  error_outputs : {json.dumps(step.get('error_outputs'), ensure_ascii=False)[:300]}")

    choices = data.get("choices") or []
    content = ""
    if choices:
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        content = (delta.get("content") or "").strip()

    if code == 0 and content:
        print("\n" + "=" * 70)
        print("✓ 接通成功，工作流回答：")
        print("=" * 70)
        print(content)
        if data.get("usage"):
            print(f"\nusage: {data['usage']}")
        return 0

    print("\n" + "=" * 70)
    print("✗ 未接通")
    print("=" * 70)
    if code == 20805 and step.get("executed_time") in (0, 0.0, None):
        print(GENERIC_20805_HINT)
    elif code in HINTS:
        print(HINTS[code])
    else:
        print(f"未收录的错误码 {code}，原始响应：\n{r.text[:600]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
