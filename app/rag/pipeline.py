"""
多阶段 RAG 检索管道
Pipeline: Query 改写 → 向量召回 → Rerank 重排 → Fallback 降级

设计理念：
- 每个阶段都有独立的 Fallback 降级方案，保证全链路可用性
- Query 改写失败 → 用原始 query
- 向量召回失败 → 降级为关键字匹配
- Rerank 失败 → 降级为规则打分
- 所有阶段异常不阻断主流程，记录日志后继续
"""

import os
import json
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
    """使用 DashScope gte-rerank 模型重排（主方案）

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
    3. Rerank 重排 → DashScope gte-rerank 重排（失败降级规则打分）
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

    # ===== Stage 2: 向量召回 =====
    pipeline_log.append(f"Stage 2: 向量召回 (top_k={top_k})...")
    candidates = vector_recall(query_info["rewritten"], top_k=top_k)

    if not candidates:
        pipeline_log.append("  ⚠️ 向量召回失败，降级为关键字匹配")
        fallback_used = True
        candidates = keyword_recall(query_info["keywords"], top_k=top_k)
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

    # ===== Stage 3: Rerank 重排 =====
    pipeline_log.append(f"Stage 3: Rerank 重排 (top_n={top_n})...")
    try:
        reranked, rerank_fellback = dashscope_rerank(user_query, candidates, top_n=top_n)
        if rerank_fellback:
            pipeline_log.append("  ⚠️ Rerank 不可用，降级为规则打分")
            fallback_used = True
        pipeline_log.append(f"  → 重排完成，返回 {len(reranked)} 条")
    except Exception as e:
        pipeline_log.append(f"  ⚠️ Rerank 失败，降级规则打分: {e}")
        fallback_used = True
        reranked = rule_based_rerank(query_info, candidates, top_n=top_n)
        pipeline_log.append(f"  → 规则打分完成，返回 {len(reranked)} 条")

    logger.info(f"[RAG] Pipeline 完成 | Query: '{user_query}' | 结果: {len(reranked)} 条 | Fallback: {fallback_used}")
    return {
        "query_info": query_info,
        "results": reranked,
        "pipeline_log": pipeline_log,
        "fallback_used": fallback_used,
    }