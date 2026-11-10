import pytest
from app import create_app
from app.extensions import db


@pytest.fixture()
def app(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    test_app = create_app({
    "TESTING": True,
    "WTF_CSRF_ENABLED": False,
    "SECRET_KEY": "test-secret",
    "SQLALCHEMY_DATABASE_URI": "sqlite://",
    "SQLALCHEMY_TRACK_MODIFICATIONS": False,
    "UPLOAD_FOLDER": str(upload_dir),
    "CLAIM_SUBMISSION_MODE": "smtp",
})
    assert test_app.config["CLAIM_SUBMISSION_MODE"] == "smtp"
    with test_app.app_context():
        db.drop_all()
        db.create_all()
    yield test_app
    with test_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def logged_in_client(client):
    client.post("/register", data={
        "name": "Test User", "email": "test@example.com",
        "password": "password123", "confirm_password": "password123",
    }, follow_redirects=True)
    return client
