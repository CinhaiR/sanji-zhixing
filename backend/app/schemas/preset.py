"""典藏线路相关 Schema"""
from typing import List, Optional

from pydantic import BaseModel, Field


class PresetRoute(BaseModel):
    """一条典藏线路。

    字段设计原则：`cities` / `days` / `themes` / `pace` / `companion`
    必须能**直接作为行程祈定册的表单参数**，做到「一键套用」。
    """

    id: str
    name: str
    # 线路动线，如「太原 → 阳泉 → 晋中 → 长治」
    route: str
    # 一句话定位
    tagline: str
    category: str            # 红色记忆 / 古建营造 / 晋商文化 / 博物馆藏 / 山川自然 / 佛教艺术
    days: int = Field(..., ge=1, le=30)
    cities: List[str]
    themes: List[str] = []
    pace: str = "研学适中"
    companion: str = "独自悠游"
    # 为何选这条线路（核心卖点，供「为什么选这条路线」展示）
    why: str
    # 沿途不可错过的点位（须取自知识库，便于点击后生成同样内容）
    highlights: List[str] = []
    best_for: str = ""       # 适合人群
    best_season: str = ""    # 建议时节
    source: str = ""         # 出处（如实标注，红色线路来自省文旅厅）
    badge: Optional[str] = None   # 角标，如「官方线路」


class PresetListResponse(BaseModel):
    total: int
    categories: List[str]
    routes: List[PresetRoute]
