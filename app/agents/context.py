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
from dataclasses import dataclass
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

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


# ========== 滑动窗口 + 增量摘要 ==========

@dataclass
class ContextBundle:
    """LLM 副本与摘要状态的构建结果（摘要覆盖式写回 state，非 reducer）"""
    messages: list
    summary: str
    summary_upto: int
    trimmed: bool
    reason: str


def _content_to_text(content) -> str:
    """把消息 content 提取为纯文本（图片块以占位符表示，避免摘要输入被 base64 撑爆）"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") in ("image_url", "image", "image_ref"):
                parts.append("[图片]")
        return " ".join(p for p in parts if p)
    return str(content or "")


def _serialize_turns_for_summary(turns: list[list]) -> str:
    """把待摘要轮次序列化为文本，并按配置截断输入长度"""
    lines = []
    for turn in turns:
        for message in turn:
            if isinstance(message, HumanMessage):
                role = "用户"
            elif isinstance(message, AIMessage):
                role = "助手"
            else:
                continue
            text = _content_to_text(getattr(message, "content", "")).strip()
            if text:
                lines.append(f"{role}: {text}")
    transcript = "\n".join(lines)
    return transcript[: max(settings.context_summary_input_max_chars, 500)]


async def maybe_summarize(
    previous_summary: str,
    turns: list[list],
    llm,
) -> Optional[str]:
    """
    增量摘要：把「上一次摘要 + 本次新滑出的旧轮次」压缩成新摘要。

    返回 None 表示摘要失败（调用方据此降级）；任何异常都不向外抛出。
    """
    if llm is None:
        return None
    transcript = _serialize_turns_for_summary(turns)
    if not transcript.strip():
        return previous_summary or ""

    prompt = (
        "你是会话压缩器。请把下面的历史对话压缩成简洁的中文摘要，必须强制保留三类信息：\n"
        "1. 用户约束：忌口、过敏、饮食目标、人数/口味偏好等\n"
        "2. 已确认结论：已推荐的菜谱名、已计算出的营养数值、已确定的做法\n"
        "3. 未完成任务：用户提出但尚未解决的问题\n"
        f"要求：不超过 {settings.context_summary_max_chars} 字，只输出摘要正文，不要寒暄或解释。\n\n"
        f"【已有摘要】\n{previous_summary or '（无）'}\n\n"
        f"【新增历史对话】\n{transcript}"
    )

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content="你是严谨的会话摘要器，只输出摘要正文。"),
                         HumanMessage(content=prompt)]),
            timeout=settings.context_summary_timeout_seconds,
        )
    except Exception as e:
        logger.warning(f"[Summary] 摘要生成失败（将降级）: {type(e).__name__}: {e}")
        return None

    text = _content_to_text(getattr(response, "content", "")).strip()
    if not text:
        logger.warning("[Summary] 摘要返回为空（将降级）")
        return None
    text = text[: settings.context_summary_max_chars]
    logger.info(f"[Summary] 增量摘要完成：覆盖 {len(turns)} 轮，摘要 {len(text)} 字")
    return text


async def _compose(
    system_message,
    messages: list,
    summary: str,
    summary_upto: int,
    trimmed: bool,
    reason: str,
    node: str,
) -> ContextBundle:
    """应用图片治理并组装最终消息（system + 摘要 + 窗口副本）"""
    copy = messages
    if settings.context_manage_enabled:
        copy = await apply_image_policy(copy)

    prefix = [system_message]
    if summary:
        prefix.append(SystemMessage(content=f"【历史会话摘要（较早轮次已压缩）】\n{summary}"))

    logger.debug(
        f"[Context] [{node}] LLM 副本构造完成：window={len(copy)} 条，"
        f"trimmed={trimmed}({reason})，summary_upto={summary_upto}，"
        f"估算 token={estimate_tokens(prefix + copy)}"
    )
    return ContextBundle(
        messages=prefix + copy,
        summary=summary,
        summary_upto=summary_upto,
        trimmed=trimmed,
        reason=reason,
    )


async def build_llm_messages(
    system_message,
    state_messages: list,
    *,
    summary: str = "",
    summary_upto: int = 0,
    llm=None,
    node: str = "",
) -> ContextBundle:
    """
    构造调用 LLM 的消息副本（system + 可选摘要 + 窗口副本）

    策略（D1/D2/D3）：
    - 双阈值触发：轮数 > context_max_turns 或估算 token > context_max_tokens
    - 保留首轮 + 最近 context_keep_recent_turns 轮，中间段增量摘要
    - 摘要失败：估算 token 未超硬上限时降级为不裁剪；超过硬上限则退化为无摘要窗口
    - 任何环节失败都降级，绝不影响 state["messages"] 与主回答

    注意：返回值始终是全新对象，state["messages"] 的内容与顺序不受任何影响。
    """
    source = list(state_messages or [])
    fallback_summary = summary or ""
    fallback_upto = summary_upto or 0

    try:
        copy = sanitize_for_llm(source)
        turns = group_turns(copy)
        total_turns = len(turns)

        if (
            not settings.context_manage_enabled
            or total_turns <= 1
            or total_turns <= settings.context_keep_recent_turns
        ):
            return await _compose(
                system_message, copy, fallback_summary, fallback_upto, False, "", node
            )

        tokens = estimate_tokens(copy)
        turns_hit = total_turns > settings.context_max_turns
        tokens_hit = tokens > settings.context_max_tokens
        if not (turns_hit or tokens_hit):
            logger.debug(
                f"[Context] [{node}] 未触发裁剪：turns={total_turns}/{settings.context_max_turns}，"
                f"tokens={tokens}/{settings.context_max_tokens}"
            )
            # 未触发时历史完整保留，无需注入摘要（避免重复占用 token）
            governed = await apply_image_policy(copy) if settings.context_manage_enabled else copy
            return ContextBundle(
                messages=[system_message] + governed,
                summary=fallback_summary,
                summary_upto=fallback_upto,
                trimmed=False,
                reason="",
            )

        reason = "turns" if turns_hit else "tokens"
        keep_recent = max(settings.context_keep_recent_turns, 1)
        keep_tail_start = max(total_turns - keep_recent, 0)

        window: list = []
        if settings.context_keep_first_turn and keep_tail_start > 0:
            window.append(turns[0])
        window.extend(turns[keep_tail_start:])
        window_messages = [m for turn in window for m in turn]

        effective_upto = min(max(fallback_upto, 0), keep_tail_start)
        to_summarize = turns[effective_upto:keep_tail_start]

        new_summary = fallback_summary
        new_upto = keep_tail_start
        summary_failed = False

        if settings.context_summary_enabled and to_summarize:
            summarized = await maybe_summarize(fallback_summary, to_summarize, llm)
            if summarized is None:
                summary_failed = True
            else:
                new_summary = summarized

        if summary_failed:
            if tokens <= settings.context_hard_limit_tokens:
                # 摘要失败且未超硬上限：当轮降级为不裁剪（保留完整历史）
                logger.warning(f"[Context] [{node}] 摘要失败，降级为不裁剪")
                governed = await apply_image_policy(copy) if settings.context_manage_enabled else copy
                return ContextBundle(
                    messages=[system_message] + governed,
                    summary=fallback_summary,
                    summary_upto=fallback_upto,
                    trimmed=False,
                    reason="summary_failed",
                )
            # 超过硬上限：退化为无摘要滑动窗口，保证上下文不撑爆
            logger.warning(f"[Context] [{node}] 摘要失败且超硬上限({tokens}>{settings.context_hard_limit_tokens})，退化为无摘要窗口")
            new_summary = fallback_summary
            new_upto = fallback_upto
            reason = "hard_limit"

        logger.info(
            f"[Context] [{node}] 触发裁剪: {reason}，原 {total_turns} 轮/{tokens} token "
            f"→ 保留 {len(window)} 轮，摘要 {len(new_summary)} 字"
        )
        return await _compose(
            system_message, window_messages, new_summary, new_upto, True, reason, node
        )

    except Exception as e:
        # 增强能力失败必须降级：退回未处理副本，绝不中断主回答
        logger.warning(f"[Context] [{node}] 副本构造失败（降级为不处理）: {type(e).__name__}: {e}")
        return ContextBundle(
            messages=[system_message] + source,
            summary=fallback_summary,
            summary_upto=fallback_upto,
            trimmed=False,
            reason="error",
        )
