"""
StateGraph 结构图可视化
运行方式: python test_graph_viz.py
输出: graph_mermaid.mmd (Mermaid 格式) + graph_structure.txt (文本结构图)
"""

from app.agents.graph import get_graph


def generate_mermaid():
    """生成 Mermaid 流程图代码"""
    g = get_graph()
    graph_data = g.get_graph()

    lines = []
    lines.append("flowchart TD")
    lines.append("    Start([开始]) --> detect_intent[/detect_intent<br/>意图检测/]")
    lines.append("")
    lines.append("    detect_intent -->|nutrition| nutrition_node[nutrition_node<br/>营养分析]")
    lines.append("    detect_intent -->|recipe| recipe_node[recipe_node<br/>菜谱推荐]")
    lines.append("    detect_intent -->|general| general_node[general_node<br/>通用对话]")
    lines.append("")
    lines.append("    nutrition_node --> End([结束])")
    lines.append("    recipe_node --> End")
    lines.append("    general_node --> End")

    return "\n".join(lines)


def generate_text_diagram():
    """生成文本结构图"""
    return """
╔══════════════════════════════════════════════════════════════╗
║              智膳 AI — LangGraph StateGraph                 ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║   ┌─────────┐                                                ║
║   │  Start  │                                                ║
║   └────┬─────┘                                                ║
║        │                                                     ║
║        ▼                                                     ║
║   ┌─────────────────────────────────────┐                   ║
║   │       detect_intent (意图检测)        │                   ║
║   │   ┌─────────────────────────────┐   │                   ║
║   │   │ • 关键词匹配                   │   │                   ║
║   │   │ • nutrition / recipe / general│   │                   ║
║   │   └─────────────────────────────┘   │                   ║
║   └──────┬────────────┬────────────┬────┘                   ║
║          │            │            │                         ║
║   ┌──────┴────┐  ┌────┴─────┐  ┌───┴────────┐              ║
║   │营养分析节点│  │菜谱推荐节点│  │ 通用对话节点 │              ║
║   │           │  │          │  │            │              ║
║   │ 工具集:   │  │ 工具集:   │  │ 工具集:    │              ║
║   │ • 营养计算│  │ • RAG检索 │  │ • RAG检索  │              ║
║   │ • 购物清单│  │ • Tavily  │  │ • Tavily   │              ║
║   │ • RAG检索 │  │ • 购物清单│  │ • 营养计算 │              ║
║   │ • 收藏菜谱│  │ • 收藏菜谱│  │ • 购物清单 │              ║
║   │           │  │          │  │ • 收藏菜谱 │              ║
║   └─────┬─────┘  └────┬─────┘  └──────┬─────┘              ║
║         │             │               │                     ║
║         └─────────────┼───────────────┘                     ║
║                       │                                      ║
║                       ▼                                      ║
║                 ┌──────────┐                                ║
║                 │   End    │                                ║
║                 └──────────┘                                ║
║                                                              ║
║   ┌──────────────────────────────────────────────────────┐  ║
║   │  持久层: SqliteSaver                                 │  ║
║   │  • 每个 thread_id 独立保存对话历史                     │  ║
║   │  • 支持多轮对话恢复                                   │  ║
║   │  • 支持历史消息查询                                   │  ║
║   └──────────────────────────────────────────────────────┘  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""


def show_node_details():
    """展示每个节点的详细信息"""
    details = {
        "detect_intent": {
            "description": "意图检测节点",
            "tools": [],
            "prompt": "根据用户输入关键词判断意图",
            "output": "intent: nutrition | recipe | general",
        },
        "nutrition_node": {
            "description": "营养分析子图",
            "tools": ["calculate_nutrition", "generate_shopping_list", "search_recipes_rag", "save_favorite_recipe"],
            "prompt": "营养分析师人设，分析食材营养成分和热量",
            "output": "营养成分分析报告（卡路里/蛋白质/脂肪/碳水/纤维）",
        },
        "recipe_node": {
            "description": "菜谱推荐子图",
            "tools": ["search_recipes_rag", "tavily", "generate_shopping_list", "save_favorite_recipe"],
            "prompt": "智能烹饪助手人设，优先 RAG 检索本地菜谱",
            "output": "菜谱推荐列表（食材匹配度+难度+营养评分）",
        },
        "general_node": {
            "description": "通用对话子图",
            "tools": ["search_recipes_rag", "tavily", "calculate_nutrition", "generate_shopping_list", "save_favorite_recipe"],
            "prompt": "全能智能烹饪助手人设，灵活调用所有工具",
            "output": "根据对话上下文动态生成回复",
        },
    }

    for name, info in details.items():
        print(f"\n  📦 {name} — {info['description']}")
        print(f"     工具: {', '.join(info['tools']) if info['tools'] else '无（仅路由）'}")
        print(f"     角色: {info['prompt']}")
        print(f"     输出: {info['output']}")


if __name__ == "__main__":
    import os

    print("\n" + "=" * 60)
    print("  智膳 AI — StateGraph 结构图")
    print("=" * 60)

    # 文本图
    text_diagram = generate_text_diagram()
    print(text_diagram)

    # 节点详情
    print("\n" + "=" * 60)
    print("  节点详情")
    print("=" * 60)
    show_node_details()

    # Mermaid
    mermaid = generate_mermaid()
    print("\n" + "=" * 60)
    print("  Mermaid 流程图代码 (粘贴到 Mermaid Live Editor 可视化)")
    print("=" * 60)
    print(mermaid)

    # 保存文件
    base = os.path.dirname(os.path.abspath(__file__))
    mmd_path = os.path.join(base, "graph_mermaid.mmd")
    txt_path = os.path.join(base, "graph_structure.txt")

    with open(mmd_path, "w", encoding="utf-8") as f:
        f.write("```mermaid\n" + mermaid + "\n```")
    print(f"\n  📄 Mermaid 文件已保存: {mmd_path}")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(generate_text_diagram())
    print(f"  📄 文本结构图已保存: {txt_path}")

    print("\n  💡 可视化方法:")
    print("     1. 访问 https://mermaid.live/")
    print("     2. 将 graph_mermaid.mmd 内容粘贴到代码区")
    print("     3. 即可看到交互式 StateGraph 流程图")
