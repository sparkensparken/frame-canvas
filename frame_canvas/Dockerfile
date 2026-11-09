ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm
FROM ${BUILD_FROM}

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      chromium ffmpeg python3 python3-venv ca-certificates tzdata \
      fonts-inter fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir \
      "samsungtvws[async,encrypted]" icalendar recurring-ical-events

ENV FRAME_DATA=/data \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Oslo

WORKDIR /app
COPY app/ /app/
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
