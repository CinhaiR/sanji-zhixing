"""知识库相关 Pydantic Schema"""
from typing import List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 条目


class KnowledgeItem(BaseModel):
    id: int
    kind: str  # attraction / food
    name: str
    aliases: List[str] = []
    city: str
    city_name: str = ""
    traditional_in: List[str] = []
    traditional_in_names: List[str] = []
    item_type: str = ""
    level: Optional[str] = None
    tags: List[str] = []
    desc: str = ""
    tips: str = ""
    taste: Optional[str] = None
    ingredients: List[str] = []
    confidence: str = "high"
    source: str = "seed"


class CityInfo(BaseModel):
    id: str
    name: str
    aliases: List[str] = []
    theme_tags: List[str] = []
    intro: str = ""
    attraction_count: int = 0
    food_count: int = 0


class KnowledgeStats(BaseModel):
    cities: int = 0
    attractions: int = 0
    foods: int = 0
    total: int = 0
    by_city: List[dict] = []
    knowledge_fingerprint: str = ""


# ---------------------------------------------------------------- 检索


class CityContext(BaseModel):
    """注入提示词的单城市知识上下文"""

    city_id: str
    city_name: str
    intro: str = ""
    attractions: List[KnowledgeItem] = []
    foods: List[KnowledgeItem] = []


class RetrievalBundle(BaseModel):
    """一次行程所需的知识上下文"""

    cities: List[CityContext] = []
    # 表单里的地名无法匹配知识库城市时记录在此（检索会退化，但不阻断生成）
    unmatched_destinations: List[str] = []
    # 被跨城过滤掉的城市名（例如用户只选了朔州，却提到洪洞）
    blocked_city_names: List[str] = []
    fingerprint: str = ""
    used_rag: bool = True


# ---------------------------------------------------------------- 校验


class GeoIssue(BaseModel):
    scope: str  # activity / day
    day_index: int
    activity_index: Optional[int] = None
    field: str  # foodPairing / place / desc
    kind: str  # food_city_mismatch / foreign_city_mention / unknown_food_city
    detail: str
    found: str = ""
    expected_city: str = ""
    found_cities: List[str] = []
    replacement: Optional[str] = None
    fixed: bool = False


class GeoReport(BaseModel):
    checked_days: int = 0
    checked_activities: int = 0
    issues: List[GeoIssue] = []
    fixed_count: int = 0
    ok: bool = True


# ---------------------------------------------------------------- API


class KnowledgeSearchResult(BaseModel):
    q: str
    total: int
    items: List[KnowledgeItem] = []


class ReloadResult(BaseModel):
    cities: int
    attractions: int
    foods: int
    fingerprint: str
    seed_file: str


class ValidateTextRequest(BaseModel):
    """校验任意文本中的地理错配（可用于前端/Agent 自查）"""

    text: str = Field(..., min_length=1)
    city: Optional[str] = None


class ValidateTextResponse(BaseModel):
    issues: List[GeoIssue] = []
    detected_cities: List[str] = []
