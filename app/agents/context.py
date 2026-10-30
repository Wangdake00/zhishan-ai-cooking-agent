"""
上下文治理：构造「LLM 输入副本」

硬约束：
- 只处理构造给 LLM 的副本，绝不修改 state["messages"] 中持久化的历史（前端历史必须完整）
- 任何增强能力失败都降级为不处理，绝不能中断主回答
- ToolMessage 不落库（节点只回写 AIMessage），因此历史中带 tool_calls 的 AIMessage
  必然悬空，必须在副本中 sanitize，否则多轮会话会触发消息结构非法

本模块只做纯函数/异步编排，不持有全局状态；LLM 由调用方（图节点）注入，避免循环导入。
"""
import asyncio
import math
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.common import oss
from app.common.logger import logger
from app.core.config import settings
from app.agents.utils import ensure_image_base64_data_url

# 历史图片占位文本（前端历史展示使用 checkpoint 原图，不受此占位影响）
IMAGE_PLACEHOLDER = "[历史图片已省略]"
IMAGE_FAILED_PLACEHOLDER = "[图片加载失败]"


# ========== token 粗估 ==========

def _is_cjk(ch: str) -> bool:
    """判断是否为中日韩表意字符（按字符估算，不引入 tokenizer 依赖）"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK 统一表意文字
        or 0x3400 <= code <= 0x4DBF    # 扩展 A
        or 0x3000 <= code <= 0x303F    # CJK 标点
        or 0xFF00 <= code <= 0xFFEF    # 全角字符
    )


def estimate_text_tokens(text: str) -> int:
    """估算纯文本 token：中文按 1.3 字符/token，其他按 4 字符/token"""
    if not text:
        return 0
    zh = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - zh
    zh_per = max(settings.context_token_chars_per_token_zh, 0.1)
    other_per = max(settings.context_token_chars_per_token_other, 0.1)
    return math.ceil(zh / zh_per) + math.ceil(other / other_per)


def estimate_message_tokens(message) -> int:
    """
    估算单条消息 token（含每条固定开销 4）

    - 图片按配置的固定估算值计（base64 不计文本 token）
    - image_ref 同样是图片语义，按图片估算
    """
    per_message = 4
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return per_message + estimate_text_tokens(content)
    if isinstance(content, list):
        total = per_message
        for block in content:
            if not isinstance(block, dict):
                total += estimate_text_tokens(str(block))
                continue
            block_type = block.get("type")
            if block_type == "text":
                total += estimate_text_tokens(str(block.get("text", "")))
            elif block_type in ("image_url", "image", "image_ref"):
                total += settings.context_image_token_estimate
            else:
                total += estimate_text_tokens(str(block))
        return total
    return per_message + estimate_text_tokens(str(content))


def estimate_tokens(messages: list) -> int:
    """估算消息列表总 token"""
    return sum(estimate_message_tokens(m) for m in messages)


# ========== 轮次切分 ==========

def group_turns(messages: list) -> list[list]:
    """
    按「轮」切分消息：每轮以 HumanMessage 开头，其后直到下一条 HumanMessage 之前的消息
    都归入本轮（含 AI 回复）。

    保证：裁剪时按整轮操作，不会出现只删 Human 或只删 AI 的非法结构。
    """
    turns: list[list] = []
    current: Optional[list] = None
    for message in messages:
        if isinstance(message, HumanMessage):
            if current:
                turns.append(current)
            current = [message]
        else:
            if current is None:
                current = [message]
            else:
                current.append(message)
    if current:
        turns.append(current)
    return turns


# ========== 消息结构净化 ==========

def _is_empty_content(content) -> bool:
    """判断消息 content 是否为空（字符串空 / 多模态块无有效文本）"""
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                if str(block).strip():
                    return False
                continue
            block_type = block.get("type")
            if block_type == "text" and str(block.get("text", "")).strip():
                return False
            if block_type in ("image_url", "image", "image_ref"):
                return False
        return True
    return not str(content).strip() if content is not None else True


def sanitize_for_llm(messages: list) -> list:
    """
    净化历史消息结构（只作用于副本）：

    - 带 tool_calls 的 AIMessage 在 state 中缺少对应 ToolMessage（ToolMessage 不落库），
      直接原样发给模型会触发"消息顺序非法"；这里降级为纯文本 AIMessage，无文本则丢弃
    - 空 content 的 AIMessage 丢弃（避免无意义占位）
    - ToolMessage 不在持久化历史中，防御性剔除
    """
    cleaned: list = []
    dropped = 0
    for message in messages:
        if isinstance(message, ToolMessage):
            dropped += 1
            continue
        if isinstance(message, AIMessage):
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                if _is_empty_content(message.content):
                    dropped += 1
                    continue
                cleaned.append(AIMessage(content=message.content))
                continue
            if _is_empty_content(message.content):
                dropped += 1
                continue
        cleaned.append(message)
    if dropped:
        logger.debug(f"[Context] sanitize 副本：剔除/降级 {dropped} 条非法或空消息")
    return cleaned


# ========== 历史图片治理 ==========

def _is_image_block(block) -> bool:
    return isinstance(block, dict) and block.get("type") in ("image_url", "image", "image_ref")


def _image_placeholder() -> dict:
    return {"type": "text", "text": IMAGE_PLACEHOLDER}


async def _resolve_image_to_data_url(block: dict) -> Optional[str]:
    """
    把图片块解析成 base64 data URL，供多模态模型直接消费（避免模型侧下载失败）

    - image_url：已有 data URL 直接返回，远程 URL 下载后转 base64
    - image_ref：先重签 GET URL，再下载转 base64
    """
    block_type = block.get("type")
    if block_type == "image_url":
        url = (block.get("image_url") or {}).get("url", "")
        if not url:
            return None
        return await ensure_image_base64_data_url(url)
    if block_type == "image":
        url = block.get("url", "")
        return await ensure_image_base64_data_url(url) if url else None
    if block_type == "image_ref":
        key = block.get("key", "")
        if not key:
            return None
        url = await oss.presign_get(key)
        return await ensure_image_base64_data_url(url)
    return None


async def apply_image_policy(messages: list) -> list:
    """
    历史图片治理（只作用于 LLM 副本）：

    - 当前轮（最后一条 HumanMessage）图片：解析为 base64 data URL，保证多模态识别正常
    - 历史轮次图片：仅保留最近 context_image_keep_recent 张（同样解析为 base64），
      其余替换为占位文本，避免 base64 常驻上下文
    - 解析失败时降级：先保留原始 URL，再失败则替换为占位文本，不中断主回答
    """
    if not settings.context_image_govern_enabled:
        return list(messages)

    # 定位当前轮起点（最后一条 HumanMessage）
    current_start = len(messages)
    for idx in range(len(messages) - 1, -1, -1):
        if isinstance(messages[idx], HumanMessage):
            current_start = idx
            break

    # 收集历史轮次中的图片位置（按时间顺序）
    history_image_positions: list[tuple[int, int]] = []
    for msg_idx in range(current_start):
        content = getattr(messages[msg_idx], "content", None)
        if isinstance(content, list):
            for block_idx, block in enumerate(content):
                if _is_image_block(block):
                    history_image_positions.append((msg_idx, block_idx))

    keep_recent = max(settings.context_image_keep_recent, 0)
    kept_positions = set(history_image_positions[-keep_recent:]) if keep_recent else set()

    # 并发解析需要保留的图片（当前轮 + 最近 N 张历史图）
    current_positions: set[tuple[int, int]] = set()
    jobs: list[tuple[int, int, dict]] = []
    for msg_idx in range(current_start, len(messages)):
        content = getattr(messages[msg_idx], "content", None)
        if isinstance(content, list):
            for block_idx, block in enumerate(content):
                if _is_image_block(block):
                    current_positions.add((msg_idx, block_idx))
                    jobs.append((msg_idx, block_idx, block))
    for msg_idx, block_idx in sorted(kept_positions):
        jobs.append((msg_idx, block_idx, messages[msg_idx].content[block_idx]))
    allowed_positions = current_positions | kept_positions

    results = await asyncio.gather(
        *[_resolve_image_to_data_url(block) for _, _, block in jobs],
        return_exceptions=True,
    )
    resolved: dict[tuple[int, int], Optional[str]] = {}
    for (msg_idx, block_idx, _), outcome in zip(jobs, results):
        if isinstance(outcome, Exception):
            logger.warning(f"[ImageGovern] 图片解析失败（降级为占位）: {type(outcome).__name__}")
            resolved[(msg_idx, block_idx)] = None
        else:
            resolved[(msg_idx, block_idx)] = outcome

    # 重建副本（不修改原消息对象）
    governed: list = []
    replaced = 0
    for msg_idx, message in enumerate(messages):
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            governed.append(message)
            continue

        new_blocks: list = []
        for block_idx, block in enumerate(content):
            if not _is_image_block(block):
                new_blocks.append(block)
                continue
            position = (msg_idx, block_idx)
            if position not in allowed_positions:
                # 超出保留额度的历史图片：直接占位，绝不保留 base64/URL
                new_blocks.append(_image_placeholder())
                replaced += 1
                continue
            data_url = resolved.get(position)
            if data_url:
                new_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": data_url},
                })
                continue
            # 解析失败：保留原始可访问 URL（若有），否则占位
            original_url = ""
            if block.get("type") == "image_url":
                original_url = (block.get("image_url") or {}).get("url", "")
            elif block.get("type") == "image":
                original_url = block.get("url", "")
            if original_url.startswith("data:"):
                new_blocks.append({
                    "type": "text",
                    "text": IMAGE_FAILED_PLACEHOLDER,
                })
            elif original_url:
                new_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": original_url},
                })
            else:
                new_blocks.append({
                    "type": "text",
                    "text": IMAGE_FAILED_PLACEHOLDER,
                })
            replaced += 1

        governed.append(HumanMessage(content=new_blocks))

    if replaced:
        logger.info(f"[ImageGovern] LLM 副本图片治理完成：替换 {replaced} 张历史/不可用图片为占位")
    return governed


# ========== 统一构造入口 ==========

async def build_llm_messages(
    system_message,
    state_messages: list,
    *,
    node: str = "",
) -> list:
    """
    构造调用 LLM 的消息副本（system + 历史副本）

    调用方（图节点）只需传入 state 中的 messages，本函数负责：
    - 结构净化（悬空 tool_calls / 空消息）
    - 历史图片治理（base64 占位、保留最近 N 张）

    注意：返回值是全新列表，state["messages"] 的内容与顺序不受任何影响。
    """
    source = list(state_messages or [])
    copy = source
    try:
        copy = sanitize_for_llm(source)
        if settings.context_manage_enabled:
            copy = await apply_image_policy(copy)
    except Exception as e:
        # 增强能力失败必须降级：退回未处理副本，绝不中断主回答
        logger.warning(f"[Context] [{node}] 副本构造失败（降级为不处理）: {type(e).__name__}: {e}")
        copy = source

    logger.debug(
        f"[Context] [{node}] LLM 副本构造完成：state={len(source)} 条 → 副本={len(copy)} 条，"
        f"估算 token={estimate_tokens(copy)}"
    )
    return [system_message] + copy
