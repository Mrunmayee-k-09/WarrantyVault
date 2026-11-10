# WarrantyVault implementation notes

## Current feature set
- Flask application with SQLite default and optional MySQL URL.
- Account-scoped products, warranty dates, document uploads, extraction and explainable field verification.
- Scanned PDF OCR: PyMuPDF renders pages and Tesseract recognizes text. Up to 10 scanned pages are processed per pass.
- Claim email submission: prepares an email to the user-supplied official support contact, attaches claim evidence and optional product document, and marks Submitted only after SMTP accepts the message.
- Scheduled warranty notifications: one dedicated APScheduler worker runs daily; in-app notifications are generated for due warranties and email is sent only after opt-in.
- Additive schema updater supports existing database files from earlier WarrantyVault ZIP builds.

## Important semantics
- OCR confidence estimates recognition quality; it is not authenticity evidence.
- `Submitted` means the configured SMTP relay accepted the outgoing claim email. It does not mean the manufacturer acknowledged or registered the claim.
- Reminder emails require SMTP configuration, explicit user opt-in, and a running `run_scheduler.py` worker.
- Only one scheduler worker should run for a given database.

## Setup additions
- Install `requirements.txt`.
- Install Tesseract OCR separately and configure `TESSERACT_CMD` if it is not on PATH.
- Configure SMTP values in `.env` for claim emails and reminders.
- Start `python run_scheduler.py` alongside `python run.py`; one-off test: `python run_scheduler.py --run-once`.
