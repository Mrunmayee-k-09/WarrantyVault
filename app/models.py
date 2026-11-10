from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
from .extensions import db, login_manager


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    reminder_days = db.Column(db.Integer, default=30, nullable=False)
    reminder_email_enabled = db.Column(db.Boolean, default=False, nullable=False)
    reminder_email = db.Column(db.String(180))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    products = db.relationship("Product", backref="owner", lazy=True, cascade="all, delete-orphan")
    notifications = db.relationship("Notification", backref="owner", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    brand = db.Column(db.String(120))
    category = db.Column(db.String(100))
    serial_number = db.Column(db.String(140))
    model_number = db.Column(db.String(140))
    retailer = db.Column(db.String(180))
    purchase_date = db.Column(db.Date)
    purchase_price = db.Column(db.Float)
    invoice_number = db.Column(db.String(100))
    warranty_start = db.Column(db.Date)
    warranty_expiry = db.Column(db.Date)
    warranty_months = db.Column(db.Integer)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    documents = db.relationship("Document", backref="product", lazy=True, cascade="all, delete-orphan")
    claims = db.relationship("Claim", backref="product", lazy=True, cascade="all, delete-orphan")
    verifications = db.relationship("Verification", backref="product", lazy=True, cascade="all, delete-orphan")

    @property
    def warranty_status(self):
        if not self.warranty_expiry:
            return "Unknown"
        today = date.today()
        if self.warranty_expiry < today:
            return "Expired"
        if (self.warranty_expiry - today).days <= 30:
            return "Expiring soon"
        return "Active"

    @property
    def days_remaining(self):
        if not self.warranty_expiry:
            return None
        return (self.warranty_expiry - date.today()).days


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(30), nullable=False)
    document_type = db.Column(db.String(80), default="Invoice", nullable=False)
    status = db.Column(db.String(40), default="Uploaded", nullable=False)
    extracted_data = db.Column(db.JSON, default=dict)
    extraction_confidence = db.Column(db.String(20), default="LOW")
    extraction_notes = db.Column(db.Text)
    file_size = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    verifications = db.relationship("Verification", backref="document", lazy=True, cascade="all, delete-orphan")


class Verification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("document.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    overall_result = db.Column(db.String(40), nullable=False)
    overall_confidence = db.Column(db.String(20), nullable=False)
    summary = db.Column(db.Text)
    notes = db.Column(db.JSON, default=list)
    rules_version = db.Column(db.String(30), default="1.0")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    fields = db.relationship("VerificationField", backref="verification", lazy=True, cascade="all, delete-orphan", order_by="VerificationField.id")


class VerificationField(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    verification_id = db.Column(db.Integer, db.ForeignKey("verification.id"), nullable=False, index=True)
    field_name = db.Column(db.String(80), nullable=False)
    stored_value = db.Column(db.Text)
    extracted_value = db.Column(db.Text)
    result = db.Column(db.String(20), nullable=False)
    confidence = db.Column(db.String(20), nullable=False)
    review_required = db.Column(db.Boolean, default=False, nullable=False)
    value_source = db.Column(db.String(40), default="OCR", nullable=False)
    reason = db.Column(db.Text)


class Claim(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    issue_title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default="Draft", nullable=False)
    attachment_name = db.Column(db.String(255))
    attachment_path = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    manufacturer_name = db.Column(db.String(180))
    manufacturer_email = db.Column(db.String(180))
    submission_channel = db.Column(db.String(30))
    submitted_at = db.Column(db.DateTime)
    submission_message_id = db.Column(db.String(255))
    submission_error = db.Column(db.Text)


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=True)
    title = db.Column(db.String(180), nullable=False)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(40), default="General")
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    event_key = db.Column(db.String(255))
    email_sent_at = db.Column(db.DateTime)
    email_error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
