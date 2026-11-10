"""Daily warranty reminder generation and optional email delivery."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..extensions import db
from ..models import Notification, Product, User
from .mailer import send_email


def local_today(app) -> date:
    timezone_name = app.config.get("TIMEZONE", "Asia/Kolkata")
    return datetime.now(ZoneInfo(timezone_name)).date()


def process_due_reminders(app, today: date | None = None) -> dict:
    """Create one in-app reminder per product/expiry/preference and send opted-in email.

    Failed email deliveries remain eligible for retry on the next scheduler run.
    In-app notices are retained even when SMTP is not configured.
    """
    stats = {"notifications_created": 0, "emails_sent": 0, "emails_failed": 0, "emails_skipped": 0}
    current_date = today or local_today(app)

    with app.app_context():
        users = User.query.all()
        for user in users:
            products = Product.query.filter_by(user_id=user.id).filter(Product.warranty_expiry.isnot(None)).all()
            for product in products:
                days_left = (product.warranty_expiry - current_date).days
                if not 0 <= days_left <= user.reminder_days:
                    continue

                event_key = f"warranty:{product.id}:{product.warranty_expiry.isoformat()}:{user.reminder_days}"
                notification = Notification.query.filter_by(user_id=user.id, event_key=event_key).first()
                if notification is None:
                    notification = Notification(
                        user_id=user.id,
                        product_id=product.id,
                        title=f"{product.name} warranty is expiring",
                        message=(
                            f"Warranty expires in {days_left} day(s), on "
                            f"{product.warranty_expiry.strftime('%d %b %Y')}."
                        ),
                        category="Warranty",
                        event_key=event_key,
                    )
                    db.session.add(notification)
                    db.session.commit()
                    stats["notifications_created"] += 1
                elif notification.email_sent_at is not None:
                    continue

                if not user.reminder_email_enabled:
                    stats["emails_skipped"] += 1
                    continue

                recipient = (user.reminder_email or user.email).strip()
                body = (
                    f"Hello {user.name},\n\n"
                    f"A WarrantyVault reminder: the warranty for {product.name} "
                    f"expires in {days_left} day(s), on {product.warranty_expiry:%d %B %Y}.\n\n"
                    f"Brand: {product.brand or 'Not provided'}\n"
                    f"Model: {product.model_number or 'Not provided'}\n"
                    f"Serial number: {product.serial_number or 'Not provided'}\n\n"
                    "Open WarrantyVault to review the product record and supporting documents.\n\n"
                    "This is an automated reminder from your WarrantyVault installation."
                )
                try:
                    send_email(
                        app.config,
                        recipient=recipient,
                        subject=f"Warranty expiry reminder: {product.name}",
                        body=body,
                    )
                    notification.email_sent_at = datetime.utcnow()
                    notification.email_error = None
                    db.session.commit()
                    stats["emails_sent"] += 1
                except Exception as exc:  # keep the in-app notification and retry next run
                    notification.email_error = str(exc)[:2000]
                    db.session.commit()
                    stats["emails_failed"] += 1
        return stats
