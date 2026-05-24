"""
最小连通性测试:加载 .env 凭证,对上游发一个 "hi" 请求,验证 Token/Cookie 是否有效。
用法: uv run --with httpx --with python-dotenv test_upstream.py
"""
import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("IMMERSIVE_TOKEN", "")
COOKIE = os.getenv("IMMERSIVE_COOKIE", "")
EXT_ID = os.getenv("EXTENSION_ID", "bpoadfkcbjbfhfodiogcnhhhpibjhbnh")
URL = os.getenv("UPSTREAM_URL", "https://api2.immersivetranslate.com/qwen/translate/stream")
MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3.5-plus"

headers = {
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
    "X-Imt-Product-Line": "ai_writing",
}

body = {
    "enable_thinking": False,
    "model": MODEL,
    "temperature": 0,
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
}

print(f"[*] Token: {TOKEN[:8]}...{TOKEN[-4:]}  (len={len(TOKEN)})")
print(f"[*] Cookie len: {len(COOKIE)}")
print(f"[*] Model: {MODEL}")
print(f"[*] POST {URL}\n")

content = ""
usage = None
with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
    with client.stream("POST", URL, headers=headers, json=body) as resp:
        print(f"[*] HTTP {resp.status_code}")
        if resp.status_code != 200:
            print("[!] 上游返回非 200:")
            print(resp.read().decode("utf-8", errors="ignore")[:1000])
            sys.exit(1)
        import json as _json
        for line in resp.iter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = _json.loads(payload)
            except Exception:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices", []):
                delta = (ch.get("delta") or {})
                if delta.get("content"):
                    content += delta["content"]

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
print(f"\n[+] 回复内容: {content!r}")
print(f"[+] usage: {usage}")
print("\n[OK] 凭证有效,上游连通正常!" if content or usage else "\n[!] 无内容返回,请检查")
