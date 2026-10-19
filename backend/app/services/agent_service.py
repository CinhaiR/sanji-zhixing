"""智能体对话服务（驿路谋士）

两种链路，由 `AGENT_PROVIDER` 切换：

1. **deepseek（默认）**：项目内自建的「工作流」，等价于讯飞工作流的三步：
     ① 接收用户输入
     ② 在知识库中检索相关问答（knowledge_txt/三晋智行_全量知识库（问答对）.txt）
     ③ 参考检索到的问答，按 prompts/讯飞大模型节点提示词.md 的提示词调 DeepSeek 作答
2. **xingchen**：转发讯飞星辰工作流（需平台侧授权）

两条链路都遵循「失败即降级、接口永不 500」的策略。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.schemas.agent import ChatMessage, ChatRequest, ChatResponse

# ---------------------------------------------------------------- 错误码映射

# 来自官方错误码表（星辰 Workflow Open API 文档）+ 实测补充
XINGCHEN_ERRORS: Dict[int, str] = {
    460: "请求参数不合法（parameters 为必填，且须与开始节点参数名一致）",
    20004: "该应用未绑定此工作流：请核对「智能体 API」页面上的 API Flowid "
           "（工作流列表里显示的标识不是 flow_id；绑定关系是唯一的，换 flow 会报此错）",
    20201: "未找到对应的工作流（请核对 flow_id）",
    20202: "工作流 ID 非法",
    20204: "工作流尚未发布，请在平台发布后再调用",
    20207: "工作流处于草稿状态，请先发布",
    20805: "工作流输出失败（OpenAPI output error）：请检查工作流编排——"
           "结束节点是否已连接、其返回变量是否与协议声明一致、"
           "各节点是否存在报错（可用平台内「对话」按钮直接调试）",
    20303: "模型请求失败，请稍后再试",
    20356: "当前用户已有连接，请等待上一条回复完成",
    20357: "服务正在处理上一条提问，请稍后再发",
    20358: "服务容量不足，请稍后再试",
    20363: "提问内容未通过内容审核，请调整后重试",
    20364: "回答内容涉及敏感信息，无法展示",
    20366: "该 appId 未开通此功能或额度不足",
    20368: "提问涉嫌违规，请调整措辞",
    20369: "服务繁忙，请稍后再问",
    20372: "上下文过长，请开启新对话或精简问题",
    20373: "账号未授权该工作流或额度已用尽（请在讯飞控制台检查服务授权与 token 余量）",
    20374: "已达当日调用上限",
    20375: "触发秒级流控，请稍后再问",
    20376: "并发超限，请稍后再问",
    20500: "工作流内知识库请求异常",
    20501: "工作流内知识库节点执行异常",
    20502: "工作流内知识库参数异常",
    20804: "工作流输出超时",
    20900: "鉴权失败：服务未授权或授权已到期",
    20901: "计量鉴权失败：业务总量或日流控超限",
    20902: "鉴权失败：QPS 秒级流控超限",
    20903: "鉴权失败：并发路数超限",
    23300: "工作流大模型节点执行失败",
    23800: "工作流问答节点执行失败",
    23900: "该对话已超时或不存在",
}


def _describe_code(code: int, message: str = "") -> str:
    known = XINGCHEN_ERRORS.get(code)
    if known:
        return known
    if message:
        return f"工作流调用失败（code={code}）：{message}"
    return f"工作流调用失败（code={code}）"


# ---------------------------------------------------------------- 会话映射

# sessionId（本服务生成）→ chat_id（传给讯飞，长度需 ≤32）
_SESSION_TO_CHAT: Dict[str, str] = {}
_CHAT_LIMIT = 500


def _remember_session(session_id: str, chat_id: str) -> None:
    if len(_SESSION_TO_CHAT) >= _CHAT_LIMIT:
        # 简单淘汰最旧的一条，避免无界增长
        _SESSION_TO_CHAT.pop(next(iter(_SESSION_TO_CHAT)), None)
    _SESSION_TO_CHAT[session_id] = chat_id


def _new_chat_id() -> str:
    """生成 ≤32 位的 chat_id"""
    return datetime.now().strftime("%y%m%d%H%M%S") + str(int(datetime.now().microsecond))[:6]


# ---------------------------------------------------------------- 历史装配


def _build_history(history: List[ChatMessage]) -> List[dict]:
    """把前端历史装配为讯飞要求的结构。

    要求：首元素必须为 user，严格 user → assistant 交替，按时间从先到后。
    本服务的 ChatMessage.role 取值为 user/agent，需映射为 user/assistant。
    """
    out: List[dict] = []
    for m in history:
        role = "user" if m.role == "user" else "assistant"
        content = (m.content or "").strip()
        if not content:
            continue
        # 跳过欢迎语等纯开场白之前没有用户提问的情况
        if not out and role != "user":
            continue
        # 保证交替：同角色连续出现时，后一条覆盖前一条
        if out and out[-1]["role"] == role:
            out[-1] = {"role": role, "content_type": "text", "content": content}
        else:
            out.append({"role": role, "content_type": "text", "content": content})
    # 截断为最近 N 轮
    keep = max(1, settings.xingchen_history_rounds) * 2
    return out[-keep:]


# ---------------------------------------------------------------- 降级应答


def _fallback_reply(message: str, reason: str = "") -> str:
    tail = "（智旅服务暂时不畅）" if not reason else f"（{reason}）"
    return (
        f"行者所询「{message}」，谋士此刻未能通达方志库{tail}。\n\n"
        "可先移步「三晋方志」页翻阅古建、佛艺、晋商、非遗与风味条目；"
        "若欲定制行程，可至「华夏定制」填写意向，由方志知识库为您编排通关卷宗。"
    )


# ---------------------------------------------------------------- 主流程


def is_configured() -> bool:
    """当前 provider 是否具备调用条件"""
    provider = (settings.agent_provider or "deepseek").lower()
    if provider == "deepseek":
        return bool(settings.deepseek_api_key)
    return bool(
        settings.xingchen_api_key
        and settings.xingchen_api_secret
        and settings.xingchen_flow_id
    )


def active_provider() -> str:
    return (settings.agent_provider or "deepseek").lower()


# ---------------------------------------------------------------- DeepSeek 链路


async def _chat_deepseek(
    question: str, history: List[ChatMessage]
) -> Tuple[str, Optional[str], List[str]]:
    """本地工作流：检索知识库 → 装配提示词 → 调 DeepSeek

    返回 (reply, error, used_cities)
    """
    from app.services import workflow_sim

    # ② 在知识库中检索相关问答
    pairs, cities = workflow_sim.retrieve(question)

    # ③ 参考问答 + 提示词作答
    system_prompt, _ = workflow_sim.load_prompts()
    user_prompt = workflow_sim.build_user_prompt(question, pairs)

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    # 多轮：只带最近的对话文本，且避免把本轮问题重复注入
    for m in history[-settings.xingchen_history_rounds * 2 :]:
        content = (m.content or "").strip()
        if not content:
            continue
        messages.append(
            {"role": "user" if m.role == "user" else "assistant", "content": content}
        )
    messages.append({"role": "user", "content": user_prompt})

    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.agent_deepseek_model,
        "messages": messages,
        "temperature": settings.agent_temperature,
        "max_tokens": settings.agent_max_tokens,
    }
    async with httpx.AsyncClient(timeout=settings.agent_timeout) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code != 200:
            return "", f"DeepSeek 返回 HTTP {resp.status_code}：{resp.text[:160]}", cities
        data = resp.json()

    choice = (data.get("choices") or [{}])[0]
    reply = ((choice.get("message") or {}).get("content") or "").strip()
    if not reply:
        return "", f"模型未返回内容（finish_reason={choice.get('finish_reason')}）", cities
    # 前端不渲染 Markdown，清洗掉模型偶发的 ** 加粗等标记
    return workflow_sim.clean_reply(reply), None, cities


async def _chat_deepseek_stream(question: str, history: List[ChatMessage]):
    """流式版本地工作流：逐块产出 (kind, data)

    kind 取值：
      - "cities"  → data 为命中的府邑列表（先发，便于前端提示）
      - "delta"   → data 为新增文本片段
      - "done"    → data 为完整文本（已清洗）
      - "error"   → data 为错误说明

    与 `_chat_deepseek` 共用同一套检索与提示词，只是改用 stream=true，
    这样首字延迟从「整段生成完」降到「首个 token」。
    """
    from app.services import workflow_sim

    pairs, cities = workflow_sim.retrieve(question)
    yield "cities", cities

    system_prompt, _ = workflow_sim.load_prompts()
    user_prompt = workflow_sim.build_user_prompt(question, pairs)

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for m in history[-settings.xingchen_history_rounds * 2 :]:
        content = (m.content or "").strip()
        if not content:
            continue
        messages.append(
            {"role": "user" if m.role == "user" else "assistant", "content": content}
        )
    messages.append({"role": "user", "content": user_prompt})

    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.agent_deepseek_model,
        "messages": messages,
        "temperature": settings.agent_temperature,
        "max_tokens": settings.agent_max_tokens,
        "stream": True,
    }

    buffer: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=settings.agent_timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    yield "error", f"DeepSeek 返回 HTTP {resp.status_code}：{body[:160]}"
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        buffer.append(piece)
                        yield "delta", piece
    except httpx.TimeoutException:
        yield "error", "方志检索超时"
        return
    except Exception as e:  # 网络等异常
        yield "error", f"{type(e).__name__}: {e}"
        return

    full = "".join(buffer).strip()
    if not full:
        yield "error", "模型未返回内容"
        return
    yield "done", workflow_sim.clean_reply(full)


# ---------------------------------------------------------------- 讯飞链路


async def _call_workflow(
    message: str, history: List[ChatMessage], chat_id: Optional[str]
) -> Tuple[str, Optional[str], Optional[str]]:
    """调用讯飞工作流。

    返回 (reply, new_chat_id, error)。error 非空表示业务失败。
    """
    url = f"{settings.xingchen_base_url.rstrip('/')}/chat/completions"
    payload: Dict[str, object] = {
        "flow_id": settings.xingchen_flow_id,
        "uid": "sanji-zhixing",
        "stream": False,
        "parameters": {settings.xingchen_input_key: message},
    }
    if chat_id:
        payload["chat_id"] = chat_id
    if settings.xingchen_send_history:
        hist = _build_history(history)
        if hist:
            payload["history"] = hist

    headers = {
        "Authorization": f"Bearer {settings.xingchen_api_key}:{settings.xingchen_api_secret}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=settings.xingchen_timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)

    if resp.status_code != 200:
        return "", None, f"服务返回 HTTP {resp.status_code}"

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return "", None, "服务返回内容无法解析"

    code = data.get("code", 0)
    if code != 0:
        return "", None, _describe_code(int(code), str(data.get("message", "")))

    choices = data.get("choices") or [{}]
    delta = choices[0].get("delta") or {}
    content = (delta.get("content") or "").strip()
    if not content:
        return "", None, "工作流未返回内容（可能知识库未命中或节点异常）"

    return content, data.get("id"), None


async def chat_with_agent(req: ChatRequest) -> ChatResponse:
    """驿路谋士对话入口"""
    message = (req.message or "").strip()
    if not message:
        return ChatResponse(reply="行者请先示下所问之事。")

    provider = active_provider()

    if not is_configured():
        why = (
            "尚未配置 DeepSeek API Key"
            if provider == "deepseek"
            else "尚未配置讯飞工作流凭据"
        )
        return ChatResponse(reply=_fallback_reply(message, why), source="fallback")

    # ---- DeepSeek 本地工作流（默认）
    if provider == "deepseek":
        try:
            reply, error, cities = await _chat_deepseek(message, req.history)
        except httpx.TimeoutException:
            return ChatResponse(
                reply=_fallback_reply(message, "方志检索超时"),
                source="fallback",
                error="请求超时",
            )
        except Exception as e:
            return ChatResponse(
                reply=_fallback_reply(message, "网络未通"),
                source="fallback",
                error=f"{type(e).__name__}: {e}",
            )
        if error:
            return ChatResponse(
                reply=_fallback_reply(message, error), source="fallback", error=error
            )
        return ChatResponse(
            reply=reply,
            sources=cities or None,
            source="deepseek",
            session_id=req.session_id,
        )

    # ---- 讯飞工作流（备用）
    chat_id = _SESSION_TO_CHAT.get(req.session_id) if req.session_id else None
    if not chat_id:
        chat_id = _new_chat_id()

    try:
        reply, new_chat_id, error = await _call_workflow(message, req.history, chat_id)
    except httpx.TimeoutException:
        return ChatResponse(
            reply=_fallback_reply(message, "方志检索超时"),
            source="fallback",
            error="请求超时",
        )
    except Exception as e:  # 网络等异常
        return ChatResponse(
            reply=_fallback_reply(message, "网络未通"),
            source="fallback",
            error=f"{type(e).__name__}: {e}",
        )

    if error:
        return ChatResponse(reply=_fallback_reply(message, error), source="fallback", error=error)

    session_id = req.session_id or None
    if not session_id:
        # 首次对话：生成并回传 sessionId，供前端后续续聊
        session_id = f"sjx-{chat_id}"
    _remember_session(session_id, new_chat_id or chat_id)

    return ChatResponse(reply=reply, session_id=session_id, source="xingchen")
