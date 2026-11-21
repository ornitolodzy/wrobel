# Obraz VerificationPipeline. Kontekst = katalog VerificationPipeline/:
#   docker build -t prawdowid-pipeline VerificationPipeline
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PRAWDOWID_ROOT=/app \
    PRAWDOWID_ENV=docker \
    KEDRO_DISABLE_TELEMETRY=true \
    DO_NOT_TRACK=1

WORKDIR /app

# Dependencies first (cached layer): a code change does not reinstall litellm & co.
COPY pyproject.toml ./
RUN mkdir -p src/prawdowid && touch src/prawdowid/__init__.py     && pip install ".[llm,retrieval]" && pip uninstall -y prawdowid

COPY src ./src
RUN pip install --no-deps .

COPY conf ./conf
COPY db ./db

RUN useradd --create-home --uid 10001 pipeline && chown -R pipeline /app
USER pipeline

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "prawdowid.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
