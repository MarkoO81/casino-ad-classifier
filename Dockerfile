FROM python:3.12-slim

WORKDIR /app

# System deps required by Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl ca-certificates fonts-liberation \
    libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdbus-1-3 \
    libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 libx11-xcb1 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxss1 \
    xdg-utils && rm -rf /var/lib/apt/lists/*

COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# Install Playwright Chromium browser
RUN playwright install chromium

COPY . .

EXPOSE 8081

# Increase timeout to 60s — Playwright rendering can take a few seconds
CMD gunicorn --bind 0.0.0.0:${PORT:-8081} --workers 1 --threads 4 --timeout 60 app:app
