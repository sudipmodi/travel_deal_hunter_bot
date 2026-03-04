FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

CMD ["sh", "-c", "gunicorn src.app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120"]
