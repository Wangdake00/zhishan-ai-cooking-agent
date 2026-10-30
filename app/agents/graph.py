"""
智膳 AI — LangGraph StateGraph 实现

设计说明：
- 显式 StateGraph 替代隐式 create_agent，意图路由、工具编排可观测
- 三路子图：NutritionFlow / RecipeFlow / GeneralFlow
- 统一 AgentState 管理对话、意图、工具结果等结构化数据
- 保留 AsyncPostgresSaver 持久化能力，支持多轮对话恢复

图结构：
  Start → detect_intent → [条件边] → nutrition_node / recipe_node / general_node → End
"""

from typing import TypedDict, Optional, Literal, Annotated
import asyncio
import operator
import json
import sys

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    HumanMessage, AIMessage, AIMessageChunk, SystemMessage, ToolMessage
)
from langchain_core.tools import tool
from langchain_tavily import TavilySearch

import traceback

from app.common.logger import logger
from app.core.config import settings
from app.core.exceptions import RAGException
from app.db import session_store, favorite_store
from app.rag.pipeline import rag_search
from app.agents.utils import ensure_image_base64_data_url
from app.agents.context import build_llm_messages
from app.common import oss
from app.agents.grounding import (
    extract_nutrition_facts,
    extract_recipe_facts,
    verify_nutrition,
    verify_recipes,
    render_nutrition_card,
    render_recipe_card,
)


# ========== 状态定义 ==========

class AgentState(TypedDict, total=False):
    """
    Agent 全局状态，跨节点共享

    关键设计：
    - messages 使用 Annotated + operator.add 作为 reducer，
      这样新消息会追加到历史消息列表，而不是覆盖
    - 其他字段（intent, user_id 等）由新值覆盖旧值
    """
    messages: Annotated[list, operator.add]
    intent: Literal["nutrition", "recipe", "general"]
    user_id: int
    thread_id: str
    user_prompt: str
    image_urls: list
    # 防幻觉接地事实（由确定性工具产出，覆盖语义；流末渲染权威数据卡 + 一致性校验）
    nutrition_facts: Optional[dict]
    recipe_facts: Optional[dict]


# ========== 工具定义 ==========

@tool
def calculate_nutrition(ingredients: str, servings: Optional[int] = 1) -> str:
    """
    计算食材的营养成分（卡路里、蛋白质、脂肪、碳水化合物等）。

    Args:
        ingredients: 食材清单字符串，格式如"鸡胸肉200g, 西兰花150g, 大米100g"
        servings: 份数，默认为1人份

    Returns:
        结构化的营养分析报告
    """
    logger.info(f"[工具调用] calculate_nutrition: ingredients={ingredients}, servings={servings}")

    nutrition_db = {
        "鸡胸肉": {"calories": 165, "protein": 31, "fat": 3.6, "carbs": 0, "fiber": 0},
        "鸡腿肉": {"calories": 184, "protein": 20, "fat": 11, "carbs": 0, "fiber": 0},
        "猪肉": {"calories": 242, "protein": 27, "fat": 14, "carbs": 0, "fiber": 0},
        "牛肉": {"calories": 250, "protein": 26, "fat": 15, "carbs": 0, "fiber": 0},
        "鸡蛋": {"calories": 155, "protein": 13, "fat": 11, "carbs": 1.1, "fiber": 0},
        "豆腐": {"calories": 76, "protein": 8, "fat": 4.8, "carbs": 1.9, "fiber": 0.3},
        "大米": {"calories": 365, "protein": 7, "fat": 0.6, "carbs": 79, "fiber": 1.3},
        "面条": {"calories": 270, "protein": 9, "fat": 1.5, "carbs": 56, "fiber": 2.5},
        "西兰花": {"calories": 34, "protein": 2.8, "fat": 0.4, "carbs": 7, "fiber": 2.6},
        "胡萝卜": {"calories": 41, "protein": 0.9, "fat": 0.2, "carbs": 10, "fiber": 2.8},
        "土豆": {"calories": 77, "protein": 2, "fat": 0.1, "carbs": 17, "fiber": 2.2},
        "番茄": {"calories": 18, "protein": 0.9, "fat": 0.2, "carbs": 3.9, "fiber": 1.2},
        "黄瓜": {"calories": 15, "protein": 0.8, "fat": 0.1, "carbs": 3.6, "fiber": 0.5},
        "青菜": {"calories": 15, "protein": 1.5, "fat": 0.2, "carbs": 2.7, "fiber": 1.6},
        "菠菜": {"calories": 23, "protein": 2.9, "fat": 0.4, "carbs": 3.6, "fiber": 2.2},
        "白菜": {"calories": 12, "protein": 1.5, "fat": 0.1, "carbs": 2.2, "fiber": 1},
        "洋葱": {"calories": 40, "protein": 1.1, "fat": 0.1, "carbs": 9.3, "fiber": 1.7},
        "大蒜": {"calories": 149, "protein": 6.4, "fat": 0.5, "carbs": 33, "fiber": 2.1},
        "辣椒": {"calories": 40, "protein": 1.9, "fat": 0.3, "carbs": 8.8, "fiber": 1.5},
        "青椒": {"calories": 20, "protein": 0.9, "fat": 0.2, "carbs": 4.6, "fiber": 1.7},
        "茄子": {"calories": 25, "protein": 1, "fat": 0.2, "carbs": 5.7, "fiber": 3},
        "玉米": {"calories": 86, "protein": 3.2, "fat": 1.2, "carbs": 19, "fiber": 2.7},
        "蘑菇": {"calories": 22, "protein": 3.1, "fat": 0.3, "carbs": 3.3, "fiber": 1},
        "虾": {"calories": 99, "protein": 24, "fat": 0.3, "carbs": 0.2, "fiber": 0},
        "鱼": {"calories": 165, "protein": 22, "fat": 8, "carbs": 0, "fiber": 0},
        "三文鱼": {"calories": 208, "protein": 20, "fat": 13, "carbs": 0, "fiber": 0},
        "牛奶": {"calories": 42, "protein": 3.4, "fat": 1, "carbs": 5, "fiber": 0},
        "酸奶": {"calories": 59, "protein": 3.5, "fat": 3.3, "carbs": 3.6, "fiber": 0},
        "芝士": {"calories": 402, "protein": 25, "fat": 33, "carbs": 3.5, "fiber": 0},
        "苹果": {"calories": 52, "protein": 0.3, "fat": 0.2, "carbs": 14, "fiber": 2.4},
        "香蕉": {"calories": 89, "protein": 1.1, "fat": 0.3, "carbs": 23, "fiber": 2.6},
        "橄榄油": {"calories": 884, "protein": 0, "fat": 100, "carbs": 0, "fiber": 0},
        "食用油": {"calories": 884, "protein": 0, "fat": 100, "carbs": 0, "fiber": 0},
        "盐": {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0},
        "酱油": {"calories": 53, "protein": 8, "fat": 0.1, "carbs": 4.9, "fiber": 0},
        "糖": {"calories": 387, "protein": 0, "fat": 0, "carbs": 100, "fiber": 0},
    }

    ingredient_list = [x.strip() for x in ingredients.split(",")]
    parsed_ingredients = []

    for item in ingredient_list:
        name = item
        weight = 100
        import re
        match = re.search(r'(\d+)\s*g', item)
        if match:
            weight = int(match.group(1))
            name = re.sub(r'\d+\s*g', '', item).strip()
        else:
            match = re.search(r'(\d+)\s*克', item)
            if match:
                weight = int(match.group(1))
                name = re.sub(r'\d+\s*克', '', item).strip()
        parsed_ingredients.append({"name": name, "weight": weight})

    results = []
    total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0}

    for ing in parsed_ingredients:
        matched_key = None
        for key in nutrition_db:
            if key in ing["name"] or ing["name"] in key:
                matched_key = key
                break
        if matched_key:
            factor = ing["weight"] / 100.0 * servings
            nutri = nutrition_db[matched_key]
            item_result = {
                "name": ing["name"],
                "weight": ing["weight"],
                "calories": round(nutri["calories"] * factor, 1),
                "protein": round(nutri["protein"] * factor, 1),
                "fat": round(nutri["fat"] * factor, 1),
                "carbs": round(nutri["carbs"] * factor, 1),
                "fiber": round(nutri["fiber"] * factor, 1),
            }
            results.append(item_result)
            for k in total:
                total[k] += item_result[k]
        else:
            results.append({"name": ing["name"], "weight": ing["weight"], "note": "未找到该食材的营养数据"})

    output = []
    output.append(f"## 营养成分分析报告（{servings}人份）\n")
    output.append("### 各食材营养明细：\n")
    output.append("| 食材 | 重量(g) | 卡路里(kcal) | 蛋白质(g) | 脂肪(g) | 碳水(g) | 纤维(g) |")
    output.append("|------|---------|--------------|-----------|---------|---------|---------|")
    for r in results:
        if "note" in r:
            output.append(f"| {r['name']} | {r['weight']} | - | - | - | - | - | *{r['note']}* |")
        else:
            output.append(f"| {r['name']} | {r['weight']} | {r['calories']} | {r['protein']} | {r['fat']} | {r['carbs']} | {r['fiber']} |")

    output.append("\n### 营养汇总：\n")
    output.append(f"- **总卡路里**：{round(total['calories'], 1)} kcal")
    output.append(f"- **蛋白质**：{round(total['protein'], 1)} g")
    output.append(f"- **脂肪**：{round(total['fat'], 1)} g")
    output.append(f"- **碳水化合物**：{round(total['carbs'], 1)} g")
    output.append(f"- **膳食纤维**：{round(total['fiber'], 1)} g\n")

    output.append("### 营养建议：\n")
    calories_per_meal = total["calories"]
    if calories_per_meal < 300:
        output.append("- ⚠️ 本餐热量偏低，建议适当增加主食或蛋白质摄入")
    elif calories_per_meal > 800:
        output.append("- ⚠️ 本餐热量偏高，注意控制分量或增加运动量")
    else:
        output.append("- ✅ 本餐热量适中，适合正常成年人一餐")
    if total["protein"] < 15:
        output.append("- ⚠️ 蛋白质摄入不足，建议增加肉类、蛋类或豆制品")
    elif total["protein"] > 60:
        output.append("- ℹ️ 蛋白质摄入较高，肾功能不佳者请注意")
    else:
        output.append("- ✅ 蛋白质含量充足")

    return "\n".join(output)


@tool
def generate_shopping_list(ingredients: str, servings: Optional[int] = 1) -> str:
    """
    根据食谱或食材清单生成购物清单，自动分类整理并估算采购量。

    Args:
        ingredients: 食谱所需食材，格式如"鸡胸肉300g, 西兰花200g, 大蒜3瓣"
        servings: 需要做几份，默认为1人份

    Returns:
        分类整理好的购物清单
    """
    logger.info(f"[工具调用] generate_shopping_list: ingredients={ingredients}, servings={servings}")

    COMMON_PORTION_LIMITS = [
        ("鸡蛋", (1.5, 3.0)), ("鸭蛋", (1.0, 2.0)), ("蛋", (1.5, 3.0)),
        ("鸡胸肉", (80.0, 180.0)), ("鸡腿", (80.0, 180.0)), ("猪肉", (80.0, 180.0)),
        ("牛肉", (80.0, 180.0)), ("羊肉", (80.0, 180.0)), ("五花肉", (60.0, 150.0)),
        ("培根", (30.0, 80.0)), ("排骨", (100.0, 200.0)), ("虾", (80.0, 180.0)),
        ("三文鱼", (80.0, 180.0)), ("鱼", (100.0, 200.0)),
        ("番茄", (0.8, 1.5)), ("西红柿", (0.8, 1.5)), ("土豆", (0.8, 1.5)),
        ("胡萝卜", (0.5, 1.2)), ("黄瓜", (0.5, 1.0)), ("茄子", (0.5, 1.0)),
        ("洋葱", (0.4, 1.0)), ("青椒", (0.3, 1.0)), ("辣椒", (0.1, 2.0)),
        ("玉米", (0.5, 1.2)), ("苹果", (1.0, 1.5)), ("香蕉", (1.0, 2.0)),
        ("橙子", (1.0, 1.5)), ("橘子", (1.0, 2.0)), ("梨", (1.0, 1.5)),
        ("西兰花", (80.0, 200.0)), ("青菜", (80.0, 200.0)), ("菠菜", (50.0, 150.0)),
        ("白菜", (80.0, 200.0)), ("生菜", (50.0, 150.0)), ("蘑菇", (40.0, 120.0)),
        ("香菇", (30.0, 100.0)), ("豆腐", (80.0, 200.0)), ("豆干", (30.0, 80.0)),
        ("牛奶", (200.0, 300.0)), ("酸奶", (150.0, 250.0)),
        ("大米", (80.0, 150.0)), ("米饭", (150.0, 250.0)), ("面条", (80.0, 150.0)),
        ("面粉", (60.0, 150.0)), ("大蒜", (1.0, 3.0)), ("蒜", (1.0, 3.0)),
        ("葱", (1.0, 2.0)), ("姜", (0.2, 1.0)), ("香菜", (3.0, 10.0)),
        ("鸡蛋液", (80.0, 150.0)),
    ]

    def find_limit(name):
        for kw, limits in COMMON_PORTION_LIMITS:
            if kw in name:
                return limits
        return None

    categories = {
        "🥩 肉类": ["鸡胸肉", "鸡腿", "猪肉", "牛肉", "羊肉", "排骨", "五花肉", "培根", "香肠", "火腿", "肉"],
        "🦐 海鲜水产": ["虾", "鱼", "三文鱼", "鱿鱼", "蟹", "扇贝", "蛤蜊", "牡蛎", "海带", "紫菜", "海鲜"],
        "🥚 蛋奶豆制品": ["鸡蛋", "鸭蛋", "牛奶", "酸奶", "芝士", "奶酪", "豆腐", "豆浆", "豆皮", "腐竹", "豆干", "蛋", "奶", "豆"],
        "🥬 蔬菜": ["西兰花", "胡萝卜", "土豆", "番茄", "西红柿", "黄瓜", "青菜", "菠菜", "白菜", "生菜", "洋葱", "大蒜", "蒜", "辣椒", "青椒", "茄子", "玉米", "蘑菇", "香菇", "金针菇", "芹菜", "韭菜", "莲藕", "山药", "冬瓜", "南瓜", "苦瓜", "丝瓜", "芦笋", "葱", "姜", "香菜", "菜"],
        "🍎 水果": ["苹果", "香蕉", "橙子", "橘子", "梨", "葡萄", "草莓", "西瓜", "芒果", "柠檬", "果"],
        "🍚 主食米面": ["大米", "米饭", "面条", "面", "面粉", "馒头", "面包", "饺子皮", "馄饨皮", "年糕", "粉丝", "粉条"],
        "🧂 调味调料": ["盐", "糖", "生抽", "老抽", "酱油", "醋", "料酒", "蚝油", "豆瓣酱", "辣椒酱", "番茄酱", "花椒", "八角", "桂皮", "香叶", "胡椒", "孜然", "味精", "鸡精", "橄榄油", "食用油", "油", "香油", "芝麻", "淀粉", "生粉", "小苏打", "泡打粉", "味醂", "味增"],
        "🫙 干货罐头": ["枸杞", "红枣", "木耳", "银耳", "莲子", "百合", "核桃", "杏仁", "花生", "腰果", "葡萄干", "罐头"],
    }

    def categorize(name):
        for cat, keywords in categories.items():
            for kw in keywords:
                if kw in name:
                    return cat
        return "📦 其他"

    import re
    ingredient_list = [x.strip() for x in ingredients.split(",") if x.strip()]
    parsed = []
    adjusted = []

    for item in ingredient_list:
        name = item
        amount = "适量"
        unit = ""
        limited_value = None

        match = re.search(r'(\d+\.?\d*)\s*(g|克|kg|千克|斤|两)', item)
        if match:
            value = float(match.group(1))
            raw_unit = match.group(2)
            value_grams = value
            if raw_unit in ("kg", "千克"):
                value_grams = value * 1000.0
            elif raw_unit in ("斤",):
                value_grams = value * 500.0
            elif raw_unit in ("两",):
                value_grams = value * 50.0

            clean_name = re.sub(r'\d+\.?\d*\s*(g|克|kg|千克|斤|两)', '', item).strip()
            limits = find_limit(clean_name)
            if limits is not None and value_grams > 0:
                min_per, max_per = limits
                per_serving_g = value_grams
                if per_serving_g < min_per:
                    per_serving_g = min_per
                    limited_value = per_serving_g
                elif per_serving_g > max_per:
                    per_serving_g = max_per
                    limited_value = per_serving_g

                new_total_g = per_serving_g * servings
                if raw_unit in ("kg", "千克"):
                    amount = f"{round(new_total_g / 1000.0, 2)} 千克"
                elif raw_unit in ("斤",):
                    amount = f"{round(new_total_g / 500.0, 2)} 斤"
                elif raw_unit in ("两",):
                    amount = f"{round(new_total_g / 50.0, 2)} 两"
                else:
                    amount = f"{round(new_total_g, 1)} 克"
                if limited_value is not None:
                    adjusted.append((clean_name or "未命名", value_grams, limited_value, "克/每份"))
            else:
                if raw_unit in ("kg", "千克"):
                    amount = f"{round(value * servings, 2)} 千克"
                elif raw_unit in ("斤",):
                    amount = f"{round(value * servings, 2)} 斤"
                elif raw_unit in ("两",):
                    amount = f"{round(value * servings, 2)} 两"
                else:
                    amount = f"{round(value * servings, 1)} 克"
        else:
            match = re.search(r'(\d+\.?\d*)\s*(个|只|片|勺|汤匙|茶匙|瓣|根|颗|把|束|包|袋|杯|碗|大匙|小匙)', item)
            if match:
                value = float(match.group(1))
                unit = match.group(2)
                clean_name = re.sub(r'\d+\.?\d*\s*(个|只|片|勺|汤匙|茶匙|瓣|根|颗|把|束|包|袋|杯|碗|大匙|小匙)', '', item).strip()
                limits = find_limit(clean_name)
                if limits is not None and value > 0:
                    min_per, max_per = limits
                    per_serving = value
                    if per_serving < min_per:
                        per_serving = min_per
                        limited_value = per_serving
                    elif per_serving > max_per:
                        per_serving = max_per
                        limited_value = per_serving
                    amount = f"{round(per_serving * servings, 1)} {unit}"
                    if limited_value is not None:
                        adjusted.append((clean_name or "未命名", value, limited_value, f"{unit}/每份"))
                else:
                    amount = f"{round(value * servings, 1)} {unit}"
            elif "少许" in item or "适量" in item:
                amount = "适量"
                name = item.replace("少许", "").replace("适量", "").strip()

        parsed.append({"name": name or "未命名", "amount": amount, "category": categorize(name or "未命名")})

    grouped = {}
    for p in parsed:
        cat = p["category"]
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(p)

    output = []
    output.append(f"## 🛒 购物清单（{servings}人份）\n")
    output.append("> ✂️ 可截图保存或勾选已购买项\n")
    for cat in categories.keys():
        if cat in grouped and grouped[cat]:
            output.append(f"\n### {cat}\n")
            for i, item in enumerate(grouped[cat], 1):
                output.append(f"- [ ] **{item['name']}**：{item['amount']}")
    if "📦 其他" in grouped and grouped["📦 其他"]:
        output.append(f"\n### 📦 其他\n")
        for i, item in enumerate(grouped["📦 其他"], 1):
            output.append(f"- [ ] **{item['name']}**：{item['amount']}")
    output.append("\n---\n")
    output.append("### 💡 采购小贴士：\n")
    output.append("1. 购买前先查看冰箱/厨房，避免重复采购\n2. 生鲜类建议当天采购，保证新鲜\n3. 调料类可根据家中库存适当减少\n4. 蔬菜建议多选当季时令")
    if adjusted:
        output.append("\n---\n")
        output.append("### 🧭 常识用量调整说明：\n")
        output.append("> 下列食材按「单人份合理范围」做了自动校正：\n")
        for n, original, limited, unit_desc in adjusted:
            action = "上调至" if limited > original else "下调至"
            output.append(f"- **{n}**：原计划 {round(original, 2)} {unit_desc} → {action} {round(limited, 2)} {unit_desc}")

    return "\n".join(output)


@tool
def search_recipes_rag(query: str, top_n: Optional[int] = 5) -> str:
    """
    从本地菜谱知识库中检索食谱（RAG 检索增强生成）。

    Args:
        query: 用户的搜索需求，支持口语化表达
        top_n: 返回的菜谱数量，默认5条

    Returns:
        检索到的菜谱列表
    """
    logger.info(f"[工具调用] search_recipes_rag: query={query}, top_n={top_n}")
    try:
        result = rag_search(query, top_k=10, top_n=top_n or 5)
        recipes = result["results"]
        if not recipes:
            return "本地知识库中没有找到相关菜谱，建议使用 web_search 搜索互联网。"

        lines = [f"🔍 RAG 检索完成，找到 {len(recipes)} 道相关菜谱：\n"]
        pipeline_info = " → ".join(result["pipeline_log"])
        if result["fallback_used"]:
            lines.append(f"⚠️ 检索链路使用了 Fallback 降级\n")
        lines.append(f"📋 检索管道: {pipeline_info}\n")
        for i, recipe in enumerate(recipes, 1):
            rerank_score = recipe.get("rerank_score", 0)
            lines.append(f"{'='*50}")
            lines.append(f"{i}. {recipe['name']}  (Rerank分数: {rerank_score})")
            lines.append(f"   菜系: {recipe['cuisine']} | 难度: {recipe['difficulty']} | 时间: {recipe['cooking_time']}")
            lines.append(f"   食材: {recipe['ingredients']}")
            lines.append(f"   标签: {recipe['tags']}")
            lines.append(f"   营养(每份): {recipe['nutrition']}")
            lines.append(f"   做法:\n{recipe['steps']}")
            lines.append(f"   简介: {recipe['description']}\n")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[RAG] search_recipes_rag 工具执行失败: {e}")
        raise RAGException(message=f"RAG 检索失败: {str(e)}", detail=str(e))


# ========== 动态工具（绑定用户 ID） ==========

def make_save_favorite_tool(user_id: int):
    """为当前用户创建一个绑定了 user_id 的收藏工具"""
    @tool
    async def save_favorite_recipe(recipe_title: str, recipe_content: str, ingredients: Optional[str] = None) -> str:
        """
        收藏一个食谱到用户的个人收藏夹。

        Args:
            recipe_title: 食谱名称/标题
            recipe_content: 食谱详细内容
            ingredients: 主要食材（可选）

        Returns:
            收藏结果提示信息
        """
        logger.info(f"[工具调用] save_favorite_recipe: title={recipe_title}, user_id={user_id}")
        try:
            favorite_id = await favorite_store.create_favorite(
                user_id=user_id,
                title=recipe_title,
                content=recipe_content,
                ingredients=ingredients or ""
            )
            return f"✅ 食谱「{recipe_title}」已成功收藏！（收藏ID: {favorite_id}）\n\n您可以随时在左侧收藏夹查看所有收藏的菜谱。"
        except Exception as e:
            logger.error(f"收藏食谱失败: {e}")
            return f"❌ 收藏失败：{str(e)}"
    return save_favorite_recipe


# ========== LLM 初始化 ==========

_model = init_chat_model(
    model=settings.llm_model,
    model_provider="openai",
    base_url=settings.dashscope_base_url,
    api_key=settings.dashscope_api_key
)

tavily = TavilySearch(
    max_results=5,
    topic="general",
    tavily_api_key=settings.tavily_api_key or "dummy-key-for-dev"
)


# ========== System Prompts ==========

SYSTEM_PROMPTS = {
    "nutrition": """你是一名营养分析师，专注于分析食材/菜品的营养成分和热量。请按以下流程：

1. 识别食材/菜品：若有照片，识别所有可见食材并估算用量
2. 营养计算：调用 calculate_nutrition 计算营养成分
3. 营养评估：根据计算结果给出健康建议

⚠️ 避免幻觉：调用 calculate_nutrition 前，将食材换算为"1人份合理用量"：
- 鸡蛋 1.5~3 个、西红柿 1 个、肉类 80~180g、叶菜 80~200g、主食 80~150g
- 调料按整道菜适量，不要按人份倍增

🔒 数据接地（必须严格遵守）：
- 必须先调用 calculate_nutrition，再给结论；禁止不调工具凭记忆报数值
- 回答中的总卡路里/蛋白质/脂肪/碳水/纤维必须与工具返回的"营养汇总"完全一致，不得自行重算或四舍五入到另一个数
- 工具表格中标注"未找到该食材的营养数据"的食材，必须明确告知用户"该食材暂无数据、未计入总计"，严禁编造其数值

请使用以下结构输出：
### 📊 营养成分分析报告
- 各食材营养明细表格
- 营养汇总（总卡路里/蛋白质/脂肪/碳水/纤维）
- 营养建议（是否均衡、健康提示）""",

    "recipe": """你是一名智能烹饪助手，专注于根据食材推荐菜谱。请按以下流程：

1. 识别食材：若有照片，识别所有可见食材
2. RAG 检索：优先调用 search_recipes_rag 检索本地 100 道经典菜谱
3. 补充搜索：RAG 结果不足时，再用 web_search
4. 排序推荐：从「食材匹配度 + 难度 + 营养」三维评分排序

⚠️ 避免幻觉：调用 generate_shopping_list 前，将食材换算为"1人份合理用量"
- 每次工具调用后，用中文友好总结结果

🔒 菜谱接地（必须严格遵守）：
- 必须先调用 search_recipes_rag 检索本地知识库
- 推荐的菜名、食材、做法只能来自检索结果，并在菜名后标注来源序号（如 [1][2]）；严禁编造知识库中不存在的菜名或把 A 菜的做法安到 B 菜上
- 若检索结果为空，必须明确告知"本地知识库暂未找到相关菜谱"，再用 web_search 联网搜索，不得虚构""",

    "general": """你是一名全能智能烹饪助手，具备强大的 Function Calling 和 RAG 检索增强生成能力。

你拥有以下 5 个工具，请根据用户需求灵活调用：
0. search_recipes_rag (RAG 本地知识库检索) ⭐ 优先使用
1. web_search (Tavily 搜索)
2. calculate_nutrition (营养计算)
3. generate_shopping_list (购物清单)
4. save_favorite_recipe (收藏食谱)

⚠️ 避免幻觉：调用 calculate_nutrition / generate_shopping_list 前，将食材换算为"1人份合理用量"
- 每次工具调用后，用中文友好总结

🔒 接地原则：营养数值必须来自 calculate_nutrition、菜谱必须来自 search_recipes_rag 的检索结果，不得凭记忆编造；检索/数据库没有的内容要明确说明"暂无数据/未找到"，不要虚构具体数字或菜名""",
}


# ========== 意图检测 ==========

def detect_intent(state: AgentState) -> dict:
    """意图检测节点：根据用户消息判断意图"""
    messages = state.get("messages", [])
    logger.debug(f"[detect_intent] 输入 messages 数量: {len(messages)}")
    for i, m in enumerate(messages):
        role = "👤" if isinstance(m, HumanMessage) else ("🤖" if isinstance(m, AIMessage) else ("🔧" if isinstance(m, ToolMessage) else "❓"))
        content_preview = (m.content[:80] if isinstance(m.content, str) else str(m.content)[:80]) if hasattr(m, 'content') else str(m)[:80]
        logger.debug(f"[detect_intent]   msg[{i}] {role} {type(m).__name__}: {content_preview}")

    if not messages:
        logger.info(f"[detect_intent] 无消息 → 兜底 general")
        return {"intent": "general"}

    last_msg = messages[-1]
    if hasattr(last_msg, "content"):
        user_input = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)
    else:
        user_input = str(last_msg)

    if not user_input.strip():
        user_input = state.get("user_prompt", "")

    nutrition_kw = ["卡路里", "热量", "营养", "多少卡", "蛋白质", "脂肪", "碳水",
                    "算一下", "计算", "分析营养", "热量多少", "营养成分", "健康吗", "减脂", "增肌"]
    recipe_kw = ["做什么菜", "菜谱", "推荐菜", "能做", "怎么做", "做什么", "食谱",
                 "烧什么", "炒什么", "炖什么", "做法", "烹饪", "搭配", "菜谱推荐"]

    nutrition_hit = next((kw for kw in nutrition_kw if kw in user_input), None)
    recipe_hit = next((kw for kw in recipe_kw if kw in user_input), None)

    # 双命中视为复合意图 → 通用路（全工具自主规划 + 双事实提取器后置校验），
    # 避免单标签路由把复合问题压扁、专路 prompt 人格偏向导致另一半回答敷衍
    if nutrition_hit and recipe_hit:
        logger.info(f"[detect_intent] → general (双命中复合意图: 营养[{nutrition_hit}] + 菜谱[{recipe_hit}])")
        return {"intent": "general"}
    if nutrition_hit:
        logger.info(f"[detect_intent] → nutrition (匹配关键词: {nutrition_hit})")
        return {"intent": "nutrition"}
    if recipe_hit:
        logger.info(f"[detect_intent] → recipe (匹配关键词: {recipe_hit})")
        return {"intent": "recipe"}

    logger.info(f"[detect_intent] → general (默认，未匹配营养/菜谱关键词)")
    return {"intent": "general"}


# ========== Agent 执行核心（带工具循环） ==========

async def _run_agent_with_tools(
    messages: list,
    tools: list,
    max_iterations: int = 5,
    required_tool: Optional[str] = None,
) -> tuple[list, list[dict]]:
    """
    执行带工具调用的 Agent 循环（异步）

    说明：
    - LLM 调用使用 ainvoke，不阻塞事件循环
    - 工具统一通过 ainvoke 调用：async 工具（如收藏）原生执行，
      同步工具（RAG/Tavily/计算类）自动转入线程池执行，避免冻结事件循环
      —— 这是修复"async-only 收藏工具在同步循环中必失败"的关键
    - 防幻觉门控：required_tool 指定本轮必须先调用的确定性工具（如营养计算 /
      RAG 检索）。模型若在调用它之前就直接作答，会被注入纠错指令并强制继续，
      从功能层杜绝"跳过工具、凭记忆编造"。

    Args:
        messages: 初始消息列表
        tools: 可用工具列表
        max_iterations: 最大工具调用轮数
        required_tool: 结束回答前必须调用过的工具名（None 表示不强制）

    Returns:
        (完整的消息列表（含工具调用和结果）, 工具调用轨迹 [{"name","result"}])
    """
    tool_map = {t.name: t for t in tools}
    current_messages = list(messages)
    model_with_tools = _model.bind_tools(tools)
    tool_traces: list[dict] = []
    called_tool_names: set[str] = set()

    logger.debug(f"[_run_agent_with_tools] 输入消息数: {len(messages)}, 工具数: {len([t.name for t in tools])}")

    for iteration in range(max_iterations):
        logger.debug(f"[_run_agent_with_tools] 迭代 {iteration+1}/{max_iterations}: 调用 LLM...")
        response = await model_with_tools.ainvoke(current_messages)
        current_messages.append(response)

        tool_calls_count = len(response.tool_calls) if (hasattr(response, "tool_calls") and response.tool_calls) else 0
        logger.debug(f"[_run_agent_with_tools]   LLM 返回 AIMessage, tool_calls 数: {tool_calls_count}")
        if hasattr(response, "content") and isinstance(response.content, str) and response.content:
            logger.debug(f"[_run_agent_with_tools]   LLM 文本回复预览: {response.content[:120]}")

        if not hasattr(response, "tool_calls") or not response.tool_calls:
            # 防幻觉门控：必需工具尚未调用时，不允许就此作答，注入纠错指令后继续
            if (
                required_tool
                and required_tool not in called_tool_names
                and iteration < max_iterations - 1
            ):
                logger.info(f"[_run_agent_with_tools] ⛔ 模型未调用必需工具 {required_tool} 即作答，强制门控")
                current_messages.append(HumanMessage(content=(
                    f"【系统纠正】你还没有调用 `{required_tool}` 工具获取真实数据。"
                    f"请立即调用 `{required_tool}`，并严格基于其返回结果作答；"
                    "在拿到工具结果前，禁止凭记忆编造任何数值或菜谱。"
                )))
                continue
            logger.info(f"[_run_agent_with_tools] 无工具调用，结束循环。当前总消息数: {len(current_messages)}")
            break

        for tc in response.tool_calls:
            tool_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            call_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
            logger.debug(f"[_run_agent_with_tools]   🔧 调用工具: {tool_name}, 参数: {json.dumps(tool_args, ensure_ascii=False)[:100]}")

            if tool_name in tool_map:
                try:
                    tool_result = await tool_map[tool_name].ainvoke(tool_args)
                    result_preview = str(tool_result)[:150].replace("\n", " ")
                    logger.debug(f"[_run_agent_with_tools]   ✅ {tool_name} 执行成功，结果预览: {result_preview}")
                except Exception as e:
                    logger.error(f"[_run_agent_with_tools]   ❌ {tool_name} 执行失败: {e}")
                    tool_result = f"❌ 工具执行失败：{str(e)}"

                called_tool_names.add(tool_name)
                tool_traces.append({"name": tool_name, "result": str(tool_result)})
                current_messages.append(
                    ToolMessage(content=tool_result, tool_call_id=call_id)
                )

    # 门控兜底：达到迭代上限仍未调用必需工具，记录警告（流末权威卡片会自然缺失）
    if required_tool and required_tool not in called_tool_names:
        logger.warning(f"[_run_agent_with_tools] 达到最大迭代 {max_iterations} 次，仍未调用必需工具 {required_tool}")

    logger.info(f"[_run_agent_with_tools] 完成！输出消息数: {len(current_messages)}, 工具轨迹: {[t['name'] for t in tool_traces]}")
    return current_messages, tool_traces


def _format_messages_debug(messages: list) -> str:
    """格式化消息列表用于 debug 输出"""
    lines = []
    for i, m in enumerate(messages):
        if isinstance(m, HumanMessage):
            role = "👤 Human"
        elif isinstance(m, AIMessage):
            role = "🤖 AI"
        elif isinstance(m, ToolMessage):
            role = "🔧 Tool"
        elif isinstance(m, SystemMessage):
            role = "⚙️  System"
        else:
            role = f"❓ {type(m).__name__}"
        content_preview = ""
        if hasattr(m, 'content'):
            if isinstance(m.content, str):
                content_preview = m.content[:60]
            elif isinstance(m.content, list):
                # 多模态消息，统计 image 和 text 块
                img_count = sum(1 for b in m.content if isinstance(b, dict) and b.get("type") == "image_url")
                text_count = sum(1 for b in m.content if isinstance(b, dict) and b.get("type") == "text")
                text_parts = [b.get("text", "")[:40] for b in m.content if isinstance(b, dict) and b.get("type") == "text"]
                content_preview = f"[多模态: {img_count}张图, {text_count}段文本] {' | '.join(text_parts)}"
            else:
                content_preview = str(m.content)[:60]
        else:
            content_preview = str(m)[:60]
        content_preview = content_preview.replace("\n", " ")
        lines.append(f"    [{i}] {role:12s}: {content_preview}")
    return "\n".join(lines)


# ========== 子图节点 ==========

async def nutrition_node(state: AgentState) -> dict:
    """营养分析子图"""
    user_id = state.get("user_id", 0)
    user_save_tool = make_save_favorite_tool(user_id)
    tools = [calculate_nutrition, generate_shopping_list, search_recipes_rag, user_save_tool]

    system = SystemMessage(content=SYSTEM_PROMPTS["nutrition"])
    state_messages = state.get("messages", [])
    logger.debug(f"[nutrition_node] 进入节点，state.messages 数量: {len(state_messages)}")
    logger.debug(f"[nutrition_node] state.messages 详情:\n{_format_messages_debug(state_messages)}")

    messages = await build_llm_messages(system, state_messages, node="nutrition")
    logger.debug(f"[nutrition_node] 加入 system prompt 后，调用 Agent 输入消息数: {len(messages)}")

    try:
        result_messages, tool_traces = await _run_agent_with_tools(
            messages, tools, required_tool="calculate_nutrition"
        )
        new_ai_messages = [m for m in result_messages if isinstance(m, AIMessage)]
        nutrition_facts = extract_nutrition_facts(tool_traces)
        logger.debug(f"[nutrition_node] Agent 返回总消息数: {len(result_messages)}, 其中 AI 消息: {len(new_ai_messages)}")
        logger.debug(f"[nutrition_node] 返回给 reducer 的新 AI 消息 (将追加到 messages):\n{_format_messages_debug(new_ai_messages)}")
        logger.debug(f"[nutrition_node] 📌 reducer 合并结果: {len(state_messages)} (旧) + {len(new_ai_messages)} (新增) = {len(state_messages) + len(new_ai_messages)} (新 total)")
        return {"messages": new_ai_messages, "nutrition_facts": nutrition_facts}
    except Exception as e:
        logger.error(f"[nutrition_node] 执行失败: {e}")
        error_msg = AIMessage(content=f"😢 营养分析处理失败：{str(e)}")
        logger.warning(f"[nutrition_node] 返回错误 AI 消息 (将追加): {_format_messages_debug([error_msg])}")
        return {"messages": [error_msg]}


async def recipe_node(state: AgentState) -> dict:
    """菜谱推荐子图"""
    user_id = state.get("user_id", 0)
    user_save_tool = make_save_favorite_tool(user_id)
    tools = [search_recipes_rag, tavily, generate_shopping_list, user_save_tool]

    system = SystemMessage(content=SYSTEM_PROMPTS["recipe"])
    state_messages = state.get("messages", [])
    logger.debug(f"[recipe_node] 进入节点，state.messages 数量: {len(state_messages)}")
    logger.debug(f"[recipe_node] state.messages 详情:\n{_format_messages_debug(state_messages)}")

    messages = await build_llm_messages(system, state_messages, node="recipe")
    logger.debug(f"[recipe_node] 加入 system prompt 后，调用 Agent 输入消息数: {len(messages)}")

    try:
        result_messages, tool_traces = await _run_agent_with_tools(
            messages, tools, required_tool="search_recipes_rag"
        )
        new_ai_messages = [m for m in result_messages if isinstance(m, AIMessage)]
        recipe_facts = extract_recipe_facts(tool_traces)
        logger.debug(f"[recipe_node] Agent 返回总消息数: {len(result_messages)}, 其中 AI 消息: {len(new_ai_messages)}")
        logger.debug(f"[recipe_node] 返回给 reducer 的新 AI 消息 (将追加到 messages):\n{_format_messages_debug(new_ai_messages)}")
        logger.debug(f"[recipe_node] 📌 reducer 合并结果: {len(state_messages)} (旧) + {len(new_ai_messages)} (新增) = {len(state_messages) + len(new_ai_messages)} (新 total)")
        return {"messages": new_ai_messages, "recipe_facts": recipe_facts}
    except Exception as e:
        logger.error(f"[recipe_node] 执行失败: {e}")
        error_msg = AIMessage(content=f"😢 菜谱推荐处理失败：{str(e)}")
        logger.warning(f"[recipe_node] 返回错误 AI 消息 (将追加): {_format_messages_debug([error_msg])}")
        return {"messages": [error_msg]}


async def general_node(state: AgentState) -> dict:
    """通用对话子图"""
    user_id = state.get("user_id", 0)
    user_save_tool = make_save_favorite_tool(user_id)
    tools = [search_recipes_rag, tavily, calculate_nutrition, generate_shopping_list, user_save_tool]

    system = SystemMessage(content=SYSTEM_PROMPTS["general"])
    state_messages = state.get("messages", [])
    logger.debug(f"[general_node] 进入节点，state.messages 数量: {len(state_messages)}")
    logger.debug(f"[general_node] state.messages 详情:\n{_format_messages_debug(state_messages)}")

    messages = await build_llm_messages(system, state_messages, node="general")
    logger.debug(f"[general_node] 加入 system prompt 后，调用 Agent 输入消息数: {len(messages)}")

    try:
        result_messages, tool_traces = await _run_agent_with_tools(messages, tools)
        new_ai_messages = [m for m in result_messages if isinstance(m, AIMessage)]
        # 通用意图不强制工具，但若模型主动调用了营养/菜谱工具，同样提取事实做接地
        nutrition_facts = extract_nutrition_facts(tool_traces)
        recipe_facts = extract_recipe_facts(tool_traces)
        logger.debug(f"[general_node] Agent 返回总消息数: {len(result_messages)}, 其中 AI 消息: {len(new_ai_messages)}")
        logger.debug(f"[general_node] 返回给 reducer 的新 AI 消息 (将追加到 messages):\n{_format_messages_debug(new_ai_messages)}")
        logger.debug(f"[general_node] 📌 reducer 合并结果: {len(state_messages)} (旧) + {len(new_ai_messages)} (新增) = {len(state_messages) + len(new_ai_messages)} (新 total)")
        return {
            "messages": new_ai_messages,
            "nutrition_facts": nutrition_facts,
            "recipe_facts": recipe_facts,
        }
    except Exception as e:
        logger.error(f"[general_node] 执行失败: {e}")
        error_msg = AIMessage(content=f"😢 对话处理失败：{str(e)}")
        logger.warning(f"[general_node] 返回错误 AI 消息 (将追加): {_format_messages_debug([error_msg])}")
        return {"messages": [error_msg]}


# ========== 图构建 ==========

def _build_graph() -> StateGraph:
    """构建 StateGraph"""
    graph = StateGraph(AgentState)

    graph.add_node("detect_intent", detect_intent)
    graph.add_node("nutrition_node", nutrition_node)
    graph.add_node("recipe_node", recipe_node)
    graph.add_node("general_node", general_node)

    graph.set_entry_point("detect_intent")

    graph.add_conditional_edges(
        "detect_intent",
        lambda state: state.get("intent", "general"),
        {
            "nutrition": "nutrition_node",
            "recipe": "recipe_node",
            "general": "general_node",
        }
    )

    graph.add_edge("nutrition_node", END)
    graph.add_edge("recipe_node", END)
    graph.add_edge("general_node", END)

    return graph


# ========== 单例管理 ==========

_checkpointer: Optional[AsyncPostgresSaver] = None
_checkpointer_cm = None
_compiled_graph = None


async def _get_checkpointer() -> AsyncPostgresSaver:
    """单例获取 AsyncPostgresSaver（首次调用时建连并初始化 checkpoint 表）"""
    global _checkpointer, _checkpointer_cm
    if _checkpointer is None:
        # Windows 下 ProactorEventLoop 与 psycopg async 不兼容（add_reader 不支持），
        # 在这里明确报错，避免后期出现难以排查的连接异常
        if sys.platform == "win32" and type(asyncio.get_running_loop()).__name__ == "ProactorEventLoop":
            raise RuntimeError(
                "检测到 ProactorEventLoop，与 psycopg async 不兼容。"
                "请使用 python -m app.main 启动，或在启动前设置 "
                "asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())"
            )
        _checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.postgres_dsn)
        _checkpointer = await _checkpointer_cm.__aenter__()
        await _checkpointer.setup()  # 自动创建 checkpoint 相关表（幂等）
        logger.info(f"[Checkpointer] AsyncPostgresSaver 初始化完成: db={settings.postgres_db}")
    return _checkpointer


async def get_graph():
    """获取编译后的 StateGraph（单例）"""
    global _compiled_graph
    if _compiled_graph is None:
        graph = _build_graph()
        _compiled_graph = graph.compile(checkpointer=await _get_checkpointer())
        logger.info("[StateGraph] 编译完成")
    return _compiled_graph


# ========== 流式对话入口 ==========

async def _ground_and_verify(
    answer: str,
    nutrition_facts: Optional[dict],
    recipe_facts: Optional[dict],
):
    """
    流末接地（防幻觉）：基于确定性工具产出的事实，生成一致性校正提示与权威数据卡。

    - 全部为纯字符串渲染，零额外 LLM 调用；
    - 以 chunk 形式 yield，直接拼接到已流式下发的正文末尾；
    - 顺序：先给出醒目的校验警示，再附上工具事实直出的权威卡片（正确值兜底）。
    """
    if nutrition_facts:
        for v in verify_nutrition(answer, nutrition_facts):
            yield f"\n\n> ⚠️ **数据校验**：{v}\n"
        yield render_nutrition_card(nutrition_facts)

    if recipe_facts:
        for v in verify_recipes(answer, recipe_facts):
            yield f"\n\n> ⚠️ **来源校验**：{v}\n"
        card = render_recipe_card(recipe_facts)
        if card:
            yield card


async def _build_image_block(img_url: str, user_id: int, thread_id: str) -> dict:
    """
    把本轮图片转成可持久化的内容块（只落对象键，避免 base64 常驻 checkpoint）

    - 前端已上传到本 bucket 的 URL：直接提取对象键，无需下载/转存
    - 其他来源：下载转 base64 后转存 OSS，返回对象键
    - OSS 未配置或转存失败：降级为旧行为（持久化 base64 data URL）
    """
    if settings.context_image_persist == "oss_key" and oss.is_configured():
        object_key = oss.extract_object_key(img_url)
        if object_key:
            return {"type": "image_ref", "key": object_key}
        try:
            data_url = await ensure_image_base64_data_url(img_url)
            object_key = await oss.upload_data_url(
                data_url, oss.build_image_object_key(user_id, thread_id, data_url)
            )
            return {"type": "image_ref", "key": object_key}
        except Exception as e:
            logger.warning(f"[ImageGovern] 图片转存 OSS 失败，降级持久化 base64: {type(e).__name__}: {e}")

    data_url = await ensure_image_base64_data_url(img_url)
    return {"type": "image_url", "image_url": {"url": data_url}}


async def stream_chat(prompt: str, image_urls: list, thread_id: str, user_id: int):
    """
    基于 StateGraph 的流式对话

    Args:
        prompt: 用户输入的文本
        image_urls: 图片 URL 列表
        thread_id: 会话 ID
        user_id: 用户 ID
    """
    logger.info(f"[stream_chat] ========================================")
    logger.info(f"[stream_chat] 新请求: prompt='{prompt[:80]}', images={len(image_urls or [])}张, thread_id={thread_id}, user_id={user_id}")
    logger.info(f"[stream_chat] ========================================")

    await session_store.upsert_session(thread_id, user_id=user_id, title=(prompt or "上传了图片")[:30])

    try:
        graph = await get_graph()

        content_blocks = []
        if image_urls:
            for img_url in image_urls:
                if img_url and img_url.strip():
                    content_blocks.append(await _build_image_block(img_url, user_id, thread_id))
            ref_count = sum(1 for b in content_blocks if b.get("type") == "image_ref")
            logger.info(
                f"[stream_chat] 图片处理完成: 共 {len(content_blocks)} 张"
                f"（对象键 {ref_count} 张，base64 降级 {len(content_blocks) - ref_count} 张）"
            )

        if not prompt or prompt.strip() == "":
            if image_urls:
                default_prompts = {
                    "nutrition": "请识别这些图片中的食材或菜品，分析营养成分和热量",
                    "recipe": "请识别这些图片中的食材，推荐能做的菜谱",
                    "general": "请识别这些图片中的食材，并给出建议",
                }
                content_blocks.append({"type": "text", "text": default_prompts["general"]})
                logger.info(f"[stream_chat] 用户无文字输入，使用默认 prompt: {default_prompts['general'][:60]}")
            else:
                content_blocks.append({"type": "text", "text": "你好"})
                logger.info(f"[stream_chat] 无输入，兜底 prompt: 你好")
        else:
            content_blocks.append({"type": "text", "text": prompt})

        message = HumanMessage(content=content_blocks)
        logger.debug(f"[stream_chat] 构造本轮 HumanMessage: {_format_messages_debug([message])}")

        # debug: 调用前查看 checkpoint 中已有多少消息
        cp = await _get_checkpointer()
        try:
            existing_checkpoint = await cp.aget({"configurable": {"thread_id": thread_id}})
            if existing_checkpoint and existing_checkpoint.get("channel_values"):
                existing_msgs = existing_checkpoint["channel_values"].get("messages", [])
                logger.debug(f"[stream_chat] 📌 Checkpoint 加载的历史消息数: {len(existing_msgs)}")
                if existing_msgs:
                    logger.debug(f"[stream_chat] Checkpoint 历史详情:\n{_format_messages_debug(existing_msgs)}")
                logger.debug(f"[stream_chat] 📌 reducer 合并后首轮 state.messages = {len(existing_msgs)} (历史) + 1 (本轮 Human) = {len(existing_msgs) + 1} 条")
            else:
                logger.debug(f"[stream_chat] 📌 无 checkpoint 历史（新会话），首轮 state.messages = 1 条 HumanMessage")
        except Exception as e:
            logger.warning(f"[stream_chat] 读取 checkpoint 失败: {e}")

        total_chunks = 0
        answer_parts: list[str] = []  # 累积实际下发给用户的文本，供流末接地校验
        async for event in graph.astream(
            {
                "messages": [message],
                "user_id": user_id,
                "thread_id": thread_id,
                "user_prompt": prompt,
                "image_urls": image_urls or [],
                "intent": "general",
            },
            {"configurable": {"thread_id": thread_id}},
            stream_mode="messages",
            durability="sync",  # 同步 checkpoint：确保每个 superstep 的持久化写入完成后再进入下一步
                               # 避免客户端断连时最后几个 superstep 的 checkpoint 来不及 flush 导致消息丢失
        ):
            if isinstance(event, tuple):
                chunk, metadata = event
                if isinstance(chunk, AIMessageChunk) and chunk.content:
                    total_chunks += 1
                    answer_parts.append(chunk.content)
                    yield chunk.content

        full_answer = "".join(answer_parts)
        logger.info(f"[stream_chat] ✅ 完成流式输出: 共 {total_chunks} 个 content chunk, 正文 {len(full_answer)} 字")

        # ===== 防幻觉接地：流末从 checkpoint 取工具事实，追加权威卡片 + 一致性校验 =====
        try:
            after_checkpoint = await cp.aget({"configurable": {"thread_id": thread_id}})
            channel_values = (after_checkpoint or {}).get("channel_values") or {}

            # debug: 调用后查看 checkpoint 中保存的消息
            after_msgs = channel_values.get("messages", [])
            if after_msgs:
                logger.debug(f"[stream_chat] 执行后 Checkpoint 保存的消息数: {len(after_msgs)}")
                logger.debug(f"[stream_chat] 执行后消息详情:\n{_format_messages_debug(after_msgs)}")

            async for grounded in _ground_and_verify(
                full_answer,
                channel_values.get("nutrition_facts"),
                channel_values.get("recipe_facts"),
            ):
                yield grounded
        except Exception as e:
            # 接地属于增强能力，绝不能因此影响主回答
            logger.warning(f"[stream_chat] 接地校验/卡片渲染失败（已忽略）: {e}")

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"\n[StateGraph 错误]: {str(e)}\n{tb}")
        yield (
            f"😢 处理请求失败：{str(e)}\n\n"
            "请检查：\n"
            "1. 图片格式是否为 jpg/png/webp 等常见格式\n"
            f"2. DASHSCOPE_API_KEY 是否有效且支持多模态模型 {settings.llm_model}\n"
            "3. 图片大小建议 < 5MB/张，过大可能上传失败\n"
        )


# ========== 会话管理 ==========

async def list_sessions(user_id: int) -> list:
    """列出指定用户的所有会话"""
    return await session_store.list_sessions(user_id)


async def delete_session(thread_id: str, user_id: int):
    """删除会话"""
    logger.info(f"删除会话，thread_id: {thread_id}, user_id: {user_id}")
    try:
        cp = await _get_checkpointer()
        await cp.adelete_thread(thread_id)
    except Exception as e:
        logger.error(f"删除 checkpointer 记录失败: {e}")
    await session_store.delete_session(thread_id, user_id)


async def _render_history_content(content):
    """
    把持久化内容块渲染成前端可消费格式：
    - image_ref（仅存对象键）→ image_url + 重新签发的 7 天 GET URL
    - 重签失败降级为占位文本，不阻断历史加载
    - 其他块原样透传（兼容旧版 base64 image_url）
    """
    if not isinstance(content, list):
        return content

    rendered = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image_ref":
            key = block.get("key")
            url = ""
            if key:
                try:
                    url = await oss.presign_get(key)
                except Exception as e:
                    logger.warning(f"[ImageGovern] 历史图片重签失败（降级为占位）: {type(e).__name__}: {e}")
            if url:
                rendered.append({"type": "image_url", "image_url": {"url": url}})
            else:
                rendered.append({"type": "text", "text": "[图片已过期]"})
            continue
        rendered.append(block)
    return rendered


async def get_messages(thread_id: str) -> list:
    """获取会话历史消息
    
    返回格式兼容前端多模态渲染：
    - 用户消息 content 可能是 str 或 list[dict]（LangChain image_url 格式）
    - 同时提供 content_parts 便于前端直接消费多模态结构
    """
    logger.info(f"获取历史消息，thread_id: {thread_id}")
    cp = await _get_checkpointer()
    checkpoint = await cp.aget({"configurable": {"thread_id": thread_id}})

    if not checkpoint:
        return []

    channel_values = checkpoint.get("channel_values")
    if not channel_values:
        return []

    messages = channel_values.get("messages", [])
    if not messages:
        return []

    result = []
    for msg in messages:
        if not msg.content:
            continue
        # LangChain Message 对象序列化为 dict 时的 content 字段
        # 可能是 str（纯文本）或 list[dict]（多模态 content_blocks）
        content = msg.content
        role = "user" if isinstance(msg, HumanMessage) else "assistant" if isinstance(msg, AIMessage) else None
        if role is None:
            continue
        result.append({"role": role, "content": await _render_history_content(content)})

    logger.info(f"[get_messages] 返回 {len(result)} 条历史消息, thread_id={thread_id}")
    return result