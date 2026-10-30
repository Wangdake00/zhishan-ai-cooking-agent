"""
对话路由：流式对话、会话管理、消息历史
"""
from fastapi import APIRouter, Depends
from app.models.schemas import ChatRequest
from fastapi.responses import StreamingResponse
from app.core.auth import get_current_user
from app.core.exceptions import PermissionDeniedException
from app.db import session_store
from app.agents.personal_chief import (
    search_recipes,
    get_messages,
    clear_messages,
    list_sessions,
    delete_session,
    prepare_preferences,
)


router = APIRouter()


@router.post("/chat/stream")
async def chat_endpoint(request: ChatRequest, current_user: dict = Depends(get_current_user)):
    """流式对话（支持多图）"""
    # 归属校验：thread_id 已存在且不属于当前用户时拒绝，防止跨用户会话注入（IDOR）
    session = await session_store.get_session(request.thread_id)
    if session is not None and session["user_id"] != current_user["id"]:
        raise PermissionDeniedException("无权操作此会话")
    # 偏好抽取前移到流开始前，便于用响应头告知前端本轮新增条数
    preferences, added_count = await prepare_preferences(request.message, current_user["id"])
    return StreamingResponse(
        search_recipes(
            request.message,
            request.image_urls or [],
            request.thread_id,
            current_user["id"],
            preferences,
        ),
        media_type="text/event-stream",
        headers={"X-Preference-Added": str(added_count)} if added_count else None,
    )


@router.get("/chat/messages")
async def get_chat_messages(thread_id: str, current_user: dict = Depends(get_current_user)):
    """获取历史消息（只能看自己的，越界返回空）"""
    session = await session_store.get_session(thread_id)
    if session is None or session["user_id"] != current_user["id"]:
        return {"messages": []}
    messages = await get_messages(thread_id)
    return {"messages": messages}


@router.delete("/chat/messages")
async def clear_chat_messages(thread_id: str, current_user: dict = Depends(get_current_user)):
    """清空历史消息（只能清空自己的）"""
    session = await session_store.get_session(thread_id)
    if session is None or session["user_id"] != current_user["id"]:
        raise PermissionDeniedException("无权操作此会话")
    await clear_messages(thread_id, current_user["id"])
    return {"success": True}


@router.get("/chat/sessions")
async def get_sessions(current_user: dict = Depends(get_current_user)):
    """列出当前用户的所有会话"""
    return {"sessions": await list_sessions(current_user["id"])}


@router.delete("/chat/sessions/{thread_id}")
async def delete_session_endpoint(thread_id: str, current_user: dict = Depends(get_current_user)):
    """删除指定会话（只能删自己的）"""
    session = await session_store.get_session(thread_id)
    if session is None or session["user_id"] != current_user["id"]:
        raise PermissionDeniedException("无权操作此会话")
    await delete_session(thread_id, current_user["id"])
    return {"success": True}