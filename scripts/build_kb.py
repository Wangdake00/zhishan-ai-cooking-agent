# -*- coding: utf-8 -*-
"""知识库构建管线：菜谱源 → staging.jsonl → app/rag/recipes.json + ingredients.json

数据源：
- 菜谱：HowToCook 程序员做饭指南（Unlicense）
- 营养：USDA FoodData Central SR Legacy（Public Domain）

用法：
    # 完整管线（解析 HowToCook + 构建）
    python scripts/build_kb.py --h2c-dir <HowToCook/dishes> --usda-dir <SR_Legacy_CSV目录>
    # 复用已有 staging，仅重建
    python scripts/build_kb.py --stage build --usda-dir <SR_Legacy_CSV目录>
    # staging 与 ITEMS 均未变更时（如仅调整派生标签），可无 --usda-dir，
    # 自动回读既有 app/rag/ingredients.json 作为营养缓存
    python scripts/build_kb.py --stage build
"""
import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGING = REPO_ROOT / "scripts" / "staging" / "recipes.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "app" / "rag"

VERSION = "2026.11.01.1"
RECIPE_SOURCE = {
    "name": "HowToCook 程序员做饭指南",
    "url": "https://github.com/Anduin2017/HowToCook",
    "license": "Unlicense",
}
NUTRITION_SOURCE = {
    "name": "USDA FoodData Central SR Legacy",
    "url": "https://fdc.nal.usda.gov/",
    "license": "Public Domain",
    "method": "食材可食部克重 × 每 100g 成分值累加（生鲜原料）",
}

# (中文规范名, fdc_id, 英文描述, 分类, 默认单位克重)
ITEMS = [
    # ---- 畜肉 ----
    ("猪肉", 167818, "Pork, fresh, loin, whole, separable lean and fat, raw", "畜肉", {}),
    ("五花肉", 167812, "Pork, fresh, belly, raw", "畜肉", {"块": 50}),
    ("瘦肉", 168249, "Pork, fresh, loin, tenderloin, separable lean only, raw", "畜肉", {}),
    ("里脊肉", 168249, "Pork, fresh, loin, tenderloin, separable lean only, raw", "畜肉", {}),
    ("猪肉末", 167818, "Pork, fresh, loin, whole, separable lean and fat, raw", "畜肉", {}),
    ("排骨", 167853, "Pork, fresh, spareribs, separable lean and fat, raw", "畜肉", {"根": 100, "块": 30}),
    ("肋排", 167853, "Pork, fresh, spareribs, separable lean and fat, raw", "畜肉", {"根": 100, "块": 30}),
    ("猪蹄", 167818, "Pork, fresh, loin, whole, separable lean and fat, raw", "畜肉", {}, "以猪里脊近似"),
    ("猪油", 171401, "Lard", "油脂", {"勺": 15}),
    ("培根", 168277, "Pork, cured, bacon, unprepared", "加工肉", {"片": 15}),
    ("火腿", 167875, "Pork, cured, ham, whole, separable lean and fat, unheated", "加工肉", {"片": 15}),
    ("火腿肠", 174587, "Luncheon sausage, pork and beef", "加工肉", {"根": 40}),
    ("香肠", 174579, "Sausage, pork and beef, fresh, cooked", "加工肉", {"根": 50}),
    ("午餐肉", 174571, "Luncheon meat, pork, canned", "加工肉", {"片": 20}),
    ("牛肉", 169483, "Beef, composite of trimmed retail cuts, separable lean and fat, trimmed to 1/8\" fat, all grades, raw", "畜肉", {}),
    ("牛腩", 169483, "Beef, composite of trimmed retail cuts, separable lean and fat, trimmed to 1/8\" fat, all grades, raw", "畜肉", {}),
    ("羊肉", 172479, "Lamb, composite of trimmed retail cuts, separable lean and fat, trimmed to 1/4\" fat, choice, raw", "畜肉", {}),
    ("羊排", 172479, "Lamb, composite of trimmed retail cuts, separable lean and fat, trimmed to 1/4\" fat, choice, raw", "畜肉", {"根": 100}),
    ("兔肉", 172521, "Game meat, rabbit, domesticated, composite of cuts, raw", "畜肉", {}),
    # ---- 禽蛋 ----
    ("鸡胸肉", 171077, "Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw", "禽肉", {}),
    ("鸡腿", 172378, "Chicken, broilers or fryers, leg, meat and skin, raw", "禽肉", {"个": 200, "只": 200}),
    ("鸡腿肉", 172378, "Chicken, broilers or fryers, leg, meat and skin, raw", "禽肉", {}),
    ("鸡翅", 172390, "Chicken, broilers or fryers, wing, meat and skin, raw", "禽肉", {"个": 60, "只": 60}),
    ("鸡肉", 171447, "Chicken, broilers or fryers, meat and skin, raw", "禽肉", {"只": 1200}),
    ("鸡爪", 171119, "Chicken, feet, boiled", "禽肉", {"个": 30, "只": 30}),
    ("鸡蛋", 171287, "Egg, whole, raw, fresh", "蛋类", {"个": 50, "只": 50, "枚": 50, "颗": 50}),
    ("蛋黄", 172184, "Egg, yolk, raw, fresh", "蛋类", {"个": 17}),
    ("蛋清", 172183, "Egg, white, raw, fresh", "蛋类", {"个": 33}),
    ("皮蛋", 172189, "Egg, duck, whole, fresh, raw", "蛋类", {"个": 60}),
    ("鸭肉", 172408, "Duck, domesticated, meat and skin, raw", "禽肉", {"只": 1200}),
    # ---- 水产 ----
    ("虾", 174210, "Crustaceans, shrimp, mixed species, raw", "水产", {"只": 10, "个": 10}),
    ("虾皮", 174210, "Crustaceans, shrimp, mixed species, raw", "水产", {}, "以鲜虾近似"),
    ("小龙虾", 174206, "Crustaceans, crayfish, mixed species, wild, raw", "水产", {"只": 20}),
    ("蟹", 174204, "Crustaceans, crab, blue, raw", "水产", {"只": 300}),
    ("鱼", 174191, "Fish, cod, Pacific, raw", "水产", {"条": 500}),
    ("鲤鱼", 171952, "Fish, carp, raw", "水产", {"条": 500}),
    ("草鱼", 171952, "Fish, carp, raw", "水产", {"条": 500}),
    ("鳕鱼", 174191, "Fish, cod, Pacific, raw", "水产", {"片": 120}),
    ("巴沙鱼", 174191, "Fish, cod, Pacific, raw", "水产", {"片": 120}),
    ("三文鱼", 175167, "Fish, salmon, Atlantic, farmed, raw", "水产", {"片": 120}),
    ("带鱼", 174191, "Fish, cod, Pacific, raw", "水产", {"条": 300}, "以鳕鱼近似"),
    ("鳝丝", 174193, "Fish, eel, mixed species, raw", "水产", {}),
    ("鲈鱼", 171948, "Fish, bass, striped, raw", "水产", {"条": 500}),
    ("蛤蜊", 174214, "Mollusks, clam, mixed species, raw", "水产", {"个": 10, "只": 10}),
    ("鱿鱼", 174223, "Mollusks, squid, mixed species, raw", "水产", {"条": 200}),
    ("扇贝", 174220, "Mollusks, scallop, mixed species, raw", "水产", {"个": 15, "只": 15}),
    ("牡蛎", 171978, "Mollusks, oyster, eastern, wild, raw", "水产", {"个": 15}),
    # ---- 豆制品 ----
    ("豆腐", 172448, "Tofu, firm, prepared with calcium sulfate and magnesium chloride (nigari)", "豆制品", {"块": 50, "盒": 300}),
    ("嫩豆腐", 172449, "Tofu, soft, prepared with calcium sulfate and magnesium chloride (nigari)", "豆制品", {"块": 50, "盒": 300}),
    ("豆干", 172448, "Tofu, firm, prepared with calcium sulfate and magnesium chloride (nigari)", "豆制品", {"块": 50}, "以老豆腐近似"),
    ("腐竹", 172450, "Tofu, dried-frozen (koyadofu)", "豆制品", {}),
    ("豆浆", 172446, "Soymilk, original and vanilla, unfortified", "豆制品", {"杯": 240}),
    # ---- 蔬菜 ----
    ("番茄", 170457, "Tomatoes, red, ripe, raw, year round average", "蔬菜", {"个": 150, "只": 150}),
    ("西红柿", 170457, "Tomatoes, red, ripe, raw, year round average", "蔬菜", {"个": 150, "只": 150}),
    ("洋葱", 170000, "Onions, raw", "蔬菜", {"个": 150}),
    ("大蒜", 169230, "Garlic, raw", "蔬菜", {"瓣": 5, "颗": 30, "粒": 5, "头": 30, "个": 5}),
    ("姜", 169231, "Ginger root, raw", "蔬菜", {"片": 5, "块": 20, "个": 20}),
    ("葱", 170005, "Onions, spring or scallions (includes tops and bulb), raw", "蔬菜", {"根": 30, "棵": 30, "把": 100, "圈": 2, "个": 30}),
    ("香菜", 169997, "Coriander (cilantro) leaves, raw", "蔬菜", {"根": 20, "把": 100, "个": 20, "颗": 10}),
    ("芹菜", 169988, "Celery, raw", "蔬菜", {"根": 50}),
    ("青椒", 170427, "Peppers, sweet, green, raw", "蔬菜", {"个": 50, "根": 50, "条": 50}),
    ("红椒", 170108, "Peppers, sweet, red, raw", "蔬菜", {"个": 50, "条": 50}),
    ("辣椒", 170106, "Peppers, hot chili, red, raw", "蔬菜", {"个": 10, "根": 10, "条": 10}),
    ("小米辣", 170106, "Peppers, hot chili, red, raw", "蔬菜", {"个": 5, "根": 5, "只": 5, "条": 5}),
    ("干辣椒", 170106, "Peppers, hot chili, red, raw", "蔬菜", {"个": 2, "根": 2, "只": 2, "支": 2, "条": 2}),
    ("土豆", 170026, "Potatoes, flesh and skin, raw", "蔬菜", {"个": 150, "块": 30}),
    ("胡萝卜", 170393, "Carrots, raw", "蔬菜", {"根": 100, "个": 100}),
    ("白萝卜", 168451, "Radishes, oriental, raw", "蔬菜", {"根": 300, "个": 300}),
    ("黄瓜", 168409, "Cucumber, with peel, raw", "蔬菜", {"根": 200, "个": 200}),
    ("茄子", 169228, "Eggplant, raw", "蔬菜", {"个": 250, "根": 250}),
    ("白菜", 169975, "Cabbage, raw", "蔬菜", {"颗": 300, "棵": 300}),
    ("酸菜", 169279, "Sauerkraut, canned, solids and liquids", "蔬菜", {}, "东北酸菜以德国酸菜近似"),
    ("娃娃菜", 169975, "Cabbage, raw", "蔬菜", {"颗": 150, "个": 150}),
    ("包菜", 169975, "Cabbage, raw", "蔬菜", {"颗": 300}),
    ("圆白菜", 169975, "Cabbage, raw", "蔬菜", {"颗": 300}),
    ("生菜", 169248, "Lettuce, iceberg (includes crisphead types), raw", "蔬菜", {"颗": 200}),
    ("青菜", 170390, "Cabbage, chinese (pak-choi), raw", "蔬菜", {"颗": 200, "把": 100}),
    ("油菜", 170390, "Cabbage, chinese (pak-choi), raw", "蔬菜", {"颗": 200}),
    ("菠菜", 168462, "Spinach, raw", "蔬菜", {"把": 100}),
    ("西兰花", 170379, "Broccoli, raw", "蔬菜", {"颗": 200}),
    ("花菜", 169986, "Cauliflower, raw", "蔬菜", {"颗": 300}),
    ("菜花", 169986, "Cauliflower, raw", "蔬菜", {"颗": 300}),
    ("韭菜", 169994, "Chives, raw", "蔬菜", {"把": 100}),
    ("豆芽", 169957, "Mung beans, mature seeds, sprouted, raw", "蔬菜", {"把": 100}),
    ("黄豆芽", 169284, "Soybeans, mature seeds, sprouted, raw", "蔬菜", {"把": 100}),
    ("绿豆芽", 169957, "Mung beans, mature seeds, sprouted, raw", "蔬菜", {"把": 100}),
    ("豆角", 169961, "Beans, snap, green, raw", "蔬菜", {"把": 100}),
    ("四季豆", 169961, "Beans, snap, green, raw", "蔬菜", {"把": 100}),
    ("豇豆", 168405, "Cowpeas, young pods with seeds, raw", "蔬菜", {"把": 100}),
    ("毛豆", 169282, "Soybeans, green, raw", "蔬菜", {"把": 100}),
    ("豌豆", 170419, "Peas, green, raw", "蔬菜", {}),
    ("玉米", 169998, "Corn, sweet, yellow, raw", "蔬菜", {"根": 200, "个": 200}),
    ("玉米粒", 169998, "Corn, sweet, yellow, raw", "蔬菜", {"把": 100}),
    ("冬瓜", 170069, "Waxgourd, (chinese preserving melon), raw", "蔬菜", {"块": 100}),
    ("南瓜", 168448, "Pumpkin, raw", "蔬菜", {"块": 100}),
    ("西葫芦", 169291, "Squash, summer, zucchini, includes skin, raw", "蔬菜", {"根": 200, "个": 200}),
    ("苦瓜", 168393, "Balsam-pear (bitter gourd), pods, raw", "蔬菜", {"根": 200, "个": 200}),
    ("丝瓜", 169291, "Squash, summer, zucchini, includes skin, raw", "蔬菜", {"根": 200}, "以西葫芦近似"),
    ("藕", 169250, "Lotus root, raw", "蔬菜", {"节": 200, "根": 200}),
    ("莲藕", 169250, "Lotus root, raw", "蔬菜", {"节": 200, "根": 200}),
    ("山药", 170071, "Yam, raw", "蔬菜", {"根": 200}),
    ("芋头", 169308, "Taro, raw", "蔬菜", {"个": 100}),
    ("红薯", 168482, "Sweet potato, raw, unprepared (Includes foods for USDA's Food Distribution Program)", "蔬菜", {"个": 150}),
    ("香菇", 169242, "Mushrooms, shiitake, raw", "菌菇", {"朵": 15, "个": 15}),
    ("干香菇", 168436, "Mushrooms, shiitake, dried", "菌菇", {"朵": 5}),
    ("金针菇", 169382, "Mushrooms, enoki, raw", "菌菇", {"袋": 100, "把": 50}),
    ("蘑菇", 169251, "Mushrooms, white, raw", "菌菇", {"朵": 15, "个": 15}),
    ("口蘑", 169251, "Mushrooms, white, raw", "菌菇", {"朵": 15, "个": 15}),
    ("杏鲍菇", 169255, "Mushrooms, portabella, raw", "菌菇", {"个": 100}),
    ("木耳", 168581, "Fungi, Cloud ears, dried", "菌菇", {}),
    ("银耳", 168581, "Fungi, Cloud ears, dried", "菌菇", {}, "以木耳近似"),
    ("海带", 168457, "Seaweed, kelp, raw", "藻类", {"片": 20}),
    ("紫菜", 168458, "Seaweed, laver, raw", "藻类", {"片": 3}),
    ("笋", 169210, "Bamboo shoots, raw", "蔬菜", {"根": 100}),
    ("冬笋", 169210, "Bamboo shoots, raw", "蔬菜", {"根": 100}),
    ("酸笋", 169210, "Bamboo shoots, raw", "蔬菜", {"根": 100}),
    ("空心菜", 169301, "Water convolvulus,raw", "蔬菜", {"把": 100}),
    ("油麦菜", 169247, "Lettuce, cos or romaine, raw", "蔬菜", {"颗": 200}),
    ("蒜苗", 170005, "Onions, spring or scallions (includes tops and bulb), raw", "蔬菜", {"根": 30}),
    # ---- 水果坚果 ----
    ("柠檬", 167746, "Lemons, raw, without peel", "水果", {"个": 100, "颗": 100}),
    ("柠檬汁", 167747, "Lemon juice, raw", "水果", {}),
    ("草莓", 167762, "Strawberries, raw", "水果", {"颗": 15, "个": 15}),
    ("红枣", 168152, "Jujube, Chinese, fresh, dried", "干果", {"颗": 5, "个": 5, "粒": 5}),
    ("桂圆", 169089, "Longans, raw", "水果", {"颗": 5, "个": 5}),
    ("葡萄干", 168165, "Raisins, dark, seedless (Includes foods for USDA's Food Distribution Program)", "干果", {}),
    ("花生", 172430, "Peanuts, all types, raw", "坚果", {"粒": 1, "颗": 1}),
    ("熟花生", 173806, "Peanuts, all types, dry-roasted, without salt", "坚果", {"粒": 1, "颗": 1}),
    ("花生碎", 173806, "Peanuts, all types, dry-roasted, without salt", "坚果", {"勺": 8}),
    ("芝麻", 170150, "Seeds, sesame seeds, whole, dried", "坚果", {"勺": 8}),
    ("白芝麻", 170150, "Seeds, sesame seeds, whole, dried", "坚果", {"勺": 8}),
    ("腰果", 170162, "Nuts, cashew nuts, raw", "坚果", {"粒": 2, "颗": 2}),
    ("核桃", 170187, "Nuts, walnuts, english", "坚果", {"个": 10}),
    ("杏仁", 170567, "Nuts, almonds", "坚果", {"粒": 1.2}),
    # ---- 主食 ----
    ("大米", 168877, "Rice, white, long-grain, regular, raw, enriched", "主食", {"碗": 200}),
    ("米饭", 168878, "Rice, white, long-grain, regular, enriched, cooked", "主食", {"碗": 200}),
    ("糯米", 168883, "Rice, white, glutinous, unenriched, uncooked", "主食", {}),
    ("小米", 169702, "Millet, raw", "主食", {}),
    ("燕麦", 173904, "Cereals, oats, regular and quick, not fortified, dry", "主食", {}),
    ("面粉", 168894, "Wheat flour, white, all-purpose, enriched, bleached", "主食", {"勺": 15}),
    ("低筋面粉", 168894, "Wheat flour, white, all-purpose, enriched, bleached", "主食", {}),
    ("中筋面粉", 168894, "Wheat flour, white, all-purpose, enriched, bleached", "主食", {}),
    ("淀粉", 169698, "Cornstarch", "主食", {"勺": 10}),
    ("生粉", 169698, "Cornstarch", "主食", {"勺": 10}),
    ("玉米淀粉", 169698, "Cornstarch", "主食", {}),
    ("土豆淀粉", 169698, "Cornstarch", "主食", {}),
    ("面条", 169736, "Pasta, dry, enriched", "主食", {}),
    ("意面", 169736, "Pasta, dry, enriched", "主食", {}),
    ("米粉", 168877, "Rice, white, long-grain, regular, raw, enriched", "主食", {}, "以大米近似"),
    ("粉丝", 168877, "Rice, white, long-grain, regular, raw, enriched", "主食", {}, "以大米近似"),
    ("年糕", 168883, "Rice, white, glutinous, unenriched, uncooked", "主食", {"片": 15, "根": 100}),
    ("面包", 174924, "Bread, white, commercially prepared (includes soft bread crumbs)", "主食", {"片": 30}),
    ("吐司", 174924, "Bread, white, commercially prepared (includes soft bread crumbs)", "主食", {"片": 30}),
    ("饺子皮", 168894, "Wheat flour, white, all-purpose, enriched, bleached", "主食", {"张": 10}),
    ("馄饨皮", 168894, "Wheat flour, white, all-purpose, enriched, bleached", "主食", {"张": 8}),
    # ---- 乳制品 ----
    ("牛奶", 171265, "Milk, whole, 3.25% milkfat, with added vitamin D", "乳制品", {"杯": 240, "盒": 250, "袋": 200}),
    ("全脂牛奶", 171265, "Milk, whole, 3.25% milkfat, with added vitamin D", "乳制品", {"杯": 240}),
    ("酸奶", 171284, "Yogurt, plain, whole milk", "乳制品", {"杯": 200, "盒": 200}),
    ("黄油", 173430, "Butter, without salt", "乳制品", {"块": 10, "勺": 14}),
    ("无盐黄油", 173430, "Butter, without salt", "乳制品", {"块": 10}),
    ("淡奶油", 170859, "Cream, fluid, heavy whipping", "乳制品", {}),
    ("奶油", 170859, "Cream, fluid, heavy whipping", "乳制品", {}),
    ("奶油奶酪", 173418, "Cheese, cream", "乳制品", {}),
    ("芝士片", 173414, "Cheese, cheddar (Includes foods for USDA's Food Distribution Program)", "乳制品", {"片": 20}),
    ("芝士", 173414, "Cheese, cheddar (Includes foods for USDA's Food Distribution Program)", "乳制品", {}),
    # ---- 油脂 ----
    ("食用油", 171411, "Oil, soybean, salad or cooking", "油脂", {"勺": 15, "汤匙": 15, "茶匙": 5}),
    ("植物油", 171411, "Oil, soybean, salad or cooking", "油脂", {"勺": 15}),
    ("菜籽油", 172336, "Oil, canola", "油脂", {"勺": 15}),
    ("花生油", 171410, "Oil, peanut, salad or cooking", "油脂", {"勺": 15}),
    ("橄榄油", 171413, "Oil, olive, salad or cooking", "油脂", {"勺": 15}),
    ("玉米油", 171411, "Oil, soybean, salad or cooking", "油脂", {"勺": 15}),
    ("大豆油", 171411, "Oil, soybean, salad or cooking", "油脂", {"勺": 15}),
    ("香油", 171016, "Oil, sesame, salad or cooking", "油脂", {"勺": 15, "茶匙": 5}),
    ("芝麻油", 171016, "Oil, sesame, salad or cooking", "油脂", {"勺": 15}),
    ("麻油", 171016, "Oil, sesame, salad or cooking", "油脂", {"勺": 15}),
    ("椰子油", 171412, "Oil, coconut", "油脂", {"勺": 15}),
    ("酥油", 171314, "Butter, Clarified butter (ghee)", "油脂", {}),
    # ---- 糖 / 调味 ----
    ("白糖", 169655, "Sugars, granulated", "调味", {"勺": 10, "汤匙": 15, "茶匙": 5}),
    ("白砂糖", 169655, "Sugars, granulated", "调味", {"勺": 10}),
    ("糖", 169655, "Sugars, granulated", "调味", {"勺": 10}),
    ("砂糖", 169655, "Sugars, granulated", "调味", {"勺": 10}),
    ("冰糖", 169655, "Sugars, granulated", "调味", {"块": 5, "粒": 5, "颗": 5}),
    ("红糖", 168833, "Sugars, brown", "调味", {"勺": 10}),
    ("蜂蜜", 169640, "Honey", "调味", {"勺": 15}),
    ("盐", 173468, "Salt, table", "调味", {"勺": 5, "汤匙": 15, "茶匙": 5}),
    ("生抽", 174277, "Soy sauce made from soy and wheat (shoyu)", "调味", {"勺": 15, "汤匙": 15, "茶匙": 5}),
    ("老抽", 174277, "Soy sauce made from soy and wheat (shoyu)", "调味", {"勺": 15}),
    ("酱油", 174277, "Soy sauce made from soy and wheat (shoyu)", "调味", {"勺": 15}),
    ("味极鲜", 174277, "Soy sauce made from soy and wheat (shoyu)", "调味", {"勺": 15}),
    ("蒸鱼豉油", 174277, "Soy sauce made from soy and wheat (shoyu)", "调味", {"勺": 15}),
    ("蚝油", 174529, "Sauce, oyster, ready-to-serve", "调味", {"勺": 15}),
    ("鱼露", 174531, "Sauce, fish, ready-to-serve", "调味", {"勺": 15}),
    ("豆瓣酱", 171605, "Sauce, chili, peppers, hot, immature green, canned", "调味", {"勺": 15}, "以辣椒酱近似"),
    ("黄豆酱", 172442, "Miso", "调味", {"勺": 15}),
    ("甜面酱", 172886, "Sauce, hoisin, ready-to-serve", "调味", {"勺": 15}, "以海鲜酱近似"),
    ("柱侯酱", 172886, "Sauce, hoisin, ready-to-serve", "调味", {"勺": 15}, "以海鲜酱近似"),
    ("海鲜酱", 172886, "Sauce, hoisin, ready-to-serve", "调味", {"勺": 15}),
    ("番茄酱", 168556, "Catsup", "调味", {"勺": 17}),
    ("醋", 172237, "Vinegar, distilled", "调味", {"勺": 15}),
    ("白醋", 172237, "Vinegar, distilled", "调味", {"勺": 15}),
    ("陈醋", 172237, "Vinegar, distilled", "调味", {"勺": 15}),
    ("香醋", 172237, "Vinegar, distilled", "调味", {"勺": 15}),
    ("米醋", 172237, "Vinegar, distilled", "调味", {"勺": 15}),
    ("料酒", 169070, "Alcoholic beverage, wine, cooking", "调味", {"勺": 15}),
    ("黄酒", 167723, "Alcoholic beverage, rice (sake)", "调味", {"勺": 15}),
    ("米酒", 167723, "Alcoholic beverage, rice (sake)", "调味", {"勺": 15}),
    ("白酒", 169070, "Alcoholic beverage, wine, cooking", "调味", {"勺": 15}),
    ("啤酒", 168746, "Alcoholic beverage, beer, regular, all", "调味", {"瓶": 500, "罐": 330}),
    ("葡萄酒", 169070, "Alcoholic beverage, wine, cooking", "调味", {"杯": 150}),
    ("朗姆酒", 169070, "Alcoholic beverage, wine, cooking", "调味", {"勺": 15}),
    ("伏特加", 169070, "Alcoholic beverage, wine, cooking", "调味", {"勺": 15}),
    ("金酒", 169070, "Alcoholic beverage, wine, cooking", "调味", {"勺": 15}),
    ("咖喱块", 170924, "Spices, curry powder", "调味", {"块": 15, "盒": 100}),
    ("咖喱粉", 170924, "Spices, curry powder", "调味", {"勺": 6}),
    ("芥末", 172234, "Mustard, prepared, yellow", "调味", {"勺": 15}),
    ("孜然粉", 170923, "Spices, cumin seed", "调味", {"勺": 5, "茶匙": 3}),
    ("孜然籽", 170923, "Spices, cumin seed", "调味", {"勺": 5}),
    ("小茴香", 171323, "Spices, fennel seed", "调味", {"勺": 5, "茶匙": 3}),
    ("胡椒粉", 170931, "Spices, pepper, black", "调味", {"勺": 5, "茶匙": 3}),
    ("白胡椒粉", 170931, "Spices, pepper, black", "调味", {"勺": 5, "茶匙": 3}),
    ("黑胡椒", 170931, "Spices, pepper, black", "调味", {"勺": 5}),
    ("黑胡椒粉", 170931, "Spices, pepper, black", "调味", {"勺": 5}),
    ("花椒", 170931, "Spices, pepper, black", "调味", {"勺": 5, "茶匙": 3, "粒": 0.2}, "以黑胡椒近似"),
    ("花椒粉", 170931, "Spices, pepper, black", "调味", {"勺": 5}, "以黑胡椒近似"),
    ("青花椒", 170931, "Spices, pepper, black", "调味", {"勺": 5}, "以黑胡椒近似"),
    ("八角", 171316, "Spices, anise seed", "调味", {"颗": 3, "个": 3, "粒": 3}),
    ("桂皮", 171320, "Spices, cinnamon, ground", "调味", {"块": 5, "片": 3}),
    ("香叶", 170917, "Spices, bay leaf", "调味", {"片": 0.5, "张": 0.5}),
    ("丁香", 171321, "Spices, cloves, ground", "调味", {"粒": 0.5, "个": 0.5, "颗": 0.5}),
    ("五香粉", 170931, "Spices, pepper, black", "调味", {"勺": 5, "茶匙": 3}, "以黑胡椒近似"),
    ("十三香", 170931, "Spices, pepper, black", "调味", {"勺": 5}, "以黑胡椒近似"),
    ("辣椒粉", 170932, "Spices, pepper, red or cayenne", "调味", {"勺": 5, "茶匙": 3}),
    ("红辣椒粉", 170932, "Spices, pepper, red or cayenne", "调味", {"勺": 5}),
    ("干辣椒面", 170932, "Spices, pepper, red or cayenne", "调味", {"勺": 5}),
    ("姜黄粉", 172231, "Spices, turmeric, ground", "调味", {"勺": 5}),
    ("香菜粉", 170922, "Spices, coriander seed", "调味", {"勺": 5}),
    ("印度综合香料粉", 170924, "Spices, curry powder", "调味", {"勺": 5}, "以咖喱粉近似"),
    ("小豆蔻粉", 170919, "Spices, cardamom", "调味", {"勺": 5}),
    ("香草精", 173471, "Vanilla extract", "调味", {"茶匙": 5}),
    ("可可粉", 169593, "Cocoa, dry powder, unsweetened", "调味", {"勺": 5}),
    ("泡打粉", 172803, "Leavening agents, baking powder, double-acting, sodium aluminum sulfate", "膨松剂", {"茶匙": 4}),
    ("酵母", 175043, "Leavening agents, yeast, baker's, active dry", "膨松剂", {"茶匙": 3}),
    ("小苏打", 175040, "Leavening agents, baking soda", "膨松剂", {"茶匙": 4}),
    ("鸡精", 171562, "Soup, chicken broth or bouillon, dry", "调味", {"勺": 5, "茶匙": 3}),
    ("味精", 173468, "Salt, table", "调味", {"勺": 5, "茶匙": 3}, "按零热量调味料计"),
    ("芝麻酱", 170189, "Seeds, sesame butter, tahini, from roasted and toasted kernels (most common type)", "调味", {"勺": 15}),
    ("花生酱", 174294, "Peanut Butter, smooth (Includes foods for USDA's Food Distribution Program)", "调味", {"勺": 16}),
    ("椰浆", 170173, "Nuts, coconut milk, canned (liquid expressed from grated meat and water)", "调味", {"杯": 240}),
    ("水", 0, "Water", "其他", {"杯": 240}),
    ("冰块", 0, "Water", "其他", {"块": 10}),
    ("可乐", 175113, "Carbonated beverage, low calorie, other than cola or pepper, with sodium saccharin, without caffeine", "饮料", {"罐": 330}, "以低热量碳酸饮料近似"),
    ("苏打水", 175113, "Carbonated beverage, low calorie, other than cola or pepper, with sodium saccharin, without caffeine", "饮料", {"罐": 330}),
    ("红茶", 0, "Water", "其他", {"杯": 240}, "按零热量茶汤计"),
]

# 同义名（精确匹配），键为清洗后的原始名
ALIASES = {
    "手枪腿": "鸡腿",
    "鸡全腿": "鸡腿",
    "琵琶腿": "鸡腿",
    "带皮五花肉": "五花肉",
    "肉蟹": "蟹",
    "青蟹": "蟹",
    "梭子蟹": "蟹",
    "罗氏虾": "虾",
    "阿根廷红虾": "虾",
    "黑虎虾": "虾",
    "鲜虾": "虾",
    "黑鳕鱼": "鳕鱼",
    "银鳕鱼": "鳕鱼",
    "鳕鱼片": "鳕鱼",
    "鲜香菇": "香菇",
    "香菇片": "香菇",
    "熟白芝麻": "白芝麻",
    "白芝麻粒": "白芝麻",
    "黑芝麻": "芝麻",
    "南乳": "豆腐",
    "南腐乳": "豆腐",
    "腐乳": "豆腐",
    "郫县豆瓣": "豆瓣酱",
    "红油豆瓣": "豆瓣酱",
    "老干妈": "豆瓣酱",
    "耗油": "蚝油",
    "生抽酱油": "生抽",
    "老抽酱油": "老抽",
    "味极鲜酱油": "味极鲜",
    "山西陈醋": "陈醋",
    "镇江香醋": "香醋",
    "米酒汁": "米酒",
    "白酒58度": "白酒",
    "食用盐": "盐",
    "精盐": "盐",
    "海盐": "盐",
    "食盐": "盐",
    "白砂糖": "白砂糖",
    "绵白糖": "白糖",
    "糖粉": "白糖",
    "蔗糖": "白糖",
    "植物油": "植物油",
    "色拉油": "食用油",
    "调和油": "食用油",
    "猪五花肉": "五花肉",
    "带骨五花肉": "五花肉",
    "羊排肉": "羊排",
    "牛里脊": "牛肉",
    "牛腱": "牛肉",
    "牛腱子": "牛肉",
    "鸡胸脯肉": "鸡胸肉",
    "鸡脯肉": "鸡胸肉",
    "鸡蛋液": "鸡蛋",
    "鸡蛋黄": "蛋黄",
    "蛋液": "鸡蛋",
    "鲜鸡蛋": "鸡蛋",
    "洋葱碎": "洋葱",
    "蒜蓉": "大蒜",
    "蒜末": "大蒜",
    "蒜泥": "大蒜",
    "蒜瓣": "大蒜",
    "蒜头": "大蒜",
    "蒜": "大蒜",
    "生姜": "姜",
    "老姜": "姜",
    "姜末": "姜",
    "姜片": "姜",
    "姜丝": "姜",
    "小葱": "葱",
    "大葱": "葱",
    "香葱": "葱",
    "葱花": "葱",
    "葱白": "葱",
    "葱绿": "葱",
    "香菜叶": "香菜",
    "香芹": "芹菜",
    "芹菜段": "芹菜",
    "小米椒": "小米辣",
    "红辣椒": "辣椒",
    "青辣椒": "青椒",
    "野山椒": "辣椒",
    "子弹头辣椒": "辣椒",
    "辣椒段": "干辣椒",
    "西红柿": "西红柿",
    "土豆块": "土豆",
    "土豆片": "土豆",
    "马铃薯": "土豆",
    "红萝卜": "胡萝卜",
    "胡萝卜丝": "胡萝卜",
    "番茄酱": "番茄酱",
    "鸡蛋番茄": "番茄",
    "鲜玉米": "玉米",
    "甜玉米": "玉米",
    "青豆": "豌豆",
    "四季豆": "四季豆",
    "白玉菇": "蘑菇",
    "蟹味菇": "蘑菇",
    "平菇": "蘑菇",
    "草菇": "蘑菇",
    "香菇干": "干香菇",
    "干木耳": "木耳",
    "黑木耳": "木耳",
    "白木耳": "银耳",
    "大米饭": "米饭",
    "冷饭": "米饭",
    "剩米饭": "米饭",
    "白米饭": "米饭",
    "小米辣圈": "小米辣",
    "番茄沙司": "番茄酱",
    "美极鲜": "味极鲜",
    "蒸鱼酱油": "蒸鱼豉油",
    "豆豉": "黄豆酱",
    "甜面酱": "甜面酱",
    "柱候酱": "柱侯酱",
    "鱼露汁": "鱼露",
    "橄榄油": "橄榄油",
    "花生油": "花生油",
    "菜籽油": "菜籽油",
    "玉米油": "玉米油",
    "葵花籽油": "食用油",
    "芝麻香油": "香油",
    "小磨香油": "香油",
    "黄油": "黄油",
    "无盐黄油": "无盐黄油",
    "淡奶油": "淡奶油",
    "动物性淡奶油": "淡奶油",
    "全脂牛奶": "全脂牛奶",
    "纯牛奶": "牛奶",
    "原味酸奶": "酸奶",
    "酸奶": "酸奶",
    "奶油奶酪": "奶油奶酪",
    "奶油芝士": "奶油奶酪",
    "马苏里拉芝士": "芝士",
    "芝士碎": "芝士",
    "白胡椒粉": "白胡椒粉",
    "黑胡椒粉": "黑胡椒粉",
    "胡椒粉": "胡椒粉",
    "花椒碎": "花椒",
    "花椒粒": "花椒",
    "青花椒": "青花椒",
    "藤椒油": "花椒油",
    "花椒油": "花椒油",
    "孜然": "孜然粉",
    "孜然粒": "孜然籽",
    "五香粉": "五香粉",
    "十三香": "十三香",
    "肉桂": "桂皮",
    "月桂叶": "香叶",
    "香叶": "香叶",
    "咖喱": "咖喱块",
    "咖喱酱": "咖喱块",
    "咖喱粉": "咖喱粉",
    "白葡萄酒": "葡萄酒",
    "红葡萄酒": "葡萄酒",
    "啤酒": "啤酒",
    "黄酒": "黄酒",
    "料酒": "料酒",
    "米酒": "米酒",
    "白酒": "白酒",
    "伏特加": "伏特加",
    "金酒": "金酒",
    "朗姆酒": "朗姆酒",
    "蜂蜜": "蜂蜜",
    "冰糖": "冰糖",
    "红糖": "红糖",
    "白砂糖": "白砂糖",
    "砂糖": "砂糖",
    "蚝油": "蚝油",
    "生抽": "生抽",
    "老抽": "老抽",
    "酱油": "酱油",
    "醋": "醋",
    "白醋": "白醋",
    "陈醋": "陈醋",
    "香醋": "香醋",
    "米醋": "米醋",
    "盐": "盐",
    "盐量": "盐",
    "油量": "食用油",
    "蒜量": "大蒜",
    "肉量": "猪肉",
    "鸡精": "鸡精",
    "味精": "味精",
    "芝麻酱": "芝麻酱",
    "花生酱": "花生酱",
    "椰浆": "椰浆",
    "小苏打": "小苏打",
    "泡打粉": "泡打粉",
    "酵母": "酵母",
    "可可粉": "可可粉",
    "香草精": "香草精",
    "水": "水",
    "清水": "水",
    "开水": "水",
    "冷水": "水",
    "热水": "水",
    "温水": "水",
    "饮用水": "水",
    "冰块": "冰块",
    "可乐": "可乐",
    "苏打水": "苏打水",
    "红茶": "红茶",
    # ---- 第二轮补充 ----
    "油": "食用油",
    "肉末": "猪肉末",
    "肉沫": "猪肉末",
    "猪肉糜": "猪肉末",
    "鸡": "鸡肉",
    "整鸡": "鸡肉",
    "三黄鸡": "鸡肉",
    "童子鸡": "鸡肉",
    "泡椒": "辣椒",
    "螺丝椒": "辣椒",
    "美人椒": "辣椒",
    "朝天椒": "辣椒",
    "灯笼椒": "青椒",
    "大料": "八角",
    "花雕酒": "黄酒",
    "千张": "豆干",
    "烧烤撒料": "孜然粉",
    "无菌蛋": "鸡蛋",
    "可生食鸡蛋": "鸡蛋",
    "东北酸菜": "酸菜",
    "酸白菜": "酸菜",
    "挂面": "面条",
    "味淋": "料酒",
    "甜椒": "青椒",
    "彩椒": "青椒",
    "吉利丁片": "吉利丁",
    "明胶": "吉利丁",
    "牡蛎": "生蚝",
    "海蛎子": "生蚝",
    "小茴香": "茴香",
    "肉": "猪肉",
    "面": "面条",
    "牛排": "牛肉",
    "肥牛": "牛肉",
    "尖椒": "辣椒",
    "菜心": "青菜",
    "鸭子": "鸭肉",
    "乳鸽": "鸡肉",
    "肘子": "猪蹄",
    "前肘": "猪蹄",
    "意大利面": "面条",
    "意大利面酱": "番茄酱",
    "菌菇": "蘑菇",
    "鳝鱼": "鳗鱼",
    "白鳝": "鳗鱼",
    "白鱔": "鳗鱼",
}

# 花椒油单独条目（USDA 无，按芝麻油近似）
EXTRA_ITEMS = [
    ("花椒油", 171016, "Oil, sesame, salad or cooking", "油脂", {"勺": 15}, "以芝麻油近似"),
    ("欧芹", 170416, "Parsley, fresh", "蔬菜", {"棵": 15, "把": 30}),
    ("茴香", 171323, "Spices, fennel seed", "调味", {"颗": 0.5, "粒": 0.5}, "按茴香籽近似"),
    ("草果", 170919, "Spices, cardamom", "调味", {"个": 3, "颗": 3}, "USDA 无草果，以豆蔻近似"),
    ("吉利丁", 169599, "Gelatins, dry powder, unsweetened", "调味", {"片": 5}),
    ("生蚝", 171978, "Mollusks, oyster, eastern, wild, raw", "水产", {"个": 80, "只": 80}),
    ("荷兰豆", 170010, "Peas, edible-podded, raw", "蔬菜", {"个": 3, "把": 100}),
    ("牛蛙", 168148, "Frog legs, raw", "水产", {"只": 150, "个": 150}),
    ("鳗鱼", 174193, "Fish, eel, mixed species, raw", "水产", {"条": 300}),
    ("白芸豆", 175202, "Beans, white, mature seeds, raw", "豆类", {}),
    ("腊肠", 172938, "Salami, dry or hard, pork", "畜肉", {"根": 60, "条": 60}, "以萨拉米近似"),
]

ITEMS = ITEMS + EXTRA_ITEMS

UNIT_RE = re.compile(
    r"(g|克|kg|千克|ml|毫升|mL|ML|L|升|斤|两|勺|小勺|大勺|汤匙|茶匙|tsp|tbsp|"
    r"片|瓣|根|颗|只|个|枚|块|盒|袋|把|碗|杯|滴|支|朵|粒|棵|圈|张|条|节|罐|瓶)"
)
NUM_WORDS = {"半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
FALLBACK_UNITS = {"个": 100, "只": 100, "枚": 50, "根": 30, "片": 5, "瓣": 5,
                  "颗": 2, "块": 50, "盒": 300, "袋": 100, "把": 20, "碗": 250,
                  "杯": 240, "滴": 0.05, "勺": 15, "小勺": 5, "大勺": 15,
                  "汤匙": 15, "茶匙": 5, "tsp": 5, "tbsp": 15, "支": 50, "朵": 15,
                  "粒": 1, "棵": 100, "圈": 2, "张": 10, "条": 50, "节": 100,
                  "罐": 330, "瓶": 500}
MASS = {"g": 1, "克": 1, "kg": 1000, "千克": 1000, "斤": 500, "两": 50,
        "ml": 1, "毫升": 1, "mL": 1, "ML": 1, "L": 1000, "升": 1000}
# 分数词：二分之一杯、一半、三分之一 等
FRAC_WORDS = {"二分之一": 0.5, "三分之一": 1 / 3, "三分之二": 2 / 3,
              "四分之一": 0.25, "一半": 0.5}
# 无法解析用量时按分类给的缺省克重
DEFAULT_GRAMS = {"调味": 8.0, "膨松剂": 8.0, "油脂": 15.0, "饮料": 200.0}
# 这些分类的单位差异太大，不走 FALLBACK_UNITS，直接交分类缺省
NO_FALLBACK_CATS = {"调味", "膨松剂", "油脂"}


def load_nutrients(usda_dir):
    # 只读取需要的营养字段
    ids = {"1008", "1003", "1004", "1005", "1079"}
    data = {}
    with open(usda_dir / "food_nutrient.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["nutrient_id"] in ids:
                data.setdefault(row["fdc_id"], {})[row["nutrient_id"]] = row["amount"]
    return data


def load_nutrients_from_ingredients(ingredients_path):
    """从既有 ingredients.json 回读每 100g 成分作为营养缓存（fdc_id → 字段）。

    仅当 staging 与 ITEMS 均未变更时安全；有新增/改名的食材会因缓存缺失而报错，
    此时必须改用 --usda-dir 重建。
    """
    payload = json.loads(Path(ingredients_path).read_text(encoding="utf-8"))
    nut = {}
    for item in payload["ingredients"].values():
        p = item["per_100g"]
        nut[str(item["usda_id"])] = {
            "1008": p["calories"], "1003": p["protein"],
            "1004": p["fat"], "1005": p["carbs"], "1079": p["fiber"],
        }
    return nut


MAPPING = {}
CATEGORY = {}
UNIT_GRAMS = {}
ASSUMPTION = {}
for item in ITEMS:
    name, fid, desc, cat, units = item[:5]
    assumption = item[5] if len(item) > 5 else None
    MAPPING[name] = (fid, desc)
    CATEGORY[name] = cat
    UNIT_GRAMS[name] = units
    if assumption:
        ASSUMPTION[name] = assumption

# 建立包含匹配的键（长键优先；同长按键名排序固定次序，
# 否则 set 迭代序受 PYTHONHASHSEED 影响，"干辣椒粉" 同时含 "干辣椒"/"辣椒粉" 时跨进程结果漂移）
MATCH_KEYS = sorted(set(list(MAPPING.keys()) + list(ALIASES.keys())), key=lambda k: (-len(k), k))
CANON = set(MAPPING.keys())


def normalize_name(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", s)
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[\s]", "", s)
    s = s.strip(",，。;；、:：*")
    s = re.sub(r"(的|量)$", "", s)
    s = re.sub(r"^[\d\.]+", "", s)
    return s


def canonical(raw: str):
    n = normalize_name(raw)
    if not n:
        return None
    # "淹过鸡蛋的冷水" 这类描述应归为水，避免包含匹配到后面的名词
    if re.match(r"^(淹过|没过|覆盖|莫过)", n):
        return "水"
    if n in CANON:
        return n
    if n in ALIASES and ALIASES[n] in CANON:
        return ALIASES[n]
    for key in MATCH_KEYS:
        if key in n:
            target = ALIASES.get(key, key)
            if target in CANON:
                return target
    return None


def parse_amount(text: str):
    """返回 (数值, 单位) 或 (None, None)"""
    t = text.strip()
    for fw, fv in FRAC_WORDS.items():
        if t.startswith(fw):
            rest = t[len(fw):]
            um = re.match(rf"^({UNIT_RE.pattern})(.*)$", rest)
            return fv, (um.group(1) if um else "")
    m = re.match(r"^(\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?|[半一二两三四五六七八九十])\s*(.*)$", t)
    if not m:
        return None, None
    num_s, rest = m.group(1), m.group(2)
    if num_s in NUM_WORDS:
        num = NUM_WORDS[num_s]
    else:
        parts = re.split(r"[-~至]", num_s)
        nums = [float(x) for x in parts if x.strip()]
        num = sum(nums) / len(nums)
    um = re.match(rf"^({UNIT_RE.pattern})(.*)$", rest)
    unit = um.group(1) if um else ""
    return num, unit


def grams_for(canon_name, amount_text, paren_values):
    """计算食材总克重。返回 (克重, 来源) 或 (None, 原因)"""
    t = (amount_text or "").strip()
    # 括号标注的克数最可靠
    if paren_values:
        pv = paren_values[0] if isinstance(paren_values, (list, tuple)) else paren_values
        if isinstance(pv, str):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(g|克|ml|毫升)", pv)
            pv = float(m.group(1)) if m else None
        if pv:
            return float(pv), "括号标注"
    # 乘法模式："15g * 5"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(g|克|ml|毫升)\s*[*＊×xX]\s*(\d+(?:\.\d+)?)$", t)
    if m:
        return float(m.group(1)) * float(m.group(3)), "乘法"
    # "兔肉斤数 * 2 克" 类比例描述：取右侧
    m = re.search(r"斤数\s*[*＊×xX]\s*(.+)$", t)
    if m:
        t = m.group(1).strip()
    # 比例/参照描述不可靠，交缺省
    if re.search(r"对应|等于|总体积|覆盖|没过|淹过", t):
        return None, "比例描述"
    num, unit = parse_amount(t)
    if num is None:
        return None, "无法解析"
    if unit in MASS:
        return num * MASS[unit], unit
    if unit in UNIT_GRAMS[canon_name]:
        return num * float(UNIT_GRAMS[canon_name][unit]), unit
    # 调味/膨松剂/油脂单位差异大，不走通用缺省，交分类缺省
    if CATEGORY.get(canon_name) in NO_FALLBACK_CATS and unit:
        return None, "调味缺省"
    if unit in FALLBACK_UNITS:
        return num * FALLBACK_UNITS[unit], unit
    # 无单位：数量型食材按个，其余按 g
    if not unit:
        if "个" in UNIT_GRAMS[canon_name]:
            return num * float(UNIT_GRAMS[canon_name]["个"]), "个(缺省)"
        return num, "g(缺省)"
    return None, f"未知单位:{unit}"


def estimate_unmatched_grams(amount_text):
    """未匹配食材的粗略克重估计，仅用于覆盖率分母"""
    num, unit = parse_amount(amount_text or "")
    if num is None:
        return 50.0
    if unit in MASS:
        return num * MASS[unit]
    if unit in FALLBACK_UNITS:
        return num * FALLBACK_UNITS[unit]
    return 50.0


def extract_name_from_amount(amount_text: str) -> str:
    """上游解析对"用量在前、名称在后"的行会产出空名称，从用量文本尾部取回食材名。
    例：'1 盒内脂豆腐' → '内脂豆腐'；'10g 的干紫菜' → '干紫菜'"""
    t = (amount_text or "").strip()
    m = re.match(
        rf"^(?:[\d\.]+(?:\s*[-~至]\s*[\d\.]+)?|[半一二两三四五六七八九十]+)\s*"
        rf"(?:{UNIT_RE.pattern})?\s*的?\s*(?P<name>.+?)\s*$",
        t,
    )
    return m.group("name") if m else ""


def clean_entry(q):
    q = q.replace("*份数", "").replace("* 份数", "").replace("份数 *", "")
    q = q.replace("用量为", "").strip()
    paren_values = []
    for pm in re.finditer(r"(\d+(?:\.\d+)?(?:\s*[-~]\s*\d+(?:\.\d+)?)?)\s*(g|克|ml|毫升)", q):
        vals = re.split(r"[-~]", pm.group(1))
        nums = [float(x) for x in vals if x.strip()]
        paren_values.append(sum(nums) / len(nums) * (1 if pm.group(2) in ("g", "克") else 1))
    if "=" in q:
        left, right = q.split("=", 1)
    else:
        left, right = q, ""
        m = re.search(r"(\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?|[半一二两三四五六七八九十])\s*" + UNIT_RE.pattern, q)
        if m:
            left, right = q[:m.start()], q[m.start():]
    name = normalize_name(left)
    return name, right.strip(), paren_values


def cuisine_of(name, category):
    for kw, c in [("麻婆", "川菜"), ("回锅", "川菜"), ("宫保", "川菜"), ("鱼香", "川菜"),
                  ("水煮", "川菜"), ("口水", "川菜"), ("辣子", "川菜"), ("夫妻肺片", "川菜"),
                  ("担担", "川菜"), ("毛血旺", "川菜"), ("白灼", "粤菜"), ("清蒸", "粤菜"),
                  ("豉汁", "粤菜"), ("叉烧", "粤菜"), ("煲仔", "粤菜"), ("肠粉", "粤菜"),
                  ("糖醋", "家常菜"), ("红烧", "家常菜"), ("小炒", "家常菜"), ("锅包肉", "东北菜"),
                  ("溜肉段", "东北菜"), ("肉夹馍", "西北菜"), ("臊子", "西北菜"), ("油泼", "西北菜")]:
        if kw in name:
            return c
    return {"主食": "主食", "早餐": "早餐", "汤羹": "汤羹"}.get(category, "家常菜")


def tags_of(name, category, cuisine, difficulty, steps):
    tags = [cuisine, category]
    method = None
    for kw in ["凉拌", "清蒸", "蒸", "红烧", "炖", "焖", "炸", "煎", "烤", "炒", "煮", "拌"]:
        if kw in name or any(kw in s for s in steps[:3]):
            method = kw
            break
    if method:
        tags.append(method)
    if difficulty in ("简单", "困难"):
        tags.append(difficulty + "上手" if difficulty == "简单" else difficulty)
    return list(dict.fromkeys(tags))[:5]


# 营养派生标签（整菜口径，阈值依 2026-10-31 库内分布定标：
# 热量 p25≈380 / 蛋白质 p50≈35 / 脂肪 p25≈16 / 纤维 p75≈9.5，取整后命中率 2%~44%）
# 作用：为"减脂/低卡/高蛋白"类查询在向量嵌入与 metadata 双侧提供语义锚点，
# 修复新库无此类标签导致的 constraint 查询语义漂移
DERIVED_TAGS = ("低卡", "高蛋白", "低脂", "高纤维", "高蛋白低脂")


def derived_tags(n):
    """按整菜营养字段派生标签。n 为 nutrition_per_serving（整菜口径）"""
    tags = []
    if n["calories"] <= 400:
        tags.append("低卡")
    if n["protein"] >= 40:
        tags.append("高蛋白")
    if n["fat"] <= 15:
        tags.append("低脂")
    if n["fiber"] >= 8:
        tags.append("高纤维")
    if n["calories"] <= 600 and n["protein"] >= 40 and n["fat"] <= 25:
        tags.append("高蛋白低脂")
    return tags


def estimate_time(rec):
    desc = rec.get("description") or ""
    m = re.search(r"(?:约需|大约需要|大约|约|耗时|需要)\s*(\d+(?:\.\d+)?)\s*(分钟|小时)", desc)
    if m:
        val = float(m.group(1))
        return int(val * 60) if m.group(2) == "小时" else int(val)
    if rec.get("time_minutes"):
        return rec["time_minutes"]
    m = re.search(r"(\d+(?:\.\d+)?)\s*(分钟|小时)", desc)
    if m:
        val = float(m.group(1))
        return int(val * 60) if m.group(2) == "小时" else int(val)
    return max(15, len(rec["steps"]) * 5)


def time_text(minutes):
    if minutes >= 90 and minutes % 60 == 0:
        return f"约{minutes // 60}小时"
    if minutes >= 120:
        return f"约{minutes // 60}小时{minutes % 60}分钟"
    return f"约{minutes}分钟"


def build(staging_path, usda_dir, out_dir):
    if usda_dir:
        nut = load_nutrients(usda_dir)
    else:
        cache = Path(out_dir) / "ingredients.json"
        if not cache.exists():
            raise SystemExit("--usda-dir 未提供，且无 ingredients.json 营养缓存可回读")
        nut = load_nutrients_from_ingredients(cache)
        print(f"营养数据回读自 {cache}（{len(nut)} 个 fdc_id）")
    missing_nut = set()
    recipes = []
    with open(staging_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recipes.append(json.loads(line))
    allowed_cats = {"荤菜", "素菜", "水产", "主食", "早餐", "汤羹", "甜品"}
    results = []
    seen_names = set()
    skipped = []

    for rec in recipes:
        if rec["category"] not in allowed_cats:
            continue
        if rec["name"] in seen_names:
            continue
        details = []
        total = {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0, "fiber": 0.0}
        matched_g = 0.0
        total_g = 0.0
        unmatched = []
        display = []
        valid = True
        rec_missing = set()  # 本菜谱用到的缺失食材，仅通过校验后才计入 missing_nut

        for raw_name, amount_text, paren, _per_serving in rec.get("ingredients_raw", []):
            eff_name = raw_name or extract_name_from_amount(amount_text)
            canon = canonical(eff_name)
            if not canon:
                if eff_name:
                    unmatched.append(eff_name)
                    # 未匹配食材按估计克重计入分母，覆盖率才真实
                    total_g += estimate_unmatched_grams(amount_text)
                continue
            grams, unit_src = grams_for(canon, amount_text, paren)
            if grams is None or grams <= 0:
                grams = DEFAULT_GRAMS.get(CATEGORY.get(canon, ""), 100.0)
                unit_src = unit_src or "缺省"
            if grams > 2000:
                grams = 100.0
                unit_src = "异常值修正"
            # 油炸/爆炒用油：按实际消耗上限 30g 计
            if canon in ("食用油", "植物油", "菜籽油", "花生油", "橄榄油", "玉米油",
                         "大豆油", "猪油", "黄油", "无盐黄油"):
                if grams > 30:
                    grams = 30.0
                    unit_src = "用油上限30g"

            fid, desc = MAPPING[canon]
            per = nut.get(str(fid))
            if per is None:
                rec_missing.add(f"{canon}(fdc:{fid})")
                per = {}
            factor = grams / 100.0
            cal = float(per.get("1008") or 0) * factor
            pro = float(per.get("1003") or 0) * factor
            fat = float(per.get("1004") or 0) * factor
            carb = float(per.get("1005") or 0) * factor
            fib = float(per.get("1079") or 0) * factor
            if fid == 0:
                cal = pro = fat = carb = fib = 0.0
            if cal == 0 and (pro or fat or carb):
                cal = 4 * pro + 9 * fat + 4 * carb
            for k, v in (("calories", cal), ("protein", pro), ("fat", fat),
                         ("carbs", carb), ("fiber", fib)):
                total[k] += v
            matched_g += grams
            total_g += grams
            details.append({
                "name": canon,
                "amount": amount_text.strip() or ("适量" if not amount_text else amount_text),
                "grams": round(grams, 1),
                "matched": True,
                "usda_id": fid,
                "usda_description": desc,
                **({"unit_source": unit_src} if unit_src not in (None, "") else {}),
            })
            # 展示文本
            disp_amount = amount_text.strip()
            if not disp_amount:
                disp_amount = f"{grams:g}g"
            display.append(f"{canon} {disp_amount}")

        if len(details) < 2:
            skipped.append((rec["name"], "食材不足"))
            continue
        ratio = matched_g / total_g if total_g else 0
        if ratio < 0.55:
            skipped.append((rec["name"], f"覆盖率低 {ratio:.0%}"))
            continue
        if not (30 <= total["calories"] <= 3500):
            skipped.append((rec["name"], f"热量异常 {total['calories']:.0f}"))
            continue
        macro_cal = 4 * total["protein"] + 9 * total["fat"] + 4 * total["carbs"]
        if total["calories"] > 0 and abs(macro_cal - total["calories"]) / total["calories"] > 0.35:
            skipped.append((rec["name"], f"宏量偏差 {macro_cal:.0f}/{total['calories']:.0f}"))
            continue

        seen_names.add(rec["name"])
        minutes = estimate_time(rec)
        cuisine = cuisine_of(rec["name"], rec["category"])
        nutrition = {
            "calories": round(total["calories"]),
            "protein": round(total["protein"], 1),
            "fat": round(total["fat"], 1),
            "carbs": round(total["carbs"], 1),
            "fiber": round(total["fiber"], 1),
        }
        rec_out = {
            "id": "",  # 后填
            "name": rec["name"],
            "cuisine": cuisine,
            "difficulty": rec["difficulty"],
            "cooking_time": time_text(minutes),
            "ingredients": display,
            "steps": "\n".join(rec["steps"]),
            "nutrition_per_serving": nutrition,
            "tags": list(dict.fromkeys(
                tags_of(rec["name"], rec["category"], cuisine, rec["difficulty"], rec["steps"])
                + derived_tags(nutrition))),
            "description": rec["description"] or f"{rec['name']}，{cuisine}{rec['category']}，以{display[0].split()[0]}等食材制作。",
            "source": {
                "name": RECIPE_SOURCE["name"],
                "url": rec["source_url"],
                "license": RECIPE_SOURCE["license"],
                "path": rec["source_path"],
            },
            "nutrition_source": {
                **NUTRITION_SOURCE,
                "matched_ratio": round(ratio, 3),
                "unmatched": unmatched,
            },
            "ingredient_details": details,
        }
        missing_nut.update(rec_missing)  # 仅通过校验的菜谱才触发缺失拦截
        results.append(rec_out)

    if missing_nut:
        raise SystemExit(
            "营养缓存缺少以下食材（staging 或 ITEMS 有变更），请改用 --usda-dir 重建: "
            + ", ".join(sorted(missing_nut)))

    for i, r in enumerate(results, 1):
        r["id"] = f"r{i:03d}"

    meta = {
        "version": VERSION,
        "recipe_count": len(results),
        "recipe_source": RECIPE_SOURCE,
        "nutrition_source": {
            "name": NUTRITION_SOURCE["name"],
            "url": NUTRITION_SOURCE["url"],
            "license": NUTRITION_SOURCE["license"],
            "method": NUTRITION_SOURCE["method"],
        },
    }
    recipes_payload = {"meta": meta, "recipes": results}

    used = {}
    for r in results:
        for d in r["ingredient_details"]:
            used[d["name"]] = used.get(d["name"], 0) + 1
    ingredients_payload = {
        "meta": {
            "version": VERSION,
            "source": NUTRITION_SOURCE["name"],
            "url": NUTRITION_SOURCE["url"],
            "license": NUTRITION_SOURCE["license"],
            "note": "每 100g 可食部。数值为 USDA SR Legacy 对应食物条目原值，未做烹饪损耗修正。",
        },
        "ingredients": {},
    }
    for name in sorted(used):
        fid, desc = MAPPING[name]
        per = nut.get(str(fid), {})
        item = {
            "usda_id": fid,
            "usda_description": desc,
            "per_100g": {
                "calories": round(float(per.get("1008") or 0), 1),
                "protein": round(float(per.get("1003") or 0), 2),
                "fat": round(float(per.get("1004") or 0), 2),
                "carbs": round(float(per.get("1005") or 0), 2),
                "fiber": round(float(per.get("1079") or 0), 2),
            },
            "default_unit_grams": UNIT_GRAMS[name],
            "category": CATEGORY[name],
            "matched": True,
        }
        if name in ASSUMPTION:
            item["assumption"] = ASSUMPTION[name]
        ingredients_payload["ingredients"][name] = item

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recipes.json").write_text(json.dumps(recipes_payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "ingredients.json").write_text(json.dumps(ingredients_payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 统计 ----
    print(f"生成菜谱 {len(results)} 条，跳过 {len(skipped)} 条")
    print("分类分布:", Counter(next((t for t in r["tags"] if t in allowed_cats), "?") for r in results))
    print("菜系分布:", Counter(r["cuisine"] for r in results))
    ratios = [r["nutrition_source"]["matched_ratio"] for r in results]
    print(f"覆盖率 均值={sum(ratios)/len(ratios):.2%} 最低={min(ratios):.2%}")
    cals = [r["nutrition_per_serving"]["calories"] for r in results]
    cals_sorted = sorted(cals)
    print(f"热量 min={cals_sorted[0]} median={cals_sorted[len(cals_sorted)//2]} max={cals_sorted[-1]}")
    print("派生标签:", Counter(t for r in results for t in r["tags"] if t in DERIVED_TAGS))
    low = [r for r in results if r["nutrition_source"]["matched_ratio"] < 0.8]
    print("低覆盖菜谱:", len(low), [(r["name"], r["nutrition_source"]["matched_ratio"]) for r in low[:10]])
    print("\n跳过原因:", Counter(reason.split()[0] for _, reason in skipped))
    for name, reason in skipped[:60]:
        print("  -", name, reason)
    print("\n食材条目数:", len(ingredients_payload["ingredients"]))
    unmatched_all = Counter()
    for r in results:
        for u in r["nutrition_source"]["unmatched"]:
            unmatched_all[u] += 1
    print("未收录食材 Top20:", unmatched_all.most_common(20))


def main():
    ap = argparse.ArgumentParser(description="知识库构建管线：菜谱源 → staging.jsonl → app/rag/*.json")
    ap.add_argument("--stage", choices=["all", "parse", "build"], default="all")
    ap.add_argument("--h2c-dir", type=Path, help="HowToCook dishes 目录（parse 阶段需要）")
    ap.add_argument("--usda-dir", type=Path, default=None,
                    help="USDA SR Legacy CSV 目录；缺省时回读既有 ingredients.json 营养缓存")
    ap.add_argument("--staging", type=Path, default=DEFAULT_STAGING, help="中间格式 JSONL 路径")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="输出目录（默认 app/rag）")
    args = ap.parse_args()

    if args.stage in ("all", "parse"):
        if not args.h2c_dir:
            ap.error("parse 阶段需要 --h2c-dir")
        from parsers.howtocook import parse_dishes

        records = parse_dishes(args.h2c_dir)
        args.staging.parent.mkdir(parents=True, exist_ok=True)
        with open(args.staging, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"staging 写入 {len(records)} 条 → {args.staging}")

    if args.stage in ("all", "build"):
        if not args.usda_dir and not (args.out_dir / "ingredients.json").exists():
            ap.error("build 阶段需要 --usda-dir，或确保已有 app/rag/ingredients.json 营养缓存")
        build(args.staging, args.usda_dir, args.out_dir)


if __name__ == "__main__":
    main()
