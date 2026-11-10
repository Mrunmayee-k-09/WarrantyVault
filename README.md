# WarrantyVault

**A digital warranty-management and document-verification platform.** WarrantyVault keeps product details, purchase evidence, warranty timelines, verification results, and claim notes together in one account-scoped vault.

> **Important:** WarrantyVault compares information extracted from a document with information saved by the user. A match is a consistency result, not proof that a document is genuine. OCR confidence describes how confidently text was read; it does not establish that the extracted value is true.

## What's included

- **Account access:** registration, login, password hashing, session handling, CSRF protection, and account-scoped queries.
- **Product and warranty records:** product name, brand, category, model and serial numbers, retailer, invoice number, purchase details, warranty dates, duration, and notes.
- **Dashboard:** database-backed product totals, active and expired coverage, upcoming expiries, review-needed documents, open claims, and recent documents.
- **Document storage:** upload, download, delete, document type, file size, timestamp, processing status, retry action, and document-to-product association.
- **Text extraction:** selectable-text PDF extraction, scanned-PDF rendering and OCR through PyMuPDF + Tesseract, plus image OCR, TXT and DOCX parsing. Parsed fields include brand, retailer, model number, serial number, purchase date, invoice number, and purchase price.
- **Explainable verification:** Match, Partial, Mismatch, and Skipped field outcomes; HIGH/MEDIUM/LOW extraction confidence; independent `review_required` flags; plain-language reasons; overall VERIFIED, REVIEW_REQUIRED, MISMATCH, or UNABLE_TO_VERIFY results.
- **Cross-field checks:** basic purchase/warranty date plausibility warnings. These are consistency checks, not fraud detection.
- **Human correction:** edit extracted values without changing the source file; manual corrections are separately identified and written to the audit log.
- **Verification history:** each verification run is preserved with field-level values, outcomes, confidence, review flags, and notes.
- **Claims tracker:** create claim drafts, attach supporting files, send a prepared claim email to a support address entered by the user, and track Draft, Submitted, In progress, Resolved, or Rejected states. Submitted means the SMTP relay accepted the message; it is not proof of manufacturer acknowledgement or a portal registration.
- **Scheduled notifications:** a separate daily APScheduler worker creates in-app expiry reminders and sends email only when the user opts in and SMTP is configured.
- **Search and filters:** search products by name, brand, model, or serial; filter by warranty status; sort by expiry or recently updated.
- **Export:** CSV product export and JSON backup of product records, extracted data, verification summaries, and claims.
- **Responsive interface:** dark navigation sidebar, light content surfaces, status labels, empty states, responsive tables, and mobile navigation.

## Tech stack

- Python 3.10+
- Flask, Flask-Login, Flask-SQLAlchemy, Flask-WTF
- SQLAlchemy ORM
- SQLite by default for a zero-setup local run; optional MySQL through `DATABASE_URL`
- HTML, Jinja templates, CSS, and vanilla JavaScript
- pypdf, python-docx, Pillow, pytesseract, PyMuPDF
- APScheduler for daily reminders
- Python smtplib/email for outbound mail
- pytest

## Quick start (Windows 11 / PowerShell)

1. Extract the ZIP file.
2. Open PowerShell in the extracted `WarrantyVault` folder.
3. Create and activate a virtual environment:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

4. Install dependencies:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. Create your local environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

   For a local demo, the default development secret works, but change `SECRET_KEY` before using real records or deploying.

6. (Optional) Create demo records:

   ```powershell
   flask --app run.py seed-demo
   ```

7. Start the web app:

   ```powershell
   python run.py
   ```

8. In a **second terminal** (same folder and activated virtual environment), start the reminder worker:

   ```powershell
   python run_scheduler.py
   ```

   The `start_warrantyvault.bat` launcher starts the reminder worker automatically in a minimized window.

9. Open **http://127.0.0.1:5000**.

   Demo login (only after `seed-demo`):
   - Email: `demo@warrantyvault.local`
   - Password: `WarrantyVault123!`

You can also register a new account from the login page. The Windows `start_warrantyvault.bat` (or macOS/Linux `start_warrantyvault.sh`) script creates the virtual environment, installs dependencies, creates the demo account and sample products, starts the reminder worker, and starts the web server. The database is created automatically on first launch. `flask --app run.py init-db` is available as an explicit database setup command. The demo account has a known password and is for local demonstration only; do not expose it on a public deployment.

### Quick start (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
flask --app run.py seed-demo  # optional
python run.py                 # terminal 1
python run_scheduler.py       # terminal 2
```

## Try document verification

1. Sign in to the demo account (or create your own account).
2. Open **My Products → Acer Nitro Lite**.
3. Under **Attached documents**, upload one of the supplied sample files:
   - `sample_data/acer_invoice_match.txt` — intended to match the demo Acer product.
   - `sample_data/acer_invoice_mismatch.txt` — deliberately has a different retailer and serial number.
4. Open the uploaded document's report and inspect the extracted fields.
5. Choose **Verify against product**.
6. The mismatch sample should show a MISMATCH result for its retailer and serial number. This is synthetic test content, not a real invoice.

## OCR setup (images and scanned PDFs)

- **TXT/DOCX:** extracted directly.
- **Selectable-text PDFs:** the embedded text layer is extracted with `pypdf`.
- **Scanned/image-only PDFs:** WarrantyVault uses **PyMuPDF** to render pages to images, then sends those images through **Tesseract OCR**. No Poppler installation is needed. The first 10 scanned pages are OCR-processed in one pass; larger documents show a note.
- **PNG/JPG/TIFF:** processed with the same Tesseract OCR pipeline.

The Python dependencies (including `pytesseract`, Pillow and PyMuPDF) are installed by `pip install -r requirements.txt`. **Tesseract itself is a separate Windows application** and must also be installed:

1. Install the Tesseract OCR engine for Windows (the commonly used Windows installer is maintained by the UB Mannheim project).
2. Open a new terminal and run `tesseract --version` to confirm it is installed.
3. If Windows cannot find it, set this in `.env` (adjust the path if needed):

   ```dotenv
   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```
4. Restart WarrantyVault and upload a clear, upright scanned PDF or image.

Tesseract confidence estimates how confidently text was recognized. It does **not** prove the invoice is genuine or that its values are correct. Low-confidence fields remain flagged for human review.

## Claim email submission

WarrantyVault can send a prepared warranty-support email to the address you enter on the **Claims** page. The email includes the product identifiers, issue description, your reply-to address, and the optional claim attachment / selected product document.

1. Configure SMTP in `.env` (the example below uses Gmail's SMTP host; use an app password or the equivalent credential method provided by your email host):

   ```dotenv
   MAIL_SERVER=smtp.gmail.com
   MAIL_PORT=587
   MAIL_USE_TLS=true
   MAIL_USE_SSL=false
   MAIL_USERNAME=your-address@gmail.com
   MAIL_PASSWORD=your-email-provider-app-password
   MAIL_DEFAULT_SENDER=your-address@gmail.com
   ```
2. Restart WarrantyVault.
3. Create a claim draft from a product page.
4. Open **Claims**, enter the manufacturer's official support email, choose any additional invoice/warranty document, and select **Send claim email**.
5. WarrantyVault changes the status to **Submitted** only after the configured SMTP server accepts the email.

This is email-based submission, not a manufacturer API or portal integration. SMTP acceptance does not confirm that the manufacturer received, read, accepted, or registered the claim. WarrantyVault does not invent a manufacturer's claim number; check the support reply and record the reference with the manufacturer.

The sender and SMTP credentials are configured by the server operator, not stored in user account settings. Never commit real credentials to source control or share your `.env` file.

## Scheduled warranty expiry reminders

In-app notifications are generated by the reminder worker. Users can opt into email on **Settings**, choose a 7/15/30/60/90-day reminder window, and choose a delivery address. Email reminders are off by default. The daily job creates one notification per product/expiry/preference combination and retries email on a later run when delivery failed.

The reminder worker is a separate process (one instance per database):

```bash
python run_scheduler.py
```

It runs daily at `REMINDER_HOUR` / `REMINDER_MINUTE` in `TIMEZONE` from `.env` (default **09:00 Asia/Kolkata**). The Windows launcher starts the worker in a minimized console; the Linux/macOS launcher starts it in the background and records its PID in `instance/reminder-scheduler.pid`.

For an immediate one-off test, run this from another terminal with the virtual environment active:

```bash
python run_scheduler.py --run-once
# or
flask --app run.py send-reminders
```

The one-off command is useful for testing delivery without waiting until the daily scheduled time. If SMTP is not configured or the user has not opted in, the in-app notification can still be created but no email is sent. Check `instance/reminder-scheduler.log` for worker output.

Do not start multiple reminder workers against the same database. In production, run one dedicated worker process/service alongside the web application.

## Optional MySQL configuration

SQLite is the default to make the extracted project runnable without installing a database server. To use MySQL instead:

1. Create a database and database user in MySQL.
2. Copy `.env.example` to `.env`.
3. Set `DATABASE_URL` to a SQLAlchemy connection string, for example:

   ```dotenv
   DATABASE_URL=mysql+pymysql://warranty_user:your_password@localhost:3306/warrantyvault?charset=utf8mb4
   ```

4. Set a strong random `SECRET_KEY` and start the app. Tables are created automatically by the application.

Do not commit `.env` or real uploaded documents to source control.

## Run tests

```bash
pytest -q
```

The included tests cover account creation, product insertion, invoice upload and mismatch verification, manual extraction corrections, claim email success/failure behavior, reminder deduplication and email opt-in, settings, and scanned-PDF OCR routing. Tests use a temporary SQLite database. Run them with `pytest -q` after installing the requirements.

## Project layout

```text
WarrantyVault/
├── app/
│   ├── __init__.py             # Flask application factory
│   ├── schema.py               # Additive upgrade for databases from earlier ZIP builds
│   ├── auth.py                 # Registration, login, logout
│   ├── extensions.py           # SQLAlchemy, LoginManager, CSRF
│   ├── main.py                 # Dashboard and application routes
│   ├── models.py               # Database models
│   ├── services/
│   │   ├── extraction.py       # File parsing, scanned-PDF rendering and OCR
│   │   ├── verification.py     # Explainable verification engine
│   │   ├── mailer.py           # SMTP email delivery and attachment handling
│   │   └── reminders.py        # Daily expiry notification/email workflow
│   ├── static/                 # CSS and JavaScript
│   └── templates/              # Jinja pages and layouts
├── instance/                   # Local database and uploaded files (gitignored)
├── sample_data/                # Synthetic demo invoice text files
├── tests/                      # pytest suite
├── run_scheduler.py            # Separate daily reminder worker
├── UPGRADE_GUIDE.md            # Existing-database upgrade notes
├── .env.example
├── config.py
├── LICENSE
├── requirements.txt
└── run.py
```

## Known limitations / production checklist

This is a functional college-project prototype, not a production warranty processor. Before public deployment or use with sensitive documents, add a production-grade secrets and deployment configuration, HTTPS, secure cookie settings, rate limiting, password reset and email verification, malware scanning, stricter file-content validation, access-controlled backups, retention/deletion policies, monitoring, and formal versioned database migrations. The current invoice parser is a transparent labelled-field baseline; a stronger layout-aware extraction model and a wider OCR test corpus would be needed for broad real-world invoice support.

The claim workflow sends an email to a support address supplied by the user; it is not connected to manufacturer APIs or portals, and SMTP acceptance is not proof of manufacturer receipt or acknowledgement. Scheduled reminders require the separate reminder worker to be running and SMTP configuration plus explicit user opt-in for email delivery. The consistency checks do not label records as fraudulent. Patentability cannot be inferred from this implementation; that requires a prior-art search and qualified patent advice.

## License

MIT. See [LICENSE](LICENSE).
