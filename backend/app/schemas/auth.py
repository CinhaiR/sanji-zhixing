"""认证相关 Pydantic Schema"""
import re

from pydantic import BaseModel, Field, field_validator

# 密码规则：≥8 位且至少含 1 个英文字母（大小写均可）
_PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z]).{8,128}$")

# 禁止注册的用户名（大小写不敏感），防止与 JS/Python 的特殊值混淆引发 bug
_FORBIDDEN_USERNAMES = {
    "null", "undefined", "nan", "none", "true", "false",
    "admin", "administrator", "root", "system",
    "guest", "anonymous", "default",
}


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=8, max_length=128, description="密码（≥8位且含字母）")
    confirm_password: str = Field(..., description="确认密码")
    captcha_id: str = Field(..., description="验证码 ID")
    captcha_text: str = Field(..., min_length=4, max_length=6, description="验证码")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        # 去除首尾空白
        v = v.strip()
        if not v:
            raise ValueError("用户名不能为空")
        # 禁止纯数字/特殊字符组成的名字（防止与 ID 混淆）
        if v.lower() in _FORBIDDEN_USERNAMES:
            raise ValueError("该用户名不允许使用，请更换")
        # 只允许中英文、数字、下划线
        if not re.match(r"^[\u4e00-\u9fa5A-Za-z0-9_]+$", v):
            raise ValueError("用户名只能包含中英文、数字和下划线")
        return v

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not _PASSWORD_PATTERN.match(v):
            raise ValueError("密码至少 8 位且必须包含英文字母")
        return v

    @field_validator("confirm_password")
    @classmethod
    def validate_confirm(cls, v: str, info) -> str:
        password = info.data.get("password")
        if password and v != password:
            raise ValueError("两次输入的密码不一致")
        return v


class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class CaptchaResponse(BaseModel):
    captcha_id: str
    image: str  # base64 PNG


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_admin: bool


class UserInfo(BaseModel):
    id: int
    username: str
    is_admin: bool
