# syntax=docker/dockerfile:1
FROM python:3.14-slim

# No secrets in this image: credentials reach the container at runtime
# via the Container App's managed identity and Key Vault, never baked in.

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY crew/ ./crew/

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "-m", "crew"]
