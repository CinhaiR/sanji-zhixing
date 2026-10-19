"""行程数据模型（SQLAlchemy ORM）"""
from sqlalchemy import Column, ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.sql import func

from app.db.database import Base


class ItineraryModel(Base):
    __tablename__ = "itineraries"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    # destinations / highlights / days 以 JSON 字符串存储
    destinations = Column(Text, nullable=False)
    highlights = Column(Text, nullable=False)
    days = Column(Text, nullable=False)
    # 「为何这样安排」——保存历史行程时一并留存
    rationale = Column(Text, nullable=False, server_default="")
    # 所属用户；nullable 是为了兼容历史无主数据，新保存的行程必须有值
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    saved_at = Column(DateTime, server_default=func.now())
