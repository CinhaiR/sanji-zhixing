"""pytest 全局配置：提供带认证的测试客户端。

由于全局认证中间件要求所有 /api/* 请求（除白名单外）携带有效 token，
测试用的 TestClient 需要自动注入 Authorization 头。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth_service import create_access_token


@pytest.fixture(scope="session")
def auth_token() -> str:
    """生成一个测试用的有效 JWT token（不查库，仅用于通过中间件校验）。

    注意：此 token 对应的 user_id=1 在测试库中可能不存在，
    但若接口依赖 `get_current_user` 会查库返回 401。
    对于只经过中间件（不查库）的接口足够；需要真实用户的接口请自行注册。
    """
    return create_access_token(user_id=1, username="testuser", is_admin=False)


@pytest.fixture(scope="session")
def admin_token() -> str:
    """管理员 token（用于测试知识库写接口等需管理员权限的场景）"""
    return create_access_token(user_id=1, username="admin", is_admin=True)


@pytest.fixture(scope="session")
def auth_headers(auth_token: str) -> dict:
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(scope="session")
def client(auth_headers: dict) -> TestClient:
    """带认证头的 TestClient，所有请求自动携带 token。"""
    return TestClient(app, headers=auth_headers)
