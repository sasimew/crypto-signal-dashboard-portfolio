# BUG FIX #8: ติดตั้ง cron ใน Dockerfile ไม่ใช่ใน CMD
FROM python:3.11-slim

WORKDIR /app

# ทุกไฟล์อยู่ที่ ~/Desktop/crypto_bot/ 

# ติดตั้ง system deps รวมถึง cron
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron curl \
    && rm -rf /var/lib/apt/lists/*

# ติดตั้ง Python packages
RUN pip install --no-cache-dir \
    flask flask-cors gunicorn \
    google-api-python-client google-auth-httplib2 google-auth-oauthlib

# Copy source code
COPY config.py bot.py config_set2.py bot_set2.py api_server.py recheck.py dashboard.html ./

# สร้าง data directory
RUN mkdir -p /app/data

# Default env
ENV DATA_DIR=/app/data

EXPOSE 3000

CMD ["sh", "-c", "gunicorn --workers 2 --bind 0.0.0.0:${PORT:-3000} --timeout 60 --access-logfile - api_server:app"]
