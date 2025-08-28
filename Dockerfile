FROM python:3.13.3-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

ENV TZ=America/Chicago \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt logging.yaml ./

RUN pip install -r requirements.txt

COPY src ./src

EXPOSE 80

WORKDIR /app/src

ENTRYPOINT exec uvicorn privateindexer_client.main:app --proxy-headers --workers 1 --host 0.0.0.0 --port 80 --log-config /app/logging.yaml