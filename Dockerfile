FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Install system deps for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt --break-system-packages

# Install Playwright browsers
RUN playwright install chromium --with-deps

COPY src/ ./src/
COPY config/ ./src/config/

ENV PORT=10000

CMD ["gunicorn", "src.app:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--timeout", "120"]
