from pathlib import Path
from flask import Flask
from .extensions import db, login_manager, csrf
from . import models  # noqa: F401


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object("config.Config")
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        db.create_all()
        from .schema import ensure_schema_compatibility
        ensure_schema_compatibility()

    from .auth import auth_bp
    from .main import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    @app.context_processor
    def inject_globals():
        from datetime import datetime
        from flask_login import current_user
        from .models import Notification
        unread = 0
        if current_user.is_authenticated:
            unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        return {"unread_notifications": unread, "now": datetime.now}

    @app.errorhandler(413)
    def too_large(_error):
        from flask import flash, redirect, url_for

        flash("That file is larger than the 16 MB upload limit.", "error")
        return redirect(url_for("main.documents"))

    return app
