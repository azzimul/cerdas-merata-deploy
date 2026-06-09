"""
Cerdas Merata — Flask API Entry Point v2.0
Run:
  PostgreSQL : python app.py
  SQLite demo: set USE_SQLITE=1 && python app.py
"""

import os

from flask import Flask
from flask_cors import CORS

from db.connection import USE_SQLITE, get_conn
from routes.pages import bp as pages_bp
from routes.auth  import bp as auth_bp
from routes.apply import bp as apply_bp
from routes.admin import bp as admin_bp

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)

app.register_blueprint(pages_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(apply_bp)
app.register_blueprint(admin_bp)


# ── Bootstrap ────────────────────────────────────────────────────────────────

def _bootstrap():
    if USE_SQLITE:
        from db.connection import init_sqlite
        init_sqlite()
    else:
        from db.connection import init_postgres
        init_postgres()

_bootstrap()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    print(f"  Mode  : {'SQLite (demo)' if USE_SQLITE else 'PostgreSQL'}")
    print(f"  URL   : http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
