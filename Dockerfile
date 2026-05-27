FROM python:3.12-slim

WORKDIR /app

# 先装依赖(利用层缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷贝服务代码
COPY immersive_proxy.py .

# 容器内固定监听 0.0.0.0:8000;凭证等其余配置由 compose 的 env_file 注入
ENV HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# 探活:命中 /health 即健康
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"

CMD ["python", "immersive_proxy.py"]
