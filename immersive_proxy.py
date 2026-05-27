"""
沉浸式翻译 Pro -> OpenAI 兼容 API 中转服务

架构:
  - 除 Gemini 外的所有模型走通用 OpenAI 网关 /qwen/translate/stream,标准 OpenAI 透传。
  - Gemini 走 /gemini/translate/stream,响应是 Gemini 原生格式,本服务翻译成 OpenAI 格式。

启动:
    uv run --with fastapi --with uvicorn --with httpx --with python-dotenv immersive_proxy.py
配置全部走 .env,见 .env.example。
"""

import os
import sys
import json
import time
import uuid
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn

# ============ 配置(从 .env 读取)============
load_dotenv()

IMMERSIVE_TOKEN = os.getenv("IMMERSIVE_TOKEN", "").strip()
IMMERSIVE_COOKIE = os.getenv("IMMERSIVE_COOKIE", "").strip()
EXTENSION_ID = os.getenv("EXTENSION_ID", "bpoadfkcbjbfhfodiogcnhhhpibjhbnh").strip()
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "").strip()
PRODUCT_LINE = os.getenv("X_IMT_PRODUCT_LINE", "ai_writing").strip()

# 上游基址 + 各端点路径
API_BASE = os.getenv("API_BASE", "https://api2.immersivetranslate.com").strip().rstrip("/")
OPENAI_PATH = os.getenv("OPENAI_PATH", "/qwen/translate/stream").strip()
GEMINI_PATH = os.getenv("GEMINI_PATH", "/gemini/translate/stream").strip()
CLAUDE_PATH = os.getenv("CLAUDE_PATH", "/claude/translate/stream").strip()

HOST = os.getenv("HOST", "127.0.0.1").strip()
PORT = int(os.getenv("PORT", "8000"))

MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "2"))
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "120"))

SUPPORTED_MODELS = [
    m.strip()
    for m in os.getenv(
        "SUPPORTED_MODELS",
        "qwen3.5-plus,DeepSeek-V4-Flash,gpt-5-mini,glm-4.7,grok-4-3,"
        "plamo-2.2-prime,gemini-3-flash-preview",
    ).split(",")
    if m.strip()
]

# model id 别名归一化:客户端常用写法 -> 上游真实 id
MODEL_ALIASES = {
    "grok-4.3": "grok-4-3",
    "grok-4-3": "grok-4-3",
    "deepseek-v4-flash": "DeepSeek-V4-Flash",
    "gemini-3-flash": "gemini-3-flash-preview",
    "claude-haiku-4.5": "claude-haiku-4.5-20251001",
    "claude-haiku-4-5": "claude-haiku-4.5-20251001",
}

# Claude 端点需要的额外请求头
CLAUDE_HEADERS = {
    "Anthropic-Version": "2023-06-01",
    "Anthropic-Dangerous-Direct-Browser-Access": "true",
}
# =============================================

# ============ 日志 ============
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("immersive_proxy")

_semaphore: Optional[asyncio.Semaphore] = None


def _check_credentials() -> None:
    if not IMMERSIVE_TOKEN or not IMMERSIVE_COOKIE:
        logger.warning("IMMERSIVE_TOKEN / IMMERSIVE_COOKIE 未配置,请检查 .env!上游会 401。")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _semaphore
    _semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    _check_credentials()
    logger.info(
        "服务启动 | base=%s | models=%d | max_concurrency=%d | auth=%s",
        API_BASE, len(SUPPORTED_MODELS), MAX_CONCURRENCY, "on" if PROXY_API_KEY else "off",
    )
    yield


app = FastAPI(title="Immersive Translate OpenAI Proxy", lifespan=lifespan)


# ============ 路由 / 归一化 ============
def normalize_model(model: str) -> str:
    return MODEL_ALIASES.get(model.lower().strip(), model)


def route_for_model(model: str) -> tuple[str, str, dict]:
    """返回 (上游 URL, 格式 'openai'|'gemini', 额外请求头)"""
    m = model.lower()
    if m.startswith("gemini"):
        return API_BASE + GEMINI_PATH, "gemini", {}
    if m.startswith("claude"):
        # Claude 走专用路径,响应仍是 OpenAI 格式,但需 Anthropic 头
        return API_BASE + CLAUDE_PATH, "openai", dict(CLAUDE_HEADERS)
    return API_BASE + OPENAI_PATH, "openai", {}


def build_upstream_headers() -> dict:
    return {
        "Accept": "text/event-stream",
        "Accept-Language": "zh-CN",
        "Content-Type": "application/json",
        "Origin": f"chrome-extension://{EXTENSION_ID}",
        "Token": IMMERSIVE_TOKEN,
        "Cookie": IMMERSIVE_COOKIE,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "X-Imt-Product-Line": PRODUCT_LINE,
    }


def build_upstream_body(body: dict, fmt: str, model: str) -> dict:
    """根据目标格式构造上游请求体"""
    messages = body.get("messages", [])
    temperature = body.get("temperature", 0)

    if fmt == "gemini":
        # Gemini 端点:OpenAI messages + generationConfig 包装(模拟扩展行为)
        gen_cfg = {
            "temperature": temperature,
            "thinkingConfig": {"thinkingBudget": 0},
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ],
        }
        if body.get("max_tokens"):
            gen_cfg["maxOutputTokens"] = body["max_tokens"]
        return {
            "generationConfig": gen_cfg,
            "model": model,
            "temperature": temperature,
            "messages": messages,
        }

    # openai 格式:基本透传,强制流式
    out = dict(body)
    out["model"] = model
    out["stream"] = True
    out.setdefault("temperature", 0)
    if model.lower().startswith("claude"):
        # Anthropic 要求 max_tokens
        out.setdefault("max_tokens", 2048)
    else:
        out.setdefault("enable_thinking", False)
    return out


def check_auth(authorization: Optional[str]) -> None:
    if not PROXY_API_KEY:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if authorization.split(" ", 1)[1] != PROXY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _friendly_upstream_error(status: int, raw: str) -> dict:
    if status == 401:
        msg = "上游认证失败(401):Token/Cookie 可能已失效,需重新抓包替换 .env"
    elif status == 403:
        msg = "上游拒绝(403):可能触发风控或权限不足"
    elif status == 429:
        msg = "上游限流(429):请求过快、额度耗尽,或该模型需更高套餐"
    elif 500 <= status < 600:
        msg = f"上游服务异常({status})"
    else:
        msg = f"上游返回错误({status})"
    return {"error": {"message": msg, "type": "upstream_error", "code": status,
                      "upstream_detail": raw[:500]}}


# ============ Gemini -> OpenAI 翻译 ============
def _gemini_extract_text(obj: dict) -> str:
    text = ""
    for cand in obj.get("candidates", []):
        for part in (cand.get("content", {}) or {}).get("parts", []):
            if part.get("text"):
                text += part["text"]
    return text


def _gemini_usage_to_openai(meta: dict) -> dict:
    return {
        "prompt_tokens": meta.get("promptTokenCount", 0),
        "completion_tokens": meta.get("candidatesTokenCount", 0),
        "total_tokens": meta.get("totalTokenCount", 0),
    }


def _openai_chunk(resp_id: str, created: int, model: str, delta: dict,
                  finish_reason=None, usage=None) -> dict:
    choices = []
    if usage is None:
        choices = [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    chunk = {
        "id": resp_id, "object": "chat.completion.chunk",
        "created": created, "model": model, "choices": choices,
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


# ============ /v1/models ============
@app.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(default=None)):
    check_auth(authorization)
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "immersive-translate"}
            for m in SUPPORTED_MODELS
        ],
    }


# ============ /v1/chat/completions ============
@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: Optional[str] = Header(default=None)):
    check_auth(authorization)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    client_model = body.get("model", "qwen3.5-plus")
    model = normalize_model(client_model)
    url, fmt, extra_headers = route_for_model(model)
    client_wants_stream = bool(body.get("stream", True))
    n_messages = len(body.get("messages", []))

    headers = build_upstream_headers()
    headers.update(extra_headers)
    upstream_body = build_upstream_body(body, fmt, model)
    started = time.monotonic()
    logger.info("请求开始 | model=%s | fmt=%s | stream=%s | messages=%d",
                model, fmt, client_wants_stream, n_messages)

    resp_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())

    # ---------- 流式 ----------
    if client_wants_stream:
        async def stream_generator():
            assert _semaphore is not None
            async with _semaphore:
                client = httpx.AsyncClient(timeout=httpx.Timeout(UPSTREAM_TIMEOUT, connect=10.0))
                try:
                    async with client.stream("POST", url, headers=headers, json=upstream_body) as resp:
                        if resp.status_code != 200:
                            raw = (await resp.aread()).decode("utf-8", errors="ignore")
                            logger.warning("上游错误 | model=%s | status=%d", model, resp.status_code)
                            err = _friendly_upstream_error(resp.status_code, raw)
                            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode()
                            yield b"data: [DONE]\n\n"
                            return

                        if fmt == "openai":
                            # 直接透传上游的 OpenAI SSE
                            async for chunk in resp.aiter_raw():
                                if chunk:
                                    yield chunk
                        else:
                            # gemini -> openai 翻译
                            async for sse in _translate_gemini_stream(resp, resp_id, created, model):
                                yield sse
                    logger.info("请求完成(流式) | model=%s | %.2fs", model, time.monotonic() - started)
                except httpx.HTTPError as e:
                    logger.error("上游连接异常 | model=%s | %s", model, e)
                    err = {"error": {"message": f"上游连接异常: {e}", "type": "network_error"}}
                    yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                finally:
                    await client.aclose()

        return StreamingResponse(
            stream_generator(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------- 非流式:聚合 ----------
    assert _semaphore is not None
    async with _semaphore:
        full_content, last_obj, usage = "", None, None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(UPSTREAM_TIMEOUT, connect=10.0)) as client:
                async with client.stream("POST", url, headers=headers, json=upstream_body) as resp:
                    if resp.status_code != 200:
                        raw = (await resp.aread()).decode("utf-8", errors="ignore")
                        logger.warning("上游错误 | model=%s | status=%d", model, resp.status_code)
                        return JSONResponse(status_code=resp.status_code,
                                            content=_friendly_upstream_error(resp.status_code, raw))
                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            line, buffer = buffer.split("\n\n", 1)
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                obj = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            last_obj = obj
                            if fmt == "gemini":
                                full_content += _gemini_extract_text(obj)
                                if obj.get("usageMetadata"):
                                    usage = _gemini_usage_to_openai(obj["usageMetadata"])
                            else:
                                if obj.get("usage"):
                                    usage = obj["usage"]
                                for ch in obj.get("choices", []):
                                    delta = ch.get("delta", {}) or {}
                                    if delta.get("content"):
                                        full_content += delta["content"]
        except httpx.HTTPError as e:
            logger.error("上游连接异常 | model=%s | %s", model, e)
            raise HTTPException(status_code=502, detail=f"上游连接异常: {e}")

        logger.info("请求完成(非流式) | model=%s | tokens=%s | %.2fs",
                    model, (usage or {}).get("total_tokens", "?"), time.monotonic() - started)
        return JSONResponse({
            "id": resp_id, "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full_content},
                         "finish_reason": "stop"}],
            "usage": usage or {},
        })


async def _translate_gemini_stream(resp, resp_id: str, created: int, model: str):
    """把 Gemini 原生 SSE 流翻译成 OpenAI chunk 流(异步生成器,产出 bytes)"""
    def emit(chunk: dict) -> bytes:
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()

    role_sent = False
    buffer = ""
    async for raw in resp.aiter_text():
        buffer += raw
        while "\n\n" in buffer:
            line, buffer = buffer.split("\n\n", 1)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            text = _gemini_extract_text(obj)
            if text:
                delta = {"content": text}
                if not role_sent:
                    delta = {"role": "assistant", "content": text}
                    role_sent = True
                yield emit(_openai_chunk(resp_id, created, model, delta))
            if obj.get("usageMetadata"):
                # 先发 finish,再发 usage chunk
                yield emit(_openai_chunk(resp_id, created, model, {}, finish_reason="stop"))
                yield emit(_openai_chunk(resp_id, created, model, {},
                                         usage=_gemini_usage_to_openai(obj["usageMetadata"])))
    yield b"data: [DONE]\n\n"


@app.get("/")
async def root():
    return {"service": "Immersive Translate OpenAI Proxy",
            "endpoints": ["/v1/chat/completions", "/v1/models"], "models": SUPPORTED_MODELS}


@app.get("/health")
async def health():
    return {"status": "ok", "credentials_configured": bool(IMMERSIVE_TOKEN and IMMERSIVE_COOKIE)}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
