FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY sources.yaml .

RUN useradd --create-home --uid 10001 scraper
USER scraper

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
