FROM python:3.13.3-slim

WORKDIR /app

RUN apt-get update && apt-get install -y tzdata

ENV TZ=America/Chicago

COPY main.py requirements.txt ./

RUN pip install -r requirements.txt

ENTRYPOINT exec python main.py