"""
Immersive Proxy Control —— 沉浸式翻译逆向代理 交互式控制台

运行:
    uv run control.py
    或  python control.py

依赖:仅标准库。
"""

import os
import sys
import json
import time
import shutil
import signal
import platform
import subprocess
import urllib.request
import urllib.error

# Windows 控制台默认 GBK,强制 UTF-8 以正常显示 ✓ ● 等字符与中文
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass
# stdin 用 utf-8-sig:正确解码中文输入,并自动吃掉可能的 BOM
try:
    sys.stdin.reconfigure(encoding="utf-8-sig")
except Exception:
    pass

# ============ 路径 ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
ENV_EXAMPLE = os.path.join(BASE_DIR, ".env.example")
SERVER_FILE = os.path.join(BASE_DIR, "immersive_proxy.py")
PID_FILE = os.path.join(BASE_DIR, ".server.pid")
LOG_FILE = os.path.join(BASE_DIR, "server.log")
IS_WIN = platform.system() == "Windows"

# ============ 颜色 ============
if IS_WIN:
    os.system("")  # 启用 Windows 终端 ANSI 转义


class C:
    R = "\033[0m"
    B = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GRN = "\033[32m"
    YLW = "\033[33m"
    BLU = "\033[34m"
    CYN = "\033[36m"
    GRY = "\033[90m"


def c(text, color):
    return f"{color}{text}{C.R}"


# ============ .env 读写 ============
def read_env():
    """返回 dict;若无 .env 但有 .env.example 则提示"""
    env = {}
    path = ENV_PATH if os.path.exists(ENV_PATH) else None
    if not path:
        return env
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            s = line.strip()
            if not s or s.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val
    return env


def set_env(key, value):
    """更新或追加 .env 中的某个 key,保留注释与其它行"""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    found = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#") and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k == key:
                lines[i] = f"{key}={value}"
                found = True
                break
    if not found:
        lines.append(f"{key}={value}")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def env_get(key, default=""):
    return read_env().get(key, default)


# 首次运行自动生成的默认配置模板(凭证留空,需用户填)
DEFAULT_ENV = """\
# 沉浸式翻译 Pro 凭证(抓包获得,重新登录后会失效需替换)
# 用 settings 里的 token / cookie 填入,或直接编辑本文件
IMMERSIVE_TOKEN=
IMMERSIVE_COOKIE=

# 沉浸式翻译扩展 ID
EXTENSION_ID=bpoadfkcbjbfhfodiogcnhhhpibjhbnh

# 上游基址与端点路径
API_BASE=https://api2.immersivetranslate.com
OPENAI_PATH=/qwen/translate/stream
GEMINI_PATH=/gemini/translate/stream
CLAUDE_PATH=/claude/translate/stream

# X-Imt-Product-Line
X_IMT_PRODUCT_LINE=ai_writing

# 中转层 API Key(留空则不鉴权)
PROXY_API_KEY=

# 监听地址
HOST=127.0.0.1
PORT=8000

# 请求参数
MAX_CONCURRENCY=2
UPSTREAM_TIMEOUT=120
LOG_LEVEL=INFO

# 可用模型清单(已探测确认)
SUPPORTED_MODELS=qwen3.5-plus,DeepSeek-V4-Flash,gpt-5-mini,glm-4.7,grok-4-3,plamo-2.2-prime,gemini-3-flash-preview,claude-haiku-4.5-20251001
"""


def ensure_env_exists():
    """无 .env 时自动生成:优先用 .env.example,否则用内置模板。"""
    if os.path.exists(ENV_PATH):
        return True
    if os.path.exists(ENV_EXAMPLE):
        shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        src = ".env.example"
    else:
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_ENV)
        src = "内置模板"
    print(c(f"  首次运行,已自动生成 .env(来源:{src})。", C.GRN))
    print(c("  ⚠ 凭证为空,请用 settings → token / cookie 填入后再 start。", C.YLW))
    return True


# ============ 服务器进程管理 ============
def server_host_port():
    env = read_env()
    return env.get("HOST", "127.0.0.1").strip() or "127.0.0.1", \
        env.get("PORT", "8000").strip() or "8000"


def health():
    """返回 dict 或 None(未运行)"""
    host, port = server_host_port()
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def read_pid():
    if os.path.exists(PID_FILE):
        try:
            return int(open(PID_FILE).read().strip())
        except Exception:
            return None
    return None


def is_running():
    return health() is not None


def build_launch_cmd():
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--with", "fastapi", "--with", "uvicorn",
                "--with", "httpx", "--with", "python-dotenv", SERVER_FILE]
    return [sys.executable, SERVER_FILE]


def start_server():
    if is_running():
        print(c("  服务器已在运行中。", C.YLW))
        return
    ensure_env_exists()
    env = read_env()
    if not env.get("IMMERSIVE_TOKEN") or not env.get("IMMERSIVE_COOKIE"):
        print(c("  ⚠ 未配置 Token / Cookie,启动后上游会 401。建议先 settings 设置。", C.YLW))
    cmd = build_launch_cmd()
    print(c(f"  启动中: {' '.join(os.path.basename(x) for x in cmd)}", C.DIM))
    logf = open(LOG_FILE, "a", encoding="utf-8")
    logf.write(f"\n===== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    logf.flush()
    kwargs = dict(cwd=BASE_DIR, stdout=logf, stderr=subprocess.STDOUT)
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    # 等待健康检查
    host, port = server_host_port()
    for _ in range(40):
        if is_running():
            print(c(f"  ✓ 服务器已启动: http://{host}:{port}/v1  (PID {proc.pid})", C.GRN))
            print(c(f"    日志: {LOG_FILE}", C.DIM))
            return
        if proc.poll() is not None:
            print(c("  ✗ 进程已退出,请查看 server.log。", C.RED))
            return
        time.sleep(0.5)
    print(c("  ✗ 启动超时(未通过健康检查),请查看 server.log。", C.RED))


def stop_server():
    pid = read_pid()
    running = is_running()
    if not running and pid is None:
        print(c("  服务器未在运行。", C.YLW))
        return
    if pid is None:
        print(c("  找不到 PID 记录,无法自动停止(可能由外部启动)。", C.YLW))
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception as e:
        print(c(f"  停止时出错: {e}", C.RED))
    finally:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    # 确认
    for _ in range(10):
        if not is_running():
            print(c("  ✓ 服务器已停止。", C.GRN))
            return
        time.sleep(0.3)
    print(c("  ⚠ 已发送停止指令,但端口仍有响应。", C.YLW))


def restart_server():
    stop_server()
    time.sleep(0.5)
    start_server()


# ============ 模型列表 ============
def show_models():
    h = health()
    if h:
        host, port = server_host_port()
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/v1/models", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
            ids = [m["id"] for m in data.get("data", [])]
            print(c("\n  当前可用模型(来自运行中的服务):", C.B))
            for m in ids:
                print(f"    {c('●', C.GRN)} {m}")
            print()
            return
        except Exception:
            pass
    # 回退:从 .env 读
    raw = env_get("SUPPORTED_MODELS")
    ids = [x.strip() for x in raw.split(",") if x.strip()]
    print(c("\n  可用模型(来自 .env,服务未运行):", C.B))
    for m in ids:
        print(f"    {c('○', C.GRY)} {m}")
    print()


# ============ 设置 ============
def mask(value, head=8, tail=4):
    if not value:
        return c("(空)", C.RED)
    if len(value) <= head + tail:
        return value
    return f"{value[:head]}…{value[-tail:]}  {c(f'(len={len(value)})', C.GRY)}"


def _edit(key, label, required=False, masked=False, choices=None, default=""):
    cur = env_get(key) or default
    shown = mask(cur) if masked else (cur if cur else c("(空)", C.RED))
    print(f"\n  {c(label, C.B)}")
    print(f"  当前值: {shown}")
    if choices:
        print(f"  可选: {c('/'.join(choices), C.CYN)}")
    tip = c("[必填]", C.RED) + " " if required else ""
    new = input(f"  {tip}输入新值(留空取消): ").strip()
    if not new:
        print(c("  已取消。", C.GRY))
        return False
    if choices and new.upper() not in [x.upper() for x in choices]:
        print(c(f"  无效值,必须是 {choices} 之一。", C.RED))
        return False
    set_env(key, new)
    print(c(f"  ✓ 已更新 {key}", C.GRN))
    return True


def settings_menu():
    cmds = {
        # 监听
        "ip":       ("HOST", "监听 IP / 主机", dict(default="127.0.0.1")),
        "port":     ("PORT", "监听端口", dict(default="8000")),
        # 鉴权
        "token":    ("IMMERSIVE_TOKEN", "沉浸式翻译 Token", dict(required=True, masked=True)),
        "cookie":   ("IMMERSIVE_COOKIE", "沉浸式翻译 Cookie", dict(required=True, masked=True)),
        "apikey":   ("PROXY_API_KEY", "中转层 API Key(留空不鉴权)", dict(masked=True)),
        # 请求
        "con":      ("MAX_CONCURRENCY", "最大并发数", dict(default="2")),
        "timeout":  ("UPSTREAM_TIMEOUT", "上游超时(秒)", dict(default="120")),
        "loglevel": ("LOG_LEVEL", "日志级别", dict(choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")),
        "product":  ("X_IMT_PRODUCT_LINE", "X-Imt-Product-Line", dict(default="ai_writing")),
        # 端点
        "baseurl":  ("API_BASE", "上游基址", {}),
        "openai":   ("OPENAI_PATH", "OpenAI Path", {}),
        "gemini":   ("GEMINI_PATH", "Gemini Path", {}),
        "claude":   ("CLAUDE_PATH", "Claude Path", {}),
        # 其他
        "ext":      ("EXTENSION_ID", "扩展 ID", {}),
        "models":   ("SUPPORTED_MODELS", "可用模型清单(逗号分隔)", {}),
    }
    changed = False
    while True:
        env = read_env()
        g = lambda k, d="": env.get(k, d) or d
        print(c("\n──────────────── 设置 ────────────────", C.CYN))
        print(c("  [监听]", C.B))
        print(f"    ip        IP/主机 : {g('HOST','127.0.0.1')}")
        print(f"    port      端口    : {g('PORT','8000')}")
        print(c("  [鉴权]", C.B))
        print(f"    token     Token   : {mask(env.get('IMMERSIVE_TOKEN',''))}")
        print(f"    cookie    Cookie  : {mask(env.get('IMMERSIVE_COOKIE',''))}")
        print(f"    apikey    API Key : {mask(env.get('PROXY_API_KEY','')) if env.get('PROXY_API_KEY') else c('(未启用鉴权)', C.GRY)}")
        print(c("  [请求]", C.B))
        print(f"    con       并发    : {g('MAX_CONCURRENCY','2')}")
        print(f"    timeout   超时(s) : {g('UPSTREAM_TIMEOUT','120')}")
        print(f"    loglevel  日志    : {g('LOG_LEVEL','INFO')}")
        print(f"    product   产品线  : {g('X_IMT_PRODUCT_LINE','ai_writing')}")
        print(c("  [端点]", C.B))
        print(f"    baseurl   基址    : {g('API_BASE')}")
        print(f"    openai    OpenAI  : {g('OPENAI_PATH')}")
        print(f"    gemini    Gemini  : {g('GEMINI_PATH')}")
        print(f"    claude    Claude  : {g('CLAUDE_PATH')}")
        print(c("  [其他]", C.B))
        print(f"    ext       扩展ID  : {g('EXTENSION_ID')}")
        n_models = len([x for x in g('SUPPORTED_MODELS').split(',') if x.strip()])
        print(f"    models    模型    : {c(f'{n_models} 个', C.GRY)} {g('SUPPORTED_MODELS')[:48]}…")
        print(c("  back  返回主菜单", C.GRY))
        cmd = input(c("\nsettings> ", C.CYN)).strip().lower()
        if cmd in ("back", "exit", "q", ""):
            break
        if cmd in cmds:
            key, label, kw = cmds[cmd]
            if _edit(key, label, **kw):
                changed = True
        else:
            print(c("  未知设置项。", C.RED))
    if changed and is_running():
        print(c("\n  ⚠ 设置已更改,需重启服务器生效。输入 restart 重启。", C.YLW))


# ============ 主界面 ============
BANNER = r"""
  ___                              _           ____
 |_ _|_ __ ___  _ __ ___   ___ _ __ ___(_)_   _____  |  _ \ _ __ _____  ___   _
  | || '_ ` _ \| '_ ` _ \ / _ \ '__/ __| \ \ / / _ \ | |_) | '__/ _ \ \/ / | | |
  | || | | | | | | | | | |  __/ |  \__ \ |\ V /  __/ |  __/| | | (_) >  <| |_| |
 |___|_| |_| |_|_| |_| |_|\___|_|  |___/_| \_/ \___| |_|   |_|  \___/_/\_\\__, |
                                                                          |___/
                          Immersive Proxy Control
"""

HELP = """
  指令:
    status      显示服务器运行状态
    start       启动逆向服务器
    stop        关闭逆向服务器
    restart     重启逆向服务器
    settings    逆向服务器设置
    model       当前可用模型
    help        显示帮助
    exit        退出控制器
"""


def status_line():
    h = health()
    host, port = server_host_port()
    if h:
        cred = "已配置" if h.get("credentials_configured") else c("未配置凭证", C.RED)
        dot = c("● 运行中", C.GRN)
        print(f"  状态: {dot}  http://{host}:{port}/v1  | 凭证: {cred}  | PID: {read_pid() or '?'}")
    else:
        print(f"  状态: {c('● 已停止', C.RED)}  (监听目标 http://{host}:{port})")


def main():
    print(c(BANNER, C.CYN))
    ensure_env_exists()
    print(HELP)
    while True:
        print()
        status_line()
        try:
            cmd = input(c("\nimmersive> ", C.B)).strip().lstrip("﻿").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            cmd = "exit"

        if cmd in ("exit", "quit", "q"):
            if is_running():
                ans = input("  服务器仍在运行,是否一并停止?(y/N) ").strip().lower()
                if ans == "y":
                    stop_server()
                else:
                    print(c("  服务器将继续在后台运行。", C.GRY))
            print(c("  再见。", C.CYN))
            break
        elif cmd == "start":
            start_server()
        elif cmd == "stop":
            stop_server()
        elif cmd == "restart":
            restart_server()
        elif cmd in ("settings", "set", "config"):
            settings_menu()
        elif cmd in ("model", "models"):
            show_models()
        elif cmd == "status":
            pass  # 顶部已显示
        elif cmd in ("help", "h", "?"):
            print(HELP)
        elif cmd == "":
            continue
        else:
            print(c(f"  未知指令: {cmd}(输入 help 查看)", C.RED))


if __name__ == "__main__":
    main()
