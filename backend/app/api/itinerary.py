"""行程相关 API 路由"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.itinerary import GenerateRequest, Itinerary, ItineraryForm, SavedItinerary
from app.schemas.auth import UserInfo
from app.services import itinerary_service
from app.services.auth_service import get_current_user_optional
from app.services.cache_service import cache

router = APIRouter(prefix="/itinerary", tags=["行程"])


@router.post("/generate", response_model=Itinerary)
async def generate(payload: GenerateRequest, db: Session = Depends(get_db)):
    """智能生成行程

    - 命中缓存时直接返回（`source=cache`），大模型零消耗
    - 未命中则走 RAG 接地生成 + 地理审计
    - 可选参数：`options.force_refresh=true` 强制重新生成；
      `options.use_cache=false` 仅本次跳过缓存；`options.audit=false` 跳过审计
    - 若天数不足以覆盖所择府邑（每地摊不到一日），返回 **400** 并附提示文案，
      前端据此弹窗请用户重新选择
    """
    form = ItineraryForm(
        destinations=payload.destinations,
        days=payload.days,
        companion=payload.companion,
        pace=payload.pace,
        themes=payload.themes,
    )
    try:
        return await itinerary_service.generate_itinerary(
            form,
            db,
            use_cache=payload.options.use_cache,
            force_refresh=payload.options.force_refresh,
            audit=payload.options.audit,
        )
    except itinerary_service.PlanUnreasonableError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "plan_unreasonable",
                "message": e.message,
                "days": e.days,
                "cities": e.cities,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": "invalid_form", "message": str(e)})


@router.post("/save", response_model=SavedItinerary)
def save(
    plan: Itinerary,
    db: Session = Depends(get_db),
    user: UserInfo | None = Depends(get_current_user_optional),
):
    """保存当前行程（登录则归属当前用户，未登录则匿名保存）

    空行程直接拒绝：前端「行驿日程」在未定制行程时会展示一个标题为
    「待定制行程」的占位对象，若把它存进「我的通关文牒」，
    用户会看到一条没有任何内容的文牒。前端已隐藏保存入口，这里再兜一层。
    """
    if not plan.days:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "empty_itinerary",
                "message": "尚未定制行程，无可保存的内容。请先在「行程祈定册」生成行程。",
            },
        )
    user_id = user.id if user else None
    return itinerary_service.save_itinerary(db, plan, user_id=user_id)


@router.get("/list", response_model=list[SavedItinerary])
def list_plans(
    db: Session = Depends(get_db),
    user: UserInfo | None = Depends(get_current_user_optional),
):
    """获取历史行程列表（登录仅返回自己的，未登录返回匿名/全部）"""
    user_id = user.id if user else None
    return itinerary_service.list_itineraries(db, user_id=user_id)


@router.get("/cache/stats")
def cache_stats(db: Session = Depends(get_db)):
    """生成缓存统计"""
    return cache.stats(db)


@router.delete("/cache")
def cache_clear(expired_only: bool = False, db: Session = Depends(get_db)):
    """清空生成缓存（`expired_only=true` 时仅清过期项）"""
    removed = cache.invalidate(db, expired_only=expired_only)
    return {"success": True, "removed": removed}


@router.delete("/{itinerary_id}")
def delete(
    itinerary_id: int,
    db: Session = Depends(get_db),
    user: UserInfo | None = Depends(get_current_user_optional),
):
    """删除指定行程（登录用户只能删除自己的）"""
    user_id = user.id if user else None
    ok = itinerary_service.delete_itinerary(db, itinerary_id, user_id=user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="行程不存在或无权删除")
    return {"success": True}
