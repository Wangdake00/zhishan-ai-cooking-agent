"""
RAG 向量存储模块
- 使用 ChromaDB 作为本地向量数据库
- 使用 DashScope Embedding 模型（通过 OpenAI 兼容接口），模型可配置
- 提供菜谱知识库的向量化存储和语义检索能力（条数以 recipes.json 为准）
- 所有模型名称/路径/参数均可通过 settings 配置
"""

import os
from typing import Optional

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from app.common.logger import logger
from app.core.config import settings
from app.rag.recipe_data import RECIPES, META

# ========== 从统一配置读取 ==========

# ChromaDB 持久化存储路径
CHROMA_DB_PATH = str(settings.chroma_db_path)

# ChromaDB 集合名称
COLLECTION_NAME = settings.chroma_collection_name

# Embedding 模型名称
EMBEDDING_MODEL = settings.embedding_model

# Embedding 批量大小
EMBEDDING_BATCH_SIZE = settings.embedding_batch_size

# 知识库数据版本（recipes.json 的 meta.version）：版本变化时自动重建集合
RECIPE_VERSION = str(META.get("version", "unknown"))
RECIPE_SOURCE = (META.get("recipe_source") or {}).get("name", "")
NUTRITION_SOURCE = (META.get("nutrition_source") or {}).get("name", "")

# DashScope API 配置
DASHSCOPE_API_KEY = settings.dashscope_api_key
DASHSCOPE_BASE_URL = settings.dashscope_base_url

# 全局单例（懒加载）
_client: Optional[chromadb.ClientAPI] = None
_collection: Optional[chromadb.Collection] = None


def _get_embedding_function() -> OpenAIEmbeddingFunction:
    """创建 DashScope Embedding 函数（走 OpenAI 兼容接口）"""
    os.environ["CHROMA_OPENAI_API_KEY"] = DASHSCOPE_API_KEY

    logger.info(f"[RAG] Embedding 模型: {EMBEDDING_MODEL} | API: {DASHSCOPE_BASE_URL}")
    return OpenAIEmbeddingFunction(
        api_key=DASHSCOPE_API_KEY,
        api_base=DASHSCOPE_BASE_URL,
        model_name=EMBEDDING_MODEL,
    )


def get_collection() -> chromadb.Collection:
    """
    获取 ChromaDB recipes 集合（懒加载 + 自动初始化）
    首次调用时：
    1. 创建持久化 ChromaDB 客户端
    2. 获取或创建 recipes 集合（绑定 DashScope Embedding）
    3. 如果集合为空，自动灌入全部菜谱数据（RECIPES，条数以 recipes.json 为准）
    """
    global _client, _collection

    if _collection is not None:
        return _collection

    try:
        os.makedirs(CHROMA_DB_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        logger.info(f"[RAG] ChromaDB 持久化路径: {CHROMA_DB_PATH}")
        logger.info(f"[RAG] 集合名称: {COLLECTION_NAME}")

        ef = _get_embedding_function()
        # 显式指定 cosine 距离空间（ChromaDB 默认为 l2），
        # 使 vector_recall 中 similarity = 1 - distance/2 的换算语义正确
        metadata = {
            "hnsw:space": "cosine",
            "description": f"{settings.app_name} 菜谱知识库 - {len(RECIPES)} 道菜谱",
            "recipe_version": RECIPE_VERSION,
        }
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
            metadata=metadata,
        )

        count = _collection.count()
        stored_version = (_collection.metadata or {}).get("recipe_version")
        if count == 0:
            _ingest_recipes(_collection)
        elif stored_version != RECIPE_VERSION:
            # 数据文件升级后旧集合必须重建，否则会一直命中过期菜谱
            logger.info(
                f"[RAG] 知识库版本变化 {stored_version} -> {RECIPE_VERSION}，重建集合"
            )
            _client.delete_collection(COLLECTION_NAME)
            _collection = _client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=ef,
                metadata=metadata,
            )
            _ingest_recipes(_collection)
        else:
            logger.info(f"[RAG] 向量库已有 {count} 条菜谱，版本一致，跳过灌入")

        return _collection

    except Exception as e:
        logger.error(f"[RAG] ChromaDB 初始化失败: {e}")
        raise


def _ingest_recipes(collection: chromadb.Collection):
    """将全部菜谱（RECIPES）灌入 ChromaDB 向量库（分批灌入）"""
    logger.info(f"[RAG] 开始灌入 {len(RECIPES)} 条菜谱到 ChromaDB (模型: {EMBEDDING_MODEL}, 批大小: {EMBEDDING_BATCH_SIZE})...")

    documents = []
    metadatas = []
    ids = []

    for recipe in RECIPES:
        source = recipe.get("source") or {}
        nutrition = recipe.get("nutrition_per_serving") or {}
        doc_text = (
            f"菜名：{recipe['name']}\n"
            f"菜系：{recipe['cuisine']}\n"
            f"难度：{recipe['difficulty']}，烹饪时间：{recipe['cooking_time']}\n"
            f"食材：{', '.join(recipe['ingredients'])}\n"
            f"标签：{', '.join(recipe['tags'])}\n"
            f"简介：{recipe['description']}"
        )
        documents.append(doc_text)

        metadatas.append({
            "id": recipe["id"],
            "name": recipe["name"],
            "cuisine": recipe["cuisine"],
            "difficulty": recipe["difficulty"],
            "cooking_time": recipe["cooking_time"],
            "ingredients": ", ".join(recipe["ingredients"]),
            "steps": recipe["steps"],
            "nutrition": str(nutrition),
            "calories": float(nutrition.get("calories", 0)),
            "tags": ", ".join(recipe["tags"]),
            "description": recipe["description"],
            "source_name": str(source.get("name", RECIPE_SOURCE)),
            "source_url": str(source.get("url", "")),
            "source_license": str(source.get("license", "")),
            "nutrition_source": str(NUTRITION_SOURCE),
        })
        ids.append(recipe["id"])

    total = len(documents)
    for start in range(0, total, EMBEDDING_BATCH_SIZE):
        end = start + EMBEDDING_BATCH_SIZE
        batch_docs = documents[start:end]
        batch_metas = metadatas[start:end]
        batch_ids = ids[start:end]
        collection.add(
            documents=batch_docs,
            metadatas=batch_metas,
            ids=batch_ids,
        )
        logger.info(f"[RAG] 灌入批次 {start // EMBEDDING_BATCH_SIZE + 1}: {len(batch_docs)} 条 (进度: {end}/{total})")

    logger.info(f"[RAG] ✅ 成功灌入 {total} 条菜谱到 ChromaDB")


def reset_collection():
    """重置向量库（删除并重建，用于调试）

    注意：需先创建客户端再删集合，否则在未初始化过的新进程里会跳过删除，
    导致 get_or_create 返回旧集合并"跳过灌入"。
    """
    global _client, _collection
    try:
        client = _client or chromadb.PersistentClient(path=CHROMA_DB_PATH)
        client.delete_collection(COLLECTION_NAME)
        logger.info("[RAG] 已删除旧集合")
    except Exception:
        pass
    _collection = None
    _client = None
    return get_collection()