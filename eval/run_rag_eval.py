"""RAG 检索质量评测脚本（智膳 AI）

必须在 Docker 容器内运行（宿主机 Windows 直连 DashScope embedding API 会卡死）：
    docker compose exec app python eval/run_rag_eval.py --mode full
    docker compose exec app python eval/run_rag_eval.py --mode all   # 4 种模式一次跑完 + 对比表

指标：Recall@1/3/5、MRR、Hit@5
负例（relevant_ids 为空）不计入 Recall/MRR，单独报告 top1 相似度以观察误召回
"""

import argparse
import json
import os
import sys
import time
from collections import OrderedDict
from datetime import datetime

# 容器内 WORKDIR=/app，但 python 执行脚本时 sys.path[0] 是脚本所在目录 /app/eval，
# 必须手动把项目根目录插到 sys.path 才能 import app.*
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.rag import pipeline  # noqa: E402

MODES = ["full", "no-rewrite", "no-rerank", "vector-only"]
MODE_DESC = {
    "full": "完整管道（改写→向量召回→Rerank）",
    "no-rewrite": "消融 Query 改写（原始 query 向量召回→Rerank）",
    "no-rerank": "消融 Rerank（改写→向量召回直接取 top_n）",
    "vector-only": "纯向量召回（不改写不重排，直接取 top_n）",
}


def load_cases(path):
    """从 jsonl 加载评测用例并做字段校验"""
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            case = json.loads(line)
            for field in ("id", "query", "category", "relevant_ids", "notes"):
                if field not in case:
                    raise ValueError(f"用例第 {line_no} 行缺少字段: {field}")
            cases.append(case)
    return cases


def run_mode(query, mode, top_k, top_n):
    """按消融模式组合 pipeline 内部各阶段函数，返回 (结果列表, 元信息)

    与生产 rag_search 对齐：约束硬过滤(Stage 2.5)全模式生效；
    阈值拒答(Stage 3.5)/偏好微调(Stage 3.6)仅在有真实 DashScope rerank 分时生效。
    """
    meta = {"mode": mode, "rewritten": query, "rewrite_changed": None,
            "rerank_fellback": None, "vector_fallback": False,
            "constraint_filtered": False, "threshold_rejected": False}
    t0 = time.time()

    # Stage 1: Query 改写（full / no-rerank 需要；其余跳过）
    if mode in ("full", "no-rerank"):
        q_info = pipeline.rewrite_query(query)
        meta["rewritten"] = q_info["rewritten"]
        meta["rewrite_changed"] = q_info["rewritten"] != query
        recall_text = q_info["rewritten"]
    else:
        recall_text = query

    # Stage 1.5: 约束抽取先于召回——有硬约束时扩大召回池，
    # 否则否定词带偏的 top_k 小池子过滤后所剩无几（与 rag_search 一致）；
    # 召回与 rerank 打分改用剔除否定短语的净化 query
    constraints = pipeline.extract_constraints(query)
    has_hard_constraints = bool(
        constraints["exclude_ingredients"] or constraints["exclude_tags"]
        or constraints["max_calories"] is not None
    )
    recall_k = max(top_k, 50) if has_hard_constraints else top_k
    scoring_query = pipeline.sanitize_query(query) if (
        has_hard_constraints or constraints["prefer_tags"]) else query
    if scoring_query != query:
        recall_text = scoring_query  # 覆盖改写结果：否定词对 embedding 只有污染

    # Stage 2: 向量召回（失败降级关键字匹配）
    candidates = pipeline.vector_recall(recall_text, top_k=recall_k)
    if not candidates:
        candidates = pipeline.keyword_recall(recall_text.split(), top_k=recall_k)
        meta["vector_fallback"] = True

    if not candidates:
        return [], meta

    # Stage 2.5: 约束硬过滤（全模式生效；过滤后全量送 rerank，不提前截断，
    # 目标菜向量序可能排在 11~50 位——与 rag_search 一致）
    if has_hard_constraints:
        before = len(candidates)
        candidates = pipeline.apply_constraints(candidates, constraints)
        meta["constraint_filtered"] = before != len(candidates)
        if not candidates:
            return [], meta

    # Stage 3: Rerank（full / no-rewrite 需要；其余按向量分直接截断）
    # 有偏好标签时取全量重排，偏好微调后再截断到 top_n（与 rag_search 一致）
    if mode in ("full", "no-rewrite"):
        rerank_top_n = len(candidates) if constraints["prefer_tags"] else top_n
        results, fellback = pipeline.dashscope_rerank(scoring_query, candidates, top_n=rerank_top_n)
        meta["rerank_fellback"] = fellback
        # Stage 3.5: 偏好微调先于阈值判定（边界正例靠微调过阈）
        if constraints["prefer_tags"] and results:
            pipeline.apply_prefer_nudge(results, constraints["prefer_tags"])
            results = results[:top_n]
        # Stage 3.6: 阈值拒答（仅真实 rerank 分可跨 query 比较；硬约束查询豁免）
        if not fellback and not has_hard_constraints and results and \
                results[0].get("rerank_score", 0) < pipeline.RAG_RERANK_THRESHOLD:
            meta["threshold_rejected"] = True
            results = []
    else:
        results = candidates[:top_n]

    meta["total_ms"] = int((time.time() - t0) * 1000)
    return results, meta


def eval_case(case, mode, top_k, top_n):
    """跑单条用例并计算指标：RR（用于 MRR）、Recall@1/3/5、Hit@5"""
    results, meta = run_mode(case["query"], mode, top_k, top_n)
    rel = set(case["relevant_ids"])
    ids = [r["id"] for r in results]

    # 首个相关结果的倒数排名
    rr = 0.0
    for rank, rid in enumerate(ids, 1):
        if rid in rel:
            rr = 1.0 / rank
            break

    recall = {k: (len(rel & set(ids[:k])) / len(rel) if rel else None) for k in (1, 3, 5)}
    hit5 = bool(rel) and bool(rel & set(ids[:5]))
    return {"case": case, "mode": mode, "results": results, "meta": meta,
            "rr": rr, "recall": recall, "hit5": hit5}


def fmt_score(r):
    s = f"vec={r['score']:.3f}"
    if "rerank_score" in r:
        s += f", rerank={r['rerank_score']:.3f}"
    return s


def print_case_detail(run):
    """逐条明细：query、改写、top5 结果、是否命中、指标"""
    c, meta = run["case"], run["meta"]
    rel = set(c["relevant_ids"])
    print(f"\n[{c['id']}|{c['category']}] {c['query']}")
    print(f"  相关: {','.join(c['relevant_ids']) or '（负例）'} | {c['notes']}")
    if meta["rewrite_changed"] is not None:
        tag = "（改写生效）" if meta["rewrite_changed"] else "（未变化/降级）"
        print(f"  改写: '{meta['rewritten']}'{tag}")
    if meta["vector_fallback"]:
        print("  ⚠️ 向量召回失败，已降级 keyword_recall")
    if meta["rerank_fellback"]:
        print("  ⚠️ Rerank 降级为 rule_based_rerank")
    if meta.get("constraint_filtered"):
        print("  🔧 约束硬过滤生效")
    if meta.get("threshold_rejected"):
        print(f"  🚫 阈值拒答（top1 rerank 分 < {pipeline.RAG_RERANK_THRESHOLD}）")
    if not run["results"]:
        print("  ❌ 无任何结果")
        return
    for i, r in enumerate(run["results"], 1):
        mark = "✅" if r["id"] in rel else ("　" if rel else "·")
        print(f"  {i}. {r['id']} {r['name']} ({fmt_score(r)}) {mark}")
    if rel:
        print(f"  RR={run['rr']:.2f} | Recall@1={run['recall'][1]:.2f} "
              f"@3={run['recall'][3]:.2f} @5={run['recall'][5]:.2f} | "
              f"Hit@5={'命中' if run['hit5'] else '未命中'}")
    else:
        top1 = run["results"][0]
        extra = f", rerank={top1['rerank_score']:.3f}" if "rerank_score" in top1 else ""
        print(f"  误召回观察: top1={top1['id']} {top1['name']} 向量相似度={top1['score']:.3f}{extra}")


def summarize(runs):
    """汇总指标：负例不计入 Recall/MRR/Hit"""
    pos = [r for r in runs if r["case"]["relevant_ids"]]
    if not pos:
        return None
    n = len(pos)

    def mean(fn):
        return sum(fn(r) for r in pos) / n

    return {
        "n_cases": len(runs), "n_positive": n,
        "recall@1": mean(lambda r: r["recall"][1]),
        "recall@3": mean(lambda r: r["recall"][3]),
        "recall@5": mean(lambda r: r["recall"][5]),
        "mrr": mean(lambda r: r["rr"]),
        "hit@5": sum(1 for r in pos if r["hit5"]) / n,
        "avg_ms": sum(r["meta"]["total_ms"] for r in runs) / len(runs),
    }


def print_summary_table(summaries):
    print("\n" + "=" * 66)
    print("汇总指标（负例不计入 Recall/MRR）")
    print(f"{'模式':<12}{'R@1':>8}{'R@3':>8}{'R@5':>8}{'MRR':>8}{'Hit@5':>8}{'均时延ms':>10}")
    print("-" * 66)
    for mode, s in summaries.items():
        if s is None:
            continue
        print(f"{mode:<12}{s['recall@1']:>8.3f}{s['recall@3']:>8.3f}{s['recall@5']:>8.3f}"
              f"{s['mrr']:>8.3f}{s['hit@5']:>8.3f}{s['avg_ms']:>10.0f}")
    print("=" * 66)


def print_category_breakdown(runs):
    """按类别分组输出 MRR / Hit@5，用于定位短板类别（负例不计入）"""
    groups = OrderedDict()
    for r in runs:
        if not r["case"]["relevant_ids"]:
            continue
        groups.setdefault(r["case"]["category"], []).append(r)
    if not groups:
        return
    print(f"\n分类别指标 [{runs[0]['mode']}]（负例不计入）")
    print(f"{'类别':<18}{'条数':>6}{'MRR':>8}{'Hit@5':>8}")
    print("-" * 40)
    for cat, rs in groups.items():
        mrr = sum(x["rr"] for x in rs) / len(rs)
        hit5 = sum(1 for x in rs if x["hit5"]) / len(rs)
        print(f"{cat:<18}{len(rs):>6}{mrr:>8.3f}{hit5:>8.3f}")


def print_negatives(runs):
    """负例误召回观察：top1 向量相似度越高 = 误召回越严重；空结果 = 已被拦截"""
    negs = [r for r in runs if not r["case"]["relevant_ids"]]
    if not negs:
        return
    intercepted = sum(1 for r in negs if not r["results"])
    print(f"\n负例误召回观察 [{runs[0]['mode']}]（拦截 {intercepted}/{len(negs)}；top1 向量相似度越高 = 误召回越严重）")
    for r in negs:
        c = r["case"]
        if not r["results"]:
            reason = "阈值拒答" if r["meta"].get("threshold_rejected") else "无结果"
            print(f"  {c['id']} {c['query']}: ✅ 已拦截（{reason}）")
            continue
        top1 = r["results"][0]
        extra = f", rerank={top1['rerank_score']:.3f}" if "rerank_score" in top1 else ""
        tail = " | ".join(f"{x['id']}{x['name']}" for x in r["results"][:5])
        print(f"  {c['id']} {c['query']}: ❌ 误召回 top1={top1['id']} {top1['name']} vec={top1['score']:.3f}{extra}")
        print(f"      top5: {tail}")


def print_failures(runs):
    """失败用例清单：正例但无一相关菜谱进 top5"""
    fails = [r for r in runs if r["case"]["relevant_ids"] and not r["hit5"]]
    if not fails:
        print(f"\n✅ [{runs[0]['mode']}] 无失败用例（所有正例至少有 1 个相关菜谱进 top5）")
        return
    print(f"\n❌ 失败用例清单 [{runs[0]['mode']}]（期望命中但无一相关菜谱进 top5）")
    for r in fails:
        c = r["case"]
        got = " | ".join(f"{x['id']}{x['name']}" for x in r["results"][:5]) or "无结果"
        print(f"  {c['id']} {c['query']} 期望: {','.join(c['relevant_ids'])}")
        print(f"      实际 top5: {got}")


def main():
    parser = argparse.ArgumentParser(description="RAG 检索质量评测")
    parser.add_argument("--mode", default="full", choices=MODES + ["all"],
                        help="评测模式，all=依次跑完 4 种消融模式并输出对比表")
    parser.add_argument("--cases",
                        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "rag_cases.jsonl"))
    parser.add_argument("--top-k", type=int, default=pipeline.RAG_TOP_K)
    parser.add_argument("--top-n", type=int, default=pipeline.RAG_TOP_N)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    modes = MODES if args.mode == "all" else [args.mode]

    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"评测集: {args.cases} ({len(cases)} 条) | top_k={args.top_k} top_n={args.top_n}")
    print(f"模式: {', '.join(modes)}")

    all_runs = OrderedDict()
    for mode in modes:
        print(f"\n{'#' * 66}\n# 模式: {mode} — {MODE_DESC[mode]}\n{'#' * 66}")
        runs = []
        for c in cases:
            run = eval_case(c, mode, args.top_k, args.top_n)
            runs.append(run)
            if len(modes) == 1:
                print_case_detail(run)
            else:
                # all 模式压缩为单行进度，最终统一输出对比表
                if c["relevant_ids"]:
                    status = "✅" if run["hit5"] else "❌"
                    desc = f"RR={run['rr']:.2f} R@5={run['recall'][5]:.2f}"
                else:
                    status, desc = "—", "负例"
                print(f"  {status} {c['id']} [{c['category']}] {c['query']} → {desc}")
        all_runs[mode] = runs

    if len(modes) == 1:
        runs = all_runs[modes[0]]
        print_summary_table({modes[0]: summarize(runs)})
        print_category_breakdown(runs)
        print_negatives(runs)
        print_failures(runs)
    else:
        print_summary_table({m: summarize(all_runs[m]) for m in modes})
        for m in modes:
            print_category_breakdown(all_runs[m])
        for m in modes:
            print_negatives(all_runs[m])
        for m in modes:
            print_failures(all_runs[m])


if __name__ == "__main__":
    main()
