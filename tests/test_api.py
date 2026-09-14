"""
智膳 AI — API 接口快速测试
============================
运行方式: python test_api.py

这是一个快速的 API 集成测试，通过 HTTP 请求验证：
  1. 用户注册/登录
  2. 流式对话接口（chat/stream）
  3. 会话管理接口
  4. 历史消息接口

前提条件：后端已启动 (python -m app.main)
"""

import requests
import time
import json
import sys

BASE_URL = "http://127.0.0.1:8001/api/v1"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def check(name: str, condition: bool, detail: str = ""):
    if condition:
        print(f"  {GREEN}✓{RESET} {name}  {CYAN}{detail}{RESET}")
    else:
        print(f"  {RED}✗{RESET} {name}  {CYAN}{detail}{RESET}")


def section(title: str):
    print(f"\n{YELLOW}{'='*60}{RESET}")
    print(f"{YELLOW}  {title}{RESET}")
    print(f"{YELLOW}{'='*60}{RESET}")


def test_health():
    section("检查 0: 服务健康检查")
    try:
        resp = requests.get("http://127.0.0.1:8001/health", timeout=5)
        check("服务在线", resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        check("返回应用名", "app" in data, f"app={data.get('app')}")
        check("返回版本", "version" in data, f"version={data.get('version')}")
        return True
    except requests.ConnectionError:
        print(f"  {RED}✗{RESET} 后端未启动，请先运行: python -m app.main")
        return False


def test_auth():
    section("检查 1: 用户认证")
    test_user = f"test_{int(time.time())}"
    test_pass = "test123456"

    # 注册
    resp = requests.post(f"{BASE_URL}/auth/register", json={
        "username": test_user,
        "password": test_pass,
    }, timeout=10)
    check("注册成功", resp.status_code == 200, f"HTTP {resp.status_code}")

    # 登录
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "username": test_user,
        "password": test_pass,
    }, timeout=10)
    check("登录成功", resp.status_code == 200, f"HTTP {resp.status_code}")

    if resp.status_code == 200:
        token = resp.json().get("access_token", "")
        check("获取 Token", len(token) > 0)
        return token, test_user

    return None, test_user


def test_stream_chat(token: str):
    section("检查 2: 流式对话 (StateGraph)")

    headers = {"Authorization": f"Bearer {token}"}
    thread_id = f"api_test_{int(time.time())}"

    # 测试通用对话
    resp = requests.post(
        f"{BASE_URL}/chat/stream",
        json={
            "message": "你好，介绍一下你自己",
            "thread_id": thread_id,
            "image_urls": [],
        },
        headers=headers,
        stream=True,
        timeout=180,  # 流式链路含多次 LLM/RAG 调用，放宽超时避免慢响应误报
    )
    check("通用对话请求", resp.status_code == 200, f"HTTP {resp.status_code}")

    # 读取流式响应
    chunks = []
    for line in resp.iter_lines(decode_unicode=True):
        if line:
            chunks.append(line)
    full_text = "".join(chunks)
    check("通用对话有响应", len(full_text) > 0, f"{len(chunks)} 个 chunk, {len(full_text)} 字符")

    # 测试营养意图
    thread_id2 = f"api_test_nutrition_{int(time.time())}"
    resp2 = requests.post(
        f"{BASE_URL}/chat/stream",
        json={
            "message": "帮我算一下100g牛肉的卡路里和蛋白质含量",
            "thread_id": thread_id2,
            "image_urls": [],
        },
        headers=headers,
        stream=True,
        timeout=180,  # 流式链路含多次 LLM/RAG 调用，放宽超时避免慢响应误报
    )
    check("营养意图请求", resp2.status_code == 200)

    chunks2 = []
    for line in resp2.iter_lines(decode_unicode=True):
        if line:
            chunks2.append(line)
    full_text2 = "".join(chunks2)
    check("营养分析有响应", len(full_text2) > 0, f"{len(chunks2)} 个 chunk")

    has_nutrition = any(kw in full_text2 for kw in ["卡路里", "蛋白质", "营养", "kcal"])
    check("营养分析包含关键词", has_nutrition, f"含卡路里/蛋白质: {has_nutrition}")

    # 测试菜谱意图
    thread_id3 = f"api_test_recipe_{int(time.time())}"
    resp3 = requests.post(
        f"{BASE_URL}/chat/stream",
        json={
            "message": "推荐几道适合减肥吃的清淡菜谱",
            "thread_id": thread_id3,
            "image_urls": [],
        },
        headers=headers,
        stream=True,
        timeout=180,  # 流式链路含多次 LLM/RAG 调用，放宽超时避免慢响应误报
    )
    check("菜谱意图请求", resp3.status_code == 200)

    chunks3 = []
    for line in resp3.iter_lines(decode_unicode=True):
        if line:
            chunks3.append(line)
    full_text3 = "".join(chunks3)
    check("菜谱推荐有响应", len(full_text3) > 0)

    has_recipe = any(kw in full_text3 for kw in ["菜谱", "做法", "食材", "推荐"])
    check("菜谱推荐包含关键词", has_recipe)

    return [thread_id, thread_id2, thread_id3]


def test_session_management(token: str, thread_ids: list):
    section("检查 3: 会话管理")

    headers = {"Authorization": f"Bearer {token}"}

    # 列出会话
    resp = requests.get(f"{BASE_URL}/chat/sessions", headers=headers, timeout=10)
    check("获取会话列表", resp.status_code == 200)

    if resp.status_code == 200:
        sessions = resp.json().get("sessions", [])
        check("会话列表非空", len(sessions) >= len(thread_ids), f"共 {len(sessions)} 个会话")

        # 检查 thread_id 是否在列表中
        thread_id_set = {s["thread_id"] for s in sessions}
        for tid in thread_ids:
            check(f"会话 {tid[:20]}... 存在", tid in thread_id_set)

    # 获取历史消息
    for tid in thread_ids:
        resp = requests.get(
            f"{BASE_URL}/chat/messages",
            params={"thread_id": tid},
            headers=headers,
            timeout=10,
        )
        check(f"获取消息 {tid[:20]}...", resp.status_code == 200)

        if resp.status_code == 200:
            msgs = resp.json().get("messages", [])
            check(f"  消息数 ≥ 2", len(msgs) >= 2, f"{len(msgs)} 条消息")

    # 删除会话
    for tid in thread_ids:
        resp = requests.delete(
            f"{BASE_URL}/chat/messages",
            params={"thread_id": tid},
            headers=headers,
            timeout=10,
        )
        check(f"删除会话 {tid[:20]}...", resp.status_code == 200)

    # 验证已删除
    resp = requests.get(f"{BASE_URL}/chat/sessions", headers=headers, timeout=10)
    if resp.status_code == 200:
        remaining = resp.json().get("sessions", [])
        deleted_count = sum(1 for tid in thread_ids if tid not in {s["thread_id"] for s in remaining})
        check("会话已删除", deleted_count == len(thread_ids),
              f"删除 {deleted_count}/{len(thread_ids)}")


def main():
    print(f"\n{CYAN}{'#'*60}{RESET}")
    print(f"{CYAN}#  智膳 AI — API 接口测试{RESET}")
    print(f"{CYAN}{'#'*60}{RESET}")

    if not test_health():
        print(f"\n{RED}请先启动后端: python -m app.main{RESET}")
        sys.exit(1)

    token, username = test_auth()
    if not token:
        print(f"\n{RED}认证失败，跳过后续测试{RESET}")
        sys.exit(1)

    thread_ids = test_stream_chat(token)
    test_session_management(token, thread_ids)

    print(f"\n{GREEN}{'🎉'*3}  API 测试完成！{'🎉'*3}{RESET}")


if __name__ == "__main__":
    main()
