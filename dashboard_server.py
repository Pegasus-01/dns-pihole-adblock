#!/usr/bin/env python3
"""
Ad-Block Web Dashboard — reads/writes the same SQLite database the proxy uses.
Serves the control panel at http://localhost:8080
"""

import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import db

app = Flask(__name__, static_folder="static")

LOG_FILE = Path(__file__).parent / "logs" / "proxy.log"


def get_conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


# ── API routes ────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
    blocked = conn.execute("SELECT COUNT(*) c FROM decisions WHERE verdict='BLOCK'").fetchone()["c"]
    allowed = total - blocked

    sources = dict(conn.execute(
        "SELECT source, COUNT(*) c FROM decisions GROUP BY source"
    ).fetchall())

    top_blocked = conn.execute(
        "SELECT domain, COUNT(*) c FROM decisions WHERE verdict='BLOCK' "
        "GROUP BY domain ORDER BY c DESC LIMIT 10"
    ).fetchall()
    top_allowed = conn.execute(
        "SELECT domain, COUNT(*) c FROM decisions WHERE verdict='ALLOW' "
        "GROUP BY domain ORDER BY c DESC LIMIT 10"
    ).fetchall()

    now = time.time()
    hourly: dict[int, dict] = {}
    rows = conn.execute(
        "SELECT ts, verdict FROM decisions ORDER BY id DESC LIMIT 20000"
    ).fetchall()
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["ts"]).timestamp()
        except Exception:
            continue
        age_h = int((now - ts) / 3600)
        if age_h > 23:
            continue
        hour_label = 23 - age_h
        bucket = hourly.setdefault(hour_label, {"block": 0, "allow": 0})
        bucket["block" if r["verdict"] == "BLOCK" else "allow"] += 1

    timeline = [{"hour": h, **hourly.get(h, {"block": 0, "allow": 0})} for h in range(24)]
    conn.close()

    return jsonify({
        "total": total,
        "blocked": blocked,
        "allowed": allowed,
        "block_rate": round(blocked / total * 100, 1) if total else 0,
        "sources": sources,
        "top_blocked": [{"domain": r["domain"], "count": r["c"]} for r in top_blocked],
        "top_allowed": [{"domain": r["domain"], "count": r["c"]} for r in top_allowed],
        "timeline": timeline,
    })


@app.route("/api/decisions")
def api_decisions():
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(200, int(request.args.get("per_page", 50)))
    search = request.args.get("search", "").strip().lower()
    verdict = request.args.get("verdict", "").upper()
    source = request.args.get("source", "").lower()

    where = []
    params: list = []
    if search:
        where.append("LOWER(domain) LIKE ?")
        params.append(f"%{search}%")
    if verdict in ("BLOCK", "ALLOW"):
        where.append("verdict = ?")
        params.append(verdict)
    if source:
        where.append("LOWER(source) = ?")
        params.append(source)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) c FROM decisions {where_sql}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT ts, domain, verdict, matched_domain, source FROM decisions {where_sql} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    conn.close()

    entries = [dict(r) for r in rows]
    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, -(-total // per_page)),
        "entries": entries,
    })


@app.route("/api/allowlist", methods=["GET"])
def get_allowlist():
    conn = get_conn()
    items = db.list_domains(conn, "allow")
    conn.close()
    return jsonify(items)


@app.route("/api/allowlist", methods=["POST"])
def add_allowlist():
    domain = (request.json or {}).get("domain", "").strip().lower()
    if not domain:
        return jsonify({"error": "domain required"}), 400
    conn = get_conn()
    db.add_domain(conn, domain, "allow", source="manual")
    conn.close()
    return jsonify({"ok": True, "domain": domain})


@app.route("/api/allowlist/<path:domain>", methods=["DELETE"])
def remove_allowlist(domain):
    conn = get_conn()
    db.remove_domain(conn, domain, list_type="allow")
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/blocklist", methods=["GET"])
def get_blocklist():
    conn = get_conn()
    items = db.list_domains(conn, "block")
    conn.close()
    return jsonify(items)


@app.route("/api/blocklist", methods=["POST"])
def add_blocklist():
    domain = (request.json or {}).get("domain", "").strip().lower()
    if not domain:
        return jsonify({"error": "domain required"}), 400
    conn = get_conn()
    db.add_domain(conn, domain, "block", source="manual")
    conn.close()
    return jsonify({"ok": True, "domain": domain})


@app.route("/api/blocklist/<path:domain>", methods=["DELETE"])
def remove_blocklist(domain):
    conn = get_conn()
    db.remove_domain(conn, domain, list_type="block")
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/log")
def api_log():
    n = int(request.args.get("n", 100))
    if not LOG_FILE.exists():
        return jsonify({"lines": []})
    lines = LOG_FILE.read_text(errors="replace").splitlines()
    return jsonify({"lines": lines[-n:]})


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    if path and (Path(app.static_folder) / path).exists():
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    conn = get_conn()
    conn.close()
    print("Ad-Block Dashboard -> http://localhost:8080")
    app.run(host="127.0.0.1", port=8080, debug=False)
