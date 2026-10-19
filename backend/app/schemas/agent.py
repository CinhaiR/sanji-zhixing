"""智能体对话相关 Pydantic Schema"""
from typing import List, Literal, Optional

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["user", "agent"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []
    # 本服务会话 id；首次可不传，响应会回传新的 sessionId 供续聊
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    sources: Optional[List[str]] = None
    # 会话 id：多轮对话时原样回传
    session_id: Optional[str] = None
    # 应答来源：xingchen 讯飞工作流 / fallback 本地降级
    source: Optional[str] = None
    # 降级原因（便于前端提示与排查，不含敏感信息）
    error: Optional[str] = None
