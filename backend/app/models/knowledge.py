"""知识库数据模型（SQLAlchemy ORM）

设计要点：
- `city` 字段是**地理校验的锚点**：景点 / 特产都记录其真实所在地，
  生成行程时据此判定并修正「朔州吃平遥牛肉」这类地理错配。
- `source` 区分种子数据（seed，可被导入脚本覆盖）与手工新增（manual，导入时保留）。
- FTS5 虚拟表不在此定义，由 `scripts/import_knowledge.py` 以原生 SQL 建立。
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.db.database import Base


class CityModel(Base):
    """府邑（山西地级市）"""

    __tablename__ = "kb_cities"

    id = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False)
    aliases = Column(Text, nullable=False, default="[]")  # JSON 数组
    theme_tags = Column(Text, nullable=False, default="[]")  # JSON 数组
    intro = Column(Text, nullable=False, default="")
    sort_order = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeItemModel(Base):
    """知识条目：景点 / 特产（后续可扩展非遗、节庆、住宿等）"""

    __tablename__ = "kb_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(24), nullable=False, index=True)  # attraction / food
    name = Column(String(200), nullable=False, index=True)
    aliases = Column(Text, nullable=False, default="[]")  # JSON 数组
    city = Column(String(32), ForeignKey("kb_cities.id"), nullable=False, index=True)
    city_name = Column(String(64), nullable=False, default="")
    # 特产专用：允许出现该特产的其它城市（JSON 数组），留空表示仅限 city
    traditional_in = Column(Text, nullable=False, default="[]")
    item_type = Column(String(48), nullable=False, default="")  # 世界文化遗产 / 面食 ...
    level = Column(String(16), nullable=True)  # 5A / 4A
    tags = Column(Text, nullable=False, default="[]")
    desc = Column(Text, nullable=False, default="")
    tips = Column(Text, nullable=False, default="")
    # 特产风味字段
    taste = Column(String(64), nullable=True)
    ingredients = Column(Text, nullable=False, default="[]")
    confidence = Column(String(16), nullable=False, default="high")
    source = Column(String(16), nullable=False, default="seed")  # seed / manual
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GenerationCacheModel(Base):
    """行程生成缓存

    key 由「表单归一化参数 + 知识库指纹」哈希而成：
    知识库内容一更新，指纹即变化，旧缓存自动失效，避免返回与知识库不符的结果。
    """

    __tablename__ = "gen_cache"

    key = Column(String(64), primary_key=True)
    payload = Column(Text, nullable=False)  # Itinerary JSON
    source = Column(String(16), nullable=False, default="llm")  # llm / mock
    provider = Column(String(64), nullable=False, default="")
    hits = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False, index=True)
