"""Tests never use the developer's .env: models off, defaults on (env vars win over .env in pydantic-settings)."""

import os

os.environ.update(
    {
        "EMBEDDINGS_PROVIDER": "none",
        "EMBEDDINGS_MODEL": "bge-m3",
        "EVENTS_PROVIDER": "none",
        "EVENTS_MODEL": "bielik",
        "EVENTS_API_BASE": "http://127.0.0.1:11435/v1",
    }
)
