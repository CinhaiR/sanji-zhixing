"""行程相关 Pydantic Schema"""
from typing import List, Optional

from pydantic import BaseModel, Field


class Activity(BaseModel):
    time: str = ""
    place: str = ""
    type: str = ""
    desc: str = ""
    tips: str = ""
    foodPairing: str = ""


class DayPlan(BaseModel):
    theme: str = ""
    title: str = ""
    totalHours: str = ""
    suggestedDeparture: str = ""
    # 当日所属府邑的本地特产：只在「主轴」标注一次，不在各时段内重复
    localSpecialties: str = ""
    activities: List[Activity] = []


class ItineraryBase(BaseModel):
    title: str
    destinations: List[str]
    highlights: List[str]
    # 「为何这样安排」——说明同行之人 / 寻古节奏与意趣 / 三晋意趣三项如何影响编排
    rationale: str = ""
    days: List[DayPlan]


class Itinerary(ItineraryBase):
    """行程方案。

    source / cached / knowledge_fingerprint / geo_fixed_count 为生成元数据，
    便于前端与调试判断结果来源与准确性修正情况；前端可忽略这些字段。
    """

    source: Optional[str] = None  # llm / cache / mock
    cached: bool = False
    knowledge_fingerprint: Optional[str] = None
    geo_fixed_count: int = 0


class ItineraryForm(BaseModel):
    destinations: List[str] = Field(..., min_length=1)
    days: int = Field(..., ge=1, le=30)
    companion: str = ""
    pace: str = ""
    themes: List[str] = []


class SavedItinerary(ItineraryBase):
    id: int
    savedAt: str


class GenerateOptions(BaseModel):
    """行程生成可选参数（放在请求体中，避免破坏前端既有调用）"""

    use_cache: bool = True
    force_refresh: bool = False
    audit: Optional[bool] = None


class GenerateRequest(ItineraryForm):
    """生成请求：兼容纯表单调用，也可附加生成选项与出行月份"""

    options: GenerateOptions = GenerateOptions()
    travel_month: Optional[int] = Field(default=None, ge=1, le=12)
