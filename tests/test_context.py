"""
智膳 AI — 长会话上下文治理测试（P0 图片治理）
==========================================
运行方式: python tests/test_context.py

覆盖:
  1. token 粗估（中文/英文/图片）
  2. 消息结构净化（悬空 tool_calls / 空消息）
  3. 历史图片治理（占位替换、保留最近 N 张、当前轮保留）
  4. 图片解析失败降级（不抛出、不中断）
  5. 不修改持久化 state 消息对象
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, HumanMessage

from app.agents import context as ctx

GREEN = "\033[92m"
RED = "\033[91m"
END = "\033[0m"

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = ""):
    """断言并打印结果"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"{GREEN}✅ {name}{END}")
    else:
        _failed += 1
        print(f"{RED}❌ {name} {detail}{END}")


def make_image_msg(text: str, image_count: int = 1) -> HumanMessage:
    """构造带 base64 data URL 的用户消息（模拟旧版持久化格式）"""
    blocks = [{"type": "text", "text": text}]
    for i in range(image_count):
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,aGVsbG8taW1nLQ=={i}"},
        })
    return HumanMessage(content=blocks)


def image_blocks(msg) -> list:
    if not isinstance(msg.content, list):
        return []
    return [b for b in msg.content if b.get("type") == "image_url"]


def text_of(msg) -> list[str]:
    if not isinstance(msg.content, list):
        return [msg.content] if isinstance(msg.content, str) else []
    return [str(b.get("text", "")) for b in msg.content if b.get("type") == "text"]


# ========== 1. token 估算 ==========

def test_token_estimate():
    zh = "红烧肉怎么做才好吃，需要准备哪些食材和调料？"
    en = "How to cook braised pork belly with soy sauce?"
    check("中文 token 估算在合理区间", 10 <= ctx.estimate_text_tokens(zh) <= 30,
          f"实际={ctx.estimate_text_tokens(zh)}")
    check("英文 token 估算小于字符数", ctx.estimate_text_tokens(en) <= len(en),
          f"实际={ctx.estimate_text_tokens(en)}")

    img_msg = make_image_msg("看看这张图", 1)
    tokens = ctx.estimate_message_tokens(img_msg)
    check("图片消息 token 不随 base64 长度膨胀", tokens < ctx.settings.context_image_token_estimate + 200,
          f"实际={tokens}")

    check("消息列表 token 汇总",
          ctx.estimate_tokens([img_msg, AIMessage(content="好的")]) ==
          ctx.estimate_message_tokens(img_msg) + ctx.estimate_message_tokens(AIMessage(content="好的")))


# ========== 2. 结构净化 ==========

def test_sanitize():
    dangling_empty = AIMessage(content="", tool_calls=[{"name": "calculate_nutrition", "args": {}, "id": "call_1"}])
    dangling_with_text = AIMessage(
        content="我先算一下", tool_calls=[{"name": "calculate_nutrition", "args": {}, "id": "call_2"}]
    )
    empty_ai = AIMessage(content="")
    source = [HumanMessage(content="hi"), dangling_empty, dangling_with_text, empty_ai, AIMessage(content="结论")]

    cleaned = ctx.sanitize_for_llm(source)
    check("悬空且无文本的 tool_calls 消息被剔除", len(cleaned) == 3, f"实际={len(cleaned)}")
    check("悬空但有文本的 tool_calls 降级为纯文本",
          isinstance(cleaned[1], AIMessage) and not cleaned[1].tool_calls and cleaned[1].content == "我先算一下")
    check("空 AI 消息被剔除", all(str(getattr(m, "content", "")) for m in cleaned))


# ========== 3/4/5. 图片治理 ==========

def test_image_governance():
    history_1 = make_image_msg("第一轮图片", 1)
    history_2 = make_image_msg("第二轮图片", 1)
    current = make_image_msg("当前轮图片", 2)
    state_messages = [history_1, AIMessage(content="回复1"), history_2, AIMessage(content="回复2"), current]

    governed = asyncio.run(ctx.apply_image_policy(state_messages))

    check("最早的历史图片被替换为占位文本", ctx.IMAGE_PLACEHOLDER in text_of(governed[0]))
    check("被替换的历史消息不再携带图片块", len(image_blocks(governed[0])) == 0)
    check("最近 1 张历史图片保留", len(image_blocks(governed[2])) == 1)
    check("当前轮全部图片保留", len(image_blocks(governed[4])) == 2)

    # 原始 state 消息对象内容不变（前端历史依赖 checkpoint 原图）
    check("原始 state 消息未被修改",
          len(image_blocks(state_messages[0])) == 1 and ctx.IMAGE_PLACEHOLDER not in text_of(state_messages[0]))
    check("治理结果为新对象", governed[0] is not state_messages[0])
    check("治理后通知文本保留", "第一轮图片" in text_of(governed[0]))


def test_image_ref_degrade():
    """OSS 不可用 / 重签失败时，图片占位降级且不抛出"""
    ref_history = HumanMessage(content=[
        {"type": "text", "text": "历史图"},
        {"type": "image_ref", "key": "chat/1/t/a.png"},
        {"type": "image_ref", "key": "chat/1/t/b.png"},
    ])
    ref_current = HumanMessage(content=[
        {"type": "text", "text": "当前图"},
        {"type": "image_ref", "key": "chat/1/t/c.png"},
    ])

    async def _fail_presign(key, expires_seconds=0):
        raise RuntimeError("OSS 未配置")

    original = ctx.oss.presign_get
    ctx.oss.presign_get = _fail_presign
    try:
        governed = asyncio.run(ctx.apply_image_policy([ref_history, AIMessage(content="回复"), ref_current]))
    finally:
        ctx.oss.presign_get = original

    history_texts = text_of(governed[0])
    check("超额历史 image_ref 降级为历史占位", ctx.IMAGE_PLACEHOLDER in history_texts)
    check("保留额度内 image_ref 重签失败降级为失败占位", ctx.IMAGE_FAILED_PLACEHOLDER in history_texts)
    check("当前轮 image_ref 重签失败降级且不抛出", ctx.IMAGE_FAILED_PLACEHOLDER in text_of(governed[2]))


def test_build_llm_messages_degrade():
    """副本构造内部异常时必须降级为原始副本，不中断主回答"""
    source = [HumanMessage(content="红烧肉热量多少"), AIMessage(content="")]

    async def _broken(messages):
        raise RuntimeError("govern down")

    original = ctx.apply_image_policy
    ctx.apply_image_policy = _broken
    try:
        built = asyncio.run(ctx.build_llm_messages(AIMessage(content="system"), source, node="test"))
    finally:
        ctx.apply_image_policy = original

    check("副本构造失败仍返回 system + 原始消息", len(built) == 1 + len(source))
    check("副本构造失败不抛出异常", built[0].content == "system")


def main():
    print("=" * 62)
    print("智膳 AI — 上下文治理测试（P0 图片治理）")
    print("=" * 62)
    print(f"配置: image_govern={ctx.settings.context_image_govern_enabled}, "
          f"keep_recent={ctx.settings.context_image_keep_recent}, "
          f"persist={ctx.settings.context_image_persist}")
    print("-" * 62)

    test_token_estimate()
    test_sanitize()
    test_image_governance()
    test_image_ref_degrade()
    test_build_llm_messages_degrade()

    print("-" * 62)
    print(f"通过 {_passed} / {_passed + _failed}")
    if _failed:
        print(f"{RED}有 {_failed} 项失败{END}")
        sys.exit(1)
    print(f"{GREEN}全部通过{END}")


if __name__ == "__main__":
    main()
