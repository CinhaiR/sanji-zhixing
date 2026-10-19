"""认证 API 路由：注册、登录、验证码、获取当前用户信息"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import UserModel
from app.schemas.auth import (
    CaptchaResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserInfo,
)
from app.services import captcha_service
from app.services.auth_service import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get("/captcha", response_model=CaptchaResponse)
def get_captcha():
    """获取图形验证码（PNG base64），有效期 5 分钟，一次性使用"""
    return captcha_service.generate_captcha()


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """用户注册

    - 用户名 2-64 字符，密码 ≥8 位且含字母
    - 需填写确认密码且两次一致
    - 需通过图形验证码校验（一次性）
    - 同 IP 每分钟最多注册 3 次（防批量刷号）
    - 注册成功直接返回 token（免登录）
    """
    # 1. 注册限流
    client_ip = captcha_service.get_client_ip(request)
    if not captcha_service.check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="注册过于频繁，请稍后再试",
        )

    # 2. 验证码校验
    if not captcha_service.verify_captcha(payload.captcha_id, payload.captcha_text):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码错误或已过期",
        )

    # 3. 确认密码一致性（schema 已校验，此处兜底）
    if payload.password != payload.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="两次输入的密码不一致",
        )

    # 4. 用户名查重（不区分大小写）
    username = payload.username.strip()
    existing = (
        db.query(UserModel)
        .filter(UserModel.username.ilike(username))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该用户名已被占用",
        )

    # 5. 创建用户
    user = UserModel(
        username=username,
        password_hash=hash_password(payload.password),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.username, user.is_admin)
    return TokenResponse(
        access_token=token, username=user.username, is_admin=user.is_admin
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """用户登录，返回 JWT token"""
    user = (
        db.query(UserModel)
        .filter(UserModel.username.ilike(payload.username.strip()))
        .first()
    )
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    token = create_access_token(user.id, user.username, user.is_admin)
    return TokenResponse(
        access_token=token, username=user.username, is_admin=user.is_admin
    )


@router.get("/me", response_model=UserInfo)
def me(user: UserInfo = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return user
