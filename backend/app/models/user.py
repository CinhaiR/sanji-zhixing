"""用户数据模型"""
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.db.database import Base


class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    # 存储 bcrypt 哈希后的密码，绝不存明文
    password_hash = Column(String(255), nullable=False)
    # 是否为管理员（可操作知识库写接口）
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
