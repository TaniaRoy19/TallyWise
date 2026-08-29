from dotenv import load_dotenv
load_dotenv()

"""
Local web server for the AI Finance Controller project.

Serves a polished dashboard frontend (templates/index.html) and two API
endpoints the frontend calls:

  GET  /api/report   -> the current reconciliation_report.json contents
  POST /api/ask       -> {"question": "..."} -> real Gemini-backed answer
  POST /api/run       -> re-runs the reconciliation pipeline, returns fresh summary

The API key stays server-side the whole time -- the browser never sees it.
This is the correct pattern: never call an LLM directly from client-side
JavaScript with a real API key embedded in it.

Run:
    python src/server.py
    -> open http://127.0.0.1:5000 in your browser
"""

import json
import os
import sys

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as pipeline_main
import qa_agent

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
REPORT_PATH = os.path.join(OUTPUT_DIR, "reconciliation_report.json")

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/report")
def get_report():
    if not os.path.exists(REPORT_PATH):
        return jsonify({"error": "No report yet. Click 'Run reconciliation' first."}), 404
    with open(REPORT_PATH) as f:
        return jsonify(json.load(f))


@app.route("/api/run", methods=["POST"])
def run_pipeline():
    """Re-runs the full pipeline (rule-based matching + LLM resolution
    stage) against whatever data currently exists in data/, and returns
    the fresh summary."""
    pipeline_main.main()
    with open(REPORT_PATH) as f:
        report = json.load(f)
    return jsonify(report["summary"])


@app.route("/api/ask", methods=["POST"])
def ask():
    body = request.get_json(force=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "No question provided."}), 400
    if not os.path.exists(REPORT_PATH):
        return jsonify({"error": "No report yet. Click 'Run reconciliation' first."}), 400

    used_real_api = bool(os.environ.get("GOOGLE_API_KEY"))
    answer_text = qa_agent.answer(question)
    return jsonify({"answer": answer_text, "used_real_api": used_real_api})


if __name__ == "__main__":
    if not os.environ.get("GOOGLE_API_KEY"):
        print("NOTE: GOOGLE_API_KEY is not set -- Q&A will use the offline "
              "fallback logic instead of real Gemini reasoning.")
    app.run(debug=True, use_reloader=False, port=5000)