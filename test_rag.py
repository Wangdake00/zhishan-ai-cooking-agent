"""RAG 管道测试脚本"""
from app.rag.pipeline import rag_search

result = rag_search("清淡的减脂餐", top_k=10, top_n=5)

print(f"\n{'='*60}")
print(f"检索到 {len(result['results'])} 道菜")
print(f"{'='*60}")

for i, recipe in enumerate(result["results"], 1):
    score = recipe.get("rerank_score", 0)
    print(f"  {i}. {recipe['name']} (Rerank: {score})")
    print(f"     菜系: {recipe['cuisine']} | 标签: {recipe.get('tags', '')}")

print(f"\n--- Pipeline 执行日志 ---")
for line in result["pipeline_log"]:
    print(f"  {line}")

print(f"\nFallback 降级: {result['fallback_used']}")
print(f"Query 改写: '{result['query_info']['original']}' -> '{result['query_info']['rewritten']}'")
print(f"关键词: {result['query_info']['keywords']}")
