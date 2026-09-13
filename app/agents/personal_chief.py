"""
智膳 AI Agent 入口层

本文件作为 Agent 的对外统一入口，核心逻辑已迁移至 app/agents/graph.py（StateGraph 实现）。
仅保留委托层：对外暴露统一接口，内部全部委托给 graph 模块。
"""

from app.agents.graph import (
    stream_chat,
    list_sessions,
    delete_session,
    get_messages,
)

# ========== 对外接口（直接引用 graph.py，保持 API 兼容） ==========

async def search_recipes(prompt: str, image_urls: list, thread_id: str, user_id: int):
    """
    调用 StateGraph 搜索食谱 / 分析营养（流式输出）

    Args:
        prompt: 用户输入的文本
        image_urls: 图片 URL 列表（支持多张）
        thread_id: 会话 ID
        user_id: 用户 ID
    """
    async for chunk in stream_chat(prompt, image_urls, thread_id, user_id):
        yield chunk


async def clear_messages(thread_id: str, user_id: int):
    """清空会话（已废弃，请使用 delete_session）"""
    await delete_session(thread_id, user_id)
