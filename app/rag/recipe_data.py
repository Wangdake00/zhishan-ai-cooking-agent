"""
RAG 食谱知识库加载器

数据文件：app/rag/recipes.json
- 菜谱来源：HowToCook 程序员做饭指南（Unlicense / 公有领域）
- 营养来源：USDA FoodData Central SR Legacy（Public Domain），按食材克重重算
"""

import json
from pathlib import Path

from app.common.logger import logger

_DATA_FILE = Path(__file__).with_name("recipes.json")

_REQUIRED_FIELDS = (
    "id",
    "name",
    "cuisine",
    "difficulty",
    "cooking_time",
    "ingredients",
    "steps",
    "nutrition_per_serving",
    "tags",
    "description",
)


def _load():
    if not _DATA_FILE.exists():
        raise RuntimeError(f"菜谱数据文件不存在: {_DATA_FILE}")

    payload = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    recipes = payload.get("recipes") or []
    if not recipes:
        raise ValueError(f"菜谱数据文件为空: {_DATA_FILE}")

    for index, recipe in enumerate(recipes):
        missing = [field for field in _REQUIRED_FIELDS if field not in recipe]
        if missing:
            raise ValueError(f"菜谱数据第 {index + 1} 条缺少字段: {missing}")

    return recipes, payload.get("meta") or {}


RECIPES, META = _load()

logger.info(f"[RAG] 已加载 {len(RECIPES)} 条菜谱，数据版本: {META.get('version', 'unknown')}")


def get_recipe_by_id(recipe_id: str):
    """根据 ID 获取菜谱"""
    for r in RECIPES:
        if r["id"] == recipe_id:
            return r
    return None


def get_all_tags():
    """获取所有标签（去重）"""
    tags = set()
    for r in RECIPES:
        tags.update(r.get("tags", []))
    return sorted(tags)


def get_all_cuisines():
    """获取所有菜系（去重）"""
    return sorted(set(r["cuisine"] for r in RECIPES))
