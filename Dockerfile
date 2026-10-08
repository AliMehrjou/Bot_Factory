# =====================================================================
# Image بات فروش ربات‌ساز (factory_bot)
# شامل docker CLI + compose plugin تا بتواند کانتینرهای مشتری را
# از طریق docker.sock هاست کنترل کند.
# ⚠️ build از ایران: اگر download.docker.com در دسترس نبود،
#    با HTTPS_PROXY بیلد (build) کن (راهنمای README).
# =====================================================================
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Tehran

# --- Docker CLI + Compose v2 (برای کنترل instanceها از داخل کانتینر) ---
RUN apt-get update -o Acquire::Retries=30 -o Acquire::http::Timeout=120 && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg gcc && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# این کانتینر باید root اجرا شود تا بتواند:
#  ۱) docker.sock هاست را بخواند (معادل دسترسی root به هاست!)
#  ۲) پوشه‌های instance روی /opt/factory را با مالکیت 1000 بسازد
# ⚠️ امنیت: تنها این سرویس به docker.sock دسترسی دارد؛ .env را هرگز افشا نکن.
CMD ["python", "main.py"]
