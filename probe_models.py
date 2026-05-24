"""
模型探测:对每个候选 model id 发一个最小请求,看哪些可用。
串行执行 + 间隔延迟,防止触发上游风控。

用法:
    uv run --with httpx --with python-dotenv probe_models.py
    uv run --with httpx --with python-dotenv probe_models.py extra-model-id  # 追加候选

输出:可用 / 不可用清单,并把可用清单写入 available_models.txt
"""
import os
import sys
import time
import json
import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("IMMERSIVE_TOKEN", "").strip()
COOKIE = os.getenv("IMMERSIVE_COOKIE", "").strip()
EXT_ID = os.getenv("EXTENSION_ID", "bpoadfkcbjbfhfodiogcnhhhpibjhbnh").strip()
URL = os.getenv("UPSTREAM_URL", "https://api2.immersivetranslate.com/qwen/translate/stream").strip()
PRODUCT_LINE = os.getenv("X_IMT_PRODUCT_LINE", "ai_writing").strip()

# 探测间隔(秒),防风控
DELAY = float(os.getenv("PROBE_DELAY", "3"))

# 候选 model id —— 含已确认 + UI 上能看到的推测值 + 常见命名变体
CANDIDATES = [
    # 已确认
    "qwen3.5-plus",
    # DeepSeek V4 Flash
    "deepseek-v4-flash", "deepseek-v4", "deepseek-chat", "deepseek-v3",
    # Gemini 3 Flash
    "gemini-3-flash", "gemini-3.0-flash", "gemini-flash-3",
    # GPT-5 mini
    "gpt-5-mini", "gpt-5-mini-2025", "gpt-5", "gpt-5-nano",
    # GLM-4.7
    "glm-4.7", "glm-4.6", "glm-4-plus",
    # Claude Haiku 4.5
    "claude-haiku-4.5", "claude-haiku-4-5", "claude-3-5-haiku",
    # Grok 4.3
    "grok-4.3", "grok-4", "grok-3",
    # PLaMo 2.2 Prime
    "plamo-2.2-prime", "plamo-2-prime", "plamo-2.2",
    # HY 2.0 Instruct (腾讯混元?)
    "hy-2.0-instruct", "hunyuan-2.0", "hunyuan-instruct", "hy-2.0",
]

# 命令行追加候选
CANDIDATES += [a for a in sys.argv[1:] if a not in CANDIDATES]

HEADERS = {
    "Accept": "text/event-stream",
    "Accept-Language": "zh-CN",
    "Content-Type": "application/json",
    "Origin": f"chrome-extension://{EXT_ID}",
    "Token": TOKEN,
    "Cookie": COOKIE,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "X-Imt-Product-Line": PRODUCT_LINE,
}


def probe(client: httpx.Client, model: str) -> tuple[str, str]:
    """返回 (状态标记, 详情)。状态: OK / FAIL"""
    body = {
        "enable_thinking": False,
        "model": model,
        "temperature": 0,
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }
    try:
        with client.stream("POST", URL, headers=HEADERS, json=body) as resp:
            if resp.status_code != 200:
                raw = resp.read().decode("utf-8", errors="ignore")[:200]
                return "FAIL", f"HTTP {resp.status_code}: {raw}"
            # 读到第一段真正的 content,确认模型真的在工作
            real_model = None
            got_content = False
            for line in resp.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                if obj.get("model"):
                    real_model = obj["model"]
                for ch in obj.get("choices", []):
                    if (ch.get("delta") or {}).get("content"):
                        got_content = True
                # 拿到内容就够了,提前结束省 token
                if got_content:
                    break
            if got_content:
                tag = f"-> 实际 model={real_model}" if real_model and real_model != model else ""
                return "OK", tag
            return "FAIL", "200 但无内容返回"
    except httpx.HTTPError as e:
        return "FAIL", f"网络异常: {e}"


def main():
    if not TOKEN or not COOKIE:
        print("[!] .env 里缺少凭证")
        sys.exit(1)

    print(f"[*] 共 {len(CANDIDATES)} 个候选,间隔 {DELAY}s 串行探测...\n")
    available, failed = [], []

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for i, model in enumerate(CANDIDATES, 1):
            status, detail = probe(client, model)
            mark = "[OK]  " if status == "OK" else "[FAIL]"
            print(f"{mark} {model:<24} {detail}")
            (available if status == "OK" else failed).append((model, detail))
            if i < len(CANDIDATES):
                time.sleep(DELAY)

    print("\n" + "=" * 50)
    print(f"可用模型 ({len(available)}):")
    for m, d in available:
        print(f"  - {m}  {d}")
    print(f"\n不可用 ({len(failed)}):")
    for m, d in failed:
        print(f"  - {m}")

    with open("available_models.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(m for m, _ in available) + "\n")
    print(f"\n[+] 可用清单已写入 available_models.txt")
    print(f"[+] 可直接粘进 .env 的 SUPPORTED_MODELS:")
    print("    SUPPORTED_MODELS=" + ",".join(m for m, _ in available))


if __name__ == "__main__":
    main()
