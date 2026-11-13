from io import BytesIO
from app.extensions import db
from app.models import Product, Document, Verification


def test_registration_and_dashboard(client):
    response = client.post("/register", data={
        "name": "A Test", "email": "a@example.com",
        "password": "password123", "confirm_password": "password123",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Good to see you" in response.data


def test_product_creation_and_account_scope(logged_in_client, app):
    response = logged_in_client.post("/products/new", data={
        "name": "Test Laptop", "brand": "Acer", "model_number": "NL16-71G",
        "serial_number": "SERIAL1234", "retailer": "Example Store",
        "purchase_date": "2026-04-10", "purchase_price": "54990",
        "warranty_start": "2026-04-10", "warranty_expiry": "2028-04-10",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Test Laptop" in response.data
    with app.app_context():
        assert Product.query.filter_by(name="Test Laptop").count() == 1


def test_upload_and_mismatch_verification(logged_in_client, app):
    logged_in_client.post("/products/new", data={
        "name": "Acer Nitro Lite", "brand": "Acer", "model_number": "NL16-71G-54MY",
        "serial_number": "NHD5ASI001526040482N00", "retailer": "S V ELECTRONICS PRIVATE LIMITED",
        "purchase_date": "2026-04-10", "purchase_price": "54990",
        "invoice_number": "INV-2026-0410-08", "warranty_expiry": "2028-04-10",
    }, follow_redirects=True)
    invoice = b"""SAMPLE INVOICE\nInvoice Number: INV-2026-0410-08\nDate: 10/04/2026\nBrand: Acer\nRetailer: SAMPLE ELECTRONICS STORE\nModel Number: NL16-71G-54MY\nSerial Number: NHD5ASI001526040482N99\nPurchase Price: INR 54990.00\n"""
    response = logged_in_client.post("/products/1/documents", data={
        "document_type": "Invoice",
        "document": (BytesIO(invoice), "test-invoice.txt"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        doc = Document.query.first()
        assert doc is not None
        assert doc.status == "Ready to verify"
    response = logged_in_client.post("/documents/1/verify", follow_redirects=True)
    assert response.status_code == 200
    assert b"MISMATCH" in response.data
    with app.app_context():
        verification = Verification.query.first()
        assert verification.overall_result == "MISMATCH"
        outcomes = {f.field_name: f.result for f in verification.fields}
        assert outcomes["Retailer"] == "Mismatch"
        assert outcomes["Serial number"] == "Mismatch"
        assert outcomes["Brand"] == "Match"


def test_manual_correction_is_logged_and_source_file_unchanged(logged_in_client, app, tmp_path):
    logged_in_client.post("/products/new", data={"name": "Test Product", "brand": "Acer", "retailer": "Right Store"}, follow_redirects=True)
    invoice = b"Brand: Acer\nRetailer: Wrong Store\n"
    logged_in_client.post("/products/1/documents", data={
        "document_type": "Invoice", "document": (BytesIO(invoice), "correction.txt")
    }, content_type="multipart/form-data", follow_redirects=True)
    response = logged_in_client.post("/documents/1/correct", data={"retailer": "Right Store"}, follow_redirects=True)
    assert response.status_code == 200
    logged_in_client.post("/documents/1/verify", follow_redirects=True)
    with app.app_context():
        from app.models import AuditLog
        doc = Document.query.first()
        verification = Verification.query.first()
        retailer_field = next(f for f in verification.fields if f.field_name == "Retailer")
        assert retailer_field.result == "Match"
        assert retailer_field.value_source == "Manual correction"
        assert AuditLog.query.filter_by(action="extraction_corrected").count() == 1
        assert "Wrong Store" in open(f"{app.config['UPLOAD_FOLDER']}/{doc.stored_name}", encoding="utf-8").read()  # original upload remains unchanged


def _add_product_and_claim(app, *, expiry=None):
    from datetime import date
    from app.models import User, Product, Claim
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        product = Product(user_id=user.id, name="Warranty Test Laptop", brand="Example",
                          serial_number="SERIAL-1234", model_number="MODEL-123",
                          retailer="Example Store", warranty_expiry=expiry)
        db.session.add(product)
        db.session.flush()
        claim = Claim(product_id=product.id, issue_title="Battery issue",
                      description="Battery does not charge after 20 minutes.", status="Draft")
        db.session.add(claim)
        db.session.commit()
        return product.id, claim.id, user.id


def test_claim_email_submission_sets_submitted_only_after_smtp_accepts(logged_in_client, app, monkeypatch):
    import app.main as main_module
    from app.models import Claim
    _product_id, claim_id, _user_id = _add_product_and_claim(app)
    app.config.update({
        "MAIL_SERVER": "smtp.example.test", "MAIL_PORT": 587,
        "MAIL_USERNAME": "sender@example.test", "MAIL_PASSWORD": "secret",
        "MAIL_DEFAULT_SENDER": "sender@example.test", "MAIL_USE_TLS": True,
        "MAIL_USE_SSL": False,
    })
    sent = {}

    def fake_send_email(config, **kwargs):
        sent.update(kwargs)
        return "<message-id@example.test>"

    monkeypatch.setattr(main_module, "send_email", fake_send_email)
    response = logged_in_client.post(f"/claims/{claim_id}/submit", data={
        "manufacturer_name": "Example Support",
        "manufacturer_email": "support@example.com",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"SMTP server" in response.data
    assert sent["recipient"] == "support@example.com"
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "Submitted"
        assert claim.submission_channel == "Email"
        assert claim.submission_message_id == "<message-id@example.test>"
        assert claim.submitted_at is not None


def test_claim_email_failure_does_not_mark_submitted(logged_in_client, app, monkeypatch):
    import app.main as main_module
    from app.models import Claim
    _product_id, claim_id, _user_id = _add_product_and_claim(app)
    app.config.update({
        "MAIL_SERVER": "smtp.example.test", "MAIL_PORT": 587,
        "MAIL_USERNAME": "sender@example.test", "MAIL_PASSWORD": "secret",
        "MAIL_DEFAULT_SENDER": "sender@example.test",
    })

    def failed_send(*_args, **_kwargs):
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr(main_module, "send_email", failed_send)
    response = logged_in_client.post(f"/claims/{claim_id}/submit", data={
        "manufacturer_email": "support@example.com",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"SMTP unavailable" in response.data
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "Draft"
        assert claim.submitted_at is None
        assert "SMTP unavailable" in claim.submission_error


def test_scheduled_reminders_create_once_and_send_only_when_opted_in(app, monkeypatch):
    from datetime import date, timedelta
    import app.services.reminders as reminders
    from app.models import User, Product, Notification
    with app.app_context():
        user = User(name="Reminder User", email="reminder@example.com", reminder_days=30,
                    reminder_email_enabled=True, reminder_email="alerts@example.com")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(Product(user_id=user.id, name="Soon Expiring", warranty_expiry=date(2026, 11, 13)))
        db.session.commit()
    app.config.update({
        "MAIL_SERVER": "smtp.example.test", "MAIL_PORT": 587,
        "MAIL_USERNAME": "sender@example.test", "MAIL_PASSWORD": "secret",
        "MAIL_DEFAULT_SENDER": "sender@example.test",
    })
    sent = []
    monkeypatch.setattr(reminders, "send_email", lambda config, **kwargs: sent.append(kwargs) or "<reminder@example.test>")
    first = reminders.process_due_reminders(app, today=date(2026, 11, 3))
    second = reminders.process_due_reminders(app, today=date(2026, 11, 3))
    assert first["notifications_created"] == 1
    assert first["emails_sent"] == 1
    assert second["notifications_created"] == 0
    assert second["emails_sent"] == 0
    assert len(sent) == 1
    assert sent[0]["recipient"] == "alerts@example.com"
    with app.app_context():
        assert Notification.query.filter_by(category="Warranty").count() == 1
        assert Notification.query.first().email_sent_at is not None


def test_settings_saves_email_opt_in_and_destination(logged_in_client, app):
    from app.models import User
    response = logged_in_client.post("/settings", data={
        "name": "Test User", "reminder_days": "15",
        "reminder_email": "warranty-alerts@example.com",
        "reminder_email_enabled": "on",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        assert user.reminder_days == 15
        assert user.reminder_email_enabled is True
        assert user.reminder_email == "warranty-alerts@example.com"


def test_scanned_pdf_pages_are_passed_to_ocr(logged_in_client, app, monkeypatch, tmp_path):
    import sys
    import types
    from app.services import extraction
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"not a real pdf; parser is mocked")

    class FakePage:
        def extract_text(self):
            return ""

    class FakeReader:
        def __init__(self, _path):
            self.pages = [FakePage()]

    fake_module = types.ModuleType("pypdf")
    fake_module.PdfReader = FakeReader
    monkeypatch.setitem(sys.modules, "pypdf", fake_module)
    monkeypatch.setattr(extraction, "_ocr_pdf_pages", lambda path, pages: (
        "Brand: Acer\\nSerial Number: SERIAL1234", ["Scanned PDF OCR ran"], 91.0
    ))
    text, notes, score = extraction.extract_text(str(pdf_path), "pdf")
    assert "Brand: Acer" in text
    assert "Scanned PDF OCR ran" in notes
    assert any("not document authenticity" in note for note in notes)
    assert score == 91.0


def _create_autofill_test_document(logged_in_client):
    """Create a product and invoice with fields that can be auto-filled."""
    logged_in_client.post("/products/new", data={
        "name": "AutoFill Test Laptop",
        "brand": "Acer",
        "retailer": "Original Store",
        "serial_number": "SERIAL-ORIGINAL",
        "purchase_date": "2026-04-10",
        "purchase_price": "54990",
        "invoice_number": "INV-TEST-001",
    }, follow_redirects=True)

    invoice = (
        b"Invoice Number: INV-TEST-001\n"
        b"Date: 10/04/2026\n"
        b"Brand: Acer\n"
        b"Retailer: Invoice Store\n"
        b"Serial Number: SERIAL-INVOICE\n"
        b"Purchase Price: INR 54990.00\n"
    )

    response = logged_in_client.post(
        "/products/1/documents",
        data={
            "document_type": "Invoice",
            "document": (BytesIO(invoice), "autofill-test.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    return 1


def test_autofill_updates_only_user_selected_fields(logged_in_client, app):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={"fields": ["retailer"]},
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        product = Product.query.first()

        assert product.retailer == "Invoice Store"
        assert product.serial_number == "SERIAL-ORIGINAL"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 1

        assert Verification.query.filter_by(
            document_id=document_id
        ).count() == 1


def test_autofill_preserves_previous_verification_history(
    logged_in_client, app
):
    document_id = _create_autofill_test_document(logged_in_client)

    # Create the original verification.
    response = logged_in_client.post(
        f"/documents/{document_id}/verify",
        follow_redirects=True,
    )
    assert response.status_code == 200

    # Approve a retailer update.
    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={"fields": ["retailer"]},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        history = Verification.query.filter_by(
            document_id=document_id
        ).all()

        assert len(history) == 2


def test_autofill_rejects_access_to_another_users_document(
    logged_in_client, app
):
    from app.models import User

    document_id = _create_autofill_test_document(logged_in_client)

    # Sign out the original user.
    logged_in_client.post("/logout")

    # Register a different user.
    response = logged_in_client.post("/register", data={
        "name": "Another User",
        "email": "another@example.com",
        "password": "password123",
        "confirm_password": "password123",
    })
    assert response.status_code in (200, 302)

    # The second user must not update the first user's document.
    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={"fields": ["retailer"]},
    )

    assert response.status_code == 404



def test_autofill_rejects_empty_field_selection(logged_in_client, app):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Select at least one field" in response.data

    with app.app_context():
        product = Product.query.first()

        assert product.retailer == "Original Store"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0


def test_autofill_rejects_invalid_field_name(logged_in_client, app):
    document_id = _create_autofill_test_document(logged_in_client)

    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={"fields": ["password"]},
    )

    assert response.status_code == 400

    with app.app_context():
        product = Product.query.first()

        assert product.retailer == "Original Store"
        assert product.serial_number == "SERIAL-ORIGINAL"



def test_parse_float_rejects_non_finite_values():
    from app.main import _parse_float

    assert _parse_float("NaN") is None
    assert _parse_float("Infinity") is None
    assert _parse_float("-Infinity") is None

    assert _parse_float("54990") == 54990.0
    assert _parse_float("54,990") == 54990.0
    assert _parse_float("₹54,990") == 54990.0
    assert _parse_float("invalid") is None



def test_parse_date_accepts_iso_format_and_rejects_invalid_dates():
    from datetime import date
    from app.main import _parse_date

    assert _parse_date("2026-04-10") == date(2026, 4, 10)
    assert _parse_date("not-a-date") is None
    assert _parse_date("2026-02-30") is None
    assert _parse_date("") is None


def test_parse_date_supports_common_invoice_formats():
    from datetime import date
    from app.main import _parse_date

    assert _parse_date("2026-04-10") == date(2026, 4, 10)
    assert _parse_date("10/04/2026") == date(2026, 4, 10)
    assert _parse_date("10-04-2026") == date(2026, 4, 10)
    assert _parse_date("10/04/26") == date(2026, 4, 10)


def test_autofill_rolls_back_product_and_audit_if_verification_fails(
    logged_in_client, app, monkeypatch
):
    from app.models import AuditLog
    import app.main as main_module

    document_id = _create_autofill_test_document(logged_in_client)

    def fail_verification(doc):
        raise RuntimeError("Simulated verification failure")

    monkeypatch.setattr(
        main_module,
        "_run_verification",
        fail_verification,
    )

    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={"fields": ["retailer"]},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"AutoFill failed. No changes were saved." in response.data

    with app.app_context():
        product = Product.query.first()

        assert product.retailer == "Original Store"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0

        assert Verification.query.filter_by(
            document_id=document_id
        ).count() == 0
