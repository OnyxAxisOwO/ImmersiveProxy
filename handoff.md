# 任务交接:沉浸式翻译 Pro → OpenAI 兼容 API 中转服务

## 背景

我购买了**沉浸式翻译 Pro 会员**,每月有 **2000 万 Token** 的 AI 额度,日常用不完。
我想把这个额度通过中转服务暴露成 **OpenAI 兼容接口**,这样可以在 Cherry Studio、NextChat、Open WebUI、各种 IDE 插件等支持自定义 Base URL 的客户端里复用这份额度。

## 关键发现(已通过抓包确认)

沉浸式翻译 Pro 后端**本身就是一个标准 OpenAI 兼容服务**——请求 Payload 和响应 SSE 流的格式都和 OpenAI Chat Completions 完全一致,所以中转层几乎不需要任何格式转换,本质上就是个**带认证改写的反向代理**。

### 上游端点

```
POST https://api2.immersivetranslate.com/qwen/translate/stream
```

(虽然路径里写着 `/qwen/`,但 `model` 字段可以指定其他模型,比如 `gpt-5-mini`、`claude-haiku-4.5` 等,只要是 Pro 套餐里开放的就行)

### 请求标头(从浏览器抓包得到,沉浸式翻译扩展自己发出去的)

```
:authority: api2.immersivetranslate.com
:method: POST
:path: /qwen/translate/stream
:scheme: https
Accept: text/event-stream
Accept-Language: zh-CN
Content-Type: application/json
Cookie: <完整 cookie 字符串,包含 immersive_translate_token=xxx 等>
Origin: chrome-extension://bpoadfkcbjbfhfodiogcnhhhpibjhbnh
Token: <长串 hex,这是核心认证凭据,和 Cookie 里的 token 配合使用>
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36
X-Imt-Product-Line: ai_writing
```

**认证机制**:`Token` Header + `Cookie` 双重认证,两个都得带上。`Token` 是核心凭据,Cookie 里也有一个 `immersive_translate_token` 字段(值和 Token Header 不同,但都属于会员凭证)。

**CORS 限制**:服务器只允许 `Origin: chrome-extension://bpoadfkcbjbfhfodiogcnhhhpibjhbnh`,但这是浏览器侧的 CORS 策略,**服务端 HTTP 请求(curl / httpx / fetch from Node)完全不受影响**。

### 请求 Body 示例(标准 OpenAI 格式)

```json
{
  "enable_thinking": false,
  "model": "qwen3.5-plus",
  "temperature": 0,
  "stream": true,
  "messages": [
    {"role": "system", "content": "You are a professional writing assistant..."},
    {"role": "user", "content": "你是谁"}
  ]
}
```

注意几点:
- `stream` 字段为 true(这个端点路径里就有 `/stream`,只支持流式)
- `enable_thinking` 是沉浸式翻译特有字段(给推理模型用),透传即可
- `model` 字段决定使用哪个模型

### 响应格式(完全是标准 OpenAI SSE 流)

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1779607074,"model":"qwen3.5-plus","choices":[{"index":0,"delta":{"role":"assistant","content":"我是一个"}}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1779607074,"model":"qwen3.5-plus","choices":[{"index":0,"delta":{"content":"专业的写作助手，"}}]}

...

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1779607074,"model":"qwen3.5-plus","choices":[{"index":0,"delta":{"content":""},"finish_reason":"stop"}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1779607074,"model":"qwen3.5-plus","choices":[],"usage":{"prompt_tokens":143,"completion_tokens":97,"total_tokens":240}}

data: [DONE]
```

最后一个 chunk 带 `usage` 统计,然后 `[DONE]` 结束。完全可以原样转发给客户端。

### 已知的 model id 取值(Pro 套餐可用)

需要在插件里切换模型并抓包确认确切 model id,目前已确认的:

- `qwen3.5-plus`(已抓包验证)

UI 上能看到的其他选项(model id 待确认,需要逐个抓):
- DeepSeek V4 Flash → 推测 `deepseek-v4-flash` 或类似
- Gemini 3 Flash → 推测 `gemini-3-flash`
- GPT-5 mini → 推测 `gpt-5-mini`
- GLM-4.7 → 推测 `glm-4.7`
- Claude Haiku 4.5 → 推测 `claude-haiku-4.5`
- Grok 4.3 → 推测 `grok-4.3`
- PLaMo 2.2 Prime
- HY 2.0 Instruct

### `X-Imt-Product-Line` 字段

不同入口值不同,目前观察到:
- AI Write 入口:`ai_writing`
- 网页翻译入口:`web_page`(从 `/qwen/translate` 接口抓到的,不是 `/stream` 那个)
- 其他值待发现:可能有 `ai_chat`、`pop_up` 等

通用值用 `ai_writing` 暂时没问题,如果遇到某些场景报错可以尝试切换。

## 已有产物

我手上已经有一份**基础版 Python 中转服务**(基于 FastAPI + httpx),具备以下功能:
- `/v1/chat/completions`(流式 + 非流式聚合)
- `/v1/models` 列表
- 简单的中转层 API Key 鉴权
- 标准 SSE 转发

文件名:`immersive_proxy.py`(我会一起发给你)

## 我希望你做的事

请按优先级帮我搞定:

### 1. 把脚本跑通

- 检查我填的 `IMMERSIVE_TOKEN` 和 `IMMERSIVE_COOKIE` 是否正确
- 装依赖、启动服务
- 用 curl 或 Python 客户端测一下 `POST /v1/chat/completions`(分别测流式和非流式)
- 测 `/v1/models`
- 验证返回结果是否符合 OpenAI 标准

### 2. 改进工程化

- 把凭证从代码里拿出来,改用 `.env` 文件(`python-dotenv`)
- 加日志(用 `logging`,记录每次请求的 model、tokens、耗时、状态)
- 加错误处理:401 / 429 / 5xx 分别给出清晰错误信息
- 加一个简单的并发限制(可选,避免触发上游风控)
- 写一份 `README.md`,包含安装、配置、启动、客户端配置示例

### 3. 帮我探测所有可用模型

写一个小脚本:对每个候选 model id 发一个最小测试请求("hi"),看哪些返回 200、哪些返回错误(比如"模型不存在"),输出一份**可用模型清单**。

候选清单从插件 UI 上能看到的所有 Pro 模型(见上文)。

### 4. (可选)Docker 化

写个 `Dockerfile` 和 `docker-compose.yml`,方便我部署到自己的小 VPS。

### 5. (可选)前端管理界面

如果你愿意加,做个超简单的 web 面板,显示:
- 今日/本月使用的 token 数
- 各模型的调用次数
- 最近 N 次请求记录(去敏感字段)

不做也行,先把核心跑通最重要。

## 注意事项

1. **凭证安全**:Token 和 Cookie 千万别写死在代码里 push 到 git,务必走 `.env` 并加进 `.gitignore`。
2. **Token 是有状态的**:绑定我的会员账号,如果我重新登录沉浸式翻译,旧 Token 会失效,需要重新抓包替换。后续可以考虑写个自动化(用 Playwright 模拟登录获取),但不是现在的优先级。
3. **速率控制**:别一上来就猛打,防止账号被风控。先低速测,确认稳定再说。
4. **不要在日志里打印完整的 Token / Cookie / messages 内容**,只记 metadata。
5. **就我自己用**,不对外开放,所以不用做特别复杂的多用户/计费/限流设计。

## 启动顺序建议

```
Step 1: 我提供凭证后,你创建 .env 文件并测试当前脚本能不能跑
Step 2: 跑通后做工程化改造(.env / 日志 / 错误处理)
Step 3: 模型探测脚本
Step 4: 写 README
Step 5: 视情况做 Docker 或 web 面板
```

每一步做完跟我确认,别一口气全做完。
