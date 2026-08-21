#!/bin/bash
cd "$(dirname "$0")"
while true; do
  echo "[$(date)] Starting SpyLickbot..."
  python3 bot.py
  echo "[$(date)] Bot exited with code $?. Restarting in 10s..."
  sleep 10
done
