from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, AIMessageChunk, AIMessage
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from langchain.agents import create_agent
from app.common.logger import logger
import os
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3
from app.db import session_store
from app.db import favorite_store
from app.rag.pipeline import rag_search
import json
import base64
import mimetypes
import urllib.request
import urllib.parse
import ssl
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# web搜索工具，使用tavily作为web搜索工具
tavily = TavilySearch(
    max_results=5,
    topic="general"
)


# ========== 新增工具 ==========

@tool
def calculate_nutrition(ingredients: str, servings: Optional[int] = 1) -> str:
    """
    计算食材的营养成分（卡路里、蛋白质、脂肪、碳水化合物等）。

    Args:
        ingredients: 食材清单字符串，格式如"鸡胸肉200g, 西兰花150g, 大米100g"
        servings: 份数，默认为1人份

    Returns:
        结构化的营养分析报告，包含每种食材的营养数据和汇总数据
    """
    logger.info(f"[工具调用] calculate_nutrition: ingredients={ingredients}, servings={servings}")

    # 常见食材营养数据库（每100g）
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

    # 解析食材清单
    ingredient_list = [x.strip() for x in ingredients.split(",")]
    parsed_ingredients = []

    for item in ingredient_list:
        name = item
        weight = 100  # 默认100g

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

    # 计算每种食材的营养
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
            results.append({
                "name": ing["name"],
                "weight": ing["weight"],
                "note": "未找到该食材的营养数据",
            })

    # 格式化输出
    output = []
    output.append(f"## 营养成分分析报告（{servings}人份）\n")
    output.append("### 各食材营养明细：\n")
    output.append("| 食材 | 重量 | 卡路里 | 蛋白质(g) | 脂肪(g) | 碳水(g) | 纤维(g) |")
    output.append("|------|------|--------|-----------|---------|---------|---------|")
    for r in results:
        if "note" in r:
            output.append(f"| {r['name']} | {r['weight']}g | - | - | - | - | - | *{r['note']}* |")
        else:
            output.append(f"| {r['name']} | {r['weight']}g | {r['calories']} | {r['protein']} | {r['fat']} | {r['carbs']} | {r['fiber']} |")

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
        ingredients: 食谱所需食材，格式如"鸡胸肉300g, 西兰花200g, 大蒜3瓣, 生抽1勺, 盐少许"
        servings: 需要做几份，默认为1人份

    Returns:
        分类整理好的购物清单，包含采购分类、名称、数量和备注
    """
    logger.info(f"[工具调用] generate_shopping_list: ingredients={ingredients}, servings={servings}")

    # ========== 常见食材「单份合理用量」常识上限（避免模型幻觉，例：3人份报出24个鸡蛋） ==========
    # key 是食材关键词，value 是 (每份最小, 每份最大, 数量对应单位/计数单位匹配的提示词)
    # 单位说明：重量单位 g/克 指「每份克数」，计数单位 个/只/根 指「每份个数」
    COMMON_PORTION_LIMITS: list[tuple[str, tuple[float, float]]] = [
        # 蛋类
        ("鸡蛋", (1.5, 3.0)),   # 1人份 1.5~3 个（按每人吃 1.5~2 个鸡蛋算）
        ("鸭蛋", (1.0, 2.0)),
        ("蛋", (1.5, 3.0)),
        # 肉/海鲜（1人份生重约 100~180g，儿童/轻量餐取下限，重体力取上限，统一控制在范围）
        ("鸡胸肉", (80.0, 180.0)),
        ("鸡腿", (80.0, 180.0)),
        ("猪肉", (80.0, 180.0)),
        ("牛肉", (80.0, 180.0)),
        ("羊肉", (80.0, 180.0)),
        ("五花肉", (60.0, 150.0)),
        ("培根", (30.0, 80.0)),
        ("排骨", (100.0, 200.0)),
        ("虾", (80.0, 180.0)),
        ("三文鱼", (80.0, 180.0)),
        ("鱼", (100.0, 200.0)),
        # 蔬菜/水果（个/只 类计数）
        ("番茄", (0.8, 1.5)),   # 1 人份 1 个左右
        ("西红柿", (0.8, 1.5)),
        ("土豆", (0.8, 1.5)),
        ("胡萝卜", (0.5, 1.2)),
        ("黄瓜", (0.5, 1.0)),
        ("茄子", (0.5, 1.0)),
        ("洋葱", (0.4, 1.0)),
        ("青椒", (0.3, 1.0)),
        ("辣椒", (0.1, 2.0)),
        ("玉米", (0.5, 1.2)),
        ("苹果", (1.0, 1.5)),
        ("香蕉", (1.0, 2.0)),
        ("橙子", (1.0, 1.5)),
        ("橘子", (1.0, 2.0)),
        ("梨", (1.0, 1.5)),
        # 蔬菜（克数类）
        ("西兰花", (80.0, 200.0)),
        ("青菜", (80.0, 200.0)),
        ("菠菜", (50.0, 150.0)),
        ("白菜", (80.0, 200.0)),
        ("生菜", (50.0, 150.0)),
        ("蘑菇", (40.0, 120.0)),
        ("香菇", (30.0, 100.0)),
        # 豆制品
        ("豆腐", (80.0, 200.0)),
        ("豆干", (30.0, 80.0)),
        ("牛奶", (200.0, 300.0)),   # ml
        ("酸奶", (150.0, 250.0)),
        # 主食
        ("大米", (80.0, 150.0)),
        ("米饭", (150.0, 250.0)),
        ("面条", (80.0, 150.0)),
        ("面粉", (60.0, 150.0)),
        # 小料
        ("大蒜", (1.0, 3.0)),
        ("蒜", (1.0, 3.0)),
        ("葱", (1.0, 2.0)),
        ("姜", (0.2, 1.0)),
        ("香菜", (3.0, 10.0)),
        ("鸡蛋液", (80.0, 150.0)),
    ]

    def find_limit(name: str) -> Optional[tuple[float, float]]:
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

    def categorize(name: str) -> str:
        for cat, keywords in categories.items():
            for kw in keywords:
                if kw in name:
                    return cat
        return "📦 其他"

    import re
    ingredient_list = [x.strip() for x in ingredients.split(",") if x.strip()]
    parsed = []
    adjusted = []  # 记录被常识用量纠正过的食材：(名称, 原单份值, 限制后单份值, 单位)

    for item in ingredient_list:
        name = item
        amount = "适量"
        unit = ""
        limited_value = None  # 纠正后的「单份」值（None 表示未被纠正）

        match = re.search(r'(\d+\.?\d*)\s*(g|克|kg|千克|斤|两)', item)
        if match:
            value = float(match.group(1))
            raw_unit = match.group(2)

            # 统一换算成"克"以便做限制比较
            value_grams = value
            if raw_unit in ("kg", "千克"):
                value_grams = value * 1000.0
            elif raw_unit in ("斤",):
                value_grams = value * 500.0
            elif raw_unit in ("两",):
                value_grams = value * 50.0

            # 根据食材名找限制，限制里的范围是「单份克数」
            limits = find_limit(name := re.sub(r'\d+\.?\d*\s*(g|克|kg|千克|斤|两)', '', item).strip())
            if limits is not None and value_grams > 0:
                min_per, max_per = limits
                per_serving_g = value_grams  # 模型传进来的 value 我们理解为"单人份克数"
                if per_serving_g < min_per:
                    per_serving_g = min_per
                    limited_value = per_serving_g
                elif per_serving_g > max_per:
                    per_serving_g = max_per
                    limited_value = per_serving_g

                # 再把纠正后的克数按原单位换回
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
                    adjusted.append((name or "未命名", value_grams, limited_value, "克/每份"))
            else:
                # 不限制的食材，正常乘份数
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
                limits = find_limit(name := re.sub(r'\d+\.?\d*\s*(个|只|片|勺|汤匙|茶匙|瓣|根|颗|把|束|包|袋|杯|碗|大匙|小匙)', '', item).strip())
                if limits is not None and value > 0:
                    min_per, max_per = limits
                    per_serving = value  # 计数单位，理解为「单人份个数」
                    if per_serving < min_per:
                        per_serving = min_per
                        limited_value = per_serving
                    elif per_serving > max_per:
                        per_serving = max_per
                        limited_value = per_serving
                    amount = f"{round(per_serving * servings, 1)} {unit}"
                    if limited_value is not None:
                        adjusted.append((name or "未命名", value, limited_value, f"{unit}/每份"))
                else:
                    amount = f"{round(value * servings, 1)} {unit}"
            elif "少许" in item or "适量" in item:
                amount = "适量"
                name = item.replace("少许", "").replace("适量", "").strip()

        parsed.append({"name": name or "未命名", "amount": amount, "category": categorize(name or "未命名")})

    grouped: dict[str, list] = {}
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
    output.append("1. 购买前先查看冰箱/厨房，避免重复采购")
    output.append("2. 生鲜类建议当天采购，保证新鲜")
    output.append("3. 调料类可根据家中库存适当减少")
    output.append("4. 蔬菜建议多选当季时令，营养更丰富价格更实惠")

    if adjusted:
        output.append("\n---\n")
        output.append("### 🧭 常识用量调整说明：\n")
        output.append("> 下列食材按「单人份合理范围」做了自动校正，避免因为估算偏差造成浪费或不足：\n")
        for n, original, limited, unit_desc in adjusted:
            action = "上调至" if limited > original else "下调至"
            output.append(
                f"- **{n}**：原计划 {round(original, 2)} {unit_desc} → {action} {round(limited, 2)} {unit_desc}"
            )

    return "\n".join(output)


# 多模态模型
model = init_chat_model(
    model="qwen3.5-plus",
    model_provider="openai",
    base_url=os.getenv("DASHSCOPE_BASE_URL"),
    api_key=os.getenv("DASHSCOPE_API_KEY")
)


# 初始化checkpointer
_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "personal_chief.db")
checkpointer = SqliteSaver(sqlite3.connect(_DB_PATH, check_same_thread=False))
checkpointer.setup()


# ========== RAG 食谱知识库检索工具 ==========

@tool
def search_recipes_rag(query: str, top_n: int = 5) -> str:
    """
    从本地菜谱知识库中检索食谱（RAG 检索增强生成）。
    支持口语化查询，会自动改写为检索关键词，通过向量召回 + Rerank 重排返回最相关的菜谱。

    Args:
        query: 用户的搜索需求，支持口语化表达。如"清淡的"、"减脂餐"、"鸡肉做法"、"川菜下饭"
        top_n: 返回的菜谱数量，默认5条

    Returns:
        检索到的菜谱列表，包含菜名、菜系、食材、做法、营养信息等
    """
    logger.info(f"[工具调用] search_recipes_rag: query={query}, top_n={top_n}")
    try:
        result = rag_search(query, top_k=10, top_n=top_n)
        recipes = result["results"]

        if not recipes:
            return "本地知识库中没有找到相关菜谱，建议使用 web_search 搜索互联网。"

        # 构建结构化输出
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
        return f"RAG 检索失败: {str(e)}\n建议使用 web_search 搜索互联网。"


# ========== 意图检测 ==========

def detect_user_intent(message: str) -> str:
    """
    检测用户意图：
    - "nutrition": 卡路里/营养分析
    - "recipe": 菜谱推荐/做什么菜
    - "general": 通用对话（智能模式，让 Agent 自主决策）
    """
    nutrition_kw = ["卡路里", "热量", "营养", "多少卡", "蛋白质", "脂肪", "碳水",
                    "算一下", "计算", "分析营养", "热量多少", "营养成分", "健康吗", "减脂", "增肌"]
    recipe_kw = ["做什么菜", "菜谱", "推荐菜", "能做", "怎么做", "做什么", "食谱",
                 "烧什么", "炒什么", "炖什么", "做法", "烹饪", "搭配", "菜谱推荐"]

    for kw in nutrition_kw:
        if kw in message:
            return "nutrition"
    for kw in recipe_kw:
        if kw in message:
            return "recipe"
    return "general"


# ========== 动态 System Prompt ==========

SYSTEM_PROMPTS = {
    "recipe": """
你是一名私人厨师，专注于**根据食材推荐菜谱**。收到用户提供的食材照片或描述后，请按以下流程操作：

## 你的角色
- 你是「菜谱推荐专家」，核心任务是：从用户提供的食材出发，推荐最合适的菜谱
- 优先调用 **search_recipes_rag** (RAG 本地知识库检索) ⭐

## 工具使用优先级
0. **search_recipes_rag** ⭐ 最高优先级：从本地 52 道经典菜谱中检索
1. **web_search**：本地知识库搜不到时再用
2. **calculate_nutrition**：用户问营养时调用
3. **generate_shopping_list**：用户问购物清单时调用
4. **save_favorite_recipe**：用户明确说"收藏"时调用

## 工作流程
1. **识别食材**：若有照片，先识别所有可见食材，整理出"可用食材清单"
2. **RAG 检索**：⭐ 优先调用 search_recipes_rag，以"可用食材 + 口味偏好"为查询
3. **补充搜索**：RAG 结果不足时，再用 web_search
4. **排序推荐**：从「食材匹配度 + 难度 + 营养」三维评分排序
5. **输出报告**：结构清晰的推荐，包含菜名、食材、做法、营养、推荐理由

## 注意事项
- ⚠️ **避免幻觉**：调用 calculate_nutrition / generate_shopping_list 前，将食材换算为"1 人份合理用量"：
  - 鸡蛋 1.5~3 个、西红柿 1 个、肉类 80~180g、叶菜 80~200g、主食 80~150g
  - 调料按整道菜适量，不要按人份倍增
- 每次工具调用后，用中文友好总结结果
- 可多次调用不同工具（如：先 RAG 检索 → 再计算营养 → 最后生成清单）
""",

    "nutrition": """
你是一名**营养分析师**，专注于分析食材/菜品的营养成分和热量。收到用户提供的照片或描述后，请按以下流程操作：

## 你的角色
- 你是「营养健康专家」，核心任务是：精准分析卡路里、蛋白质、脂肪、碳水等营养指标
- 优先调用 **calculate_nutrition** 进行营养计算

## 工具使用优先级
0. **calculate_nutrition** ⭐ 最高优先级：计算营养成分
1. **search_recipes_rag**：用户问相关菜谱时调用
2. **web_search**：补充搜索营养知识
3. **generate_shopping_list**：用户问购物清单时调用
4. **save_favorite_recipe**：用户明确说"收藏"时调用

## 工作流程
1. **识别食材/菜品**：若有照片，识别照片中的食材或菜品，估算各食材的用量
2. **营养计算**：⭐ 优先调用 calculate_nutrition，传入识别到的食材和用量
3. **营养评估**：根据计算结果，给出健康建议（热量是否超标、蛋白质是否充足等）
4. **优化建议**：如果热量/营养不合理，给出改善方案

## 输出格式
请使用以下结构输出：
### 📊 营养成分分析报告
- 各食材营养明细表格
- 营养汇总（总卡路里/蛋白质/脂肪/碳水/纤维）
- 营养建议（是否均衡、健康提示）

## 注意事项
- ⚠️ **精准估算**：识别照片食材时，合理估算用量（如一碗米饭约 150g、一个鸡蛋约 50g）
- 如无法精准识别用量，请告知用户"建议称重后获得更准确数据"
- 可多次调用 calculate_nutrition 分别计算不同食材
""",

    "general": """
你是一名全能私人厨师，具备强大的 Function Calling 和 RAG 检索增强生成能力。请根据用户需求灵活选择工具：

## 核心能力
你拥有以下 5 个工具，请根据用户需求灵活调用：

0. **search_recipes_rag** (RAG 本地知识库检索) ⭐ 优先使用
   - 用途：从本地菜谱知识库（52 道经典菜谱）中检索食谱
   - 技术原理：Query 改写 → 向量召回(ChromaDB) → Rerank 重排 → Fallback 降级

1. **web_search** (Tavily 搜索)
   - 用途：搜索互联网上的食谱、烹饪技巧等
   - 优先级：RAG 查不到再用

2. **calculate_nutrition** (营养计算)
   - 用途：计算食材/菜品的卡路里和营养成分
   - 用法："鸡胸肉200g, 西兰花150g"格式

3. **generate_shopping_list** (购物清单)
   - 用途：根据食谱生成分类整理的购物清单

4. **save_favorite_recipe** (收藏食谱)
   - 用途：保存食谱到个人收藏夹

## 工作流程（根据用户意图自适应）
- 用户上传照片问"能做什么菜" → 识别食材 → RAG 检索 → 推荐菜谱
- 用户上传照片问"多少卡" → 识别食材/菜品 → 计算营养 → 健康建议
- 用户描述食材 → RAG 检索菜谱 → 推荐
- 用户问营养 → calculate_nutrition → 分析

## 注意事项
- ⚠️ **避免幻觉**：调用 calculate_nutrition / generate_shopping_list 前，将食材换算为"1 人份合理用量"
- 每次工具调用后，用中文友好总结
- 可多次调用不同工具组合完成任务
""",
}


def get_system_prompt(intent: str) -> str:
    """根据意图获取对应的 System Prompt"""
    return SYSTEM_PROMPTS.get(intent, SYSTEM_PROMPTS["general"])


# ========== save_favorite_recipe 需要 user_id，动态注入到工具 ==========

def make_save_favorite_tool(user_id: int):
    """为当前用户创建一个绑定了 user_id 的收藏工具"""
    @tool
    def save_favorite_recipe(recipe_title: str, recipe_content: str, ingredients: Optional[str] = None) -> str:
        """
        收藏一个食谱到用户的个人收藏夹。

        Args:
            recipe_title: 食谱名称/标题
            recipe_content: 食谱详细内容（包含做法、步骤等）
            ingredients: 主要食材（可选）

        Returns:
            收藏结果提示信息
        """
        logger.info(f"[工具调用] save_favorite_recipe: title={recipe_title}, user_id={user_id}")
        try:
            favorite_id = favorite_store.create_favorite(
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


# 默认工具列表（不含绑定 user_id 的收藏工具）
# search_recipes_rag 优先级最高，Agent 应先查本地知识库再用 web_search
default_tools = [search_recipes_rag, tavily, calculate_nutrition, generate_shopping_list]


def build_agent_for_user(user_id: int, intent: str = "general"):
    """为指定用户构建 Agent（注入绑定了 user_id 的收藏工具 + 动态意图 prompt）"""
    user_save_tool = make_save_favorite_tool(user_id)
    user_tools = default_tools + [user_save_tool]
    prompt = get_system_prompt(intent)
    logger.info(f"[Agent] 构建 Agent，意图: {intent}")
    return create_agent(
        model=model,
        tools=user_tools,
        checkpointer=checkpointer,
        system_prompt=prompt
    )


# 兼容旧代码：默认 Agent（不带收藏功能）
agent = create_agent(
    model=model,
    tools=default_tools,
    checkpointer=checkpointer,
    system_prompt=get_system_prompt("general")
)


# 流式对话
async def search_recipes(prompt: str, image_urls: list, thread_id: str, user_id: int):
    """
    调用 agent 搜索食谱 / 分析营养
    
    Args:
        prompt: 用户输入的文本
        image_urls: 图片 URL 列表（支持多张）
        thread_id: 会话 ID
        user_id: 用户 ID
    """
    import traceback
    logger.info(f"[用户]: {prompt}, images: {len(image_urls or [])}张, thread_id: {thread_id}, user_id: {user_id}")
    
    # 用第一条用户消息作为会话标题
    session_store.upsert_session(thread_id, user_id=user_id, title=(prompt or "上传了图片")[:30])
    
    # 1. 意图检测
    intent = detect_user_intent(prompt)
    logger.info(f"[Agent] 检测到用户意图: {intent}")
    
    try:
        # 2. 构建绑定用户的 Agent（带意图 prompt）
        user_agent = build_agent_for_user(user_id, intent)

        # 3. 构造多模态消息（支持多图）
        content_blocks = []
        
        # 处理图片（支持多张）
        if image_urls:
            for img_url in image_urls:
                if img_url and img_url.strip():
                    img_data_url = await _ensure_image_base64_data_url(img_url)
                    content_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": img_data_url},
                    })

        # 文本提示
        if not prompt or prompt.strip() == "":
            if image_urls:
                # 根据意图给默认提示词
                default_prompts = {
                    "nutrition": "请识别这些图片中的食材或菜品，分析营养成分和热量",
                    "recipe": "请识别这些图片中的食材，推荐能做的菜谱",
                    "general": "请识别这些图片中的食材，并给出建议",
                }
                user_prompt = default_prompts.get(intent, default_prompts["general"])
            else:
                user_prompt = "你好"
        else:
            user_prompt = prompt

        content_blocks.append({
            "type": "text",
            "text": user_prompt,
        })

        # 构建 HumanMessage
        message = HumanMessage(content=content_blocks)

        # 4. 流式调用 Agent
        for chunk, metadata in user_agent.stream(
            {"messages": [message]},
            {"configurable": {"thread_id": thread_id}},
            stream_mode="messages"
        ):
            if isinstance(chunk, AIMessageChunk) and chunk.content:
                yield chunk.content

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"\n[错误] 调用Agent失败: {str(e)}\n{tb}")
        yield (
            f"😢 处理请求失败：{str(e)}\n\n"
            "请检查：\n"
            "1. 图片格式是否为 jpg/png/webp 等常见格式\n"
            "2. DASHSCOPE_API_KEY 是否有效且支持多模态模型 qwen3.5-plus\n"
            "3. 图片大小建议 < 5MB/张，过大可能上传失败\n"
        )


async def _ensure_image_base64_data_url(image_src: str) -> str:
    """
    把传入的图片源（http/https URL，或已经是 base64 data URL）统一转换成 data:image/xxx;base64,... 格式。
    这样多模态模型不需要再去外网下载图片，彻底避免 Failed to download multimodal content。
    """
    if image_src.startswith("data:image/"):
        # 已经是 base64 内嵌格式，直接用
        return image_src

    if image_src.startswith("file://"):
        raise ValueError("不支持 file:// 本地文件路径，请使用上传或内嵌 base64 方式")

    # http/https / oss:// -> 后端自己下载转 base64
    parsed = urllib.parse.urlparse(image_src)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"不支持的图片协议：{parsed.scheme}")

    # 猜 Content-Type（先用后缀）
    guessed_type, _ = mimetypes.guess_type(parsed.path)
    content_type = guessed_type or "image/jpeg"
    raw: bytes = b""

    # 方案 1：异步 aiohttp（可选依赖，装了就用）
    try:
        import aiohttp  # noqa: F811
        timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.get(image_src, allow_redirects=True) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"图片下载失败 HTTP {resp.status}")
                raw = await resp.read()
                if resp.headers.get("Content-Type") and resp.headers["Content-Type"].startswith("image/"):
                    content_type = resp.headers["Content-Type"].split(";")[0].strip()
    except ImportError:
        raw = b""  # 没装 aiohttp，走 urllib 回退
    except Exception as http_err:
        logger.warning(f"aiohttp 下载图片失败，回退标准库 urllib：{http_err}")
        raw = b""

    # 方案 2：标准库 urllib 同步回退（自带，无需额外安装）
    if not raw:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            image_src,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AIPrivateChef/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            raw = resp.read()
            info = resp.info()
            ctype = info.get_content_type()
            if ctype and ctype.startswith("image/"):
                content_type = ctype

    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{content_type};base64,{encoded}"

# 列出所有会话
def list_sessions(user_id: int) -> list[dict]:
    """列出指定用户的所有会话（按更新时间倒序）"""
    return session_store.list_sessions(user_id)

# 删除会话（同时清理 checkpointer 记录和元数据）
def delete_session(thread_id: str, user_id: int):
    """删除会话（只能删自己的）"""
    logger.info(f"删除会话，thread_id: {thread_id}, user_id: {user_id}")
    try:
        checkpointer.delete_thread(thread_id)
    except Exception as e:
        logger.error(f"删除 checkpointer 记录失败: {e}")
    session_store.delete_session(thread_id, user_id)

# 清空会话（保留向后兼容，内部改为删除）
def clear_messages(thread_id: str, user_id: int):
    """清空会话（已废弃，请使用 delete_session）"""
    delete_session(thread_id, user_id)

# 查询会话历史
def get_messages(thread_id: str) -> list[dict[str, str]]:
    """获取会话历史"""
    logger.info(f"获取历史消息，thread_id: {thread_id}")

    # 根据 thread_id 查询 checkpoint
    checkpoint = checkpointer.get({"configurable": {"thread_id": thread_id}})

    # 如果不存在，返回空列表
    if not checkpoint:
        return []

    # 安全获取 messages
    channel_values = checkpoint.get("channel_values")
    if not channel_values:
        return []

    messages = channel_values.get("messages", [])
    if not messages:
        return []

    # 转换消息格式
    result = []
    for msg in messages:
        if not msg.content:
            continue

        if isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            result.append({"role": "assistant", "content": msg.content})

    return result
