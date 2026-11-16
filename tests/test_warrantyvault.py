from io import BytesIO
from pathlib import Path
from app.extensions import db
from app.models import Product, Document, Verification, Claim, Notification, User

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

def test_upload_rejects_file_with_mismatched_signature(logged_in_client, app):
    logged_in_client.post("/products/new", data={
        "name": "Test Laptop",
        "brand": "Acer",
    }, follow_redirects=True)

    response = logged_in_client.post("/products/1/documents", data={
        "document_type": "Invoice",
        "document": (BytesIO(b"This is not a PDF"), "fake-invoice.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200
    assert b"does not appear to match" in response.data

    with app.app_context():
        assert Document.query.count() == 0

def test_upload_rejects_empty_file(logged_in_client, app):
    logged_in_client.post("/products/new", data={
        "name": "Test Laptop",
        "brand": "Acer",
    }, follow_redirects=True)

    response = logged_in_client.post("/products/1/documents", data={
        "document_type": "Invoice",
        "document": (BytesIO(b""), "empty-invoice.txt"),
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200
    assert b"uploaded file is empty" in response.data

    with app.app_context():
        assert Document.query.count() == 0

def test_upload_accepts_valid_pdf_signature(logged_in_client, app):
    logged_in_client.post("/products/new", data={
        "name": "Test Laptop",
        "brand": "Acer",
    }, follow_redirects=True)

    pdf_content = b"%PDF-1.4\nTest PDF content"

    response = logged_in_client.post("/products/1/documents", data={
        "document_type": "Invoice",
        "document": (BytesIO(pdf_content), "invoice.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200

    with app.app_context():
        doc = Document.query.first()
        assert doc is not None

        saved_path = Path(app.config["UPLOAD_FOLDER"]) / doc.stored_name
        assert saved_path.exists()
        assert saved_path.read_bytes().startswith(b"%PDF-")


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



def _prepare_manual_correction_document(client, app):
    """Create a document with predictable extracted fields for correction tests."""
    response = client.post("/products/new", data={
        "name": "Correction Test Product",
        "brand": "Acer",
        "retailer": "Wrong Store",
    }, follow_redirects=True)
    assert response.status_code == 200

    response = client.post("/products/1/documents", data={
        "document_type": "Invoice",
        "document": (BytesIO(b"Sample correction test invoice"), "correction-test.txt"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        doc = Document.query.order_by(Document.id.desc()).first()
        assert doc is not None

        doc.extracted_data = {
            "fields": {
                "brand": "Acer",
                "retailer": "Wrong Store",
                "purchase_date": "2026-04-10",
                "purchase_price": "100.0",
            },
            "field_confidences": {},
        }
        db.session.commit()
        return doc.id


def test_manual_correction_rejects_invalid_purchase_date(logged_in_client, app):
    document_id = _prepare_manual_correction_document(logged_in_client, app)

    response = logged_in_client.post(
        f"/documents/{document_id}/correct",
        data={"purchase_date": "31/02/2026"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"valid purchase date" in response.data.lower()

    with app.app_context():
        doc = db.session.get(Document, document_id)
        assert doc.extracted_data["fields"]["purchase_date"] == "2026-04-10"


def test_manual_correction_rejects_negative_purchase_price(logged_in_client, app):
    document_id = _prepare_manual_correction_document(logged_in_client, app)

    response = logged_in_client.post(
        f"/documents/{document_id}/correct",
        data={"purchase_price": "-500"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"valid non-negative purchase price" in response.data.lower()

    with app.app_context():
        doc = db.session.get(Document, document_id)
        assert doc.extracted_data["fields"]["purchase_price"] == "100.0"


def test_manual_correction_rejects_oversized_text(logged_in_client, app):
    document_id = _prepare_manual_correction_document(logged_in_client, app)

    response = logged_in_client.post(
        f"/documents/{document_id}/correct",
        data={"brand": "A" * 121},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"cannot exceed 120 characters" in response.data

    with app.app_context():
        doc = db.session.get(Document, document_id)
        assert doc.extracted_data["fields"]["brand"] == "Acer"


def test_manual_correction_accepts_and_normalizes_date_and_price(logged_in_client, app):
    document_id = _prepare_manual_correction_document(logged_in_client, app)

    response = logged_in_client.post(
        f"/documents/{document_id}/correct",
        data={
            "purchase_date": "11/04/2026",
            "purchase_price": "54990.50",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        doc = db.session.get(Document, document_id)
        fields = doc.extracted_data["fields"]

        assert fields["purchase_date"] == "2026-04-11"
        assert fields["purchase_price"] == "54990.5"
        assert "purchase_date" in doc.extracted_data["manual_corrections"]
        assert "purchase_price" in doc.extracted_data["manual_corrections"]


def test_manual_correction_rejects_all_changes_if_one_field_is_invalid(logged_in_client, app):
    document_id = _prepare_manual_correction_document(logged_in_client, app)

    response = logged_in_client.post(
        f"/documents/{document_id}/correct",
        data={
            "retailer": "Correct Store",
            "purchase_date": "not-a-date",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"valid purchase date" in response.data.lower()

    with app.app_context():
        doc = db.session.get(Document, document_id)
        fields = doc.extracted_data["fields"]

        assert fields["retailer"] == "Wrong Store"
        assert fields["purchase_date"] == "2026-04-10"
        assert "manual_corrections" not in doc.extracted_data


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
    send_calls = []

    def fake_send_email(config, **kwargs):
        sent.update(kwargs)
        send_calls.append(kwargs)
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
            # Verify the actual email payload.
    assert sent["recipient"] == "support@example.com"
    assert "Warranty support request" in sent["subject"]
    assert "Warranty Test Laptop" in sent["body"]
    assert "Battery issue" in sent["body"]
    assert "Battery does not charge" in sent["body"]
    assert sent["reply_to"] is not None
    assert len(send_calls) == 1

    # A second submission must not send another email.
    second_response = logged_in_client.post(
        f"/claims/{claim_id}/submit",
        data={
            "manufacturer_name": "Example Support",
            "manufacturer_email": "support@example.com",
        },
        follow_redirects=True,
    )

    assert second_response.status_code == 200
    assert len(send_calls) == 1


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
    assert b"service error" in response.data
    assert b"SMTP unavailable" not in response.data
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "Draft"
        assert claim.submitted_at is None
        assert "SMTP unavailable" in claim.submission_error


def test_claim_simulation_does_not_mark_claim_as_submitted(
    logged_in_client, app, monkeypatch
):
    import app.main as main_module
    from app.models import Claim

    _product_id, claim_id, _user_id = _add_product_and_claim(app)

    app.config.update({
        "DEBUG": True,
        "CLAIM_SUBMISSION_MODE": "simulation",
        "CLAIM_SIMULATION_ALLOWED": True,
    })

    def unexpected_email(*_args, **_kwargs):
        raise AssertionError("Simulation must not send an actual email")

    monkeypatch.setattr(main_module, "send_email", unexpected_email)

    response = logged_in_client.post(
        f"/claims/{claim_id}/submit",
        data={
            "manufacturer_name": "Example Support",
            "manufacturer_email": "support@example.com",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Simulation completed" in response.data

    with app.app_context():
        claim = db.session.get(Claim, claim_id)

        assert claim.status == "Draft"
        assert claim.submission_channel == "Simulation"
        assert claim.submitted_at is None
        assert claim.submission_message_id is None
        assert claim.submission_error is None


def test_claim_submission_rejects_missing_claim_attachment(logged_in_client, app):
    from app.models import Claim

    _product_id, claim_id, _user_id = _add_product_and_claim(app)

    app.config.update({
        "MAIL_SERVER": "smtp.example.test",
        "MAIL_PORT": 587,
        "MAIL_USERNAME": "sender@example.test",
        "MAIL_PASSWORD": "secret",
        "MAIL_DEFAULT_SENDER": "sender@example.test",
    })

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.attachment_path = "missing-evidence.pdf"
        claim.attachment_name = "missing-evidence.pdf"
        db.session.commit()

    response = logged_in_client.post(
        f"/claims/{claim_id}/submit",
        data={"manufacturer_email": "support@example.com"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"missing or cannot be read" in response.data

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "Draft"
        assert claim.submitted_at is None


def test_mailer_rejects_missing_attachment(tmp_path):
    from app.services.mailer import send_email

    missing_file = tmp_path / "missing-evidence.pdf"

    config = {
        "MAIL_SERVER": "smtp.example.test",
        "MAIL_PORT": 587,
        "MAIL_USERNAME": "sender@example.test",
        "MAIL_PASSWORD": "secret",
        "MAIL_DEFAULT_SENDER": "sender@example.test",
    }

    import pytest

    with pytest.raises(FileNotFoundError, match="missing-evidence.pdf"):
        send_email(
            config,
            recipient="support@example.com",
            subject="Warranty claim",
            body="Please review the attached evidence.",
            attachments=[(str(missing_file), "missing-evidence.pdf")],
        )


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


from html.parser import HTMLParser


def _post_autofill(client, document_id, data=None, follow_redirects=True):
    """Fetch a fresh review token and submit the AutoFill approval."""

    class SnapshotTokenParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.token = None

        def handle_starttag(self, tag, attrs):
            if tag == "input":
                attributes = dict(attrs)
                if attributes.get("name") == "snapshot_token":
                    self.token = attributes.get("value")

    # Open the review page to obtain its signed snapshot token.
    response = client.get(
        f"/documents/{document_id}/autofill"
    )

    assert response.status_code == 200

    parser = SnapshotTokenParser()
    parser.feed(response.get_data(as_text=True))

    assert parser.token, "AutoFill snapshot token was not found."

    # Include the token in the approval submission.
    form_data = dict(data or {})
    form_data["snapshot_token"] = parser.token

    return client.post(
        f"/documents/{document_id}/autofill",
        data=form_data,
        follow_redirects=follow_redirects,
    )



def test_autofill_updates_only_user_selected_fields(logged_in_client, app):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    response = _post_autofill(
    logged_in_client,
    document_id,
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
    response = _post_autofill(
    logged_in_client,
    document_id,
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

    response = _post_autofill(
        logged_in_client,
        document_id,
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

    response = _post_autofill(
        logged_in_client,
        document_id,
        data={"fields": ["password"]},
        follow_redirects=False,
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

    response = _post_autofill(
    logged_in_client,
    document_id,
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



def test_failed_autofill_preserves_existing_verification_history(
    logged_in_client, app, monkeypatch
):
    from app.models import AuditLog
    import app.main as main_module

    document_id = _create_autofill_test_document(logged_in_client)

    # Create an original successful verification.
    response = logged_in_client.post(
    f"/documents/{document_id}/verify",
    follow_redirects=True,
)
    assert response.status_code == 200

    with app.app_context():
        original_history = Verification.query.filter_by(
            document_id=document_id
        ).all()

        assert len(original_history) == 1
        original_verification_id = original_history[0].id

    # Force the next verification to fail.
    def fail_verification(doc):
        raise RuntimeError("Simulated verification failure")

    monkeypatch.setattr(
        main_module,
        "_run_verification",
        fail_verification,
    )

    response = _post_autofill(
    logged_in_client,
    document_id,
    data={"fields": ["retailer"]},
    follow_redirects=True,
)

    assert response.status_code == 200
    assert b"AutoFill failed" in response.data

    with app.app_context():
        product = Product.query.first()

        history = Verification.query.filter_by(
            document_id=document_id
        ).all()

        assert product.retailer == "Original Store"
        assert len(history) == 1
        assert history[0].id == original_verification_id

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0



def test_parse_float_rejects_invalid_purchase_prices():
    from app.main import _parse_float

    # Valid prices
    assert _parse_float("54990") == 54990.0
    assert _parse_float("54990.50") == 54990.50
    assert _parse_float("₹54,990") == 54990.0
    assert _parse_float("12,34,567.50") == 1234567.50
    assert _parse_float("1,234,567.50") == 1234567.50
    assert _parse_float("0") == 0.0

    # Invalid prices
    assert _parse_float("-500") is None
    assert _parse_float("abc123") is None
    assert _parse_float("₹") is None
    assert _parse_float("12,34,56") is None
    assert _parse_float("1e309") is None
    assert _parse_float("") is None
    assert _parse_float("   ") is None


def test_parse_date_handles_whitespace_and_rejects_unsupported_formats():
    from datetime import date
    from app.main import _parse_date

    assert _parse_date("  10/04/2026  ") == date(2026, 4, 10)
    assert _parse_date("04/10/2026") == date(2026, 10, 4)
    assert _parse_date("2026/04/10") is None
    assert _parse_date("10-Apr-2026") is None
    assert _parse_date("   ") is None

def test_autofill_rejects_oversized_text_values(logged_in_client, app):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    with app.app_context():
        document = db.session.get(Document, document_id)
        extracted_data = document.extracted_data.copy()
        extracted_fields = extracted_data.get("fields", {}).copy()

        extracted_fields["retailer"] = "A" * 181

        extracted_data["fields"] = extracted_fields
        document.extracted_data = extracted_data
        db.session.commit()

    response = _post_autofill(
    logged_in_client, document_id,
    data={"fields": ["retailer"]},
    follow_redirects=True,
)

    assert response.status_code == 200
    assert b"No valid changes were available to apply." in response.data

    with app.app_context():
        product = Product.query.first()

        assert product.retailer == "Original Store"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0

        assert Verification.query.filter_by(
            document_id=document_id
        ).count() == 0

def test_autofill_skips_oversized_field_but_updates_valid_field(
    logged_in_client, app
):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    with app.app_context():
        document = db.session.get(Document, document_id)

        extracted_data = document.extracted_data.copy()
        extracted_fields = extracted_data.get("fields", {}).copy()

        # Invalid: exceeds retailer's 180-character limit.
        extracted_fields["retailer"] = "A" * 181

        # Valid: should still be applied.
        extracted_fields["serial_number"] = "SERIAL-NEW-123"

        extracted_data["fields"] = extracted_fields
        document.extracted_data = extracted_data
        db.session.commit()

    response = _post_autofill(
    logged_in_client, document_id,
    data={"fields": ["retailer", "serial_number"]},
    follow_redirects=True,
)

    assert response.status_code == 200

    with app.app_context():
        product = Product.query.first()

        # Oversized retailer must remain unchanged.
        assert product.retailer == "Original Store"

        # Valid serial number must be updated.
        assert product.serial_number == "SERIAL-NEW-123"

        # Exactly one successful AutoFill audit.
        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 1

        # A verification record should be created.
        assert Verification.query.filter_by(
            document_id=document_id
        ).count() == 1



import pytest


@pytest.mark.parametrize(
    "invalid_price",
    [
        "-500",
        "12,34,56",
        "abc123",
        "₹",
    ],
)
def test_autofill_skips_invalid_purchase_price(
    logged_in_client, app, invalid_price
):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    with app.app_context():
        product = Product.query.first()
        original_price = product.purchase_price

        document = db.session.get(Document, document_id)

        extracted_data = document.extracted_data.copy()
        extracted_fields = extracted_data.get("fields", {}).copy()

        extracted_fields["purchase_price"] = invalid_price

        extracted_data["fields"] = extracted_fields
        document.extracted_data = extracted_data
        db.session.commit()

    response = _post_autofill(
    logged_in_client, document_id,
    data={"fields": ["purchase_price"]},
    follow_redirects=True,
)

    assert response.status_code == 200

    with app.app_context():
        product = Product.query.first()

        assert product.purchase_price == original_price

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0

        assert Verification.query.filter_by(
            document_id=document_id
        ).count() == 0


def test_autofill_skips_invalid_price_but_updates_valid_field(
    logged_in_client, app
):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    with app.app_context():
        product = Product.query.first()
        original_price = product.purchase_price

        document = db.session.get(Document, document_id)

        extracted_data = document.extracted_data.copy()
        extracted_fields = extracted_data.get("fields", {}).copy()

        # Invalid: must not overwrite the original price.
        extracted_fields["purchase_price"] = "-500"

        # Valid: should still be applied.
        extracted_fields["retailer"] = "New Invoice Store"

        extracted_data["fields"] = extracted_fields
        document.extracted_data = extracted_data
        db.session.commit()

    response = _post_autofill(
    logged_in_client, document_id,
    data={"fields": ["purchase_price", "retailer"]},
    follow_redirects=True,
)

    assert response.status_code == 200

    with app.app_context():
        product = Product.query.first()

        # Invalid price remains unchanged.
        assert product.purchase_price == original_price

        # Valid retailer is successfully updated.
        assert product.retailer == "New Invoice Store"

        # Successful AutoFill should be recorded.
        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 1

        # Verification should be created for the successful update.
        assert Verification.query.filter_by(
            document_id=document_id
        ).count() == 1




def test_autofill_accepts_text_at_maximum_allowed_length(
    logged_in_client, app
):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    with app.app_context():
        document = db.session.get(Document, document_id)

        extracted_data = document.extracted_data.copy()
        extracted_fields = extracted_data.get("fields", {}).copy()

        valid_retailer = "A" * 180
        extracted_fields["retailer"] = valid_retailer

        extracted_data["fields"] = extracted_fields
        document.extracted_data = extracted_data
        db.session.commit()

    response = _post_autofill(
    logged_in_client, document_id,
    data={"fields": ["retailer"]},
    follow_redirects=True,
)

    assert response.status_code == 200

    with app.app_context():
        product = Product.query.first()

        assert product.retailer == "A" * 180
        assert len(product.retailer) == 180

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 1

        assert Verification.query.filter_by(
            document_id=document_id
        ).count() == 1


def test_autofill_rejects_missing_snapshot_token(logged_in_client, app):
    from app.models import AuditLog

    document_id = _create_autofill_test_document(logged_in_client)

    # Submit an AutoFill approval without a snapshot token.
    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={"fields": ["retailer"]},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Your review has expired or is invalid" in response.data

    with app.app_context():
        product = Product.query.first()

        assert product.retailer == "Original Store"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0



def test_autofill_rejects_tampered_snapshot_token(logged_in_client, app):
    from app.models import AuditLog
    import re

    document_id = _create_autofill_test_document(logged_in_client)

    # Fetch a legitimate snapshot token.
    review_response = logged_in_client.get(
        f"/documents/{document_id}/autofill"
    )
    assert review_response.status_code == 200

    html = review_response.get_data(as_text=True)
    match = re.search(
        r'name="snapshot_token"\s+value="([^"]+)"',
        html,
    )
    assert match is not None

    token = match.group(1)

    # Change one character in the token.
    tampered_token = token[:-1] + (
        "A" if token[-1] != "A" else "B"
    )

    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={
            "fields": ["retailer"],
            "snapshot_token": tampered_token,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Your review has expired or is invalid" in response.data

    with app.app_context():
        product = Product.query.first()
        assert product.retailer == "Original Store"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0



def test_autofill_rejects_expired_snapshot_token(logged_in_client, app):
    from app.models import AuditLog
    from itsdangerous import URLSafeTimedSerializer
    from flask import current_app

    document_id = _create_autofill_test_document(logged_in_client)

    # Fetch a legitimate snapshot token.
    review_response = logged_in_client.get(
        f"/documents/{document_id}/autofill"
    )
    assert review_response.status_code == 200

    html = review_response.get_data(as_text=True)

    import re
    match = re.search(
        r'name="snapshot_token"\s+value="([^"]+)"',
        html,
    )
    assert match is not None

    # Generate a valid token, then backdate its timestamp.
    with app.app_context():
        serializer = URLSafeTimedSerializer(
            current_app.config["SECRET_KEY"]
        )

        snapshot = serializer.loads(
            match.group(1),
            salt="autofill-review",
        )

        expired_token = serializer.dumps(
            snapshot,
            salt="autofill-review",
        )

    # The token must actually be expired. We'll test this
    # by temporarily using a very small max_age in the route
    # through a controlled timestamp adjustment.
    import itsdangerous.timed

    original_time = itsdangerous.timed.time.time
    try:
        itsdangerous.timed.time.time = lambda: original_time() + 601

        response = logged_in_client.post(
            f"/documents/{document_id}/autofill",
            data={
                "fields": ["retailer"],
                "snapshot_token": expired_token,
            },
            follow_redirects=True,
        )
    finally:
        itsdangerous.timed.time.time = original_time

    assert response.status_code == 200
    assert b"Your review has expired or is invalid" in response.data

    with app.app_context():
        product = Product.query.first()
        assert product.retailer == "Original Store"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0


def test_autofill_rejects_changed_invoice_after_review(logged_in_client, app):
    from app.models import AuditLog, Document

    document_id = _create_autofill_test_document(logged_in_client)

    # Open the review page and obtain the original token.
    review_response = logged_in_client.get(
        f"/documents/{document_id}/autofill"
    )
    assert review_response.status_code == 200

    html = review_response.get_data(as_text=True)
    import re

    match = re.search(
        r'name="snapshot_token"\s+value="([^"]+)"',
        html,
    )
    assert match is not None
    original_token = match.group(1)

    # Change the extracted invoice data after the review.
    with app.app_context():
        document = db.session.get(Document, document_id)
        extracted_data = dict(document.extracted_data)
        extracted_fields = dict(extracted_data["fields"])
        extracted_fields["retailer"] = "Different Store"
        extracted_data["fields"] = extracted_fields
        document.extracted_data = extracted_data
        db.session.commit()

    # Submit approval using the old token.
    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={
            "fields": ["retailer"],
            "snapshot_token": original_token,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"changed after your review" in response.data

    with app.app_context():
        product = Product.query.first()
        assert product.retailer == "Original Store"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0



def test_autofill_rejects_changed_product_after_review(logged_in_client, app):
    from app.models import AuditLog, Product

    document_id = _create_autofill_test_document(logged_in_client)

    # Obtain the original review token.
    review_response = logged_in_client.get(
        f"/documents/{document_id}/autofill"
    )
    assert review_response.status_code == 200

    import re

    match = re.search(
        r'name="snapshot_token"\s+value="([^"]+)"',
        review_response.get_data(as_text=True),
    )
    assert match is not None
    original_token = match.group(1)

    # Change the product after the review.
    with app.app_context():
        product = Product.query.first()
        product.retailer = "Manually Changed Store"
        db.session.commit()

    # Submit approval using the old token.
    response = logged_in_client.post(
        f"/documents/{document_id}/autofill",
        data={
            "fields": ["retailer"],
            "snapshot_token": original_token,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"changed after your review" in response.data

    with app.app_context():
        product = Product.query.first()

        assert product.retailer == "Manually Changed Store"

        assert AuditLog.query.filter_by(
            action="invoice_autofill_applied"
        ).count() == 0

def test_upload_rejects_files_over_16_mb(logged_in_client, app):
    logged_in_client.post("/products/new", data={
        "name": "Test Laptop",
        "brand": "Acer",
    }, follow_redirects=True)

    oversized_file = b"x" * (16 * 1024 * 1024 + 1)

    response = logged_in_client.post("/products/1/documents", data={
        "document_type": "Invoice",
        "document": (BytesIO(oversized_file), "large-invoice.txt"),
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200
    assert b"16 MB upload limit" in response.data

    with app.app_context():
        assert Document.query.count() == 0



def _register_second_user(client):
    client.post("/logout")
    response = client.post("/register", data={
        "name": "Another User",
        "email": "another@example.com",
        "password": "password123",
        "confirm_password": "password123",
    }, follow_redirects=True)
    assert response.status_code == 200


def test_another_user_cannot_view_edit_or_delete_product(
    logged_in_client, app
):
    product_id, _claim_id, _user_id = _add_product_and_claim(app)

    _register_second_user(logged_in_client)

    # Cannot view another user's product.
    response = logged_in_client.get(f"/products/{product_id}")
    assert response.status_code == 404

    # Cannot edit another user's product.
    response = logged_in_client.post(
        f"/products/{product_id}/edit",
        data={"name": "Hijacked Product"},
    )
    assert response.status_code == 404

    # Cannot delete another user's product.
    response = logged_in_client.post(f"/products/{product_id}/delete")
    assert response.status_code == 404

    with app.app_context():
        product = db.session.get(Product, product_id)
        assert product.name == "Warranty Test Laptop"


def test_another_user_cannot_access_or_update_claim(
    logged_in_client, app
):
    _product_id, claim_id, _user_id = _add_product_and_claim(app)

    _register_second_user(logged_in_client)

    # Cannot submit someone else's claim.
    response = logged_in_client.post(
        f"/claims/{claim_id}/submit",
        data={
            "manufacturer_name": "Example Support",
            "manufacturer_email": "support@example.com",
        },
    )
    assert response.status_code == 404

    # Cannot change someone else's claim status.
    response = logged_in_client.post(
        f"/claims/{claim_id}/status",
        data={"status": "Resolved"},
    )
    assert response.status_code == 404

    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        assert claim.status == "Draft"


def test_another_user_cannot_read_or_mark_notification_as_read(
    logged_in_client, app
):
    from app.models import Notification

    _product_id, _claim_id, user_id = _add_product_and_claim(app)

    with app.app_context():
        notification = Notification(
            user_id=user_id,
            title="Warranty reminder",
            message="Your warranty is expiring soon.",
            category="Warranty",
        )
        db.session.add(notification)
        db.session.commit()
        notification_id = notification.id

    _register_second_user(logged_in_client)

    # The second user must not see the first user's notification.
    response = logged_in_client.get("/notifications")
    assert response.status_code == 200
    assert b"Warranty reminder" not in response.data

    # The second user must not mark it as read.
    response = logged_in_client.post(
        f"/notifications/{notification_id}/read"
    )
    assert response.status_code == 404

    with app.app_context():
        notification = db.session.get(Notification, notification_id)
        assert notification.is_read is False


def test_schema_compatibility_upgrades_existing_user_table(tmp_path):
    import sqlite3

    from app import create_app
    from app.extensions import db
    from sqlalchemy import text

    # Create an older database with the original user table structure.
    db_path = tmp_path / "legacy.db"

    connection = sqlite3.connect(db_path)
    connection.execute("""
        CREATE TABLE user (
            id INTEGER PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            email VARCHAR(180) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            reminder_days INTEGER NOT NULL,
            created_at DATETIME NOT NULL
        )
    """)

    connection.execute("""
        INSERT INTO user
        (id, name, email, password_hash, reminder_days, created_at)
        VALUES (1, 'Existing User', 'old@example.com',
                'existing-hash', 30, '2026-01-01 10:00:00')
    """)
    connection.commit()
    connection.close()

    # Start the application against this existing database.
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}",
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    })

    with app.app_context():
        # Verify that the old record survived the schema upgrade.
        user = db.session.execute(
            text("SELECT * FROM user WHERE id = 1")
        ).mappings().first()

        assert user is not None
        assert user["name"] == "Existing User"
        assert user["email"] == "old@example.com"

        # Verify that the new columns were added with correct defaults.
        assert user["reminder_email_enabled"] == 0
        assert user["reminder_email"] is None

        # Verify that running the compatibility upgrade again is safe.
        from app.schema import ensure_schema_compatibility
        ensure_schema_compatibility()
                # Verify that the migration baseline was recorded exactly once.
        versions = db.session.execute(
            text("SELECT version FROM schema_migrations")
        ).scalars().all()

        assert versions == [
    "001_additive_compatibility_columns",
    "002_unique_notification_event_key",
]

        db.session.remove()

def test_document_download_and_delete(logged_in_client, app):
    product_id, _, _ = _add_product_and_claim(app)

    content = b"Brand: Acer\nRetailer: Example Store\n"

    response = logged_in_client.post(
        f"/products/{product_id}/documents",
        data={
            "document_type": "Invoice",
            "document": (BytesIO(content), "invoice.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        doc = Document.query.first()
        document_id = doc.id
        stored_path = Path(app.config["UPLOAD_FOLDER"]) / doc.stored_name
        assert stored_path.is_file()

    # Download must return the original uploaded file.
    response = logged_in_client.get(
    f"/documents/{document_id}/download"
)
    assert response.status_code == 200
    assert response.data == content

# Release the downloaded file handle before deleting on Windows.
    response.close()

# Delete the document.
    response = logged_in_client.post(
        f"/documents/{document_id}/delete",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert not stored_path.exists()

    with app.app_context():
        assert db.session.get(Document, document_id) is None

def test_missing_document_file_returns_404(logged_in_client, app):
    product_id, _, _ = _add_product_and_claim(app)

    logged_in_client.post(
        f"/products/{product_id}/documents",
        data={
            "document_type": "Invoice",
            "document": (BytesIO(b"Brand: Acer"), "invoice.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with app.app_context():
        doc = Document.query.first()
        document_id = doc.id
        stored_path = Path(app.config["UPLOAD_FOLDER"]) / doc.stored_name
        stored_path.unlink()

    response = logged_in_client.get(
        f"/documents/{document_id}/download"
    )
    assert response.status_code == 404

def test_claim_attachment_upload_and_download(logged_in_client, app):
    product_id, _, _ = _add_product_and_claim(app)

    content = b"Claim evidence attachment"

    response = logged_in_client.post(
        f"/products/{product_id}/claims",
        data={
            "issue_title": "Attachment claim",
            "description": "Testing claim attachment storage.",
            "attachment": (BytesIO(content), "evidence.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        claim = Claim.query.filter_by(
            issue_title="Attachment claim"
        ).first()

        assert claim is not None
        assert claim.attachment_path

        claim_id = claim.id
        stored_path = (
            Path(app.config["UPLOAD_FOLDER"]) / claim.attachment_path
        )
        assert stored_path.is_file()

    response = logged_in_client.get(
        f"/claims/{claim_id}/attachment"
    )

    assert response.status_code == 200
    assert response.data == content
def test_product_deletion_removes_document_and_claim_files(
    logged_in_client, app
):
    product_id, _, _ = _add_product_and_claim(app)

    logged_in_client.post(
        f"/products/{product_id}/documents",
        data={
            "document_type": "Invoice",
            "document": (BytesIO(b"Brand: Acer"), "invoice.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    logged_in_client.post(
        f"/products/{product_id}/claims",
        data={
            "issue_title": "Attachment claim",
            "description": "Testing product cleanup.",
            "attachment": (BytesIO(b"Evidence"), "evidence.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with app.app_context():
        doc = Document.query.first()
        claim = Claim.query.filter_by(
            issue_title="Attachment claim"
        ).first()

        document_path = Path(app.config["UPLOAD_FOLDER"]) / doc.stored_name
        claim_path = Path(app.config["UPLOAD_FOLDER"]) / claim.attachment_path

        assert document_path.is_file()
        assert claim_path.is_file()

    response = logged_in_client.post(
        f"/products/{product_id}/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert not document_path.exists()
    assert not claim_path.exists()

    with app.app_context():
        assert Product.query.filter_by(id=product_id).first() is None

def test_invoice_parser_extracts_fields_and_currency():
    from app.services.extraction import parse_invoice_fields

    invoice_text = """
    Tax Invoice
    Sold By: Example Electronics
    Invoice Number: INV-2026-12345
    25/09/2025 Invoice Date
    Serial No: NHD5ASI001526040482N00
    Acer Gaming Laptop NL16-71G-54MY
    Grand Total ₹58,605.00
    """

    fields, confidences, overall = parse_invoice_fields(
        invoice_text,
        90.0,
    )

    assert fields["retailer"] == "Example Electronics"
    assert fields["invoice_number"] == "INV-2026-12345"
    assert fields["purchase_date"] == "2025-09-25"
    assert fields["serial_number"] == "NHD5ASI001526040482N00"
    assert fields["purchase_price"] == "58605.0"
    assert fields["brand"] == "Acer"
    assert fields["model_number"] == "NL16-71G-54MY"
    assert all(value == "HIGH" for value in confidences.values())
    assert overall == "HIGH"


def test_invoice_parser_assigns_confidence_from_ocr_score():
    from app.services.extraction import parse_invoice_fields

    invoice_text = """
    Tax Invoice
    Sold By: Example Electronics
    Serial No: SERIAL123456
    Gaming Laptop
    Grand Total Rs. 25,000.00
    """

    _, high_confidence, _ = parse_invoice_fields(invoice_text, 90)
    _, medium_confidence, _ = parse_invoice_fields(invoice_text, 65)
    _, low_confidence, _ = parse_invoice_fields(invoice_text, 30)

    assert set(high_confidence.values()) == {"HIGH"}
    assert set(medium_confidence.values()) == {"MEDIUM"}
    assert set(low_confidence.values()) == {"LOW"}


def test_invoice_parser_handles_empty_and_unrecognized_text():
    from app.services.extraction import parse_invoice_fields

    fields, confidences, overall = parse_invoice_fields("")

    assert fields == {}
    assert confidences == {}
    assert overall == "LOW"

    fields, confidences, overall = parse_invoice_fields(
        "This is an ordinary document without invoice details."
    )

    assert fields == {}
    assert confidences == {}
    assert overall == "LOW"


def test_ocr_image_returns_text_and_average_confidence(monkeypatch):
    import pytesseract
    from PIL import Image
    from app.services.extraction import _ocr_image

    monkeypatch.setattr(
        pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {
            "conf": ["90", "80", "-1", "not-a-number"]
        },
    )

    monkeypatch.setattr(
        pytesseract,
        "image_to_string",
        lambda image: "Invoice OCR text",
    )

    image = Image.new("RGB", (800, 600), "white")

    text, notes, confidence = _ocr_image(image)

    assert text == "Invoice OCR text"
    assert confidence == 85.0
    assert isinstance(notes, list)


def test_unsupported_document_type_returns_note():
    from app.services.extraction import extract_text

    text, notes, confidence = extract_text(
        "unused.file",
        "unsupported",
    )

    assert text == ""
    assert notes == ["Unsupported document type."]
    assert confidence is None


def _make_file_backed_backup_app(tmp_path):
    import sqlite3
    from app import create_app

    database = tmp_path / "backup-test.db"
    uploads = tmp_path / "uploads"
    instance = tmp_path / "instance"
    uploads.mkdir()
    instance.mkdir()

    test_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "backup-test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database.as_posix()}",
        "UPLOAD_FOLDER": str(uploads),
        "WTF_CSRF_ENABLED": False,
    })

    test_app.instance_path = str(instance)

    connection = sqlite3.connect(database)
    try:
        connection.execute(
        "CREATE TABLE backup_probe (value TEXT NOT NULL)"
    )
        connection.execute(
        "INSERT INTO backup_probe VALUES (?)", ("original",)
    )
        connection.commit()
    finally:
        connection.close()

    (uploads / "evidence.pdf").write_bytes(b"original evidence")

    return test_app, database, uploads



def test_full_backup_and_restore(tmp_path):
    import sqlite3
    from app.services.backup import create_backup, restore_backup

    test_app, database, uploads = _make_file_backed_backup_app(tmp_path)

    with test_app.app_context():
        archive = create_backup(
            test_app, tmp_path / "warrantyvault-test.zip"
        )

        assert archive.is_file()

        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "UPDATE backup_probe SET value = 'changed'"
            )
            connection.commit()
        finally:
            connection.close()

        (uploads / "evidence.pdf").write_bytes(b"changed evidence")
        (uploads / "new-file.pdf").write_bytes(b"new file")

        safety_backup = restore_backup(test_app, archive)

        assert safety_backup.is_file()

        connection = sqlite3.connect(database)
        try:
            value = connection.execute(
                "SELECT value FROM backup_probe"
            ).fetchone()[0]
        finally:
            connection.close()

        assert value == "original"
        assert (uploads / "evidence.pdf").read_bytes() == b"original evidence"
        assert not (uploads / "new-file.pdf").exists()



def test_backup_rejects_corrupted_archive(tmp_path):
    import zipfile
    from app.services.backup import create_backup, restore_backup

    test_app, database, uploads = _make_file_backed_backup_app(tmp_path)

    with test_app.app_context():
        archive = create_backup(
            test_app, tmp_path / "valid-backup.zip"
        )
        corrupted = tmp_path / "corrupted-backup.zip"

        with zipfile.ZipFile(archive, "r") as source:
            contents = {
                name: source.read(name)
                for name in source.namelist()
            }

        contents["uploads/evidence.pdf"] = b"tampered evidence"

        with zipfile.ZipFile(corrupted, "w") as destination:
            for name, content in contents.items():
                destination.writestr(name, content)

        try:
            restore_backup(test_app, corrupted)
            assert False, "Corrupted backup should have been rejected."
        except ValueError as error:
            assert "integrity check failed" in str(error).lower()

        assert database.is_file()


def test_backup_rejects_unsafe_archive_paths(tmp_path):
    import zipfile
    from app.services.backup import restore_backup

    test_app, database, uploads = _make_file_backed_backup_app(tmp_path)
    malicious = tmp_path / "unsafe-backup.zip"

    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../outside.txt", b"unsafe")
        archive.writestr("manifest.json", b"{}")

    with test_app.app_context():
        try:
            restore_backup(test_app, malicious)
            assert False, "Unsafe archive should have been rejected."
        except ValueError as error:
            assert "unsafe archive path" in str(error).lower()

    assert database.is_file()
    assert not (tmp_path / "outside.txt").exists()
