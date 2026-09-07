"""
智膳 AI — LangGraph StateGraph 功能测试脚本
==========================================

运行方式: python test_stategraph.py

测试覆盖:
  1. 图结构验证（StateGraph 编译、节点/边完整性）
  2. 意图路由测试（nutrition / recipe / general）
  3. 工具调用测试（RAG 检索、营养计算、购物清单）
  4. 多轮对话持久化（SqliteSaver checkpoint）
  5. 流式对话接口（stream_chat）
  6. 会话管理（列表、历史消息、删除）
"""

import asyncio
import json
import os
import sys

from langchain_core.messages import HumanMessage, AIMessage

from app.agents.graph import (
    get_graph,
    _get_checkpointer,
    detect_intent,
    list_sessions,
    get_messages,
    delete_session,
)
from app.common.logger import logger


# ========== 颜色输出 ==========
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  {GREEN}✓{RESET} {name}  {CYAN}{detail}{RESET}")
    else:
        failed += 1
        print(f"  {RED}✗{RESET} {name}  {CYAN}{detail}{RESET}")


def section(title: str):
    print(f"\n{YELLOW}{'='*60}{RESET}")
    print(f"{YELLOW}  {title}{RESET}")
    print(f"{YELLOW}{'='*60}{RESET}")


# ========== 测试 1: 图结构验证 ==========
def test_graph_structure():
    section("测试 1: 图结构验证")
    g = get_graph()
    check("StateGraph 编译成功", g is not None)

    # 检查节点
    nodes = list(g.get_graph().nodes.keys()) if hasattr(g, 'get_graph') else []
    check("节点数量正确 (≥4)", len(nodes) >= 4, f"实际: {len(nodes)} 个节点")
    check("包含 detect_intent 节点", "detect_intent" in nodes)
    check("包含 nutrition_node 节点", "nutrition_node" in nodes)
    check("包含 recipe_node 节点", "recipe_node" in nodes)
    check("包含 general_node 节点", "general_node" in nodes)
    check("包含 END 节点", "__end__" in nodes)


# ========== 测试 2: 意图路由 ==========
def test_intent_routing():
    section("测试 2: 意图检测（关键词路由）")

    # Nutrition 意图
    state_nutrition = {
        "messages": [HumanMessage(content="帮我算一下鸡胸肉的卡路里和蛋白质")],
        "intent": "general",
    }
    result = detect_intent(state_nutrition)
    check("营养意图检测", result["intent"] == "nutrition",
          f"输入: '帮我算一下鸡胸肉的卡路里' → 输出: {result['intent']}")

    # Recipe 意图
    state_recipe = {
        "messages": [HumanMessage(content="冰箱里有西红柿和鸡蛋，能做什么菜")],
        "intent": "general",
    }
    result = detect_intent(state_recipe)
    check("菜谱意图检测", result["intent"] == "recipe",
          f"输入: '冰箱里有西红柿和鸡蛋' → 输出: {result['intent']}")

    # General 意图
    state_general = {
        "messages": [HumanMessage(content="你好，你是谁")],
        "intent": "general",
    }
    result = detect_intent(state_general)
    check("通用意图检测", result["intent"] == "general",
          f"输入: '你好，你是谁' → 输出: {result['intent']}")

    # 空输入
    state_empty = {
        "messages": [],
        "user_prompt": "",
        "intent": "general",
    }
    result = detect_intent(state_empty)
    check("空消息兜底", result["intent"] == "general")


# ========== 测试 3: 子图执行 ==========
def test_subgraph_execution():
    section("测试 3: 子图节点执行")
    g = get_graph()

    thread_id = "test_nutrition_flow"
    config = {"configurable": {"thread_id": thread_id}}

    # Nutrition Flow
    result = g.invoke({
        "messages": [HumanMessage(content="帮我算一下50g鸡胸肉的营养成分")],
        "user_id": 1,
        "thread_id": thread_id,
        "user_prompt": "帮我算一下50g鸡胸肉的营养成分",
        "image_urls": [],
        "intent": "nutrition",
    }, config)

    msgs = result.get("messages", [])
    check("Nutrition Flow 返回消息", len(msgs) >= 1, f"共 {len(msgs)} 条")

    ai_msgs = [m for m in msgs if isinstance(m, AIMessage)]
    if ai_msgs:
        content = ai_msgs[-1].content
        check("营养分析包含卡路里", "卡路里" in content or "kcal" in str(content),
              f"内容: {str(content)[:80]}...")

    # 清理
    delete_session(thread_id, user_id=1)


# ========== 测试 4: RAG 工具调用 ==========
def test_rag_tool():
    section("测试 4: RAG 菜谱检索工具")
    g = get_graph()

    thread_id = "test_rag_flow"
    config = {"configurable": {"thread_id": thread_id}}

    result = g.invoke({
        "messages": [HumanMessage(content="推荐几道清淡的减脂餐")],
        "user_id": 1,
        "thread_id": thread_id,
        "user_prompt": "推荐几道清淡的减脂餐",
        "image_urls": [],
        "intent": "recipe",
    }, config)

    msgs = result.get("messages", [])
    check("Recipe Flow 返回消息", len(msgs) >= 1, f"共 {len(msgs)} 条")

    ai_msgs = [m for m in msgs if isinstance(m, AIMessage)]
    if ai_msgs:
        content = str(ai_msgs[-1].content)
        has_rag = "🔍" in content or "菜谱" in content or "做法" in content
        check("RAG 检索结果包含菜谱", has_rag, f"内容: {content[:100]}...")

    delete_session(thread_id, user_id=1)


# ========== 测试 5: 多轮对话持久化 ==========
def test_multi_turn():
    section("测试 5: 多轮对话持久化")
    g = get_graph()

    thread_id = "test_multi_turn"
    config = {"configurable": {"thread_id": thread_id}}

    # 第一轮
    result1 = g.invoke({
        "messages": [HumanMessage(content="你好")],
        "user_id": 1,
        "thread_id": thread_id,
        "user_prompt": "你好",
        "image_urls": [],
        "intent": "general",
    }, config)

    msgs1 = result1.get("messages", [])
    check("第 1 轮完成", len(msgs1) == 2, f"用户+AI = {len(msgs1)} 条")

    # 第二轮（会继承第一轮消息）
    result2 = g.invoke({
        "messages": [HumanMessage(content="帮我推荐菜谱")],
        "user_id": 1,
        "thread_id": thread_id,
        "user_prompt": "帮我推荐菜谱",
        "image_urls": [],
        "intent": "recipe",
    }, config)

    msgs2 = result2.get("messages", [])
    check("第 2 轮完成（含历史）", len(msgs2) >= 3, f"共 {len(msgs2)} 条")

    # 第三轮
    result3 = g.invoke({
        "messages": [HumanMessage(content="这些菜谱多少卡")],
        "user_id": 1,
        "thread_id": thread_id,
        "user_prompt": "这些菜谱多少卡",
        "image_urls": [],
        "intent": "nutrition",
    }, config)

    msgs3 = result3.get("messages", [])
    check("第 3 轮完成（含历史）", len(msgs3) >= 4, f"共 {len(msgs3)} 条")

    # 验证持久化
    persisted = get_messages(thread_id)
    check("持久化消息数", len(persisted) >= 4, f"Checkpoint 中 {len(persisted)} 条消息")

    delete_session(thread_id, user_id=1)


# ========== 测试 6: 会话管理 ==========
def test_session_management():
    section("测试 6: 会话管理")

    # 先创建一些会话
    g = get_graph()
    for i in range(3):
        thread_id = f"test_session_{i}"
        g.invoke({
            "messages": [HumanMessage(content=f"测试会话 {i}")],
            "user_id": 99,
            "thread_id": thread_id,
            "user_prompt": f"测试会话 {i}",
            "image_urls": [],
            "intent": "general",
        }, {"configurable": {"thread_id": thread_id}})

    sessions = list_sessions(user_id=99)
    check("列出会话", len(sessions) >= 3, f"用户 99 有 {len(sessions)} 个会话")

    # 删除一个
    if sessions:
        tid = sessions[0]["thread_id"]
        delete_session(tid, user_id=99)
        sessions_after = list_sessions(user_id=99)
        check("删除会话", len(sessions_after) < len(sessions),
              f"删除前 {len(sessions)}，删除后 {len(sessions_after)}")

        # 清理剩余
        for s in sessions_after:
            delete_session(s["thread_id"], user_id=99)

    # 验证空
    empty_sessions = list_sessions(user_id=99)
    check("清理后无会话", len(empty_sessions) == 0, f"剩余 {len(empty_sessions)} 个")


# ========== 测试 7: 流式接口 ==========
async def test_streaming():
    section("测试 7: 流式对话接口")

    from app.agents.graph import stream_chat

    thread_id = "test_streaming"
    chunks = []

    async for chunk in stream_chat(
        "你好，介绍一下你自己", [], thread_id, 1
    ):
        chunks.append(chunk)

    total = "".join(chunks)
    check("流式输出有内容", len(total) > 0, f"共 {len(chunks)} 个 chunk，{len(total)} 字符")
    check("流式回复包含自我介绍", "你好" in total or "烹饪" in total or "助手" in total,
          f"前 100 字: {total[:100]}...")

    delete_session(thread_id, user_id=1)


# ========== 主入口 ==========
async def main():
    global passed, failed
    passed = 0
    failed = 0

    print(f"\n{CYAN}{'#'*60}{RESET}")
    print(f"{CYAN}#  智膳 AI — StateGraph 功能测试{RESET}")
    print(f"{CYAN}{'#'*60}{RESET}")

    try:
        test_graph_structure()
        test_intent_routing()
        test_subgraph_execution()
        test_rag_tool()
        test_multi_turn()
        test_session_management()
        await test_streaming()
    except Exception as e:
        print(f"\n{RED}{'!'*60}{RESET}")
        print(f"{RED}  测试过程中发生异常: {e}{RESET}")
        import traceback
        traceback.print_exc()
        print(f"{RED}{'!'*60}{RESET}")

    print(f"\n{YELLOW}{'='*60}{RESET}")
    print(f"{YELLOW}  测试结果汇总{RESET}")
    print(f"{YELLOW}{'='*60}{RESET}")
    print(f"  {GREEN}通过: {passed}{RESET}")
    print(f"  {RED}失败: {failed}{RESET}")
    print(f"  总计: {passed + failed}")

    if failed == 0:
        print(f"\n  {GREEN}{'🎉'*3}  所有测试通过！ StateGraph 功能完好！{'🎉'*3}{RESET}")
    else:
        print(f"\n  {RED}⚠️  有 {failed} 项测试失败，请检查日志{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
