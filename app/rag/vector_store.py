"""
RAG 向量存储模块
- 使用 ChromaDB 作为本地向量数据库
- 使用 DashScope text-embedding-v3 作为 Embedding 模型（通过 OpenAI 兼容接口）
- 提供 52 条菜谱的向量化存储和语义检索能力
- 所有模型名称/路径/参数均可通过 .env 环境变量配置
"""

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from app.common.logger import logger
from app.rag.recipe_data import RECIPES

# ========== 从环境变量读取配置 ==========

# ChromaDB 持久化存储路径（相对于项目根目录）
_CHROMA_DB_DIR = os.getenv("CHROMA_DB_DIR", "db/chroma_data")
CHROMA_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    *_CHROMA_DB_DIR.split("/")
)

# ChromaDB 集合名称
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "recipes")

# Embedding 模型名称
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")

# Embedding 批量大小（DashScope 限制每次最多 10 条）
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "10"))

# DashScope API 配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

# 全局单例（懒加载）
_client: Optional[chromadb.ClientAPI] = None
_collection: Optional[chromadb.Collection] = None


def _get_embedding_function() -> OpenAIEmbeddingFunction:
    """创建 DashScope Embedding 函数（走 OpenAI 兼容接口）"""
    # ChromaDB 的 OpenAIEmbeddingFunction 要求 CHROMA_OPENAI_API_KEY 环境变量必须存在
    # 即使传了 api_key 参数，它仍然会检查这个环境变量
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
    3. 如果集合为空，自动灌入 52 条菜谱数据
    """
    global _client, _collection

    if _collection is not None:
        return _collection

    try:
        # 1. 创建持久化客户端
        os.makedirs(CHROMA_DB_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        logger.info(f"[RAG] ChromaDB 持久化路径: {CHROMA_DB_PATH}")
        logger.info(f"[RAG] 集合名称: {COLLECTION_NAME}")

        # 2. 获取或创建集合（绑定 DashScope Embedding）
        ef = _get_embedding_function()
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
            metadata={"description": "智膳 AI 菜谱知识库 - 52 条常见菜谱"},
        )

        # 3. 如果集合为空，灌入菜谱数据
        if _collection.count() == 0:
            _ingest_recipes(_collection)
        else:
            logger.info(f"[RAG] 向量库已有 {_collection.count()} 条菜谱，跳过灌入")

        return _collection

    except Exception as e:
        logger.error(f"[RAG] ChromaDB 初始化失败: {e}")
        raise


def _ingest_recipes(collection: chromadb.Collection):
    """将 52 条菜谱灌入 ChromaDB 向量库（分批灌入，每批不超过 EMBEDDING_BATCH_SIZE）"""
    logger.info(f"[RAG] 开始灌入 {len(RECIPES)} 条菜谱到 ChromaDB (模型: {EMBEDDING_MODEL}, 批大小: {EMBEDDING_BATCH_SIZE})...")

    documents = []
    metadatas = []
    ids = []

    for recipe in RECIPES:
        # 构建用于向量化检索的文档内容（菜名 + 食材 + 标签 + 简介 + 菜系）
        doc_text = (
            f"菜名：{recipe['name']}\n"
            f"菜系：{recipe['cuisine']}\n"
            f"难度：{recipe['difficulty']}，烹饪时间：{recipe['cooking_time']}\n"
            f"食材：{', '.join(recipe['ingredients'])}\n"
            f"标签：{', '.join(recipe['tags'])}\n"
            f"简介：{recipe['description']}"
        )
        documents.append(doc_text)

        # metadata 存储所有结构化字段（用于 Rerank 和过滤）
        metadatas.append({
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
        })
        ids.append(recipe["id"])

    # 分批灌入（DashScope Embedding API 限制每次最多 10 条）
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
    """重置向量库（删除并重建，用于调试）"""
    global _client, _collection
    if _client is not None:
        try:
            _client.delete_collection(COLLECTION_NAME)
            logger.info("[RAG] 已删除旧集合")
        except Exception:
            pass
    _collection = None
    _client = None
    return get_collection()
