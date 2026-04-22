"""DataPilot AI Flask application.
 
Small API + single-page frontend. State is kept in an in-process session
store (see ``data_store.SessionStore``). Good enough for a local demo; not
intended to run behind multiple workers without swapping in Redis.
"""
 
from __future__ import annotations
 
import os
 
from flask import Flask, abort, jsonify, render_template, request
from flask_cors import CORS
 
from analyst import build_analyst
from data_store import SessionStore, read_tabular
 
 
def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    CORS(app)
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload cap.
    app.config["SESSION_STORE"] = SessionStore()
 
    register_routes(app)
    return app
 
 
def register_routes(app: Flask) -> None:
    store: SessionStore = app.config["SESSION_STORE"]
 
    @app.get("/")
    def index():
        return render_template("index.html")
 
    @app.get("/api/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "llm_mode": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            }
        )
 
    @app.post("/api/upload")
    def upload():
        if not request.files:
            abort(400, description="No files uploaded")
        session_id = request.form.get("session_id")
        session = store.get_or_create(session_id)
 
        accepted = {"sales", "purchase"}
        added: list[str] = []
        for kind in accepted:
            if kind in request.files:
                f = request.files[kind]
                if not f.filename:
                    continue
                try:
                    df = read_tabular(f.filename, f.read())
                except ValueError as exc:
                    abort(400, description=str(exc))
                session.set_table(kind, df)
                added.append(kind)
 
        if not added:
            abort(400, description="Upload at least one 'sales' or 'purchase' file")
 
        return jsonify(
            {
                "session_id": session.session_id,
                "tables": list(session.tables.keys()),
                "schema": session.schema(),
            }
        )
 
    @app.get("/api/schema")
    def schema():
        session_id = request.args.get("session_id", "")
        session = store.get(session_id)
        if session is None:
            abort(404, description="Unknown session")
        return jsonify({"session_id": session.session_id, "schema": session.schema()})
 
    @app.post("/api/ask")
    def ask():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id") or ""
        question = (payload.get("question") or "").strip()
        if not question:
            abort(400, description="Missing 'question'")
        session = store.get(session_id)
        if session is None:
            abort(404, description="Unknown session — upload files first")
 
        analyst = build_analyst(
            sales=session.tables.get("sales"),
            purchase=session.tables.get("purchase"),
        )
        response = analyst.answer(question)
        return jsonify(response.to_dict())
 
    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def _error(err):
        return (
            jsonify({"error": getattr(err, "description", str(err))}),
            err.code if hasattr(err, "code") else 500,
        )
 
 
app = create_app()
 
 
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))