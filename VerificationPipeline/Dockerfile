FROM python:3.12-slim
WORKDIR /app
ENV PRAWDOWID_ROOT=/app
COPY pyproject.toml ./
COPY src ./src
COPY conf ./conf
RUN pip install --no-cache-dir .
CMD ["uvicorn", "prawdowid.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
