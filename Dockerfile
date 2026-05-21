FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=run.py \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    APP_THREADS=8

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN mkdir -p /app/instance

EXPOSE 8080

CMD ["python", "serve_local.py"]
