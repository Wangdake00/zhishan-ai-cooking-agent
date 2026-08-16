from fastapi import APIRouter, Depends
from app.models.schemas import ChatRequest
from fastapi.responses import StreamingResponse
from app.core.auth import get_current_user
from app.agents.personal_chief import (
    search_recipes,
    get_messages,
    clear_messages,
    list_sessions,
    delete_session,
)


router = APIRouter()


@router.post("/chat/stream")
async def chat_endpoint(request: ChatRequest, current_user: dict = Depends(get_current_user)):
    """流式对话"""
    return StreamingResponse(
        search_recipes(request.message, request.image_url, request.thread_id, current_user["id"]),
        media_type="text/event-stream"
    )


@router.get("/chat/messages")
async def get_chat_messages(thread_id: str, current_user: dict = Depends(get_current_user)):
    """获取历史消息"""
    messages = get_messages(thread_id)
    return {"messages": messages}


@router.delete("/chat/messages")
async def clear_chat_messages(thread_id: str, current_user: dict = Depends(get_current_user)):
    """清空历史消息"""
    clear_messages(thread_id, current_user["id"])
    return {"success": True}


@router.get("/chat/sessions")
async def get_sessions(current_user: dict = Depends(get_current_user)):
    """列出当前用户的所有会话"""
    return {"sessions": list_sessions(current_user["id"])}


@router.delete("/chat/sessions/{thread_id}")
async def delete_session_endpoint(thread_id: str, current_user: dict = Depends(get_current_user)):
    """删除指定会话（只能删自己的）"""
    delete_session(thread_id, current_user["id"])
    return {"success": True}
