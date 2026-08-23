FROM python:3.12-slim

# Keep Python lean and unbuffered for container logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first for better layer caching. Only the core requirements —
# the optional LLM classifier (requirements-llm.txt) is not baked in.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + config + web UI.
COPY src ./src
COPY config ./config
COPY web ./web

# Run as a non-root user; data/ is a mounted volume owned by that user.
RUN useradd -m appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
