"""认证服务：密码哈希、JWT 签发与校验、当前用户依赖"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.user import UserModel
from app.schemas.auth import UserInfo

# OAuth2 密码模式：从 Authorization: Bearer <token> 提取 token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    """明文密码 → bcrypt 哈希"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码是否匹配哈希"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, username: str, is_admin: bool) -> str:
    """签发 JWT access token"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "username": username,
        "is_admin": is_admin,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> UserInfo:
    """FastAPI 依赖：解析 token 并返回当前用户。
    token 缺失或无效时返回 401。
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录或登录已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exc
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id = int(payload.get("sub", 0))
        username = payload.get("username")
        is_admin = bool(payload.get("is_admin", False))
    except (JWTError, ValueError):
        raise credentials_exc

    # 校验用户仍存在（防止已被删除的用户的 token 仍可用）
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not user:
        raise credentials_exc
    return UserInfo(id=user.id, username=user.username, is_admin=user.is_admin)


def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[UserInfo]:
    """可选用户依赖：token 无效时返回 None，不抛 401。
    用于「登录可选」的接口（如行程列表：未登录返回空列表）。
    """
    if not token:
        return None
    return _decode_token_to_user(token, db)


def _decode_token_to_user(token: str, db: Session) -> Optional[UserInfo]:
    """解析 token 并返回用户信息（供中间件等非依赖注入场景复用）。
    token 无效或用户不存在时返回 None。
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id = int(payload.get("sub", 0))
    except (JWTError, ValueError):
        return None
    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not user:
        return None
    return UserInfo(id=user.id, username=user.username, is_admin=user.is_admin)


def require_admin(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """管理员依赖：非管理员返回 403"""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


def ensure_default_admin(db: Session) -> None:
    """首次启动时创建默认管理员账号（已存在则跳过）。

    管理员可操作知识库写接口（增删条目、重新导入种子）。
    """
    existing = (
        db.query(UserModel)
        .filter(UserModel.username.ilike(settings.admin_username))
        .first()
    )
    if existing:
        # 已存在则确保是管理员（防止历史账号不是 admin）
        if not existing.is_admin:
            existing.is_admin = True
            db.commit()
        return
    admin = UserModel(
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    print(f"[认证] 已创建默认管理员账号：{settings.admin_username}")
