"""Run the dedicated WarrantyVault daily reminder worker.

Run exactly one scheduler process per database/environment:
    python run_scheduler.py
Run a single reminder pass for testing:
    python run_scheduler.py --run-once
"""
import argparse
import logging
import os
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.services.reminders import process_due_reminders


def main():
    parser = argparse.ArgumentParser(description="WarrantyVault reminder scheduler")
    parser.add_argument("--run-once", action="store_true", help="send due reminders once and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app()

    def job():
        stats = process_due_reminders(app)
        logging.getLogger("warrantyvault.reminders").info("Reminder run: %s", stats)

    if args.run_once:
        job()
        return

    timezone_name = app.config.get("TIMEZONE", "Asia/Kolkata")
    scheduler = BlockingScheduler(timezone=ZoneInfo(timezone_name))
    scheduler.add_job(
        job,
        trigger="cron",
        hour=int(app.config.get("REMINDER_HOUR", 9)),
        minute=int(app.config.get("REMINDER_MINUTE", 0)),
        id="daily_warranty_reminders",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logging.getLogger("warrantyvault.reminders").info(
        "Scheduler started; daily run at %02d:%02d (%s). Ctrl+C to stop.",
        int(app.config.get("REMINDER_HOUR", 9)),
        int(app.config.get("REMINDER_MINUTE", 0)),
        timezone_name,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
