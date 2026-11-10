
import csv
import io
import json
import os
import uuid
import re

from datetime import date, datetime, timedelta
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
from .services.verification import build_verification
from .services.mailer import smtp_is_configured, send_email


main_bp = Blueprint("main", __name__)


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
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_float(value):
    if not value:
        return None
    try:
        return float(value.replace(",", "").replace("₹", "").strip())
    except (ValueError, AttributeError):
        return None


def _save_upload(file_storage):
    if not file_storage or not file_storage.filename:
        return None, None
    original = secure_filename(file_storage.filename)
    ext = Path(original).suffix.lower().lstrip(".")
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ValueError("Unsupported file type. Use PDF, PNG, JPG, TIFF, TXT, or DOCX.")
    stored = f"{uuid.uuid4().hex}.{ext}"
    destination = Path(current_app.config["UPLOAD_FOLDER"]) / stored
    file_storage.save(destination)
    return original, stored


def _process_doc(doc):
    doc.status = "Processing"
    db.session.commit()
    path = Path(current_app.config["UPLOAD_FOLDER"]) / doc.stored_name
    data, confidence_map, overall, extracted_text, notes = process_document(str(path), doc.file_type)
    doc.extracted_data = {"fields": data, "field_confidences": confidence_map, "text_preview": extracted_text}
    doc.extraction_confidence = overall
    doc.extraction_notes = "\n".join(notes)
    doc.status = "Ready to verify" if data else "Processing failed"
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
    _audit("document", doc.id, "verification_created", {"verification_id": verification.id, "result": overall})
    db.session.commit()
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
    # Remove physical attachments as well as their cascading database records.
    for doc in product.documents:
        (Path(current_app.config["UPLOAD_FOLDER"]) / doc.stored_name).unlink(missing_ok=True)
    for claim in product.claims:
        if claim.attachment_path:
            (Path(current_app.config["UPLOAD_FOLDER"]) / claim.attachment_path).unlink(missing_ok=True)
    Notification.query.filter_by(user_id=current_user.id, product_id=product.id).delete(synchronize_session=False)
    _audit("product", product.id, "product_deleted", {"name": name})
    db.session.delete(product)
    db.session.commit()
    flash(f"{name} and its attached records were deleted.", "info")
    return redirect(url_for("main.products"))


@main_bp.post("/products/<int:product_id>/documents")
@login_required
def upload_document(product_id):
    product = _owned_product(product_id)
    file = request.files.get("document")
    try:
        original, stored = _save_upload(file)
        if not original:
            flash("Choose a document to upload.", "error")
            return redirect(url_for("main.product_detail", product_id=product.id))
        path = Path(current_app.config["UPLOAD_FOLDER"]) / stored
        doc = Document(product_id=product.id, original_name=original, stored_name=stored,
                       file_type=path.suffix.lstrip("."), document_type=request.form.get("document_type", "Invoice"),
                       status="Uploaded", file_size=path.stat().st_size)
        db.session.add(doc)
        db.session.flush()
        _audit("document", doc.id, "document_uploaded", {"filename": original})
        db.session.commit()
        _process_doc(doc)
        if doc.status == "Processing failed":
            flash("The file was saved, but readable invoice fields could not be extracted. You can review it or retry after fixing the document/OCR setup.", "warning")
        else:
            flash("Document uploaded and processed. Review the extracted fields before verification.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.product_detail", product_id=product.id))


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
    path = Path(current_app.config["UPLOAD_FOLDER"]) / doc.stored_name
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=doc.original_name)


@main_bp.post("/documents/<int:document_id>/retry")
@login_required
def retry_document(document_id):
    doc = _owned_document(document_id)
    path = Path(current_app.config["UPLOAD_FOLDER"]) / doc.stored_name
    if not path.exists():
        flash("The stored file is missing. Upload the document again.", "error")
    else:
        _process_doc(doc)
        flash("Document processing retried.", "success" if doc.status != "Processing failed" else "warning")
    return redirect(request.referrer or url_for("main.documents"))


@main_bp.post("/documents/<int:document_id>/verify")
@login_required
def verify_document(document_id):
    doc = _owned_document(document_id)
    if not (doc.extracted_data or {}).get("fields"):
        flash("No extracted fields are available. Review the document or retry processing first.", "warning")
        return redirect(url_for("main.document_report", document_id=doc.id))
    verification = _run_verification(doc)
    flash("Verification completed. This result checks consistency, not authenticity.", "success")
    return redirect(url_for("main.verification_report", verification_id=verification.id))


@main_bp.get("/documents/<int:document_id>/report")
@login_required
def document_report(document_id):
    doc = _owned_document(document_id)
    latest = Verification.query.filter_by(document_id=doc.id).order_by(desc(Verification.created_at)).first()
    history = Verification.query.filter_by(document_id=doc.id).order_by(desc(Verification.created_at)).all()
    return render_template("document_report.html", document=doc, latest=latest, history=history)


@main_bp.get("/verifications/<int:verification_id>")
@login_required
def verification_report(verification_id):
    verification = Verification.query.join(Product).filter(Verification.id == verification_id, Product.user_id == current_user.id).first_or_404()
    return render_template("verification_report.html", verification=verification)


@main_bp.post("/documents/<int:document_id>/correct")
@login_required
def correct_extraction(document_id):
    doc = _owned_document(document_id)
    fields = (doc.extracted_data or {}).get("fields", {}).copy()
    confs = (doc.extracted_data or {}).get("field_confidences", {}).copy()
    allowed = {"brand", "retailer", "model_number", "serial_number", "purchase_date", "invoice_number", "purchase_price"}
    changes = {}
    for key in allowed:
        if key in request.form:
            value = request.form.get(key, "").strip()
            old = fields.get(key)
            if value != (old or ""):
                changes[key] = {"from": old, "to": value}
                if value:
                    fields[key] = value
                else:
                    fields.pop(key, None)
                    confs.pop(key, None)
    payload = doc.extracted_data or {}
    payload["fields"] = fields
    payload["field_confidences"] = confs
    manual = payload.get("manual_corrections", {}).copy()
    for key, change in changes.items():
        manual[key] = {"from": change["from"], "to": change["to"], "updated_at": datetime.utcnow().isoformat()}
    payload["manual_corrections"] = manual
    doc.extracted_data = payload
    try:
        from sqlalchemy.orm.attributes import flag_modified as _flag_modified
        _flag_modified(doc, "extracted_data")
    except Exception:
        pass
    _audit("document", doc.id, "extraction_corrected", {"changes": changes, "manual_review": True})
    db.session.commit()
    flash("Corrections saved. The original uploaded file remains unchanged. Run verification again to create a new history entry.", "success")
    return redirect(url_for("main.document_report", document_id=doc.id))


@main_bp.post("/documents/<int:document_id>/delete")
@login_required
def delete_document(document_id):
    doc = _owned_document(document_id)
    product_id = doc.product_id
    _audit("document", doc.id, "document_deleted", {"filename": doc.original_name})
    path = Path(current_app.config["UPLOAD_FOLDER"]) / doc.stored_name
    db.session.delete(doc)
    db.session.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    flash("Document and its verification history deleted.", "info")
    return redirect(url_for("main.product_detail", product_id=product_id))



@main_bp.route("/claims", methods=["GET"])
@login_required
def claims():
    items = Claim.query.join(Product).filter(
        Product.user_id == current_user.id
    ).order_by(desc(Claim.updated_at)).all()

    return render_template(
        "claims.html",
        claims=items,
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
    claim = Claim(product_id=product.id, issue_title=title, description=description, status="Draft")
    file = request.files.get("attachment")
    if file and file.filename:
        try:
            original, stored = _save_upload(file)
            claim.attachment_name = original
            claim.attachment_path = stored
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("main.product_detail", product_id=product.id))
    db.session.add(claim)
    db.session.flush()
    _audit("claim", claim.id, "claim_created", {"title": title})
    db.session.commit()
    flash("Claim draft created. Open Claims to email it to a manufacturer/support contact.", "success")
    return redirect(url_for("main.claims"))


@main_bp.get("/claims/<int:claim_id>/attachment")
@login_required
def download_claim_attachment(claim_id):
    claim = _owned_claim(claim_id)
    if not claim.attachment_path:
        abort(404)
    path = Path(current_app.config["UPLOAD_FOLDER"]) / claim.attachment_path
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=claim.attachment_name or "claim-attachment")



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

    attachments = []
    upload_root = Path(current_app.config["UPLOAD_FOLDER"])

    if claim.attachment_path:
        attachments.append((
            str(upload_root / claim.attachment_path),
            claim.attachment_name or "claim-evidence",
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

        evidence_path = upload_root / evidence_document.stored_name

        if evidence_path.exists():
            attachments.append((
                str(evidence_path),
                evidence_document.original_name,
            ))

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

    # Handle simulation or actual SMTP submission.
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

            flash(f"The claim email was not sent: {exc}", "error")
            return redirect(url_for("main.claims"))

        submission_channel = "Email"
        audit_action = "claim_email_sent"

    # Update the claim only after successful SMTP acceptance
    # or an explicitly permitted simulation.
    claim.manufacturer_name = manufacturer_name
    claim.manufacturer_email = manufacturer_email
    claim.submission_channel = submission_channel
    claim.submitted_at = datetime.utcnow()
    claim.submission_message_id = message_id
    claim.submission_error = None

    previous = claim.status
    claim.status = "Submitted"

    _audit("claim", claim.id, audit_action, {
        "from": previous,
        "to": "Submitted",
        "recipient": manufacturer_email,
        "message_id": message_id,
        "attachments": [name for _path, name in attachments],
    })

    db.session.commit()

    if submission_mode == "simulation":
        flash(
            "SIMULATED SUBMISSION: Your claim was marked as submitted. "
            "No email was sent.",
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
    payload = {"exported_at": datetime.utcnow().isoformat() + "Z", "account": current_user.email, "products": []}
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
    products = Product.query.filter_by(user_id=current_user.id).order_by(Product.name).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Product", "Brand", "Category", "Serial number", "Model number", "Retailer", "Purchase date", "Purchase price", "Warranty start", "Warranty expiry", "Warranty status"])
    for p in products:
        writer.writerow([p.name, p.brand, p.category, p.serial_number, p.model_number, p.retailer,
            p.purchase_date, p.purchase_price, p.warranty_start, p.warranty_expiry, p.warranty_status])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=warrantyvault-products.csv"
    return response
