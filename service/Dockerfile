# Standalone Spinner service — slim, self-contained.
FROM python:3.13-slim

WORKDIR /app

# system deps kept minimal; websockets/requests are pure-python wheels
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite identity/meter stores live here (mount a volume to persist)
RUN mkdir -p /app/data

EXPOSE 8080
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
