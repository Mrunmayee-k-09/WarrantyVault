# WarrantyVault upgrade guide

This ZIP can run as a fresh installation or alongside a database created by the previous WarrantyVault ZIP build.

## Existing database

The application runs `db.create_all()` and then applies additive, non-destructive schema updates automatically at startup. It adds the new reminder email preference fields, claim email-submission metadata, and notification delivery metadata. Existing products, documents, claims, users, and verification records are retained.

Before upgrading an important vault, stop the app and make a copy of `instance/warrantyvault.db` (or take a MySQL backup). Do not copy your old `.env` into a public repository.

## New installation

Extract the ZIP, install Python dependencies using the launch script, then start WarrantyVault. New feature configuration lives in `.env`; see `.env.example`.

## New optional system dependency

Install the Tesseract OCR application to process image files and scanned PDFs. `pytesseract` is only a Python wrapper. PyMuPDF handles PDF rendering; Poppler is not required.

## Email configuration

SMTP settings are server-level settings. Users opt into reminder emails from Settings. Claim emails are sent only when a user explicitly submits a claim from the Claims page. Never commit SMTP credentials.

## Processes

Run one Flask web process and exactly one reminder worker for each database:

```bash
python run.py
python run_scheduler.py
```

The daily job runs at the time configured in `.env`; `python run_scheduler.py --run-once` performs a one-time due-reminder pass.
