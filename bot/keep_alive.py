from __future__ import annotations

from threading import Thread

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def healthcheck() -> tuple[dict[str, str], int]:
        return jsonify({"status": "ok", "service": "fluxer_bot"}), 200

    return app


def run_keep_alive(host: str = "0.0.0.0", port: int = 8080) -> Thread:
    app = create_app()

    def _run() -> None:
        app.run(host=host, port=port, debug=False, use_reloader=False)

    thread = Thread(target=_run, name="keep_alive_server", daemon=True)
    thread.start()
    return thread
