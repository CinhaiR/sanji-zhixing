"""典藏线路 API 路由"""
from typing import List

from fastapi import APIRouter, HTTPException, Query

from app.schemas.preset import PresetListResponse, PresetRoute
from app.services import preset_service

router = APIRouter(prefix="/presets", tags=["典藏线路"])


@router.get("", response_model=PresetListResponse)
def list_presets(
    category: str = Query("全部", description="按主题筛选；默认返回全部"),
):
    """列出典藏线路

    - `category` 传主题名（如「红色记忆」）可筛选，传「全部」或不传返回全部
    - 每条线路的 `cities` / `days` / `themes` / `pace` / `companion`
      可直接作为 `POST /api/itinerary/generate` 的入参，实现「一键套用」
    """
    routes = preset_service.list_routes(category)
    return PresetListResponse(
        total=len(routes),
        categories=preset_service.categories(),
        routes=[PresetRoute(**r) for r in routes],
    )


@router.get("/{route_id}", response_model=PresetRoute)
def get_preset(route_id: str):
    """按 id 取单条线路"""
    route = preset_service.get_route(route_id)
    if not route:
        raise HTTPException(
            status_code=404,
            detail={"code": "preset_not_found", "message": f"未找到线路：{route_id}"},
        )
    return PresetRoute(**route)
