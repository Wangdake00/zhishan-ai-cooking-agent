"""
智膳 AI — StateGraph 快速功能验证（无需 LLM 调用）
================================================
运行方式: python test_quick.py

这个脚本快速验证 StateGraph 的架构完整性，
不需要调用 LLM API，秒级完成。
"""

import asyncio
import os
import sys
from pathlib import Path

# 允许直接 `python tests/test_quick.py` 运行，将项目根目录加入导入路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _run(coro):
    """Windows 上 psycopg async（AsyncPostgresSaver）需要 SelectorEventLoop"""
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)


def _reset_singletons():
    """每次 asyncio.run 都是新事件循环，需重置 graph 单例让连接绑定当前循环"""
    import app.agents.graph as g
    g._checkpointer = None
    g._checkpointer_cm = None
    g._compiled_graph = None

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

passed = 0
failed = 0
failed_checks: list[str] = []


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  {GREEN}✓{RESET} {name}  {CYAN}{detail}{RESET}")
    else:
        failed += 1
        item = f"{name} {detail}".strip()
        failed_checks.append(item)
        print(f"  {RED}✗{RESET} {name}  {CYAN}{detail}{RESET}")
        # CI 中把失败项写成注解，日志被截断时也能在 Run 页面看到具体原因
        if os.getenv("GITHUB_ACTIONS") == "true":
            print(f"::error title=冒烟测试失败::{item}", flush=True)


def section(title: str):
    print(f"\n{YELLOW}{'='*60}{RESET}")
    print(f"{YELLOW}  {title}{RESET}")
    print(f"{YELLOW}{'='*60}{RESET}")


def test_imports():
    section("1. 模块导入测试")
    try:
        from app.agents.graph import (
            get_graph, _get_checkpointer,
            detect_intent, list_sessions,
            get_messages, delete_session,
            stream_chat, AgentState,
        )
        check("graph.py 核心模块导入", True)
    except Exception as e:
        check("graph.py 核心模块导入", False, str(e))
        return False

    try:
        from app.agents.personal_chief import (
            search_recipes, get_messages,
            list_sessions, delete_session, clear_messages,
        )
        check("personal_chief.py 委托层导入", True)
    except Exception as e:
        check("personal_chief.py 委托层导入", False, str(e))

    try:
        from app.agents.utils import ensure_image_base64_data_url
        check("utils.py 工具函数导入", True)
    except Exception as e:
        check("utils.py 工具函数导入", False, str(e))

    try:
        from app.api.v1.chat import router
        check("chat.py API 路由导入", True)
    except Exception as e:
        check("chat.py API 路由导入", False, str(e))

    return True


def test_state_graph_compile():
    section("2. StateGraph 编译测试")
    from app.agents.graph import get_graph

    _reset_singletons()
    g = _run(get_graph())
    check("StateGraph 编译成功", g is not None)

    # 获取图信息
    graph_info = g.get_graph()
    nodes = list(graph_info.nodes.keys())
    check("节点数量", len(nodes) >= 5, f"共 {len(nodes)} 个: {nodes}")

    expected_nodes = ["detect_intent", "nutrition_node", "recipe_node", "general_node", "__end__"]
    for node in expected_nodes:
        check(f"  包含节点: {node}", node in nodes)

    edges = graph_info.get_edge_links() if hasattr(graph_info, 'get_edge_links') else []
    check("边数量", len(edges) >= 4 if edges else True, f"共 {len(edges)} 条边")


def test_state_definition():
    section("3. AgentState 状态定义测试")
    from app.agents.graph import AgentState

    fields = list(AgentState.__annotations__.keys())
    check("状态字段数", len(fields) >= 5, f"共 {len(fields)} 个字段")

    expected = ["messages", "intent", "user_id", "thread_id", "user_prompt", "image_urls"]
    for field in expected:
        check(f"  包含字段: {field}", field in fields)

    check("intent 类型正确", "intent" in fields)


def test_intent_detection():
    section("4. 意图检测逻辑测试")
    from app.agents.graph import detect_intent
    from langchain_core.messages import HumanMessage

    # 营养意图
    r = detect_intent({"messages": [HumanMessage(content="多少卡路里")]})
    check("营养关键词: 卡路里", r["intent"] == "nutrition")

    r = detect_intent({"messages": [HumanMessage(content="分析一下营养成分")]})
    check("营养关键词: 营养", r["intent"] == "nutrition")

    # 同时命中营养与菜谱关键词 → 复合意图走 general（已确认的路由行为）
    r = detect_intent({"messages": [HumanMessage(content="减脂餐怎么做")]})
    check("双命中: 减脂+怎么做 → general", r["intent"] == "general")

    # 菜谱意图
    r = detect_intent({"messages": [HumanMessage(content="推荐菜谱")]})
    check("菜谱关键词: 菜谱", r["intent"] == "recipe")

    r = detect_intent({"messages": [HumanMessage(content="能做什么菜")]})
    check("菜谱关键词: 能做", r["intent"] == "recipe")

    r = detect_intent({"messages": [HumanMessage(content="怎么做红烧肉")]})
    check("菜谱关键词: 怎么做", r["intent"] == "recipe")

    # 通用意图
    r = detect_intent({"messages": [HumanMessage(content="你好")]})
    check("通用关键词: 你好", r["intent"] == "general")

    r = detect_intent({"messages": [HumanMessage(content="今天天气如何")]})
    check("通用关键词: 天气", r["intent"] == "general")

    # 空输入兜底
    r = detect_intent({"messages": [], "user_prompt": ""})
    check("空输入兜底", r["intent"] == "general")

    # 边界情况
    r = detect_intent({"messages": []})
    check("无消息列表", r["intent"] == "general")


def test_checkpointer():
    section("5. Checkpointer 持久化测试")
    from app.agents.graph import _get_checkpointer

    _reset_singletons()
    cp = _run(_get_checkpointer())
    check("AsyncPostgresSaver 初始化", cp is not None)
    check("类型正确", "AsyncPostgresSaver" in type(cp).__name__)

    # 验证方法存在
    check("有 aget 方法", hasattr(cp, 'aget'))
    check("有 aput 方法", hasattr(cp, 'aput'))
    check("有 adelete_thread 方法", hasattr(cp, 'adelete_thread'))
    check("有 alist 方法", hasattr(cp, 'alist'))


def test_tools_definition():
    section("6. 工具定义测试")
    from app.agents.graph import calculate_nutrition, generate_shopping_list, search_recipes_rag

    check("calculate_nutrition 存在", calculate_nutrition is not None)
    check("generate_shopping_list 存在", generate_shopping_list is not None)
    check("search_recipes_rag 存在", search_recipes_rag is not None)

    # 检查工具名
    check("营养工具名正确", calculate_nutrition.name == "calculate_nutrition")
    check("购物清单工具名正确", generate_shopping_list.name == "generate_shopping_list")
    check("RAG 工具名正确", search_recipes_rag.name == "search_recipes_rag")

    # 测试工具描述
    check("营养工具有描述", "营养" in calculate_nutrition.description or "卡路里" in calculate_nutrition.description)
    check("购物清单有描述", "购物" in generate_shopping_list.description or "清单" in generate_shopping_list.description)


def test_system_prompts():
    section("7. System Prompts 测试")
    from app.agents.graph import SYSTEM_PROMPTS

    check("Prompts 数量", len(SYSTEM_PROMPTS) == 3, f"共 {len(SYSTEM_PROMPTS)} 个")

    expected_keys = ["nutrition", "recipe", "general"]
    for key in expected_keys:
        check(f"  包含 {key} prompt", key in SYSTEM_PROMPTS)

    # 检查内容关键词
    check("nutrition prompt 含 '营养'", "营养" in SYSTEM_PROMPTS["nutrition"])
    check("recipe prompt 含 '菜谱'", "菜谱" in SYSTEM_PROMPTS["recipe"] or "烹饪" in SYSTEM_PROMPTS["recipe"])
    check("general prompt 含 '全能'", "全能" in SYSTEM_PROMPTS["general"])


def test_session_functions():
    section("8. 会话管理函数测试")
    from app.agents.graph import list_sessions, get_messages, delete_session

    _reset_singletons()

    async def _t():
        # 空用户查询
        sessions = await list_sessions(user_id=-999)
        check("查询空会话", isinstance(sessions, list))

        # 空线程查询
        msgs = await get_messages("nonexistent_thread_xyz")
        check("查询空消息", msgs == [])

        # 删除空会话（不应报错）
        try:
            await delete_session("nonexistent_thread_xyz", user_id=-999)
            check("删除空会话不报错", True)
        except Exception:
            check("删除空会话不报错", False)

    _run(_t())


def test_delegation():
    section("9. 委托层一致性测试")
    from app.agents.personal_chief import (
        search_recipes, get_messages as pm_get,
        list_sessions as pm_list, delete_session as pm_del,
        clear_messages as pm_clear,
    )
    from app.agents.graph import (
        get_messages as g_get, list_sessions as g_list,
        delete_session as g_del,
    )

    # 检查是否指向同一个函数
    check("get_messages 委托一致", pm_get == g_get)
    check("list_sessions 委托一致", pm_list == g_list)
    check("delete_session 委托一致", pm_del == g_del)


def main():
    global passed, failed
    passed = 0
    failed = 0
    failed_checks.clear()

    print(f"\n{CYAN}{'#'*60}{RESET}")
    print(f"{CYAN}#  智膳 AI — StateGraph 快速验证（无 LLM 调用）{RESET}")
    print(f"{CYAN}{'#'*60}{RESET}")

    if not test_imports():
        print(f"\n{RED}✗ 导入失败，后续测试跳过{RESET}")
        if os.getenv("GITHUB_ACTIONS") == "true":
            print("::error title=冒烟测试失败::模块导入失败，详见上方输出", flush=True)
        sys.exit(1)

    test_state_graph_compile()
    test_state_definition()
    test_intent_detection()
    test_checkpointer()
    test_tools_definition()
    test_system_prompts()
    test_session_functions()
    test_delegation()

    print(f"\n{YELLOW}{'='*60}{RESET}")
    print(f"{YELLOW}  测试结果汇总{RESET}")
    print(f"{YELLOW}{'='*60}{RESET}")
    print(f"  {GREEN}通过: {passed}{RESET}")
    print(f"  {RED}失败: {failed}{RESET}")
    print(f"  总计: {passed + failed}")

    if failed == 0:
        print(f"\n  {GREEN}{'🎉'*3}  架构验证全部通过！{'🎉'*3}{RESET}")
        print(f"\n  💡 下一步:")
        print(f"     1. 运行 test_stategraph.py (需 LLM API, 耗时较长)")
        print(f"     2. 启动后端 python -m app.main, 访问 http://localhost:3000")
        print(f"     3. 运行 test_api.py 进行 API 集成测试")
    else:
        print(f"\n  {RED}⚠️  有 {failed} 项测试失败{RESET}")
        for item in failed_checks:
            print(f"    - {item}")
        # GitHub Actions 日志可能被截断，同时写入步骤摘要
        summary_path = os.getenv("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## 冒烟测试失败项\n\n")
                f.writelines(f"- {item}\n" for item in failed_checks)
        sys.exit(1)


if __name__ == "__main__":
    main()
