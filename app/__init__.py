import os
from pathlib import Path

from flask import Flask, redirect, url_for
from flask_login import current_user

from app.config import Config
from app.extensions import db, migrate, login_manager
from app.security import init_security
from app.utils.formatters import moeda_br


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    os.makedirs(app.instance_path, exist_ok=True)
    init_security(app)

    app.jinja_env.filters["moeda_br"] = moeda_br

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import (
        User,
        TipoRPV,
        SituacaoEmpenho,
        SituacaoImposto,
        Processo,
        RegistroRPV,
        RPVPendenciaDocumento,
        DativoCI,
        DativoLote,
        DativoItem,
        HistoricoAlteracao,
        PasswordResetToken,
    )
    from app.seed import register_seed_commands
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.cadastros import cadastros_bp
    from app.routes.dativos import dativos_bp
    from app.routes.historico import historico_bp
    from app.routes.reinf import reinf_bp
    from app.routes.usuarios import usuarios_bp

    migrate.init_app(app, db)
    register_seed_commands(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(cadastros_bp)
    app.register_blueprint(dativos_bp)
    app.register_blueprint(historico_bp)
    app.register_blueprint(reinf_bp)
    app.register_blueprint(usuarios_bp)
    

    @app.route("/home")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    def static_asset_version(filename: str) -> str:
        asset_path = Path(app.static_folder or "") / filename
        try:
            return str(int(asset_path.stat().st_mtime))
        except OSError:
            return "1"

    app.jinja_env.globals["static_asset_version"] = static_asset_version

    @app.context_processor
    def inject_app_template_globals():
        return {
            "app_release_label": app.config.get("APP_RELEASE_LABEL", ""),
            "static_asset_version": static_asset_version,
        }

    return app
