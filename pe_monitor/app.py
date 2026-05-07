"""Flask app: serves the dashboard and JSON API, runs the background crawler."""

from flask import Flask, abort, jsonify, render_template

import config
import fetcher
import storage

CONFIG = config.load_config()
storage.init_db(CONFIG["database_path"])

app = Flask(__name__)


@app.route("/")
def dashboard():
    return render_template("dashboard.html", tickers=CONFIG["tickers"])


@app.route("/api/tickers")
def api_tickers():
    return jsonify(CONFIG["tickers"])


@app.route("/api/history/<ticker>")
def api_history(ticker: str):
    ticker = ticker.upper()
    if ticker not in CONFIG["tickers"]:
        abort(404)
    return jsonify(storage.read_history(CONFIG["database_path"], ticker))


@app.route("/api/latest")
def api_latest():
    return jsonify(storage.latest_per_ticker(CONFIG["database_path"], CONFIG["tickers"]))


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    fetcher.snapshot_all(CONFIG["tickers"], CONFIG["database_path"])
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    fetcher.start_scheduler(
        CONFIG["tickers"],
        CONFIG["database_path"],
        CONFIG["fetch_interval_seconds"],
    )
    app.run(host=CONFIG["host"], port=CONFIG["port"])
