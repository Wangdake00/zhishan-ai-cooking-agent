# -*- coding: utf-8 -*-
"""HowToCook 解析器：dishes 目录 → staging 记录列表

数据源：HowToCook 程序员做饭指南（Unlicense）
https://github.com/Anduin2017/HowToCook
"""
import re
from pathlib import Path

CATEGORY_MAP = {
    "vegetable_dish": "素菜", "meat_dish": "荤菜", "aquatic": "水产",
    "breakfast": "早餐", "staple": "主食", "soup": "汤羹",
    "dessert": "甜品", "drink": "饮品", "condiment": "调料",
}


def parse_file(path: Path, root: Path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.rstrip() for ln in text.splitlines()]
    title = path.stem
    for ln in lines:
        if ln.startswith("# "):
            title = ln[2:].strip().removesuffix("的做法")
            break

    sections = {}
    current = "_intro"
    sections[current] = []
    for ln in lines:
        if ln.startswith("## "):
            current = ln[3:].strip()
            sections[current] = []
        else:
            sections[current].append(ln)

    intro = sections.get("_intro", [])
    description = ""
    for ln in intro:
        s = ln.strip()
        if not s or s.startswith("![") or s.startswith("#") or s.startswith("预估"):
            continue
        description = s
        break
    meta = "\n".join(intro)
    star = re.search(r"预估烹饪难度：([★☆]+)", meta)
    stars = star.group(1).count("★") if star else 3
    difficulty = "简单" if stars <= 2 else ("中等" if stars == 3 else "困难")
    time_match = re.search(r"约需\s*(\d+)\s*分钟", description) or re.search(r"约需\s*(\d+)\s*分钟", meta) \
        or re.search(r"大约需要\s*(\d+(?:\.\d+)?)\s*(分钟|小时)", description) \
        or re.search(r"大约\s*(\d+(?:\.\d+)?)\s*(分钟|小时)", description)
    if time_match:
        val = float(time_match.group(1))
        unit = time_match.group(2) if time_match.lastindex and time_match.lastindex >= 2 else "分钟"
        minutes = int(val * 60) if unit == "小时" else int(val)
    else:
        minutes = None

    has_calc = "计算" in sections
    quantities = []
    for ln in sections.get("计算", []):
        s = ln.strip()
        if s.startswith(("- ", "* ")):
            body = s[2:].strip()
            if body.startswith(("- ", "* ")):
                body = body[2:].strip()
            quantities.append(body)
    quantities = [q for q in quantities if not q.startswith("注意") and not q.startswith("使用上述")]

    steps = []
    for ln in sections.get("操作", []):
        s = ln.strip()
        m = re.match(r"^(\d+)\s*[.、]\s*(.*)", s)
        if m:
            steps.append(m.group(2).strip())
        elif re.match(r"^-\s+", s):
            steps.append(re.sub(r"^-\s+", "", s))
    steps = [s for s in steps if s]

    rel = path.relative_to(root)
    category_key = rel.parts[0]
    category = CATEGORY_MAP.get(category_key, category_key)
    if not quantities or not has_calc or len(steps) < 2:
        return None
    return {
        "name": title,
        "category": category,
        "difficulty": difficulty,
        "time_minutes": minutes,
        "description": description,
        "quantities": quantities,
        "steps": steps,
        "source_path": str(rel).replace("\\", "/"),
        "source_url": "https://github.com/Anduin2017/HowToCook/blob/master/dishes/" + str(rel).replace("\\", "/"),
        "has_calc": has_calc,
    }


UNIT = r"g|克|kg|千克|ml|毫升|mL|L|升|斤|两|勺|小勺|大勺|汤匙|茶匙|tsp|tbsp|片|瓣|根|颗|只|个|枚|块|盒|袋|把|碗|杯|滴|支|朵|粒|棵|圈|张|条|斤"
NUMW = r"半|一|二|两|三|四|五|六|七|八|九|十"


def clean_entry(q: str):
    """返回 (名称, 用量文本, 括号内克重或None, 每份标记)"""
    q = q.replace("*份数", "").replace("* 份数", "").replace("份数 *", "")
    q = q.replace("用量为", "").strip()
    per_serving = False
    m_per = re.search(r"(\d+(?:\.\d+)?)\s*g\s*/\s*人", q)
    if m_per:
        per_serving = True
    # 名称与用量通常用 = 分隔
    if "=" in q:
        left, right = q.split("=", 1)
    else:
        left, right = q, ""
        m = re.search(rf"([{NUMW}\d\.]+(?:\s*[-~]\s*\d+(?:\.\d+)?)?\s*(?:{UNIT}))", q)
        if m:
            idx = m.start()
            left, right = q[:idx], q[idx:]
        else:
            m = re.search(r"(\d+(?:\.\d+)?)\s*$", q)
            if m:
                left, right = q[:m.start()], q[m.start():]
    name = left.strip().strip(",，:：;；、*")
    name = re.sub(r"^[\d\.\s]+", "", name)
    name = re.sub(r"（[^）]*）|\([^)]*\)", "", name).strip()
    name = re.split(r"或者|\bor\b|/", name)[0].strip().strip(",，:：")
    right = right.strip().strip(",，;；")
    # 括号中克重优先
    paren_g = None
    for pm in re.finditer(r"([\d\.]+(?:\s*[-~]\s*[\d\.]+)?)\s*(g|克|ml|毫升)", right):
        paren_g = pm.group(0)
        break
    # 主用量：= 后的第一段
    main = re.split(r"[，,（(]", right)[0].strip()
    if not main:
        main = right
    return name, main, paren_g, per_serving


def parse_dishes(root) -> list:
    """解析 HowToCook dishes 目录，返回 staging 记录列表（含 ingredients_raw）"""
    root = Path(root)
    records = []
    for md in sorted(root.rglob("*.md")):
        rel = md.relative_to(root)
        if rel.parts[0] not in CATEGORY_MAP:
            continue
        rec = parse_file(md, root)
        if rec:
            rec["ingredients_raw"] = [clean_entry(q) for q in rec["quantities"]]
            records.append(rec)
    return records
