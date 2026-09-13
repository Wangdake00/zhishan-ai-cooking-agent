from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.core.auth import get_current_user
from app.db import favorite_store
from typing import Optional

router = APIRouter()


# === Pydantic models ===

class FavoriteCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="食谱标题")
    content: str = Field(..., min_length=1, description="食谱详细内容")
    ingredients: Optional[str] = Field("", description="主要食材")


# === Routes ===

@router.post("/favorites")
async def create_favorite(request: FavoriteCreateRequest, current_user: dict = Depends(get_current_user)):
    """创建收藏（Agent 内部也会通过工具调用此逻辑，此接口给前端直接手动收藏用）"""
    fav_id = await favorite_store.create_favorite(
        user_id=current_user["id"],
        title=request.title,
        content=request.content,
        ingredients=request.ingredients or "",
    )
    return {"id": fav_id, "success": True}


@router.get("/favorites")
async def list_favorites(current_user: dict = Depends(get_current_user)):
    """列出当前用户所有收藏"""
    return {"favorites": await favorite_store.list_favorites(current_user["id"])}


@router.get("/favorites/{favorite_id}")
async def get_favorite(favorite_id: int, current_user: dict = Depends(get_current_user)):
    """获取单条收藏详情"""
    fav = await favorite_store.get_favorite(favorite_id, current_user["id"])
    if not fav:
        raise HTTPException(status_code=404, detail="收藏不存在")
    return fav


@router.delete("/favorites/{favorite_id}")
async def delete_favorite(favorite_id: int, current_user: dict = Depends(get_current_user)):
    """删除收藏"""
    deleted = await favorite_store.delete_favorite(favorite_id, current_user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="收藏不存在或无权删除")
    return {"success": True}
