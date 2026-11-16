import math
import csv
import io
import json
import os
import uuid
import re

from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    send_file, current_app, abort, make_response
)
from flask_login import login_required, current_user
from sqlalchemy import or_, desc
from werkzeug.utils import secure_filename

from .extensions import db
from .models import (
    Product, Document, Verification, VerificationField,
    Claim, Notification, AuditLog
)
from .services.extraction import process_document
from .services.storage import get_storage
from .services.verification import build_verification
from .services.mailer import smtp_is_configured, send_email
from itsdangerous import (
    URLSafeTimedSerializer,
    BadSignature,
    SignatureExpired,
)


main_bp = Blueprint("main", __name__)

def _create_autofill_snapshot_token(document_id, extracted_fields, product):
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])

    snapshot = {
        "document_id": document_id,
        "extracted_fields": {
            field: extracted_fields.get(field)
            for field in (
                "brand",
                "retailer",
                "model_number",
                "serial_number",
                "purchase_date",
                "purchase_price",
                "invoice_number",
            )
        },
        "product_id": product.id,
        "product_values": {
            field: (
                getattr(product, field).isoformat()
                if isinstance(getattr(product, field), date)
                else getattr(product, field)
            )
            for field in (
                "brand",
                "retailer",
                "model_number",
                "serial_number",
                "purchase_date",
                "purchase_price",
                "invoice_number",
            )
        },
    }

    return serializer.dumps(snapshot, salt="autofill-review")


def _verify_autofill_snapshot_token(token, max_age=600):
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])

    return serializer.loads(
        token,
        salt="autofill-review",
        max_age=max_age,
    )



def _owned_product(product_id):
    product = Product.query.filter_by(id=product_id, user_id=current_user.id).first_or_404()
    return product


def _owned_document(document_id):
    doc = Document.query.join(Product).filter(Document.id == document_id, Product.user_id == current_user.id).first_or_404()
    return doc


def _owned_claim(claim_id):
    claim = Claim.query.join(Product).filter(Claim.id == claim_id, Product.user_id == current_user.id).first_or_404()
    return claim


def _audit(entity_type, entity_id, action, details=None):
    db.session.add(AuditLog(user_id=current_user.id, entity_type=entity_type, entity_id=entity_id, action=action, details=details or {}))



def _parse_date(value):
    if not value:
        return None

    value = str(value).strip()

    date_formats = [
        "%Y-%m-%d",  # 2026-04-10
        "%d/%m/%Y",  # 10/04/2026
        "%d-%m-%Y",  # 10-04-2026
        "%d/%m/%y",  # 10/04/26
    ]

    for date_format in date_formats:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue

    return None




def _parse_float(value):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    # Remove the rupee symbol and surrounding whitespace.
    value = value.replace("₹", "").strip()

    # Reject negative values.
    if value.startswith("-"):
        return None

    # Accept either:
    # 54990
    # 54990.50
    # 54,990.50
    # 12,34,567.50
    # 1,234,567.50
    import re

    valid_number = re.fullmatch(
    r"(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d+)?",
    value
)

    if not valid_number:
        return None

    try:
        parsed_value = float(value.replace(",", ""))

        if not math.isfinite(parsed_value):
            return None

        return parsed_value

    except (ValueError, AttributeError):
        return None




def _save_upload(file_storage):
    if not file_storage or not file_storage.filename:
        return None, None, 0

    original = secure_filename(file_storage.filename)
    ext = Path(original).suffix.lower().lstrip(".")

    allowed_extensions = current_app.config["ALLOWED_EXTENSIONS"]

    if ext not in allowed_extensions:
        raise ValueError(
            "Unsupported file type. Use PDF, PNG, JPG, TIFF, TXT, or DOCX."
        )

    # Read the file once and reject empty uploads.
    content = file_storage.read()
    file_storage.seek(0)

    if not content:
        raise ValueError("The uploaded file is empty.")

    # Validate common file signatures.
    signatures = {
        "pdf": lambda data: data.startswith(b"%PDF-"),
        "png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
        "jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
        "tif": lambda data: data.startswith((b"II*\x00", b"MM\x00*")),
        "tiff": lambda data: data.startswith((b"II*\x00", b"MM\x00*")),
        "docx": lambda data: data.startswith(b"PK\x03\x04"),
    }

    if ext in signatures and not signatures[ext](content):
        raise ValueError(
            "The uploaded file does not appear to match its file extension."
        )

    stored = f"{uuid.uuid4().hex}.{ext}"

    try:
        get_storage().save(
            stored,
            content,
            content_type=file_storage.mimetype or "application/octet-stream",
        )
        return original, stored, len(content)
    except Exception:
        try:
            get_storage().delete(stored)
        except Exception:
            current_app.logger.exception("Could not clean up failed upload.")
        raise



def _process_doc(doc):
    doc.status = "Processing"
    db.session.commit()

    try:
        with get_storage().temporary_file(
            doc.stored_name,
            suffix=f".{doc.file_type}",
        ) as temp_path:
            data, confidence_map, overall, extracted_text, notes = (
                process_document(str(temp_path), doc.file_type)
            )

        doc.extracted_data = {
            "fields": data,
            "field_confidences": confidence_map,
            "text_preview": extracted_text,
        }
        doc.extraction_confidence = overall
        doc.extraction_notes = "\n".join(notes)
        doc.status = "Ready to verify" if data else "Processing failed"

    except Exception as exc:
        current_app.logger.exception(
            "Document extraction failed for document %s",
            doc.id,
        )

        db.session.rollback()

        doc = db.session.get(Document, doc.id)

        if doc is None:
            raise

        doc.extracted_data = {
            "fields": {},
            "field_confidences": {},
            "text_preview": "",
        }
        doc.extraction_confidence = "LOW"
        doc.extraction_notes = (
            f"Unexpected document processing error: {exc}"
        )
        doc.status = "Processing failed"

    db.session.commit()


def _run_verification(doc):
    product = doc.product
    payload = doc.extracted_data or {}
    data = payload.get("fields", {})
    confidence_map = payload.get("field_confidences", {})
    overall, confidence, summary, fields, notes = build_verification(product, data, confidence_map, payload.get("manual_corrections", {}))
    verification = Verification(
        document_id=doc.id, product_id=product.id, overall_result=overall,
        overall_confidence=confidence, summary=summary, notes=notes,
    )
    db.session.add(verification)
    db.session.flush()
    for item in fields:
        db.session.add(VerificationField(
            verification_id=verification.id, field_name=item["field_name"],
            stored_value=item["stored_value"], extracted_value=item["extracted_value"],
            result=item["result"], confidence=item["confidence"],
            review_required=item["review_required"], value_source=item.get("value_source", "OCR"), reason=item["reason"],
        ))
    doc.status = "Verified" if overall == "VERIFIED" else "Review required" if overall in {"REVIEW_REQUIRED", "UNABLE_TO_VERIFY"} else "Mismatch found"
    _audit("document",doc.id,"verification_created",{"verification_id": verification.id,"result": overall,},
    )
    return verification


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.get("/dashboard")
@login_required
def dashboard():
    products = Product.query.filter_by(user_id=current_user.id).all()
    today = date.today()
    active = sum(1 for p in products if p.warranty_expiry and p.warranty_expiry >= today and (p.warranty_expiry - today).days > 30)
    expired = sum(1 for p in products if p.warranty_expiry and p.warranty_expiry < today)
    expiring = sorted([p for p in products if p.warranty_expiry and today <= p.warranty_expiry <= today + timedelta(days=30)], key=lambda p: p.warranty_expiry)[:5]
    documents = Document.query.join(Product).filter(Product.user_id == current_user.id).all()
    needs_review = sum(1 for d in documents if d.status in {"Review required", "Mismatch found", "Processing failed"})
    open_claims = Claim.query.join(Product).filter(Product.user_id == current_user.id, Claim.status.in_(["Draft", "Submitted", "In progress"])).count()
    recent_docs = sorted(documents, key=lambda d: d.uploaded_at, reverse=True)[:5]
    latest_activity = AuditLog.query.filter_by(user_id=current_user.id).order_by(desc(AuditLog.created_at)).limit(6).all()
    return render_template("dashboard.html", total=len(products), active=active, expired=expired,
                           expiring=expiring, needs_review=needs_review, open_claims=open_claims,
                           recent_docs=recent_docs, activity=latest_activity)


@main_bp.route("/products", methods=["GET", "POST"])
@login_required
def products():
    if request.method == "POST":
        return _create_product()
    query = Product.query.filter_by(user_id=current_user.id)
    term = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    sort = request.args.get("sort", "expiry")
    if term:
        like = f"%{term}%"
        query = query.filter(or_(Product.name.ilike(like), Product.brand.ilike(like), Product.model_number.ilike(like), Product.serial_number.ilike(like)))
    records = query.all()
    if status == "active":
        records = [p for p in records if p.warranty_status in {"Active", "Expiring soon"}]
    elif status == "expired":
        records = [p for p in records if p.warranty_status == "Expired"]
    elif status == "expiring":
        records = [p for p in records if p.warranty_status == "Expiring soon"]
    if sort == "recent":
        records.sort(key=lambda p: p.updated_at, reverse=True)
    else:
        records.sort(key=lambda p: p.warranty_expiry or date.max)
    return render_template("products.html", products=records, term=term, status=status, sort=sort)


def _create_product():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Product name is required.", "error")
        return redirect(url_for("main.products"))
    product = Product(
        user_id=current_user.id, name=name, brand=request.form.get("brand", "").strip() or None,
        category=request.form.get("category", "").strip() or None,
        serial_number=request.form.get("serial_number", "").strip() or None,
        model_number=request.form.get("model_number", "").strip() or None,
        retailer=request.form.get("retailer", "").strip() or None,
        purchase_date=_parse_date(request.form.get("purchase_date")),
        purchase_price=_parse_float(request.form.get("purchase_price")),
        invoice_number=request.form.get("invoice_number", "").strip() or None,
        warranty_start=_parse_date(request.form.get("warranty_start")),
        warranty_expiry=_parse_date(request.form.get("warranty_expiry")),
        warranty_months=int(request.form.get("warranty_months")) if request.form.get("warranty_months", "").isdigit() else None,
        notes=request.form.get("notes", "").strip() or None,
    )
    db.session.add(product)
    db.session.flush()
    _audit("product", product.id, "product_created", {"name": name})
    db.session.commit()
    flash("Product added to your vault.", "success")
    return redirect(url_for("main.product_detail", product_id=product.id))


@main_bp.route("/products/new", methods=["GET", "POST"])
@login_required
def new_product():
    if request.method == "POST":
        return _create_product()
    return render_template("product_form.html", product=None)


@main_bp.route("/products/<int:product_id>")
@login_required
def product_detail(product_id):
    product = _owned_product(product_id)
    documents = Document.query.filter_by(product_id=product.id).order_by(desc(Document.uploaded_at)).all()
    claims = Claim.query.filter_by(product_id=product.id).order_by(desc(Claim.created_at)).all()
    return render_template("product_detail.html", product=product, documents=documents, claims=claims)


@main_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit_product(product_id):
    product = _owned_product(product_id)
    if request.method == "POST":
        submitted_name = request.form.get("name", "").strip()
        if not submitted_name:
            flash("Product name is required.", "error")
            return redirect(url_for("main.edit_product", product_id=product.id))
        old_name = product.name
        for attr in ("name", "brand", "category", "serial_number", "model_number", "retailer", "invoice_number", "notes"):
            value = request.form.get(attr, "").strip()
            setattr(product, attr, value or None)
        product.purchase_date = _parse_date(request.form.get("purchase_date"))
        product.purchase_price = _parse_float(request.form.get("purchase_price"))
        product.warranty_start = _parse_date(request.form.get("warranty_start"))
        product.warranty_expiry = _parse_date(request.form.get("warranty_expiry"))
        product.warranty_months = int(request.form.get("warranty_months")) if request.form.get("warranty_months", "").isdigit() else None
        _audit("product", product.id, "product_updated", {"previous_name": old_name, "new_name": product.name})
        db.session.commit()
        flash("Product details updated.", "success")
        return redirect(url_for("main.product_detail", product_id=product.id))
    return render_template("product_form.html", product=product)


@main_bp.post("/products/<int:product_id>/delete")
@login_required
def delete_product(product_id):
    product = _owned_product(product_id)
    name = product.name

    # Collect storage keys first. Do not delete files before the DB commit.
    files_to_delete = []

    for doc in product.documents:
        if doc.stored_name:
            files_to_delete.append(doc.stored_name)

    for claim in product.claims:
        if claim.attachment_path:
            files_to_delete.append(claim.attachment_path)

    Notification.query.filter_by(
        user_id=current_user.id,
        product_id=product.id
    ).delete(synchronize_session=False)

    _audit("product", product.id, "product_deleted", {"name": name})
    db.session.delete(product)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Product deletion failed.")
        flash("Could not delete the product. Please try again.", "error")
        return redirect(url_for("main.products"))

    # Only remove stored objects after successful database deletion.
    cleanup_failed = False

    for storage_key in files_to_delete:
        try:
            get_storage().delete(storage_key)
        except Exception:
            cleanup_failed = True
            current_app.logger.exception(
                "Could not remove an attachment after product deletion."
            )

    if cleanup_failed:
        flash(
            f"{name} was deleted, but some stored attachment files "
            "could not be removed.",
            "warning"
        )
    else:
        flash(f"{name} and its attached records were deleted.", "info")

    return redirect(url_for("main.products"))


@main_bp.post("/products/<int:product_id>/documents")
@login_required

def upload_document(product_id):
    product = _owned_product(product_id)
    file = request.files.get("document")

    stored = None

    try:
        original, stored, file_size = _save_upload(file)

        if not original:
            flash("Choose a document to upload.", "error")
            return redirect(url_for(
                "main.product_detail",
                product_id=product.id
            ))

        doc = Document(
            product_id=product.id,
            original_name=original,
            stored_name=stored,
            file_type=Path(original).suffix.lstrip("."),
            document_type=request.form.get("document_type", "Invoice"),
            status="Uploaded",
            file_size=file_size,
        )

        db.session.add(doc)
        db.session.flush()

        _audit("document", doc.id, "document_uploaded", {
            "filename": original
        })

        db.session.commit()

    except ValueError as exc:
        db.session.rollback()

        if stored:
            try:
                get_storage().delete(stored)
            except Exception:
                current_app.logger.exception(
                    "Could not clean up failed document upload."
                )

        flash(str(exc), "error")
        return redirect(url_for(
            "main.product_detail",
            product_id=product.id
        ))

    except Exception:
        db.session.rollback()

        if stored:
            try:
                get_storage().delete(stored)
            except Exception:
                current_app.logger.exception(
                    "Could not clean up failed document upload."
                )

        current_app.logger.exception("Document upload failed.")
        flash("Document upload failed. Please try again.", "error")
        return redirect(url_for(
            "main.product_detail",
            product_id=product.id
        ))

    _process_doc(doc)

    if doc.status == "Processing failed":
        flash(
            "The file was saved, but readable invoice fields could not be "
            "extracted. You can review it or retry after fixing the "
            "document/OCR setup.",
            "warning"
        )
    else:
        flash(
            "Document uploaded and processed. Review the extracted fields "
            "before verification.",
            "success"
        )

    return redirect(url_for(
        "main.product_detail",
        product_id=product.id
    ))



@main_bp.get("/documents")
@login_required
def documents():
    query = Document.query.join(Product).filter(Product.user_id == current_user.id)
    term = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    if term:
        like = f"%{term}%"
        query = query.filter(or_(Document.original_name.ilike(like), Product.name.ilike(like), Product.brand.ilike(like)))
    if status:
        query = query.filter(Document.status == status)
    docs = query.order_by(desc(Document.uploaded_at)).all()
    return render_template("documents.html", documents=docs, term=term, status=status)


@main_bp.get("/documents/<int:document_id>/download")
@login_required
def download_document(document_id):
    doc = _owned_document(document_id)
    try:
        content = get_storage().read(doc.stored_name)
    except FileNotFoundError:
        abort(404)
    except Exception:
        current_app.logger.exception("Document download failed for %s", doc.id)
        abort(503)

    return send_file(
        io.BytesIO(content),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=doc.original_name,
    )


@main_bp.post("/documents/<int:document_id>/retry")
@login_required
def retry_document(document_id):
    doc = _owned_document(document_id)
    if not get_storage().exists(doc.stored_name):
        abort(404)
    else:
        _process_doc(doc)
        flash("Document processing retried.", "success" if doc.status != "Processing failed" else "warning")
    return redirect(request.referrer or url_for("main.documents"))



@main_bp.post("/documents/<int:document_id>/verify")
@login_required
def verify_document(document_id):
    doc = _owned_document(document_id)

    if not (doc.extracted_data or {}).get("fields"):
        flash(
            "No extracted fields are available. Review the document "
            "or retry processing first.",
            "warning",
        )
        return redirect(url_for(
            "main.document_report",
            document_id=doc.id,
        ))

    try:
        verification = _run_verification(doc)
        db.session.commit()

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Verification failed for document %s",
            doc.id,
        )
        flash("Verification failed. No changes were saved.", "danger")
        return redirect(url_for(
            "main.document_report",
            document_id=doc.id,
        ))

    flash(
        "Verification completed. This result checks consistency, "
        "not authenticity.",
        "success",
    )

    return redirect(url_for(
        "main.verification_report",
        verification_id=verification.id,
    ))



@main_bp.get("/documents/<int:document_id>/report")
@login_required
def document_report(document_id):
    doc = _owned_document(document_id)
    latest = Verification.query.filter_by(document_id=doc.id).order_by(desc(Verification.created_at)).first()
    history = Verification.query.filter_by(document_id=doc.id).order_by(desc(Verification.created_at)).all()
    return render_template("document_report.html", document=doc, latest=latest, history=history)


@main_bp.get("/documents/<int:document_id>/autofill")
@login_required
def review_product_autofill(document_id):
    doc = _owned_document(document_id)
    product = doc.product

    extracted_fields = (doc.extracted_data or {}).get("fields", {})
    confidence_map = (doc.extracted_data or {}).get("field_confidences", {})

    field_mapping = {
        "brand": "brand",
        "retailer": "retailer",
        "model_number": "model_number",
        "serial_number": "serial_number",
        "purchase_date": "purchase_date",
        "purchase_price": "purchase_price",
        "invoice_number": "invoice_number",
    }

    suggestions = []

    for invoice_field, product_field in field_mapping.items():
        invoice_value = extracted_fields.get(invoice_field)

        if invoice_value is None or not str(invoice_value).strip():
            continue

        current_value = getattr(product, product_field)
        confidence = confidence_map.get(invoice_field, "LOW")

        if product_field == "purchase_date":
            invoice_value = _parse_date(str(invoice_value))
            if invoice_value is None:
                continue

        elif product_field == "purchase_price":
            invoice_value = _parse_float(str(invoice_value))
            if invoice_value is None:
                continue

        current_display = (
            current_value.isoformat()
            if isinstance(current_value, date)
            else str(current_value) if current_value is not None else ""
        )

        invoice_display = (
            invoice_value.isoformat()
            if isinstance(invoice_value, date)
            else str(invoice_value)
        )

        if current_display != invoice_display:
            suggestions.append({
                "field": product_field,
                "label": product_field.replace("_", " ").title(),
                "current": current_display or "Not provided",
                "suggested": invoice_display,
                "confidence": confidence,
            })

    snapshot_token = _create_autofill_snapshot_token(
    doc.id,
    extracted_fields,
    product,
)

    return render_template(
    "autofill_review.html",
    document=doc,
    product=product,
    suggestions=suggestions,
    snapshot_token=snapshot_token,
)




@main_bp.post("/documents/<int:document_id>/autofill")
@login_required

def apply_product_autofill(document_id):
    doc = _owned_document(document_id)
    product = doc.product

    # Verify the signed snapshot submitted with the approval.
    token = request.form.get("snapshot_token", "")

    try:
        approved_snapshot = _verify_autofill_snapshot_token(token)

    except (SignatureExpired, BadSignature):
        flash(
            "Your review has expired or is invalid. "
            "Please review the suggestions again.",
            "warning",
        )
        return redirect(url_for(
            "main.review_product_autofill",
            document_id=doc.id,
        ))

    # Generate a snapshot of the current document and product.
    current_snapshot_token = _create_autofill_snapshot_token(
        doc.id,
        (doc.extracted_data or {}).get("fields", {}),
        product,
    )

    current_snapshot = _verify_autofill_snapshot_token(
        current_snapshot_token
    )

    # Reject approval if the reviewed information has changed.
    if approved_snapshot != current_snapshot:
        flash(
            "The invoice or product information changed after your review. "
            "Please review the suggestions again.",
            "warning",
        )
        return redirect(url_for(
            "main.review_product_autofill",
            document_id=doc.id,
        ))

    extracted_fields = (doc.extracted_data or {}).get("fields", {})


    field_mapping = {
        "brand": ("brand", 120),
        "retailer": ("retailer", 180),
        "model_number": ("model_number", 140),
        "serial_number": ("serial_number", 140),
        "purchase_date": ("purchase_date", None),
        "purchase_price": ("purchase_price", None),
        "invoice_number": ("invoice_number", 100),
    }

    selected_fields = set(request.form.getlist("fields"))
    changes = {}

    if not selected_fields:
        flash("Select at least one field to update.", "warning")
        return redirect(url_for(
            "main.review_product_autofill",
            document_id=doc.id
        ))

    if not selected_fields.issubset(field_mapping):
        abort(400)

    for invoice_field in selected_fields:
        product_field, max_length = field_mapping[invoice_field]
        raw_value = extracted_fields.get(invoice_field)

        if raw_value is None or not str(raw_value).strip():
            continue

        if product_field == "purchase_date":
            new_value = _parse_date(str(raw_value))
            if new_value is None:
                continue

        elif product_field == "purchase_price":
            new_value = _parse_float(str(raw_value))
            if new_value is None:
                continue

        else:
            new_value = str(raw_value).strip()
            if len(new_value) > max_length:
                continue

        old_value = getattr(product, product_field)

        if old_value != new_value:
            changes[product_field] = {
                "from": (
                    old_value.isoformat()
                    if isinstance(old_value, date)
                    else old_value
                ),
                "to": (
                    new_value.isoformat()
                    if isinstance(new_value, date)
                    else new_value
                ),
            }

            setattr(product, product_field, new_value)

    if not changes:
        flash("No valid changes were available to apply.", "warning")
        return redirect(url_for(
            "main.document_report",
            document_id=doc.id
        ))

    try:
        _audit(
            "product",
            product.id,
            "invoice_autofill_applied",
            {
                "document_id": doc.id,
                "changes": changes,
                "user_approved": True,
            },
        )

        verification = _run_verification(doc)

        db.session.commit()

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "AutoFill failed for document %s",
            doc.id,
        )
        flash(
            "AutoFill failed. No changes were saved.",
            "danger",
        )
        return redirect(url_for(
            "main.document_report",
            document_id=doc.id,
        ))

    flash(
        "Approved product details updated successfully. "
        "A new verification record has been created.",
        "success",
    )

    return redirect(url_for(
        "main.verification_report",
        verification_id=verification.id
    ))




@main_bp.get("/verifications/<int:verification_id>")
@login_required
def verification_report(verification_id):
    verification = Verification.query.join(Product).filter(Verification.id == verification_id, Product.user_id == current_user.id).first_or_404()
    return render_template("verification_report.html", verification=verification)



@main_bp.post("/documents/<int:document_id>/correct")
@login_required
def correct_extraction(document_id):
    doc = _owned_document(document_id)

    payload = dict(doc.extracted_data or {})
    fields = dict(payload.get("fields") or {})
    confs = dict(payload.get("field_confidences") or {})

    allowed = {
        "brand": 120,
        "retailer": 180,
        "model_number": 140,
        "serial_number": 140,
        "purchase_date": None,
        "invoice_number": 100,
        "purchase_price": None,
    }

    changes = {}
    corrections = {}

    for key, max_length in allowed.items():
        if key not in request.form:
            continue

        value = request.form.get(key, "").strip()
        normalized_value = value

        # Validate text field lengths.
        if max_length is not None and len(value) > max_length:
            flash(
                f"{key.replace('_', ' ').title()} cannot exceed "
                f"{max_length} characters.",
                "error",
            )
            return redirect(url_for(
                "main.document_report",
                document_id=doc.id,
            ))

        # Validate and normalize dates.
        if key == "purchase_date" and value:
            parsed_date = _parse_date(value)

            if parsed_date is None:
                flash("Please enter a valid purchase date.", "error")
                return redirect(url_for(
                    "main.document_report",
                    document_id=doc.id,
                ))

            normalized_value = parsed_date.isoformat()

        # Validate and normalize purchase prices.
        elif key == "purchase_price" and value:
            parsed_price = _parse_float(value)

            if parsed_price is None:
                flash("Please enter a valid non-negative purchase price.", "error")
                return redirect(url_for(
                    "main.document_report",
                    document_id=doc.id,
                ))

            normalized_value = str(parsed_price)

        old_value = fields.get(key)

        if normalized_value != (old_value or ""):
            changes[key] = {
                "from": old_value,
                "to": normalized_value,
            }

            if normalized_value:
                fields[key] = normalized_value
            else:
                fields.pop(key, None)
                confs.pop(key, None)

    # Nothing changed: avoid unnecessary database writes.
    if not changes:
        flash("No changes were made.", "info")
        return redirect(url_for(
            "main.document_report",
            document_id=doc.id,
        ))

    # Record manual correction history.
    manual = dict(payload.get("manual_corrections") or {})

    timestamp = datetime.now(timezone.utc).replace(
        tzinfo=None
    ).isoformat()

    for key, change in changes.items():
        manual[key] = {
            "from": change["from"],
            "to": change["to"],
            "updated_at": timestamp,
        }

    payload["fields"] = fields
    payload["field_confidences"] = confs
    payload["manual_corrections"] = manual

    doc.extracted_data = payload

    _audit(
        "document",
        doc.id,
        "extraction_corrected",
        {
            "changes": changes,
            "manual_review": True,
        },
    )

    db.session.commit()

    flash(
        "Corrections saved. The original uploaded file remains unchanged. "
        "Run verification again to create a new history entry.",
        "success",
    )

    return redirect(url_for(
        "main.document_report",
        document_id=doc.id,
    ))



@main_bp.post("/documents/<int:document_id>/delete")
@login_required

def delete_document(document_id):
    doc = _owned_document(document_id)
    product_id = doc.product_id
    stored_name = doc.stored_name

    _audit("document", doc.id, "document_deleted", {
        "filename": doc.original_name
    })

    db.session.delete(doc)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Document deletion failed.")
        flash("Could not delete the document. Please try again.", "error")
        return redirect(url_for(
            "main.product_detail",
            product_id=product_id
        ))

    try:
        get_storage().delete(stored_name)
    except Exception:
        current_app.logger.exception(
            "Document record deleted, but its stored object could not be removed."
        )
        flash(
            "Document record deleted, but its stored file could not be removed.",
            "warning"
        )
        return redirect(url_for(
            "main.product_detail",
            product_id=product_id
        ))

    flash("Document and its verification history deleted.", "info")
    return redirect(url_for(
        "main.product_detail",
        product_id=product_id
    ))




@main_bp.route("/claims", methods=["GET"])
@login_required
def claims():
    items = Claim.query.join(Product).filter(
        Product.user_id == current_user.id
    ).order_by(desc(Claim.updated_at)).all()

    return render_template(
    "claims.html",
    claims=items,
    mail_ready=smtp_is_configured(current_app.config),
    submission_mode=current_app.config.get(
        "CLAIM_SUBMISSION_MODE", "smtp"
    ).strip().lower(),
)


@main_bp.post("/products/<int:product_id>/claims")
@login_required
def create_claim(product_id):
    product = _owned_product(product_id)

    title = request.form.get("issue_title", "").strip()
    description = request.form.get("description", "").strip()

    if not title or not description:
        flash("Add a claim title and describe the issue.", "error")
        return redirect(url_for("main.product_detail", product_id=product.id))

    claim = Claim(
        product_id=product.id,
        issue_title=title,
        description=description,
        status="Draft"
    )

    file = request.files.get("attachment")
    stored = None

    if file and file.filename:
        try:
            original, stored, _file_size = _save_upload(file)
            claim.attachment_name = original
            claim.attachment_path = stored
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("main.product_detail", product_id=product.id))

    try:
        db.session.add(claim)
        db.session.flush()

        _audit("claim", claim.id, "claim_created", {"title": title})
        db.session.commit()

    except Exception:
        db.session.rollback()

        if stored:
            try:
                get_storage().delete(stored)
            except Exception:
                current_app.logger.exception(
                    "Could not clean up failed claim attachment upload."
                )

        current_app.logger.exception("Claim creation failed.")
        flash("Could not create the claim. Please try again.", "error")
        return redirect(url_for("main.product_detail", product_id=product.id))

    flash(
        "Claim draft created. Open Claims to email it to a manufacturer/support contact.",
        "success"
    )
    return redirect(url_for("main.claims"))


@main_bp.get("/claims/<int:claim_id>/attachment")
@login_required
def download_claim_attachment(claim_id):
    claim = _owned_claim(claim_id)
    if not claim.attachment_path:
        abort(404)
    try:
        content = get_storage().read(claim.attachment_path)
    except FileNotFoundError:
        abort(404)
    except Exception:
        current_app.logger.exception(
            "Claim attachment download failed for claim %s", claim.id
        )
        abort(503)

    return send_file(
        io.BytesIO(content),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=claim.attachment_name or "claim-attachment",
    )



@main_bp.post("/claims/<int:claim_id>/submit")
@login_required
def submit_claim_email(claim_id):
    claim = _owned_claim(claim_id)

    if claim.status not in {"Draft", "Rejected"} or claim.submitted_at is not None:
        flash(
            "Only a draft or rejected claim without a previous send can be emailed. "
            "Track an already submitted claim separately.",
            "warning",
        )
        return redirect(url_for("main.claims"))

    manufacturer_email = request.form.get("manufacturer_email", "").strip()
    manufacturer_name = (
        request.form.get("manufacturer_name", "").strip()
        or "Manufacturer support"
    )

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", manufacturer_email):
        flash("Enter a valid manufacturer or support email address.", "error")
        return redirect(url_for("main.claims"))

    submission_mode = current_app.config.get(
        "CLAIM_SUBMISSION_MODE", "smtp"
    ).strip().lower()

    if submission_mode not in {"smtp", "simulation"}:
        flash("Invalid claim submission mode configuration.", "error")
        return redirect(url_for("main.claims"))

    # Simulation is allowed only in local development when explicitly enabled.
    if submission_mode == "simulation":
        simulation_allowed = current_app.config.get(
            "CLAIM_SIMULATION_ALLOWED", False
        )

        if not current_app.debug or not simulation_allowed:
            flash(
                "Simulated submissions are disabled. "
                "Enable them only in local development.",
                "error",
            )
            return redirect(url_for("main.claims"))

    # SMTP mode must have all required mail settings.
    if submission_mode == "smtp" and not smtp_is_configured(current_app.config):
        flash(
            "Outbound email is not configured yet. "
            "Add your SMTP settings to .env, restart WarrantyVault, and try again.",
            "warning",
        )
        return redirect(url_for("main.claims"))

    # Keep staged files alive until send_email has finished reading them.
    with ExitStack() as attachment_stack:
        attachments = []
        storage = get_storage()

        # Collect the claim attachment key first.
        # Missing files are handled by the validation below.
        if claim.attachment_path:
            attachments.append((
                claim.attachment_path,
                claim.attachment_name or "claim-evidence",
                Path(claim.attachment_name or "claim-evidence").suffix,
            ))

        selected_document_id = request.form.get(
            "evidence_document_id", ""
        ).strip()

        if selected_document_id:
            try:
                document_id = int(selected_document_id)
            except ValueError:
                abort(400)

            evidence_document = Document.query.filter_by(
                id=document_id,
                product_id=claim.product_id,
            ).first()

            if not evidence_document:
                abort(400)

            attachments.append((
                evidence_document.stored_name,
                evidence_document.original_name,
                Path(evidence_document.original_name).suffix,
            ))

        # Stage each file only after checking that it exists.
        staged_attachments = []

        for storage_key, display_name, suffix in attachments:
            try:
                file_path = attachment_stack.enter_context(
                    storage.temporary_file(storage_key, suffix=suffix)
                )

                with open(file_path, "rb") as attachment_file:
                    attachment_file.read(1)

            except (OSError, FileNotFoundError):
                flash(
                    f"The selected evidence file '{display_name}' is missing "
                    "or cannot be read. Please upload it again before submitting.",
                    "error",
                )
                return redirect(url_for("main.claims"))

            staged_attachments.append((
                str(file_path),
                display_name,
            ))

        attachments = staged_attachments


        for file_path, display_name in attachments:
            try:
                with open(file_path, "rb") as attachment_file:
                    attachment_file.read(1)
            except OSError:
                flash(
                    f"The selected evidence file '{display_name}' is missing "
                    "or cannot be read. Please upload it again before submitting.",
                    "error",
                )
                return redirect(url_for("main.claims"))

        product = claim.product
        body = "\n".join([
            f"Dear {manufacturer_name} team,",
            "",
            "I would like to request warranty support for the product described below.",
            "",
            f"Product: {product.name}",
            f"Brand: {product.brand or 'Not provided'}",
            f"Model number: {product.model_number or 'Not provided'}",
            f"Serial number: {product.serial_number or 'Not provided'}",
            f"Retailer: {product.retailer or 'Not provided'}",
            f"Purchase date: {product.purchase_date.isoformat() if product.purchase_date else 'Not provided'}",
            f"Warranty expiry: {product.warranty_expiry.isoformat() if product.warranty_expiry else 'Not provided'}",
            "",
            f"Issue: {claim.issue_title}",
            "Description:",
            claim.description,
            "",
            "Please let me know the next steps and provide a service/claim reference if applicable.",
            "",
            current_user.name,
            current_user.email,
            "",
            "Sent using WarrantyVault. This message was prepared by the product owner.",
        ])

        if submission_mode == "simulation":
            message_id = None
            submission_channel = "Simulation"
            audit_action = "claim_email_simulated"
        else:
            try:
                message_id = send_email(
                    current_app.config,
                    recipient=manufacturer_email,
                    subject=f"Warranty support request – {product.name}: {claim.issue_title}",
                    body=body,
                    reply_to=current_user.email,
                    attachments=attachments,
                )
            except Exception as exc:
                claim.manufacturer_name = manufacturer_name
                claim.manufacturer_email = manufacturer_email
                claim.submission_error = str(exc)[:2000]
                _audit("claim", claim.id, "claim_email_failed", {
                    "recipient": manufacturer_email,
                    "error": str(exc)[:500],
                })
                db.session.commit()
                flash(
                    "The claim email could not be sent due to an email service error. "
                    "Please try again later or contact the administrator.",
                    "error",
                )
                return redirect(url_for("main.claims"))

            submission_channel = "Email"
            audit_action = "claim_email_sent"

        previous = claim.status
        claim.manufacturer_name = manufacturer_name
        claim.manufacturer_email = manufacturer_email
        claim.submission_channel = submission_channel
        claim.submission_message_id = message_id
        claim.submission_error = None

        if submission_mode == "simulation":
            claim.status = "Draft"
            claim.submitted_at = None
        else:
            claim.status = "Submitted"
            claim.submitted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            _audit("claim", claim.id, audit_action, {
                "from": previous,
                "to": claim.status,
                "recipient": manufacturer_email,
                "message_id": message_id,
                "attachments": [name for _path, name in attachments],
            })

        db.session.commit()

        if submission_mode == "simulation":
            flash(
                "Simulation completed successfully. No email was sent.",
                "success",
            )
        else:
            flash(
                "Claim email accepted by your SMTP server. "
                "This does not confirm manufacturer receipt or acknowledgment.",
                "success",
            )

        return redirect(url_for("main.claims"))


@main_bp.post("/claims/<int:claim_id>/status")
@login_required
def update_claim_status(claim_id):
    claim = _owned_claim(claim_id)
    status = request.form.get("status", "")
    # Submitted is only set by the successful email-send route above.
    if status not in {"Draft", "In progress", "Resolved", "Rejected"}:
        abort(400)
    previous = claim.status
    claim.status = status
    _audit("claim", claim.id, "claim_status_changed", {"from": previous, "to": status})
    db.session.commit()
    flash("Claim status updated.", "success")
    return redirect(request.referrer or url_for("main.claims"))


@main_bp.get("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(desc(Notification.created_at)).all()
    mail_ready = smtp_is_configured(current_app.config)
    return render_template("notifications.html", notifications=items, mail_ready=mail_ready)


@main_bp.post("/notifications/<int:notification_id>/read")
@login_required
def mark_notification_read(notification_id):
    item = Notification.query.filter_by(id=notification_id, user_id=current_user.id).first_or_404()
    item.is_read = True
    db.session.commit()
    return redirect(url_for("main.notifications"))


@main_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        current_user.name = request.form.get("name", current_user.name).strip() or current_user.name
        try:
            days = int(request.form.get("reminder_days", "30"))
            if days not in {7, 15, 30, 60, 90}:
                raise ValueError
            reminder_email = request.form.get("reminder_email", "").strip()
            email_enabled = request.form.get("reminder_email_enabled") == "on"
            if email_enabled and reminder_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", reminder_email):
                flash("Enter a valid reminder email address.", "error")
                return redirect(url_for("main.settings"))
            current_user.reminder_days = days
            current_user.reminder_email = reminder_email or None
            current_user.reminder_email_enabled = email_enabled
            db.session.commit()
            flash("Settings saved.", "success")
        except ValueError:
            flash("Choose a valid reminder interval.", "error")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", mail_ready=smtp_is_configured(current_app.config), reminder_hour=current_app.config.get("REMINDER_HOUR", 9), reminder_minute=current_app.config.get("REMINDER_MINUTE", 0), timezone=current_app.config.get("TIMEZONE", "Asia/Kolkata"))


@main_bp.get("/export/json")
@login_required
def export_json():
    products = Product.query.filter_by(user_id=current_user.id).all()
    payload = {"exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "account": current_user.email, "products": []}
    for p in products:
        payload["products"].append({
            "product": {"name": p.name, "brand": p.brand, "category": p.category, "serial_number": p.serial_number,
                "model_number": p.model_number, "retailer": p.retailer, "purchase_date": p.purchase_date.isoformat() if p.purchase_date else None,
                "purchase_price": p.purchase_price, "invoice_number": p.invoice_number,
                "warranty_start": p.warranty_start.isoformat() if p.warranty_start else None,
                "warranty_expiry": p.warranty_expiry.isoformat() if p.warranty_expiry else None, "notes": p.notes},
            "documents": [{"name": d.original_name, "type": d.document_type, "status": d.status, "uploaded_at": d.uploaded_at.isoformat(), "extracted_data": d.extracted_data,
                           "verification_history": [{"result": v.overall_result, "created_at": v.created_at.isoformat(), "summary": v.summary, "overall_confidence": v.overall_confidence, "fields": [{"field": f.field_name, "stored": f.stored_value, "extracted": f.extracted_value, "result": f.result, "confidence": f.confidence, "review_required": f.review_required, "value_source": f.value_source, "reason": f.reason} for f in v.fields], "notes": v.notes} for v in d.verifications]} for d in p.documents],
            "claims": [{"title": c.issue_title, "description": c.description, "status": c.status,
                        "created_at": c.created_at.isoformat(),
                        "manufacturer_name": c.manufacturer_name, "manufacturer_email": c.manufacturer_email,
                        "submission_channel": c.submission_channel,
                        "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
                        "submission_message_id": c.submission_message_id} for c in p.claims]
        })
    response = make_response(json.dumps(payload, indent=2, default=str))
    response.headers["Content-Type"] = "application/json"
    response.headers["Content-Disposition"] = "attachment; filename=warrantyvault-backup.json"
    return response


@main_bp.get("/export/products.csv")
@login_required

def export_csv():
    products = Product.query.filter_by(
        user_id=current_user.id
    ).order_by(Product.name).all()

    output = io.StringIO()
    writer = csv.writer(output)

    def safe_csv_value(value):
        if value is None:
            return ""

        value = str(value)

        # Prevent spreadsheet formula injection.
        if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value

        return value

    writer.writerow([
        "Product", "Brand", "Category", "Serial number",
        "Model number", "Retailer", "Purchase date",
        "Purchase price", "Warranty start", "Warranty expiry",
        "Warranty status"
    ])

    for p in products:
        writer.writerow([
            safe_csv_value(p.name),
            safe_csv_value(p.brand),
            safe_csv_value(p.category),
            safe_csv_value(p.serial_number),
            safe_csv_value(p.model_number),
            safe_csv_value(p.retailer),
            safe_csv_value(p.purchase_date),
            safe_csv_value(p.purchase_price),
            safe_csv_value(p.warranty_start),
            safe_csv_value(p.warranty_expiry),
            safe_csv_value(p.warranty_status)
        ])

    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        "attachment; filename=warrantyvault-products.csv"
    )

    return response

