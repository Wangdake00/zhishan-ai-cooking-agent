"""
智膳 AI — 长会话上下文治理测试（P0 图片治理 + P1 滑动窗口/摘要 + P2 偏好记忆）
==============================================================================
运行方式: python tests/test_context.py

覆盖:
  1. token 粗估（中文/英文/图片）
  2. 消息结构净化（悬空 tool_calls / 空消息）
  3. 历史图片治理（占位替换、保留最近 N 张、当前轮保留）
  4. 图片解析失败降级（不抛出、不中断）
  5. 不修改持久化 state 消息对象
  6. 短会话不触发裁剪 / 长会话触发且消息结构合法
  7. 增量摘要 + 摘要失败降级（未超硬上限不裁剪 / 超硬上限降为无摘要窗口）
  8. 跨会话偏好：显式触发词抽取、JSON 解析失败降级、system prompt 注入格式
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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


def build_turns(count: int, with_images: bool = True) -> list:
    """构造指定轮数的会话（每轮含 1 张图 + 文本回复）"""
    messages = []
    for i in range(count):
        if with_images:
            messages.append(make_image_msg(f"第{i + 1}轮问题，带一张食材图", 1))
        else:
            messages.append(HumanMessage(content=f"第{i + 1}轮问题和饮食需求"))
        messages.append(AIMessage(content=f"第{i + 1}轮回答，包含菜谱步骤和营养建议。" * 2))
    return messages


class FakeLLM:
    """摘要 LLM 桩：可控成功/失败"""

    def __init__(self, text: str = "用户目标减脂，忌口香菜；已推荐番茄鸡胸肉；未完成：计算晚餐热量。", fail: bool = False):
        self.text = text
        self.fail = fail

    async def ainvoke(self, messages):
        if self.fail:
            raise RuntimeError("summary llm down")
        return AIMessage(content=self.text)


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
        bundle = asyncio.run(ctx.build_llm_messages(AIMessage(content="system"), source, node="test"))
    finally:
        ctx.apply_image_policy = original

    check("副本构造失败仍返回 system + 原始消息", len(bundle.messages) == 1 + len(source))
    check("副本构造失败标记降级", bundle.trimmed is False and bundle.reason == "error")
    check("副本构造失败不抛出异常", bundle.messages[0].content == "system")


# ========== 6. 裁剪触发 ==========

def test_window_trigger():
    short = [
        HumanMessage(content="鸡胸肉怎么做好吃"),
        AIMessage(content="建议香煎鸡胸肉。"),
        HumanMessage(content="那热量多少"),
        AIMessage(content="约 165 kcal/100g。"),
    ]
    short_bundle = asyncio.run(ctx.build_llm_messages(SystemMessage(content="sys"), short, llm=FakeLLM()))
    check("短会话不触发裁剪", not short_bundle.trimmed, f"reason={short_bundle.reason}")
    check("短会话完整保留历史", len(short_bundle.messages) == 1 + len(short))

    long_messages = build_turns(50)
    long_bundle = asyncio.run(ctx.build_llm_messages(SystemMessage(content="sys"), long_messages, llm=FakeLLM()))
    check("长会话触发裁剪", long_bundle.trimmed, f"reason={long_bundle.reason}")

    turn_messages = [m for m in long_bundle.messages[1:] if not isinstance(m, SystemMessage)]
    turns_after = ctx.group_turns(turn_messages)
    check("裁剪后消息结构合法（每条 Human 在轮首）",
          all(isinstance(t[0], HumanMessage) for t in turns_after if t))
    check("裁剪保留首轮", "第1轮问题" in " ".join(text_of(turns_after[0][0])))
    check("裁剪后轮数不超过 首轮+最近 N 轮",
          len(turns_after) <= ctx.settings.context_keep_recent_turns + 1, f"实际={len(turns_after)}")

    kept_images = sum(len(image_blocks(m)) for m in turn_messages)
    check("窗口内图片仅保留最近 1 张历史图 + 当前轮图", kept_images == 2, f"实际={kept_images}")


def test_increment_summary():
    messages = build_turns(12, with_images=False)
    bundle = asyncio.run(ctx.build_llm_messages(SystemMessage(content="sys"), messages, llm=FakeLLM()))
    check("轮数超阈值触发裁剪", bundle.trimmed and bundle.reason == "turns", f"reason={bundle.reason}")
    check("生成摘要并写回摘要状态", bool(bundle.summary) and 0 < bundle.summary_upto <= 12,
          f"upto={bundle.summary_upto}")
    check("摘要注入为独立 SystemMessage",
          any(isinstance(m, SystemMessage) and "历史会话摘要" in m.content for m in bundle.messages))
    check("摘要覆盖轮数等于滑出轮数",
          bundle.summary_upto == len(messages) // 2 - ctx.settings.context_keep_recent_turns,
          f"实际={bundle.summary_upto}")

    # 二次触发时应增量摘要（不重复压缩已覆盖轮次）
    more_messages = build_turns(14, with_images=False)
    bundle2 = asyncio.run(ctx.build_llm_messages(
        SystemMessage(content="sys"), more_messages,
        summary=bundle.summary, summary_upto=bundle.summary_upto, llm=FakeLLM(),
    ))
    check("增量摘要覆盖新的滑出轮次",
          bundle2.summary_upto == len(more_messages) // 2 - ctx.settings.context_keep_recent_turns,
          f"实际={bundle2.summary_upto}")


def test_summary_failure_degrade():
    """摘要失败且未超硬上限：降级为不裁剪；超硬上限：退化为无摘要窗口"""
    messages = build_turns(12, with_images=False)
    bundle = asyncio.run(ctx.build_llm_messages(SystemMessage(content="sys"), messages, llm=FakeLLM(fail=True)))
    check("摘要失败未超硬上限时降级为不裁剪", not bundle.trimmed and bundle.reason == "summary_failed",
          f"reason={bundle.reason}")
    check("摘要失败时完整保留历史", len(bundle.messages) == 1 + len(messages))

    huge = build_turns(50)
    huge_bundle = asyncio.run(ctx.build_llm_messages(SystemMessage(content="sys"), huge, llm=FakeLLM(fail=True)))
    check("摘要失败且超硬上限时退化为无摘要窗口", huge_bundle.trimmed and huge_bundle.reason == "hard_limit",
          f"reason={huge_bundle.reason}")
    check("硬上限窗口内无摘要注入",
          not any(isinstance(m, SystemMessage) and "历史会话摘要" in m.content for m in huge_bundle.messages))


# ========== 7. 50 轮带图会话 token 对比 ==========

def test_token_comparison():
    messages = build_turns(50, with_images=True)
    before = ctx.estimate_tokens(messages)
    bundle = asyncio.run(ctx.build_llm_messages(SystemMessage(content="sys"), messages, llm=FakeLLM()))
    after = ctx.estimate_tokens(bundle.messages)
    print(f"    50 轮带图会话：改动前 ≈ {before} token → 改动后 ≈ {after} token，"
          f"降幅 ≈ {round((1 - after / before) * 100, 1)}%")
    check("50 轮带图会话 token 降幅大于 80%", after < before * 0.2, f"before={before}, after={after}")


# ========== 8. 跨会话偏好记忆 ==========

class FakeExtractLLM:
    """偏好抽取 LLM 桩"""

    def __init__(self, payload: str):
        self.payload = payload
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return AIMessage(content=self.payload)


def test_preference_format():
    from app.agents import graph as g

    text = g._format_preferences([
        {"category": "忌口", "content": "不吃香菜"},
        {"category": "过敏", "content": "对花生过敏"},
    ])
    check("偏好注入段包含类别与内容", "【用户长期偏好" in text and "[忌口] 不吃香菜" in text)
    check("无偏好时不注入", g._format_preferences([]) == "")


def test_preference_extract():
    from app.agents import graph as g

    original_model = g._model
    try:
        fake = FakeExtractLLM('```json\n[{"category":"过敏","content":"对花生过敏"}]\n```')
        g._model = fake
        extracted = asyncio.run(g._extract_preferences("记住，我对花生过敏，以后别推荐含花生的菜"))
        check("命中触发词时抽取偏好", extracted == [{"category": "过敏", "content": "对花生过敏"}],
              f"实际={extracted}")

        calls_before = fake.calls
        empty = asyncio.run(g._extract_preferences("今晚想吃点清淡的"))
        check("无触发词时不调用 LLM", empty == [] and fake.calls == calls_before)

        g._model = FakeExtractLLM("这不是 JSON")
        failed = asyncio.run(g._extract_preferences("记住我不吃辣"))
        check("抽取结果非法时降级为空", failed == [])
    finally:
        g._model = original_model


def main():
    print("=" * 66)
    print("智膳 AI — 上下文治理测试（P0 + P1）")
    print("=" * 66)
    print(f"配置: max_turns={ctx.settings.context_max_turns}, max_tokens={ctx.settings.context_max_tokens}, "
          f"keep_recent={ctx.settings.context_keep_recent_turns}, keep_first={ctx.settings.context_keep_first_turn}, "
          f"hard_limit={ctx.settings.context_hard_limit_tokens}")
    print("-" * 66)

    test_token_estimate()
    test_sanitize()
    test_image_governance()
    test_image_ref_degrade()
    test_build_llm_messages_degrade()
    test_window_trigger()
    test_increment_summary()
    test_summary_failure_degrade()
    test_token_comparison()
    test_preference_format()
    test_preference_extract()

    print("-" * 66)
    print(f"通过 {_passed} / {_passed + _failed}")
    if _failed:
        print(f"{RED}有 {_failed} 项失败{END}")
        sys.exit(1)
    print(f"{GREEN}全部通过{END}")


if __name__ == "__main__":
    main()
