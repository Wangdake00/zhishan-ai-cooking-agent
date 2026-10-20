"""临时调试：逐案打印 constraint 失败用例的管道细节（验证后删除）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.rag import pipeline

QUERIES = [
    ("case_11", "青瓜怎么拌好吃？"),
    ("case_32", "正在减脂，想要低卡又可以当主食的菜"),
    ("case_34", "口腔溃疡不能吃辣，推荐清淡不辣的菜"),
    ("case_39", "孕早期想吃酸的，不要油腻"),
    ("case_40", "清真饮食，不能吃猪肉"),
    ("case_42", "胃不舒服，想吃软烂好消化的"),
]

for cid, q in QUERIES:
    constraints = pipeline.extract_constraints(q)
    scoring = pipeline.sanitize_query(q) if pipeline._constraints_active(constraints) else q
    has_hard = bool(constraints["exclude_ingredients"] or constraints["exclude_tags"] or constraints["max_calories"] is not None)
    recall_k = max(10, 50) if has_hard else 10
    candidates = pipeline.vector_recall(scoring, top_k=recall_k)
    n_recall = len(candidates)
    filtered = pipeline.apply_constraints(candidates, constraints)[:10] if has_hard else candidates
    rerank_top_n = len(filtered) if constraints["prefer_tags"] else 5
    results, fb = pipeline.dashscope_rerank(scoring, filtered, top_n=rerank_top_n)
    if constraints["prefer_tags"] and results:
        pipeline.apply_prefer_nudge(results, constraints["prefer_tags"])
        results = results[:5]
    top1 = results[0]["rerank_score"] if results else None
    rejected = (not fb) and results and top1 < pipeline.RAG_RERANK_THRESHOLD
    print(f"\n[{cid}] {q}")
    print(f"  净化query: '{scoring}' | 召回{n_recall}→过滤后{len(filtered)} | rerank降级={fb}")
    print(f"  约束: {constraints}")
    if results:
        for r in results:
            print(f"    {r['id']} {r['name']} rerank={r.get('rerank_score')}")
        print(f"  top1={top1} → {'❌ 阈值拒答' if rejected else '✅ 通过'}")
    else:
        print("    无结果（过滤为空或rerank为空）")
