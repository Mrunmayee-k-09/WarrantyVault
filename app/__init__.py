
from pathlib import Path

from flask import Flask

from .extensions import db, login_manager, csrf
from . import models  # noqa: F401


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object("config.Config")

    if test_config:
        app.config.update(test_config)

    # Create the Flask instance directory.
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # Only create a local upload directory when local storage is selected.
    storage_backend = str(
        app.config.get("STORAGE_BACKEND", "local")
    ).strip().lower()

    if storage_backend == "local":
        Path(app.config["UPLOAD_FOLDER"]).mkdir(
            parents=True,
            exist_ok=True,
        )

    elif storage_backend != "supabase":
        raise RuntimeError(
            "STORAGE_BACKEND must be either 'local' or 'supabase'."
        )

    # Initialize Flask extensions.
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Register application blueprints.
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
            unread = Notification.query.filter_by(
                user_id=current_user.id,
                is_read=False,
            ).count()

        return {
            "unread_notifications": unread,
            "now": datetime.now,
        }

    @app.errorhandler(413)
    def too_large(_error):
        from flask import flash, redirect, url_for

        flash(
            "That file is larger than the 16 MB upload limit.",
            "error",
        )

        return redirect(url_for("main.documents"))

    return app
