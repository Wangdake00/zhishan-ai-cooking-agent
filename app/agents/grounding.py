"""
输出接地（Grounding）与防幻觉自校验

设计原则：
- 零额外 LLM 调用：所有校验都基于"确定性工具"（营养计算 calculate_nutrition、
  RAG 检索 search_recipes_rag）返回的结构化事实，用正则 / 集合比对完成。
  不引入"校验器自身幻觉"，也不增加模型额度消耗与响应时延。
- 不破坏流式：LLM 自然语言照常流式输出给用户；流结束后再追加由工具事实
  直接渲染的"权威数据卡"（硬数字不经过 LLM），并在检测到冲突时追加校正提示。

数据流：
  工具执行结果 (tool_traces)
      → extract_*_facts 解析为权威事实 dict
      → 节点写入 AgentState（随 checkpoint 持久化）
      → stream_chat 流末：verify_answer 校验 + render_*_card 渲染权威卡片
"""
import re
from typing import Optional

from app.common.logger import logger


# ========== 营养事实提取 ==========

# 形如：## 营养成分分析报告（2人份）
_SERVINGS_RE = re.compile(r"营养成分分析报告（\s*(\d+)\s*人份）")
# 形如：- **总卡路里**：330.0 kcal
_CALORIES_RE = re.compile(r"\*\*\s*总卡路里\s*\*\*[：:]\s*([\d.]+)\s*kcal", re.IGNORECASE)
_PROTEIN_RE = re.compile(r"\*\*\s*蛋白质\s*\*\*[：:]\s*([\d.]+)\s*g", re.IGNORECASE)
_FAT_RE = re.compile(r"\*\*\s*脂肪\s*\*\*[：:]\s*([\d.]+)\s*g", re.IGNORECASE)
_CARBS_RE = re.compile(r"\*\*\s*碳水化合物\s*\*\*[：:]\s*([\d.]+)\s*g", re.IGNORECASE)
_FIBER_RE = re.compile(r"\*\*\s*膳食纤维\s*\*\*[：:]\s*([\d.]+)\s*g", re.IGNORECASE)

# 热量校验：容忍"约/大约"，仅匹配明确带"总"字的表述，避免与单食材热量混淆
_TOTAL_CAL_CLAIM_RE = re.compile(r"总(?:卡路里|热量|能量)[^\d]{0,12}?([\d]{2,5}(?:\.\d+)?)\s*(?:kcal|千卡|大卡|卡路里)", re.IGNORECASE)

# 允许的相对偏差：模型常说"约 500 卡"，阈值内视为一致
_CAL_TOLERANCE = 0.25


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def extract_nutrition_facts(tool_traces: list[dict]) -> Optional[dict]:
    """
    从 calculate_nutrition 工具结果（取最后一次）解析权威营养事实。

    Returns:
        {
            "servings": int,
            "calories": float, "protein": float, "fat": float,
            "carbs": float, "fiber": float,
            "unmatched": [未收录食材名...],
        }
        解析失败返回 None。
    """
    last_result = None
    for t in tool_traces:
        if t.get("name") == "calculate_nutrition" and t.get("result"):
            last_result = t["result"]
    if not last_result:
        return None

    text = str(last_result)
    calories = _to_float(_CALORIES_RE.search(text).group(1)) if _CALORIES_RE.search(text) else None
    # 汇总区缺失关键数值时不构造卡片，防止用半截数据误导
    if calories is None:
        logger.warning("[grounding] 营养工具结果未解析到总卡路里，跳过事实提取")
        return None

    servings_m = _SERVINGS_RE.search(text)
    servings = int(servings_m.group(1)) if servings_m else 1

    unmatched: list[str] = []
    for line in text.splitlines():
        if "未找到该食材的营养数据" in line:
            cells = [c.strip() for c in line.split("|")]
            # 形如 ['', '食材名', '100', '-', ..., ''] → 第 1 列是食材名
            if len(cells) >= 2 and cells[1]:
                unmatched.append(cells[1])

    facts = {
        "servings": servings,
        "calories": calories,
        "protein": _to_float(_PROTEIN_RE.search(text).group(1)) if _PROTEIN_RE.search(text) else None,
        "fat": _to_float(_FAT_RE.search(text).group(1)) if _FAT_RE.search(text) else None,
        "carbs": _to_float(_CARBS_RE.search(text).group(1)) if _CARBS_RE.search(text) else None,
        "fiber": _to_float(_FIBER_RE.search(text).group(1)) if _FIBER_RE.search(text) else None,
        "unmatched": unmatched,
    }
    logger.info(f"[grounding] 提取营养事实: {facts['calories']} kcal/{servings}人份, 未收录食材={unmatched}")
    return facts


# ========== 菜谱事实提取 ==========

# 形如：1. 宫保鸡丁  (Rerank分数: 0.82)
_RECIPE_LINE_RE = re.compile(r"^\s*\d+\.\s*(.+?)\s*\(\s*Rerank分数[：:]?\s*([\d.]+)\s*\)", re.MULTILINE)
_RAG_EMPTY_MARKER = "本地知识库中没有找到相关菜谱"


def extract_recipe_facts(tool_traces: list[dict]) -> Optional[dict]:
    """
    从 search_recipes_rag 工具结果（可能多次调用，合并去重）解析权威菜谱名。

    Returns:
        {"names": [菜名...], "empty": bool, "calls": int}
        从未调用 RAG 工具返回 None；调用了但无结果返回 {"names": [], "empty": True}。
    """
    rag_results = [
        str(t["result"]) for t in tool_traces
        if t.get("name") == "search_recipes_rag" and t.get("result")
    ]
    if not rag_results:
        return None

    names: list[str] = []
    seen = set()
    any_empty = False
    for text in rag_results:
        if _RAG_EMPTY_MARKER in text:
            any_empty = True
            continue
        for m in _RECIPE_LINE_RE.finditer(text):
            name = m.group(1).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)

    facts = {
        "names": names,
        # 只有"全部检索都为空"才标记 empty；只要有一次拿到菜谱就算 grounded
        "empty": len(names) == 0 and any_empty,
        "calls": len(rag_results),
    }
    logger.info(f"[grounding] 提取菜谱事实: {len(names)} 道菜, empty={facts['empty']}, calls={facts['calls']}")
    return facts


# ========== 确定性一致性校验 ==========

def verify_nutrition(answer: str, facts: dict) -> list[str]:
    """
    校验 LLM 回答中的"总热量"数值是否与工具计算一致。
    高精确、低误报：仅匹配明确的"总卡路里/总热量 + 数字"表述，超阈值才报警。
    """
    violations: list[str] = []
    authoritative = facts.get("calories")
    if authoritative is None:
        return violations

    for m in _TOTAL_CAL_CLAIM_RE.finditer(answer or ""):
        claimed = _to_float(m.group(1))
        if claimed is None or claimed <= 0:
            continue
        deviation = abs(claimed - authoritative) / authoritative if authoritative else 1.0
        if deviation > _CAL_TOLERANCE:
            violations.append(
                f"回答中的总热量 **{m.group(1)} kcal** 与工具计算结果 "
                f"**{authoritative:g} kcal** 偏差较大（{deviation:.0%}），请以工具计算为准"
            )
            break  # 只报一次，避免刷屏
    return violations


def verify_recipes(answer: str, facts: dict) -> list[str]:
    """
    校验回答是否接地（grounded）到 RAG 检索结果。
    采用高精确规则：RAG 有结果、但回答中一道权威菜名都未出现时，
    判定为"可能脱离检索自由发挥"，提示以清单为准。
    """
    violations: list[str] = []
    names = facts.get("names") or []
    if not names:
        return violations  # 空检索由 render_recipe_card 单独提示，不在此误报

    text = answer or ""
    mentioned = [n for n in names if n in text]
    if not mentioned:
        violations.append(
            "回答未明确引用本次知识库检索到的菜谱，具体菜名与做法请以下方清单为准，勿采信未列出的菜名"
        )
    return violations


# ========== 权威数据卡渲染（硬数据直出，不经过 LLM） ==========

def render_nutrition_card(facts: dict) -> str:
    """由工具事实直接渲染营养数据卡（markdown），作为权威数字兜底。"""
    lines = [
        "",
        "---",
        f"### 📊 营养数据卡（工具计算 · {facts.get('servings', 1)} 人份）",
        "",
        f"- 总热量：**{_fmt(facts.get('calories'))} kcal**",
        f"- 蛋白质：**{_fmt(facts.get('protein'))} g**",
        f"- 脂肪：**{_fmt(facts.get('fat'))} g**",
        f"- 碳水化合物：**{_fmt(facts.get('carbs'))} g**",
        f"- 膳食纤维：**{_fmt(facts.get('fiber'))} g**",
    ]
    unmatched = facts.get("unmatched") or []
    if unmatched:
        lines.append("")
        lines.append(
            "> ⚠️ 以下食材暂无营养数据库数据，**未计入**上方总计，其数值请勿估算编造："
            + "、".join(unmatched)
        )
    return "\n".join(lines)


def render_recipe_card(facts: dict) -> str:
    """由 RAG 事实直接渲染参考菜谱清单（markdown），锚定可信菜名。"""
    names = facts.get("names") or []
    if not names:
        if facts.get("empty"):
            return (
                "\n---\n"
                "> ℹ️ 本地菜谱知识库本次**未检索到**相关菜谱。若上方内容来自联网搜索或模型常识，"
                "请注意甄别，具体用量与做法建议二次核实。"
            )
        return ""

    lines = ["", "---", f"### 📚 本次参考的本地菜谱（知识库实检 {len(names)} 道）", ""]
    for i, name in enumerate(names, 1):
        lines.append(f"{i}. {name}")
    lines.append("")
    lines.append("> 以上为知识库实际检索结果，菜名与做法请以本清单为准。")
    return "\n".join(lines)


def _fmt(v) -> str:
    """数值格式化：None → '—'，浮点去掉多余尾零。"""
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)
