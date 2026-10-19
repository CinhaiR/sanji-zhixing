"""知识库 API 路由

供前端知识库页面浏览检索，也便于用脚本/接口增量补充内容。
写接口（新增/删除条目、重新导入种子）需要管理员权限。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.auth import UserInfo
from app.schemas.knowledge import (
    CityInfo,
    KnowledgeItem,
    KnowledgeSearchResult,
    KnowledgeStats,
    ReloadResult,
    ValidateTextRequest,
    ValidateTextResponse,
)
from app.services import knowledge_service
from app.services.auth_service import require_admin

router = APIRouter(prefix="/knowledge", tags=["知识库"])


@router.get("/cities", response_model=List[CityInfo])
def cities(db: Session = Depends(get_db)):
    """府邑列表（含景点/特产数量）"""
    return knowledge_service.get_cities(db)


@router.get("/stats", response_model=KnowledgeStats)
def stats(db: Session = Depends(get_db)):
    """知识库统计与内容指纹"""
    return knowledge_service.get_stats(db)


@router.get("/search", response_model=KnowledgeSearchResult)
def search(
    q: str = Query(..., min_length=1, description="关键词，支持空格分隔多词"),
    kind: Optional[str] = Query(None, description="attraction / food"),
    city: Optional[str] = Query(None, description="府邑名或 id，如 朔州 / shuozhou"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """全文检索知识条目（FTS5，退化为 LIKE）"""
    items = knowledge_service.search_items(db, q, kind=kind, city=city, limit=limit)
    return KnowledgeSearchResult(q=q, total=len(items), items=items)


@router.get("/validate", response_model=ValidateTextResponse)
def validate_text(
    text_value: str = Query(..., alias="text", min_length=1),
    city: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """校验任意文本的地理错配（如直接贴一段行程文案）"""
    issues, detected = knowledge_service.validate_text(db, text_value, city)
    return ValidateTextResponse(issues=issues, detected_cities=detected)


@router.get("/items", response_model=List[KnowledgeItem])
def items(
    kind: Optional[str] = Query(None, description="attraction / food"),
    city: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """按分类/府邑列出知识条目"""
    return knowledge_service.list_items(db, kind=kind, city=city, limit=limit, offset=offset)


@router.get("/items/{item_id}", response_model=KnowledgeItem)
def item_detail(item_id: int, db: Session = Depends(get_db)):
    """条目详情"""
    found = knowledge_service.get_item(db, item_id)
    if not found:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return found


@router.post("/items", response_model=KnowledgeItem, status_code=201)
def create_item(
    payload: KnowledgeItem,
    db: Session = Depends(get_db),
    _admin: UserInfo = Depends(require_admin),
):
    """新增知识条目（source=manual，不会被种子文件导入覆盖）—— 需管理员"""
    if payload.kind not in ("attraction", "food"):
        raise HTTPException(status_code=400, detail="kind 必须为 attraction 或 food")
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="name 不能为空")
    if not payload.city:
        raise HTTPException(status_code=400, detail="city 不能为空")
    return knowledge_service.add_item(db, payload)


@router.delete("/items/{item_id}")
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    _admin: UserInfo = Depends(require_admin),
):
    """删除知识条目 —— 需管理员"""
    ok = knowledge_service.delete_item(db, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return {"success": True}


@router.post("/reload", response_model=ReloadResult)
def reload_knowledge(
    db: Session = Depends(get_db),
    _admin: UserInfo = Depends(require_admin),
):
    """从种子文件重新导入知识库 —— 需管理员"""
    try:
        return knowledge_service.load_seed(db)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"种子文件不存在：{e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败：{e}")
