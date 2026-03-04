FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright dependencies and browser
RUN playwright install-deps
RUN playwright install chromium

COPY src/ ./src/
COPY config/ ./src/config/

CMD ["sh", "-c", "gunicorn src.app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120"]
