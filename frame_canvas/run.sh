#!/usr/bin/with-contenv sh
set -e
export FRAME_DATA=/data PYTHONUNBUFFERED=1
exec /opt/venv/bin/python /app/frame_dashboard.py run
