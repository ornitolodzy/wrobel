# Obraz DiscordBot. Kontekst = katalog DiscordBot/:
#   docker build -t prawdowid-discord-bot DiscordBot
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HEALTH_FILE=/tmp/discord-bot.alive

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY bot ./bot

RUN useradd --create-home --uid 10001 bot
USER bot

# Healthy = the bot is connected to Discord (heartbeat file refreshed every 30 s).
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
    CMD python -c "import os,sys,time; f=os.environ['HEALTH_FILE']; sys.exit(0 if os.path.exists(f) and time.time()-os.path.getmtime(f) < 90 else 1)"

CMD ["python", "-m", "bot.main"]
