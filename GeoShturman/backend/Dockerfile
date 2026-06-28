# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Образ backend-приложения.
# Содержит API, сервисный слой и статический веб-интерфейс.
# FastAPI отдаёт SPA из app/static и REST из app/api.
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# Не пишем .pyc, выводим логи без буферизации (удобно для docker logs).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Системные зависимости для numpy/scipy/matplotlib и сборки C-расширений.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Сначала зависимости — слой кэшируется, пока requirements.txt не изменился.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Затем код приложения.
COPY app ./app

# Каталог артефактов (NMEA, result.json, PNG) монтируется томом в compose.
RUN mkdir -p /app/app/outputs

EXPOSE 8000

# Простой healthcheck — nginx и docker видят, что сервис жив.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
