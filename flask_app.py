from flask import Flask, render_template_string, render_template, jsonify, request, redirect, url_for, session
from flask import render_template
from flask import json
from urllib.request import urlopen
from werkzeug.utils import secure_filename
import sqlite3
import time
import requests
from datetime import datetime

app = Flask(__name__)
@app.get("/")
def consignes():
     return render_template('consignes.html')

API_NAME = "Open-Meteo"
API_URL = "https://api.open-meteo.com/v1/forecast?latitude=48.85&longitude=2.35&current_weather=true"
DB_PATH = "runs.db"

def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            api TEXT NOT NULL,
            status TEXT NOT NULL,
            http_code INTEGER,
            latency_ms REAL,
            passed INTEGER,
            failed INTEGER,
            details TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_run(row):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO runs (ts, api, status, http_code, latency_ms, passed, failed, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, row)
    conn.commit()
    conn.close()

def list_runs(limit=20):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ts, api, status, http_code, latency_ms, passed, failed
        FROM runs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def list_runs_full(limit=20):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ts, status, http_code, latency_ms, passed, failed
        FROM runs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def build_series(rows):
    # rows = [(id, ts, status, http, lat, passed, failed), ...] newest first
    rows_rev = list(reversed(rows))  # oldest -> newest for charts
    labels = [fmt_ts(r[1])[0:16] for r in rows_rev]  # "dd/mm/yyyy hh:mm"
    lat = [r[4] if r[4] is not None else 0 for r in rows_rev]
    passed = [r[5] for r in rows_rev]
    failed = [r[6] for r in rows_rev]
    return labels, lat, passed, failed

def fmt_ts(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return ts

def compute_qos():
    # QoS simple : avg latency, p95 latency, error rate sur les 20 derniers
    rows = list_runs(20)
    if not rows:
        return {"count": 0}

    latencies = [r[5] for r in rows if r[5] is not None]
    errors = [r for r in rows if r[3] != "PASS"]

    latencies_sorted = sorted(latencies) if latencies else []
    def p95(arr):
        if not arr: return None
        k = int(round(0.95 * (len(arr)-1)))
        return arr[k]

    return {
        "count": len(rows),
        "error_rate": round(len(errors)/len(rows), 3),
        "latency_ms_avg": round(sum(latencies)/len(latencies), 2) if latencies else None,
        "latency_ms_p95": p95(latencies_sorted),
        "last_status": rows[0][3],
        "last_latency_ms": rows[0][5],
        "last_http_code": rows[0][4],
        "last_ts": rows[0][1],
    }

def run_tests():
    """
    6 tests minimum (contrat + robustesse simple)
    - HTTP 200
    - JSON
    - champs content/author présents
    - types string
    - timeout + 1 retry
    - mesure latence
    """
    tests = []
    passed = 0
    failed = 0
    http_code = None
    latency_ms = None

    def add_test(name, ok, details=""):
        nonlocal passed, failed
        tests.append({"name": name, "status": "PASS" if ok else "FAIL", "details": details})
        if ok: passed += 1
        else: failed += 1

    # timeout + 1 retry max
    last_exc = None
    for attempt in [1, 2]:
        try:
            start = time.time()
            r = requests.get(API_URL, timeout=3)
            latency_ms = round((time.time() - start) * 1000, 2)
            http_code = r.status_code

            add_test("HTTP status is 200", r.status_code == 200, f"got {r.status_code}")
            add_test("Content-Type looks like JSON", "application/json" in r.headers.get("Content-Type", ""), r.headers.get("Content-Type",""))

            # si pas 200, inutile de parser
            if r.status_code != 200:
                add_test("JSON parse", False, "status != 200")
                add_test("Field 'content' present", False, "status != 200")
                add_test("Field 'author' present", False, "status != 200")
                add_test("Types content/author are strings", False, "status != 200")
                break

            data = r.json()
            add_test("JSON parse", True)
            # Open-Meteo checks
            add_test("Field 'current_weather' present", "current_weather" in data, str(list(data.keys())[:10]))
          
            cw = data.get("current_weather", {})
            add_test("Field 'temperature' present", "temperature" in cw, str(list(cw.keys())[:10]))
            add_test("Temperature is number", isinstance(cw.get("temperature"), (int, float)),
                    f"type: {type(cw.get('temperature'))}")
          
            add_test("Field 'windspeed' present", "windspeed" in cw, str(list(cw.keys())[:10]))
            add_test("Windspeed is number", isinstance(cw.get("windspeed"), (int, float)),
                     f"type: {type(cw.get('windspeed'))}")

            break  # succès, stop retry

        except Exception as e:
            last_exc = str(e)
            if attempt == 2:
                # tout fail
                add_test("HTTP status is 200", False, last_exc)
                add_test("Content-Type looks like JSON", False, last_exc)
                add_test("JSON parse", False, last_exc)
                add_test("Field 'content' present", False, last_exc)
                add_test("Field 'author' present", False, last_exc)
                add_test("Types content/author are strings", False, last_exc)

    status = "PASS" if failed == 0 else "FAIL"
    return {
        "api": API_NAME,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "http_code": http_code,
        "latency_ms": latency_ms,
        "passed": passed,
        "failed": failed,
        "tests": tests
    }

# --- Routes demandées ---

@app.get("/run")
def run_endpoint():
    db_init()
    result = run_tests()
    save_run((
        result["timestamp"],
        result["api"],
        result["status"],
        result["http_code"],
        result["latency_ms"],
        result["passed"],
        result["failed"],
        json.dumps(result["tests"])
    ))
    return jsonify(result)

from flask import make_response

@app.get("/export.json")
def export_json():
    db_init()
    rows = list_runs(200)
    data = [{
        "id": r[0],
        "timestamp": r[1],
        "api": r[2],
        "status": r[3],
        "http_code": r[4],
        "latency_ms": r[5],
        "passed": r[6],
        "failed": r[7],
    } for r in rows]

    payload = {"api": API_NAME, "runs": data}

    resp = make_response(json.dumps(payload, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="runs_export.json"'
    return resp


def get_run_details(run_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, ts, status, http_code, latency_ms, passed, failed, details FROM runs WHERE id=?", (run_id,))
    row = cur.fetchone()
    conn.close()
    return row


def rolling_p95(series, window=10):
    out = []
    for i in range(len(series)):
        w = series[max(0, i-window+1):i+1]
        w = sorted([x for x in w if x is not None])
        if not w:
            out.append(0)
            continue
        k = int(round(0.95 * (len(w)-1)))
        out.append(w[k])
    return out

@app.get("/run/<int:run_id>")
def run_details(run_id):
    db_init()
    row = get_run_details(run_id)
    if not row:
        return "Not found", 404

    tests = []
    try:
        tests = json.loads(row[7] or "[]")
    except Exception:
        tests = []

    data = {
        "id": row[0],
        "ts": fmt_ts(row[1]),
        "status": row[2],
        "http": row[3],
        "lat": row[4],
        "passed": row[5],
        "failed": row[6],
        "tests": tests,
    }
    return render_template("details.html", api=API_NAME, run=data)

from flask import render_template

@app.get("/dashboard")
def dashboard():
    db_init()
    qos = compute_qos()

    rows = list_runs_full(20)  # newest first
    runs_fmt = []
    for idx, r in enumerate(rows):
        run_id, ts, status, http, lat, passed, failed = r
        total = (passed or 0) + (failed or 0)

        # ratio texte
        ratio_txt = f"{passed} pass / {failed} fail"

        # err rate par run
        err_rate = round((failed or 0) / total, 3) if total else 0

        # trend lat vs previous (car rows est newest first: idx+1 est previous)
        trend_dir = "eq"
        trend_delta = 0
        if idx + 1 < len(rows):
            prev_lat = rows[idx + 1][4]
            if lat is not None and prev_lat is not None:
                trend_delta = round(lat - prev_lat, 2)
                if trend_delta > 0: trend_dir = "up"
                elif trend_delta < 0: trend_dir = "down"

        runs_fmt.append({
            "id": run_id,
            "ts": fmt_ts(ts),
            "status": status,
            "http": http,
            "lat": lat,
            "passed": passed,
            "failed": failed,
            "ratio": ratio_txt,
            "err_rate": err_rate,
            "trend_dir": trend_dir,
            "trend_delta": trend_delta,
        })

    last = runs_fmt[0] if runs_fmt else None
    prev = runs_fmt[1] if len(runs_fmt) > 1 else None

    labels, lat_series, pass_series, fail_series = build_series(rows)

    # p95 roulante (sur lat_series)
    p95_series = rolling_p95(lat_series, window=10)

    # delta vs previous (pour petit badge)
    delta_lat = None
    if last and prev and last["lat"] is not None and prev["lat"] is not None:
        delta_lat = round(last["lat"] - prev["lat"], 2)

    return render_template(
        "dashboard.html",
        api=API_NAME,
        q=qos,
        runs=runs_fmt,
        last=last,
        delta_lat=delta_lat,
        labels=labels,
        lat_series=lat_series,
        p95_series=p95_series,   
        pass_series=pass_series,
        fail_series=fail_series,
    )

@app.get("/health")
def health():
    return jsonify({"status": "running", "api": API_NAME})
