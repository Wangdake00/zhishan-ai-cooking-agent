"""
多阶段 RAG 检索管道
Pipeline: Query 改写 → 向量召回 → 约束硬过滤 → Rerank 重排 → 相关性阈值拒答 → Fallback 降级

设计理念：
- 每个阶段都有独立的 Fallback 降级方案，保证全链路可用性
- Query 改写失败 → 用原始 query
- 向量召回失败 → 降级为关键字匹配
- Rerank 失败 → 降级为规则打分
- 所有阶段异常不阻断主流程，记录日志后继续
- 否定/饮食约束（"不辣""清真"）在向量空间天然失效，走规则抽取 + metadata 硬过滤
- Rerank top1 分数低于阈值视为知识库无相关菜谱，触发"未找到"链路，不硬塞结果给 LLM
"""

import ast
import os
import json
import re
from typing import List, Dict, Optional

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from app.common.logger import logger
from app.core.config import settings
from app.rag.vector_store import get_collection
from app.rag.recipe_data import RECIPES


# ========== 从统一配置读取 ==========

LLM_MODEL = settings.llm_model
RERANK_MODEL = settings.rerank_model
RAG_TOP_K = settings.rag_top_k
RAG_TOP_N = settings.rag_top_n
RAG_RERANK_THRESHOLD = settings.rag_rerank_threshold
DASHSCOPE_API_KEY = settings.dashscope_api_key
DASHSCOPE_BASE_URL = settings.dashscope_base_url


# ========== 初始化 LLM（用于 Query 改写）==========

_model = None


def _get_model():
    global _model
    if _model is None:
        logger.info(f"[RAG] Query 改写 LLM 模型: {LLM_MODEL} | API: {DASHSCOPE_BASE_URL}")
        _model = init_chat_model(
            model=LLM_MODEL,
            model_provider="openai",
            base_url=DASHSCOPE_BASE_URL,
            api_key=DASHSCOPE_API_KEY,
        )
    return _model


# ========== Stage 1: Query 改写 ==========

def rewrite_query(user_query: str) -> Dict[str, str]:
    """
    将用户的口语化 query 改写为检索友好的关键词

    Returns:
        {"original": 原始query, "rewritten": 改写后query, "keywords": 关键词列表}
    """
    try:
        model = _get_model()
        messages = [
            SystemMessage(content=(
                "你是一个菜谱搜索 Query 改写助手。用户会用口语化的方式描述想吃什么菜，"
                "你需要将其改写为适合向量检索的中文关键词串。\n"
                "要求：\n"
                "1. 提取核心食材、烹饪方式、口味偏好、菜系等检索维度\n"
                "2. 输出 5~10 个关键词，用空格分隔\n"
                "3. 只输出关键词，不要解释\n"
                "示例：\n"
                "用户：今天天好热想吃点清淡的 → 清淡 凉菜 清蒸 夏日 低卡 凉拌\n"
                "用户：冰箱里有鸡肉和土豆怎么做 → 鸡肉 土豆 家常菜 炖菜 炒菜 红烧\n"
                "用户：减肥期间能吃什么肉 → 减脂 低卡 高蛋白 鸡胸肉 鱼肉 清蒸 水煮"
            )),
            HumanMessage(content=f"改写以下搜索需求：{user_query}"),
        ]
        response = model.invoke(messages)
        rewritten = response.content.strip()

        if len(rewritten) < 2:
            logger.warning(f"[RAG] Query 改写结果过短，回退到原始 query: {user_query}")
            rewritten = user_query

        keywords = rewritten.split()
        logger.info(f"[RAG] Query 改写: '{user_query}' → '{rewritten}' (keywords: {keywords})")
        return {"original": user_query, "rewritten": rewritten, "keywords": keywords}

    except Exception as e:
        logger.error(f"[RAG] Query 改写失败，降级为原始 query: {e}")
        return {"original": user_query, "rewritten": user_query, "keywords": user_query.split()}


# ========== Stage 2: 向量召回 ==========

def vector_recall(query_text: str, top_k: int = 10) -> List[Dict]:
    """
    使用 ChromaDB 语义检索召回 Top-K 菜谱
    """
    try:
        collection = get_collection()
        results = collection.query(
            query_texts=[query_text],
            n_results=min(top_k, collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        if not results["ids"] or not results["ids"][0]:
            logger.warning("[RAG] 向量召回结果为空")
            return []

        recipes = []
        for i, recipe_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            similarity = max(0.0, 1.0 - distance / 2.0)

            recipes.append({
                "id": meta.get("id", recipe_id),
                "name": meta.get("name", ""),
                "cuisine": meta.get("cuisine", ""),
                "difficulty": meta.get("difficulty", ""),
                "cooking_time": meta.get("cooking_time", ""),
                "ingredients": meta.get("ingredients", ""),
                "steps": meta.get("steps", ""),
                "nutrition": meta.get("nutrition", ""),
                "tags": meta.get("tags", ""),
                "description": meta.get("description", ""),
                "score": round(similarity, 4),
            })

        logger.info(f"[RAG] 向量召回 {len(recipes)} 条结果 (query: '{query_text[:30]}...')")
        return recipes

    except Exception as e:
        logger.error(f"[RAG] 向量召回失败，将降级为关键字匹配: {e}")
        return []


# ========== Stage 2 Fallback: 关键字匹配 ==========

def keyword_recall(keywords: List[str], top_k: int = 10) -> List[Dict]:
    """Fallback：当向量召回失败时，使用关键字匹配检索菜谱"""
    try:
        logger.info(f"[RAG] Fallback 关键字匹配 (keywords: {keywords})")
        scored = []

        for recipe in RECIPES:
            searchable = (
                recipe["name"] + " " +
                recipe["cuisine"] + " " +
                " ".join(recipe["ingredients"]) + " " +
                " ".join(recipe["tags"]) + " " +
                recipe["description"]
            ).lower()

            hit_count = sum(1 for kw in keywords if kw.lower() in searchable)
            if hit_count > 0:
                scored.append({
                    "id": recipe["id"],
                    "name": recipe["name"],
                    "cuisine": recipe["cuisine"],
                    "difficulty": recipe["difficulty"],
                    "cooking_time": recipe["cooking_time"],
                    "ingredients": ", ".join(recipe["ingredients"]),
                    "steps": recipe["steps"],
                    "nutrition": str(recipe["nutrition_per_serving"]),
                    "tags": ", ".join(recipe["tags"]),
                    "description": recipe["description"],
                    "score": round(hit_count / max(len(keywords), 1), 4),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        result = scored[:top_k]
        logger.info(f"[RAG] Fallback 关键字匹配返回 {len(result)} 条结果")
        return result

    except Exception as e:
        logger.error(f"[RAG] Fallback 关键字匹配也失败: {e}")
        return []


# ========== Stage 2.5: 约束抽取与硬过滤（零 LLM，纯规则）==========
# 50 条评测显示 constraint 类 query（否定/饮食约束）是唯一重灾区（MRR=0.34，占失败用例 100%），
# 根因是否定语义在向量空间几乎失效（"不辣"与"辣"向量距离很近）。
# 此类约束本质是结构化过滤条件，用规则抽取 + metadata 硬过滤即可覆盖。

# 否定/限制词，捕获其后 1~4 个汉字作为约束目标
_CONSTRAINT_TRIGGER_RE = re.compile(r"(?:不|别|免|戒|忌[口嘴]?|无|少)[要吃放做碰沾能加喝]?([一-龥]{1,4})")

# 约束目标 → (需排除的食材/名称子串, 需排除的标签子串)；匹配时按触发词长度降序（"猪肉"先于"肉"）
_NEGATION_RULES = [
    ("猪肉", (["猪", "火腿", "培根", "腊肉", "猪油"], [])),
    # "鸡肉"类排除子串只取真实鸡肉食材形态；单字"鸡"会误杀 27 道仅用"鸡精"调味的菜
    # （梅菜扣肉/小酥肉/糖醋排骨等），鸡蛋/蛋黄/蛋清天然不含以下子串
    ("鸡肉", (["鸡肉", "鸡胸", "鸡腿", "鸡翅", "鸡爪"], [])),
    ("辣", (["辣椒", "干辣椒", "泡椒", "小米辣", "辣椒油"], ["辣"])),
    ("麻", (["花椒", "麻椒", "藤椒"], ["麻辣"])),
    ("油", (["油炸", "肥肉", "五花"], ["油炸", "红油", "油腻"])),
    ("肉", (["猪", "牛", "羊", "鸡", "鸭", "鱼", "虾", "肉", "排骨", "火腿"], [])),
    ("海鲜", (["虾", "蟹", "贝", "鱿鱼", "海鲜"], ["海鲜"])),
    ("甜", (["糖醋", "白糖", "冰糖"], ["甜", "糖醋"])),
    ("酸", (["醋", "酸菜", "泡椒"], ["酸"])),
]

# 关键词触发的硬约束（无需否定词）
_HALAL_EXCLUDE = ["猪", "火腿", "培根", "腊肉", "猪油"]
_LOW_CAL_KEYWORDS = ["减脂", "减肥", "低卡", "低热量", "瘦身"]
_LOW_CAL_LIMIT = 400  # kcal/整菜，与 build_kb.py 派生标签"低卡"阈值对齐（2026-11-01 新库整菜口径 p25≈380）
# "清淡"语义上排斥强刺激口味（仅排除麻辣/香辣等级，微辣不受影响）
_LIGHT_EXCLUDE_TAGS = ["麻辣", "香辣", "红油", "油炸"]

# 偏好标签（仅在 rerank 同分时微调排序，不做硬过滤）；其首字被否定时不生效
_PREFER_RULES = [
    ("清淡", ["清淡", "清蒸", "凉拌", "素"]),
    ("酸口", ["酸", "酸甜", "醋"]),
    ("酸甜", ["酸甜", "糖醋", "醋"]),
    ("软烂", ["软", "烂", "炖", "蒸", "粥", "汤"]),
    ("下饭", ["下饭"]),
]

# 每个命中偏好标签对 rerank 分的微调权重（仅作同分区分，不改变分数语义）
_PREFER_WEIGHT = 0.02


def _corpus_contains(term: str) -> bool:
    """语料校验：通用排除目标必须在菜谱库的食材/菜名中真实存在，
    避免"不舒服"→"舒服"这类否定正则的误抽"""
    for r in RECIPES:
        if term in r["name"] or term in "".join(r["ingredients"]):
            return True
    return False


def extract_constraints(query: str) -> Dict:
    """
    从原始 query 抽取结构化约束（必须在改写前的原文上抽取，改写会丢失否定语义）。

    Returns:
        {
            "exclude_ingredients": [需排除的食材/名称子串],
            "exclude_tags": [需排除的标签子串],
            "max_calories": int | None,
            "prefer_tags": [偏好标签子串],
        }
    """
    exclude_ingredients: List[str] = []
    exclude_tags: List[str] = []
    negated_targets: List[str] = []
    max_calories: Optional[int] = None

    # 1) 显式否定：不X / 别X / 无X / 忌X / 免X / 少X
    for m in _CONSTRAINT_TRIGGER_RE.finditer(query):
        target = m.group(1)
        negated_targets.append(target)
        # 最长 trigger 优先，保证"鸡肉"先于泛化"肉"命中（"猪肉"先于"肉"同理）
        for trigger, (ing_subs, tag_subs) in sorted(_NEGATION_RULES, key=lambda r: -len(r[0])):
            if trigger in target:
                exclude_ingredients.extend(ing_subs)
                exclude_tags.extend(tag_subs)
                break
        else:
            # 未命中已知类别时，按食材原名做子串排除（如"不吃香菜"），
            # 但必须在语料库中真实存在，防止"不舒服"→"舒服"式误抽
            if 1 <= len(target) <= 3 and _corpus_contains(target):
                exclude_ingredients.append(target)

    # 2) 关键词硬约束
    if "清真" in query:
        exclude_ingredients.extend(_HALAL_EXCLUDE)
    if any(kw in query for kw in _LOW_CAL_KEYWORDS):
        max_calories = _LOW_CAL_LIMIT
    if "清淡" in query:
        exclude_tags.extend(_LIGHT_EXCLUDE_TAGS)

    # 3) 偏好标签（首字被否定则跳过，如"不酸"不会触发"酸口"偏好）
    prefer_tags: List[str] = []
    for trigger, prefs in _PREFER_RULES:
        if trigger in query and not any(trigger[0] in t for t in negated_targets):
            prefer_tags.extend(prefs)

    # 去重保持顺序
    constraints = {
        "exclude_ingredients": list(dict.fromkeys(exclude_ingredients)),
        "exclude_tags": list(dict.fromkeys(exclude_tags)),
        "max_calories": max_calories,
        "prefer_tags": list(dict.fromkeys(prefer_tags)),
    }
    if _constraints_active(constraints):
        logger.info(f"[RAG] 约束抽取: '{query}' → {constraints}")
    return constraints


def sanitize_query(query: str) -> str:
    """
    剔除否定短语后的"净化 query"，供向量召回和 Rerank 打分使用。
    否定语义由规则过滤承担（apply_constraints）；embedding/rerank 模型对"不辣"≈"辣"，
    保留否定词会污染语义打分，把"最像被排除项"的候选排到最前。
    净化后过短（<2 字）时回退原 query。
    """
    cleaned = _CONSTRAINT_TRIGGER_RE.sub("", query)
    cleaned = re.sub(r"[，。、！？；\s]+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 2 else query


def _constraints_active(constraints: Dict) -> bool:
    return bool(
        constraints["exclude_ingredients"]
        or constraints["exclude_tags"]
        or constraints["max_calories"] is not None
        or constraints["prefer_tags"]
    )


def _parse_calories(nutrition_str: str) -> Optional[float]:
    """解析 metadata 中的营养字段（dict 的 str 形式），失败返回 None（该条不参与热量过滤）"""
    try:
        data = ast.literal_eval(nutrition_str)
        return float(data["calories"])
    except (ValueError, SyntaxError, KeyError, TypeError):
        return None


def apply_constraints(candidates: List[Dict], constraints: Dict) -> List[Dict]:
    """对召回候选做硬过滤：排除食材/标签、热量上限。过滤为空属预期（走未找到链路）。"""
    filtered = []
    for c in candidates:
        name_ing = c.get("name", "") + " " + c.get("ingredients", "")
        tags = c.get("tags", "")
        if any(kw in name_ing for kw in constraints["exclude_ingredients"]):
            continue
        if any(kw in tags for kw in constraints["exclude_tags"]):
            continue
        if constraints["max_calories"] is not None:
            cal = _parse_calories(c.get("nutrition", ""))
            if cal is not None and cal > constraints["max_calories"]:
                continue
        filtered.append(c)
    return filtered


def apply_prefer_nudge(reranked: List[Dict], prefer_tags: List[str]) -> None:
    """按偏好标签命中数对 rerank 分做有界微调（in-place 重排），仅用于同分区分"""
    if not prefer_tags or not reranked:
        return
    for recipe in reranked:
        text = recipe.get("name", "") + " " + recipe.get("tags", "")
        hits = sum(1 for t in prefer_tags if t in text)
        base = recipe.get("rerank_score", 0) or 0
        recipe["rerank_score"] = round(base + hits * _PREFER_WEIGHT, 4)
    reranked.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)


# ========== Stage 3: Rerank 重排 ==========

def rule_based_rerank(query_info: Dict, candidates: List[Dict], top_n: int = 5) -> List[Dict]:
    """基于规则的重排打分（Fallback 方案）"""
    original_kw = query_info["original"].lower().split()
    rewritten_kw = [kw.lower() for kw in query_info.get("keywords", [])]

    for recipe in candidates:
        name = recipe.get("name", "").lower()
        ingredients = recipe.get("ingredients", "").lower()
        tags = recipe.get("tags", "").lower()
        cuisine = recipe.get("cuisine", "").lower()
        vector_score = recipe.get("score", 0)

        name_hits = sum(1 for kw in original_kw if kw in name)
        ingredient_hits = sum(1 for kw in original_kw if kw in ingredients)
        tag_hits = sum(1 for kw in rewritten_kw if kw in tags)
        cuisine_hits = sum(1 for kw in rewritten_kw if kw in cuisine)

        rerank_score = (
            name_hits * 3.0 +
            ingredient_hits * 2.0 +
            tag_hits * 1.5 +
            cuisine_hits * 1.0 +
            vector_score * 1.0
        )
        recipe["rerank_score"] = round(rerank_score, 4)

    candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
    return candidates[:top_n]


def dashscope_rerank(query: str, candidates: List[Dict], top_n: int = 5) -> tuple[List[Dict], bool]:
    """使用 DashScope Rerank 模型重排（主方案，模型名可配置）

    Returns:
        (重排结果, 是否降级为规则打分)
    """
    try:
        import dashscope

        if not DASHSCOPE_API_KEY:
            logger.warning("[RAG] 未配置 DASHSCOPE_API_KEY，Rerank 降级为规则打分")
            return rule_based_rerank(
                {"original": query, "keywords": query.split()},
                candidates, top_n
            ), True

        os.environ["DASHSCOPE_API_KEY"] = DASHSCOPE_API_KEY

        documents = []
        for c in candidates:
            doc_text = f"{c['name']} {c.get('ingredients', '')} {c.get('tags', '')} {c.get('description', '')}"
            documents.append(doc_text)

        logger.info(f"[RAG] Rerank 模型: {RERANK_MODEL}")
        resp = dashscope.TextReRank.call(
            model=RERANK_MODEL,
            query=query,
            documents=documents,
            top_n=top_n,
            return_documents=False,
            api_key=DASHSCOPE_API_KEY,
        )

        if resp.status_code != 200:
            logger.warning(f"[RAG] DashScope Rerank 返回非200: {resp.status_code}，降级规则打分")
            return rule_based_rerank(
                {"original": query, "keywords": query.split()},
                candidates, top_n
            ), True

        reranked = []
        for item in resp.output["results"]:
            idx = item["index"]
            score = item["relevance_score"]
            recipe = candidates[idx].copy()
            recipe["rerank_score"] = round(float(score), 4)
            reranked.append(recipe)

        logger.info(f"[RAG] DashScope Rerank 重排完成，返回 {len(reranked)} 条")
        return reranked, False

    except Exception as e:
        logger.warning(f"[RAG] DashScope Rerank 失败，降级规则打分: {e}")
        return rule_based_rerank(
            {"original": query, "keywords": query.split()},
            candidates, top_n
        ), True


# ========== 完整 RAG Pipeline ==========

def rag_search(user_query: str, top_k: int = RAG_TOP_K, top_n: int = RAG_TOP_N) -> Dict:
    """
    完整的多阶段 RAG 检索管道

    Pipeline:
    1. Query 改写 → LLM 将口语化 query 改写为检索关键词
    2. 向量召回 → ChromaDB 语义搜索 Top-K（失败降级关键字匹配）
    2.5. 约束硬过滤 → 规则抽取否定/饮食约束，过滤候选（过滤为空返回未找到）
    3. Rerank 重排 → DashScope Rerank 模型重排（失败降级规则打分）
    3.5. 阈值拒答 → top1 rerank 分低于阈值返回空（触发"未找到"链路）
    3.6. 偏好微调 → 命中偏好标签的结果同分小幅上浮
    4. 返回 Top-N 结果 + 全链路执行日志

    Returns:
        {
            "query_info": {"original", "rewritten", "keywords"},
            "results": [Top-N 菜谱],
            "pipeline_log": [各阶段执行状态],
            "fallback_used": bool
        }
    """
    pipeline_log = []
    fallback_used = False

    # ===== Stage 1: Query 改写 =====
    pipeline_log.append("Stage 1: Query 改写...")
    query_info = rewrite_query(user_query)
    pipeline_log.append(f"  → 改写结果: '{query_info['rewritten']}'")

    # ===== Stage 1.5: 约束抽取（必须先于召回）=====
    # 否定词会把向量召回带偏（query 提"猪肉"就召回一堆猪肉菜），
    # 因此有硬约束时：1) 扩大召回池，让过滤在全库范围生效；
    # 2) 召回与 rerank 打分改用剔除否定短语的净化 query
    constraints = extract_constraints(user_query)
    has_hard_constraints = bool(
        constraints["exclude_ingredients"]
        or constraints["exclude_tags"]
        or constraints["max_calories"] is not None
    )
    recall_k = max(top_k, 50) if _constraints_active(constraints) else top_k
    # 有约束时：召回/打分用净化 query（否定词对 embedding/rerank 只有污染）；
    # 无约束时：召回用 LLM 改写结果、rerank 用原始 query（沿用原有行为）
    scoring_query = sanitize_query(user_query) if _constraints_active(constraints) else user_query
    recall_text = scoring_query if _constraints_active(constraints) else query_info["rewritten"]
    if scoring_query != user_query:
        pipeline_log.append(f"  → 净化打分 query: '{scoring_query}'")

    # ===== Stage 2: 向量召回 =====
    pipeline_log.append(f"Stage 2: 向量召回 (top_k={recall_k})...")
    candidates = vector_recall(recall_text, top_k=recall_k)

    if not candidates:
        pipeline_log.append("  ⚠️ 向量召回失败，降级为关键字匹配")
        fallback_used = True
        candidates = keyword_recall(query_info["keywords"], top_k=recall_k)
    else:
        pipeline_log.append(f"  → 召回 {len(candidates)} 条候选")

    if not candidates:
        pipeline_log.append("  ❌ 无检索结果")
        return {
            "query_info": query_info,
            "results": [],
            "pipeline_log": pipeline_log,
            "fallback_used": fallback_used,
        }

    # ===== Stage 2.5: 约束硬过滤（否定/饮食约束，向量无法表达，规则抽取后过滤）=====
    # 过滤后候选全量送 rerank，不提前截断 top_k：目标菜向量序可能排在 11~50 位
    if has_hard_constraints:
        before = len(candidates)
        candidates = apply_constraints(candidates, constraints)
        pipeline_log.append(
            f"Stage 2.5: 约束硬过滤（排除食材{constraints['exclude_ingredients']} "
            f"排除标签{constraints['exclude_tags']} 热量上限{constraints['max_calories']}）"
            f"→ {before} 条剩 {len(candidates)} 条"
        )
        if not candidates:
            pipeline_log.append("  ❌ 约束过滤后无候选，按未找到处理")
            logger.info(f"[RAG] 约束过滤后无候选 | Query: '{user_query}'")
            return {
                "query_info": query_info,
                "results": [],
                "pipeline_log": pipeline_log,
                "fallback_used": fallback_used,
            }

    # ===== Stage 3: Rerank 重排 =====
    # 有偏好标签时取全量重排，让偏好微调能把 top_n 之外的目标菜浮上来，再截断
    rerank_top_n = len(candidates) if constraints["prefer_tags"] else top_n
    pipeline_log.append(f"Stage 3: Rerank 重排 (top_n={rerank_top_n})...")
    rerank_fellback = True  # 默认按降级处理，仅 DashScope 真实返回后置 False
    try:
        reranked, rerank_fellback = dashscope_rerank(scoring_query, candidates, top_n=rerank_top_n)
        if rerank_fellback:
            pipeline_log.append("  ⚠️ Rerank 不可用，降级为规则打分")
            fallback_used = True
        pipeline_log.append(f"  → 重排完成，返回 {len(reranked)} 条")
    except Exception as e:
        pipeline_log.append(f"  ⚠️ Rerank 失败，降级规则打分: {e}")
        fallback_used = True
        reranked = rule_based_rerank(query_info, candidates, top_n=rerank_top_n)
        pipeline_log.append(f"  → 规则打分完成，返回 {len(reranked)} 条")

    # ===== Stage 3.5: 偏好标签同分微调（先于阈值判定，让命中偏好的边界正例有机会过阈）=====
    if constraints["prefer_tags"]:
        apply_prefer_nudge(reranked, constraints["prefer_tags"])
        reranked = reranked[:top_n]
        pipeline_log.append(f"Stage 3.5: 偏好微调 {constraints['prefer_tags']}")

    # ===== Stage 3.6: 相关性阈值拒答（仅 DashScope 分数可跨 query 比较，规则分不适用）=====
    # 评测依据：负例 top1 rerank 分全部 ≤0.179，正例最低 0.173，阈值 0.16 可拦截 9/10 负例
    # 硬约束查询豁免：幸存者已被规则过滤保证约束合规，低 rerank 分多因净化后 query 较短，不应拒答
    if not rerank_fellback and reranked and not has_hard_constraints:
        top1_score = reranked[0].get("rerank_score", 0)
        if top1_score < RAG_RERANK_THRESHOLD:
            pipeline_log.append(
                f"Stage 3.6: top1 相关性 {top1_score} < 阈值 {RAG_RERANK_THRESHOLD}，判定知识库无相关菜谱"
            )
            logger.info(f"[RAG] 阈值拒答 | Query: '{user_query}' | top1 rerank 分: {top1_score}")
            return {
                "query_info": query_info,
                "results": [],
                "pipeline_log": pipeline_log,
                "fallback_used": fallback_used,
            }

    logger.info(f"[RAG] Pipeline 完成 | Query: '{user_query}' | 结果: {len(reranked)} 条 | Fallback: {fallback_used}")
    return {
        "query_info": query_info,
        "results": reranked,
        "pipeline_log": pipeline_log,
        "fallback_used": fallback_used,
    }