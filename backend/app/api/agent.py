"""智能体对话 API 路由"""
import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.schemas.agent import ChatRequest, ChatResponse
from app.services import agent_service

router = APIRouter(prefix="/agent", tags=["智能体"])


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """驿路谋士对话接口（一次性返回）

    - 已配置 DeepSeek 时走项目内自建链路（知识库检索 + 提示词 + 模型）
    - 未配置或调用失败时降级为本地应答；据响应中的 `source`/`error` 可判断
    - 需要逐字上屏请用 `/api/agent/chat/stream`
    """
    return await agent_service.chat_with_agent(req)


def _sse(event: str, data) -> str:
    """SSE 帧：事件名 + JSON 数据"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """驿路谋士对话接口（SSE 流式）

    逐块推送模型输出，前端可即时上屏 —— 避免「等整段生成完再逐字播放」
    造成的双重延迟。事件类型：

    - `sources` 命中的府邑（先发）
    - `delta`   新增文本片段
    - `done`    完整文本（已清洗 Markdown）
    - `error`   失败原因（此时前端应展示降级提示）
    """

    async def gen() -> AsyncIterator[str]:
        message = (req.message or "").strip()
        if not message:
            yield _sse("error", "行者请先示下所问之事。")
            return

        if agent_service.active_provider() != "deepseek" or not settings.deepseek_api_key:
            # 非流式链路（讯飞）或未配置：直接给一次性结果，前端按普通消息渲染
            res = await agent_service.chat_with_agent(req)
            if res.sources:
                yield _sse("sources", res.sources)
            if res.error:
                yield _sse("error", res.error)
            yield _sse("done", res.reply)
            return

        final_text = ""
        async for kind, data in agent_service._chat_deepseek_stream(message, req.history):
            if kind == "cities":
                if data:
                    yield _sse("sources", data)
            elif kind == "delta":
                final_text += data
                yield _sse("delta", data)
            elif kind == "done":
                # 用清洗后的完整文本收尾，顺带修正流式期间可能露出的 Markdown 标记
                final_text = data
                yield _sse("done", data)
                return
            else:  # error
                yield _sse("error", data)
                # 给出降级话术，避免前端只显示半截内容
                yield _sse("done", agent_service._fallback_reply(message, data))
                return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关闭 Nginx 等反代的缓冲，否则 SSE 会被攒成一次性返回
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status")
def agent_status():
    """驿路谋士服务状态（供前端提示与排查，不暴露任何凭据）"""
    provider = agent_service.active_provider()
    configured = agent_service.is_configured()

    if provider == "deepseek":
        from app.services import workflow_sim

        workflow_sim.index.load()
        return {
            "configured": configured,
            "provider": "deepseek",
            "model": settings.agent_deepseek_model,
            "kb_file": settings.agent_kb_file,
            "kb_pairs": workflow_sim.index.size,
            "kb_cities": len(workflow_sim.index.cities),
            "retrieval_top": settings.agent_retrieval_top,
            "message": (
                "已接入本地工作流（知识库检索 + DeepSeek）"
                if configured
                else "未配置 DEEPSEEK_API_KEY，对话将使用本地降级应答"
            ),
        }

    return {
        "configured": configured,
        "provider": "xingchen",
        "flow_id_set": bool(settings.xingchen_flow_id),
        "input_key": settings.xingchen_input_key,
        "send_history": settings.xingchen_send_history,
        "timeout": settings.xingchen_timeout,
        "message": (
            "已配置讯飞工作流凭据"
            if configured
            else "未配置讯飞工作流凭据，对话将使用本地降级应答"
        ),
    }
