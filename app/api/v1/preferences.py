"""
跨会话饮食偏好管理路由：查看与删除
"""
from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.db import preference_store

router = APIRouter()


@router.get("/preferences")
async def list_preferences(current_user: dict = Depends(get_current_user)):
    """列出当前用户被记住的饮食偏好"""
    return {"preferences": await preference_store.list_preferences(current_user["id"])}


@router.delete("/preferences/{preference_id}")
async def delete_preference(preference_id: int, current_user: dict = Depends(get_current_user)):
    """删除一条饮食偏好（只能删自己的）"""
    deleted = await preference_store.delete_preference(current_user["id"], preference_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="偏好不存在或无权删除")
    return {"success": True}
