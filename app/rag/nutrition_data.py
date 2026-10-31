"""
营养成分数据访问层

数据文件：app/rag/ingredients.json
- 来源：USDA FoodData Central SR Legacy（Public Domain）
- 每个食材提供每 100g 的热量/蛋白质/脂肪/碳水/纤维，以及常见单位换算（个/勺等）
"""

import json
import re
from pathlib import Path
from typing import Optional

from app.common.logger import logger

_DATA_FILE = Path(__file__).with_name("ingredients.json")

# 常见同义名 -> 数据文件中的规范名（仅在规范名存在时生效）
_ALIASES = {
    "酱油": "生抽",
    "蒜": "大蒜",
    "蒜瓣": "大蒜",
    "蒜头": "大蒜",
    "生姜": "姜",
    "姜片": "姜",
    "姜末": "姜",
    "小葱": "葱",
    "香葱": "葱",
    "大葱": "葱",
    "食盐": "盐",
    "食用盐": "盐",
    "白砂糖": "白糖",
    "糖": "白糖",
    "植物油": "食用油",
    "花生油": "食用油",
    "菜籽油": "食用油",
    "玉米油": "食用油",
    "橄榄油": "食用油",
    "香油": "芝麻油",
    "麻油": "芝麻油",
    "老抽": "老抽",
    "生抽": "生抽",
    "番茄": "番茄",
    "西红柿": "番茄",
    "青辣椒": "青椒",
    "小米椒": "小米辣",
    "内脂豆腐": "豆腐",
    "嫩豆腐": "豆腐",
    "老豆腐": "豆腐",
    "鸡肉": "鸡胸肉",
    "鸡腿肉": "鸡腿",
    "猪肉馅": "猪肉",
    "肉末": "猪肉",
    "肉糜": "猪肉",
    "清水": "水",
    "开水": "水",
    "冷水": "水",
    "饮用水": "水",
}

# 单位缺省重量（g）；数据文件里 default_unit_grams 优先
_DEFAULT_UNIT_GRAMS = {
    "个": 100.0, "只": 100.0, "枚": 50.0, "根": 30.0, "片": 5.0,
    "瓣": 5.0, "颗": 2.0, "块": 50.0, "盒": 300.0, "袋": 100.0,
    "把": 20.0, "碗": 250.0, "杯": 240.0, "滴": 0.05,
    "勺": 15.0, "小勺": 5.0, "大勺": 15.0, "汤匙": 15.0, "茶匙": 5.0,
    "tsp": 5.0, "tbsp": 15.0,
}

# 重量/体积单位 -> 克
_MASS_UNITS = {"g": 1.0, "克": 1.0, "kg": 1000.0, "千克": 1000.0, "斤": 500.0, "两": 50.0}
_VOLUME_UNITS = {"ml": 1.0, "毫升": 1.0, "l": 1000.0, "升": 1000.0}

_NUM_WORDS = {
    "半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

UNIT_PATTERN = (
    "g|克|kg|千克|ml|毫升|mL|ML|L|升|斤|两|勺|小勺|大勺|汤匙|茶匙|tsp|tbsp|"
    "片|瓣|根|颗|只|个|枚|块|盒|袋|把|碗|杯|滴"
)
_AMOUNT_RE = re.compile(
    rf"^(?P<amount>\d+(?:\.\d+)?(?:\s*[-~]\s*\d+(?:\.\d+)?)?|[半一二两三四五六七八九十])\s*"
    rf"(?P<unit>{UNIT_PATTERN})\s*(?P<name>.*)$"
)


def _load():
    if not _DATA_FILE.exists():
        raise RuntimeError(f"营养成分数据文件不存在: {_DATA_FILE}")
    payload = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    ingredients = payload.get("ingredients") or {}
    if not ingredients:
        raise ValueError(f"营养成分数据文件为空: {_DATA_FILE}")
    return ingredients, payload.get("meta") or {}


INGREDIENTS, META = _load()

logger.info(f"[Nutrition] 已加载 {len(INGREDIENTS)} 种食材营养成分，版本: {META.get('version', 'unknown')}")


def find_ingredient(name: str) -> Optional[dict]:
    """按名称查找食材（精确 -> 同义名 -> 包含匹配，优先更长的键）"""
    name = name.strip()
    if not name:
        return None
    if name in INGREDIENTS:
        return INGREDIENTS[name]
    alias = _ALIASES.get(name)
    if alias and alias in INGREDIENTS:
        return INGREDIENTS[alias]

    best_key = None
    for key in INGREDIENTS:
        if key in name or name in key:
            if best_key is None or len(key) > len(best_key):
                best_key = key
    return INGREDIENTS[best_key] if best_key else None


def convert_amount(amount: str, unit: str, entry: Optional[dict]) -> Optional[float]:
    """把「2个/1勺/200g」换算为克；无法换算返回 None"""
    unit_l = unit.lower()
    value = None
    if amount in _NUM_WORDS:
        value = _NUM_WORDS[amount]
    else:
        parts = re.split(r"[-~]", amount)
        try:
            nums = [float(p.strip()) for p in parts if p.strip()]
        except ValueError:
            return None
        if nums:
            value = sum(nums) / len(nums)

    if value is None:
        return None

    if unit in _MASS_UNITS:
        return value * _MASS_UNITS[unit]
    if unit_l in _VOLUME_UNITS:
        return value * _VOLUME_UNITS[unit_l]

    if entry:
        per_unit = (entry.get("default_unit_grams") or {}).get(unit) or (entry.get("default_unit_grams") or {}).get(unit_l)
        if per_unit:
            return value * float(per_unit)
    if unit in _DEFAULT_UNIT_GRAMS:
        return value * _DEFAULT_UNIT_GRAMS[unit]
    return None


def parse_ingredient_text(item: str) -> dict:
    """解析「鸡胸肉200g」「2个鸡蛋」「鸡蛋 2 个」等文本"""
    item = item.strip().strip(",，;；")
    match = _AMOUNT_RE.match(item)
    if match:
        amount, unit, name = match.group("amount"), match.group("unit"), match.group("name")
    else:
        # 名称在前：鸡蛋2个 / 食用油 10
        match = re.match(
            rf"(?P<name>.*?)\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{UNIT_PATTERN}|\b)\s*$",
            item,
        )
        if match and match.group("name"):
            amount, unit, name = match.group("amount"), match.group("unit") or "g", match.group("name")
        else:
            match = re.match(r"(?P<name>.*?)\s*(?P<amount>\d+(?:\.\d+)?)\s*$", item)
            if match and match.group("name"):
                amount, unit, name = match.group("amount"), "g", match.group("name")
            else:
                return {"name": item, "weight": 100.0, "raw": item}

    name = name.strip(" :：,，。")
    entry = find_ingredient(name)
    grams = convert_amount(amount, unit, entry)
    if grams is None:
        grams = 100.0
    return {
        "name": name,
        "weight": round(grams, 1),
        "amount": f"{amount}{unit}",
        "raw": item,
        "matched": entry is not None,
    }
