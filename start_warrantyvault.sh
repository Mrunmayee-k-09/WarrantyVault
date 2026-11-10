#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
flask --app run.py seed-demo
mkdir -p instance
if [ -f instance/reminder-scheduler.pid ] && kill -0 "$(cat instance/reminder-scheduler.pid)" 2>/dev/null; then
  echo "WarrantyVault reminder worker already appears to be running."
else
  nohup .venv/bin/python run_scheduler.py >> instance/reminder-scheduler.log 2>&1 &
  echo $! > instance/reminder-scheduler.pid
  echo "Started daily reminder worker (PID $(cat instance/reminder-scheduler.pid))."
fi
python run.py
