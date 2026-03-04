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

@app.get("/dashboard")
def dashboard():
    db_init()
    qos = compute_qos()
    runs = list_runs(20)

    runs_fmt = []
    for r in runs:
        r = list(r)
        r[1] = fmt_ts(r[1])  # colonne Timestamp
        runs_fmt.append(r)

    if qos.get("last_ts"):
        qos["last_ts"] = fmt_ts(qos["last_ts"])
    html = """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Dashboard — API Monitoring</title>
  <style>
    :root{
      --bg:#0b1020; --card:#121a33; --muted:#aab4d4; --text:#e9edff;
      --accent:#77baff; --ok:#62d6a0; --bad:#ff6b7a; --line:#23305c;
    }
    *{box-sizing:border-box}
    body{
       margin:0;
       min-height:100vh;
       font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
       background: radial-gradient(1200px 600px at 10% 0%, #172257 0%, var(--bg) 60%);
       color:var(--text);
       line-height:1.45;
     }
    .container{max-width:1100px; margin:auto; padding:40px 22px;}
    .row{display:flex; gap:10px; flex-wrap:wrap; align-items:center;}
    .pill{
      display:inline-flex; gap:8px; align-items:center;
      padding:6px 10px; border-radius:999px;
      background: rgba(119,186,255,0.12);
      border:1px solid rgba(119,186,255,0.28);
      color:#d9ecff; font-size:12px;
    }
    .pill.ok{background: rgba(98,214,160,0.12); border-color: rgba(98,214,160,0.28);}
    .pill.bad{background: rgba(255,107,122,0.12); border-color: rgba(255,107,122,0.28);}
    a{color:var(--accent); text-decoration:none}
    a:hover{text-decoration:underline}
    h1{margin:14px 0 6px; font-size: clamp(22px, 3vw, 32px);}
    .muted{color:var(--muted)}
    .card{
      margin-top:14px;
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
      border:1px solid rgba(255,255,255,0.08);
      box-shadow: 0 12px 30px rgba(0,0,0,0.35);
      border-radius:16px; padding:16px;
    }
    .kpi{
      display:grid; grid-template-columns: repeat(5, 1fr);
      gap:10px; margin-top:10px;
    }
    @media (max-width: 980px){ .kpi{grid-template-columns: 1fr 1fr;} }
    @media (max-width: 560px){ .kpi{grid-template-columns: 1fr;} }
    .box{
      padding:12px; border-radius:14px;
      border:1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.02);
    }
    .box b{display:block; font-size:13px; margin-bottom:6px;}
    .box span{color:var(--muted); font-size:12px;}
    .table{
      width:100%; border-collapse: collapse; margin-top:12px; font-size:13px;
      overflow:hidden; border-radius:12px;
      border:1px solid rgba(255,255,255,0.08);
    }
    .table th,.table td{
      padding:10px; border-bottom:1px solid rgba(255,255,255,0.08); vertical-align:top;
    }
    .table th{background: rgba(119,186,255,0.10); text-align:left; font-weight:600;}
    .table tr:last-child td{border-bottom:none;}
    .status-pass{color: var(--ok); font-weight:700;}
    .status-fail{color: var(--bad); font-weight:700;}
  </style>
</head>
<body>
  <div class="container">
    <div class="row">
      <span class="pill">API Monitoring</span>
      <span class="pill ok">QoS</span>
      <span class="pill">SQLite</span>
      <span class="pill">PythonAnywhere</span>
    </div>

    <h1>Dashboard — {{api}}</h1>
    <p class="muted">Endpoints: <a href="/run">/run</a> · <a href="/health">/health</a></p>

    <div class="card">
      <h2 style="margin:0;">QoS (last 20 runs)</h2>

      <div class="kpi">
        <div class="box"><b>Count</b><span>{{q.count}}</span></div>
        <div class="box"><b>Error rate</b><span>{{q.error_rate}}</span></div>
        <div class="box"><b>Latency avg (ms)</b><span>{{q.latency_ms_avg}}</span></div>
        <div class="box"><b>Latency p95 (ms)</b><span>{{q.latency_ms_p95}}</span></div>
        <div class="box">
          <b>Last run</b>
          <span>
            {{q.last_ts}} ·
            {% if q.last_status == "PASS" %}
              <span class="status-pass">PASS</span>
            {% else %}
              <span class="status-fail">FAIL</span>
            {% endif %}
            · HTTP {{q.last_http_code}} · {{q.last_latency_ms}} ms
          </span>
        </div>
      </div>
    </div>

    <div class="card">
      <h2 style="margin:0 0 10px;">History</h2>
      <table class="table">
        <thead>
          <tr><th>ID</th><th>Timestamp</th><th>Status</th><th>HTTP</th><th>Latency (ms)</th><th>Passed</th><th>Failed</th></tr>
        </thead>
        <tbody>
          {% for r in runs %}
          <tr>
            <td>{{r[0]}}</td>
            <td>{{r[1]}}</td>
            <td>
              {% if r[3] == "PASS" %}
                <span class="status-pass">PASS</span>
              {% else %}
                <span class="status-fail">FAIL</span>
              {% endif %}
            </td>
            <td>{{r[4]}}</td><td>{{r[5]}}</td><td>{{r[6]}}</td><td>{{r[7]}}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

  </div>
</body>
</html>
"""
    return render_template_string(html, api=API_NAME, q=qos, runs=runs_fmt)

@app.get("/health")
def health():
    return jsonify({"status": "running", "api": API_NAME})
