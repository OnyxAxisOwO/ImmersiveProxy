# Immersive Translate → OpenAI 兼容 API 中转

把**沉浸式翻译 Pro 会员**的 AI 额度,通过一个本地反向代理暴露成**标准 OpenAI 兼容接口**,即可在 Cherry Studio、NextChat、Open WebUI、各种 IDE 插件等支持自定义 Base URL 的客户端里复用这份额度。

## 原理

沉浸式翻译 Pro 后端各模型家族有独立端点,其中 `/qwen/translate/stream` 是一个**通用 OpenAI 网关**,绝大多数模型(qwen / deepseek / glm / gpt-5-mini / grok / plamo)直接走它即可,请求与响应都是标准 OpenAI 格式。两个例外:

- **Gemini** 走 `/gemini/translate/stream`,响应是 Gemini 原生格式,本代理会自动翻译成 OpenAI 格式。
- **Claude** 走 `/claude/translate/stream`,响应仍是 OpenAI 格式,但需要额外的 `Anthropic-Version` 等请求头,本代理会自动补上。

本服务本质是个**带认证改写 + 多供应商路由 + Gemini 格式翻译的反向代理**。

## 功能

- `POST /v1/chat/completions` —— 流式(SSE)+ 非流式聚合,完全 OpenAI 兼容
- `GET /v1/models` —— 模型列表
- `GET /health` —— 健康检查
- 多供应商路由(Gemini 自动翻译,其余透传)
- model id 别名归一化
- 中转层 API Key 鉴权(可选)
- 并发限制(防上游风控)
- 结构化日志(只记 metadata,不打印凭证/消息内容)
- 401 / 403 / 429 / 5xx 清晰错误提示

## 可用模型(已探测确认)

| 在客户端填写的 model | 实际模型 | 端点 |
|---|---|---|
| `qwen3.5-plus` | Qwen 3.5 Plus | OpenAI 透传 |
| `DeepSeek-V4-Flash` | DeepSeek V4 Flash | OpenAI 透传 |
| `gpt-5-mini` | GPT-5 mini | OpenAI 透传 |
| `glm-4.7` | GLM-4.7 | OpenAI 透传 |
| `grok-4-3`(别名 `grok-4.3`) | Grok 4.3 | OpenAI 透传 |
| `plamo-2.2-prime` | PLaMo 2.2 Prime | OpenAI 透传 |
| `gemini-3-flash-preview`(别名 `gemini-3-flash`) | Gemini 3 Flash | Gemini→OpenAI 翻译 |
| `claude-haiku-4.5-20251001`(别名 `claude-haiku-4.5`) | Claude Haiku 4.5 | `/claude/` + Anthropic 头 |

> 满血 GPT-5 不可用(Pro 套餐返回 429,需 Max 套餐)。HY 2.0 的正确 model id 尚未确认。

## 安装

依赖通过 [uv](https://github.com/astral-sh/uv) 管理,无需手动建虚拟环境:

```powershell
# 已安装 uv 的话,直接运行即可(uv 会自动装 Python 和依赖)
```

或用传统 pip:

```bash
pip install fastapi uvicorn httpx python-dotenv
```

## 配置

1. 复制 `.env.example` 为 `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

2. 抓包获取凭证并填进 `.env`:
   - 打开浏览器装好的沉浸式翻译扩展,使用 **AI Write** 功能发一条消息
   - 按 `F12` → **Network(网络)** → 找到 `…/translate/stream` 请求
   - 从**请求标头**里复制:
     - `Token`(一长串 hex)→ 填到 `IMMERSIVE_TOKEN`
     - `Cookie`(完整字符串)→ 填到 `IMMERSIVE_COOKIE`

   ```dotenv
   IMMERSIVE_TOKEN=你的Token
   IMMERSIVE_COOKIE=完整的Cookie字符串
   ```

3. (可选)给中转服务设一个 API Key,客户端需用它鉴权:

   ```dotenv
   PROXY_API_KEY=sk-your-own-secret
   ```

完整配置项见 [.env.example](.env.example)。

## 启动

```powershell
uv run --with fastapi --with uvicorn --with httpx --with python-dotenv immersive_proxy.py
```

或(已 pip 安装依赖):

```bash
python immersive_proxy.py
```

启动后监听 `http://127.0.0.1:8000`。

## 交互式控制台(推荐)

不想记命令行?用控制台一站式管理:

```powershell
uv run control.py
```

支持的指令:

| 指令 | 作用 |
|---|---|
| `start` / `stop` / `restart` | 启停 / 重启逆向服务器(后台运行,带健康检查) |
| `status` | 显示运行状态、地址、凭证是否配置、PID |
| `model` | 列出当前可用模型(优先取运行中服务的 `/v1/models`) |
| `settings` | 进入设置:改 token/cookie(脱敏显示)、并发、超时、日志级别、基址与各 Path |
| `exit` | 退出(可选择是否一并停止服务) |

设置项更改前会先显示当前值;改完若服务在运行会提示 `restart` 生效。

## 客户端配置示例

通用设置:

- **Base URL**：`http://127.0.0.1:8000/v1`
- **API Key**：随便填(若设了 `PROXY_API_KEY` 则填该值)
- **Model**：上表任一 model id

### curl(流式)

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3-flash-preview",
    "stream": true,
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="anything")
resp = client.chat.completions.create(
    model="grok-4-3",
    messages=[{"role": "user", "content": "你好"}],
)
print(resp.choices[0].message.content)
```

### Cherry Studio / NextChat

新建一个 OpenAI 类型的提供商,Base URL 填 `http://127.0.0.1:8000/v1`,API Key 随意,模型手动添加上表 id 即可。

## 凭证失效与更换

`Token` 绑定会员账号且**有状态**:重新登录沉浸式翻译后旧 Token 会失效,需重新抓包替换 `.env` 里的 `IMMERSIVE_TOKEN` / `IMMERSIVE_COOKIE`,然后重启服务。

服务返回 `上游认证失败(401)` 即提示凭证过期。

## 注意事项

- **凭证安全**:`.env` 已加入 `.gitignore`,切勿提交到 git。
- **速率控制**:`MAX_CONCURRENCY`(默认 2)限制同时发往上游的请求数,防止账号被风控。
- **仅自用**:本服务无多用户/计费设计,建议只监听 `127.0.0.1`。
- 日志只记录 model、token 数、耗时、状态,**不记录** Token / Cookie / 消息内容。

## 文件说明

| 文件 | 作用 |
|---|---|
| `immersive_proxy.py` | 主服务 |
| `control.py` | 交互式控制台:启停服务、查看状态/模型、编辑设置(`uv run control.py`) |
| `.env` | 凭证与配置(本地,勿提交) |
| `.env.example` | 配置模板 |
| `test_upstream.py` | 上游连通性测试:`uv run --with httpx --with python-dotenv test_upstream.py [model]` |
| `probe_models.py` | 模型探测脚本,输出可用清单 |
| `available_models.txt` | 探测得到的可用模型清单 |
