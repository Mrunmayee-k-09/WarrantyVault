import os
from dotenv import load_dotenv
load_dotenv()
from app import create_app
from app.extensions import db
from app.models import User, Product
from datetime import date

app = create_app()


@app.cli.command("init-db")
def init_db_command():
    """Create all application tables and apply schema compatibility updates."""
    db.create_all()

    from app.schema import ensure_schema_compatibility
    ensure_schema_compatibility()

    print("WarrantyVault database tables are ready.")


@app.cli.command("seed-demo")
def seed_demo_command():
    """Create a local demo account and example product records."""
    if app.config.get("IS_PRODUCTION"):
        raise RuntimeError("The seed-demo command is disabled in production.")

    email = "demo@warrantyvault.local"
    user = User.query.filter_by(email=email).first()
    if user:
        print("Demo account already exists: demo@warrantyvault.local")
        print("Password: WarrantyVault123!")
        return
    user = User(name="Demo User", email=email, reminder_days=30)
    user.set_password("WarrantyVault123!")
    db.session.add(user)
    db.session.flush()
    db.session.add_all([
        Product(user_id=user.id, name="Acer Nitro Lite", brand="Acer", category="Laptop",
                model_number="NL16-71G-54MY", serial_number="NHD5ASI001526040482N00",
                retailer="S V ELECTRONICS PRIVATE LIMITED", purchase_date=date(2026, 4, 10),
                purchase_price=54990, invoice_number="INV-2026-0410-08",
                warranty_start=date(2026, 4, 10), warranty_expiry=date(2028, 4, 10), warranty_months=24,
                notes="Demo record. Upload one of the sample invoice files in sample_data/ to test verification."),
        Product(user_id=user.id, name="Samsung Galaxy Tab", brand="Samsung", category="Tablet",
                model_number="SM-X210", serial_number="R52X100DEMO", retailer="Tech World",
                purchase_date=date(2024, 7, 15), purchase_price=21999,
                warranty_start=date(2024, 7, 15), warranty_expiry=date(2025, 7, 15), warranty_months=12,
                notes="Demo expired-warranty example.")
    ])
    db.session.commit()
    print("Demo account and two sample products created.")
    print("Email: demo@warrantyvault.local")
    print("Password: WarrantyVault123!")




@app.cli.command("send-reminders")
def send_reminders_command():
    """Run one daily warranty reminder pass (useful for testing SMTP setup)."""
    from app.services.reminders import process_due_reminders
    print(process_due_reminders(app))


if __name__ == "__main__":
    if app.config.get("IS_PRODUCTION"):
        from waitress import serve
        serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
    else:
        app.run(
            host="127.0.0.1",
            port=int(os.getenv("PORT", "5000")),
            debug=os.getenv("FLASK_DEBUG", "0") == "1"
        )