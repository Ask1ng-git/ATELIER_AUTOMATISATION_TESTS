import time
import requests
from datetime import datetime

API_NAME = "Quotable"
API_URL = "https://api.quotable.io/random"
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

            add_test("Field 'content' present", "content" in data, str(list(data.keys())[:10]))
            add_test("Field 'author' present", "author" in data, str(list(data.keys())[:10]))

            ok_types = isinstance(data.get("content"), str) and isinstance(data.get("author"), str)
            add_test("Types content/author are strings", ok_types, f"types: {type(data.get('content'))}, {type(data.get('author'))}")

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
    # Affichage simple sans template supplémentaire
    html = """
    <h1>API Monitoring Dashboard</h1>
    <p><b>API:</b> {{api}}</p>
    <h2>QoS (last 20 runs)</h2>
    <ul>
      <li>Count: {{q.count}}</li>
      <li>Error rate: {{q.error_rate}}</li>
      <li>Latency avg (ms): {{q.latency_ms_avg}}</li>
      <li>Latency p95 (ms): {{q.latency_ms_p95}}</li>
      <li>Last run: {{q.last_ts}} | {{q.last_status}} | HTTP {{q.last_http_code}} | {{q.last_latency_ms}} ms</li>
    </ul>
    <p><a href="/run">Run now</a> | <a href="/health">Health</a></p>
    <h2>History</h2>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>ID</th><th>Timestamp</th><th>Status</th><th>HTTP</th><th>Latency (ms)</th><th>Passed</th><th>Failed</th></tr>
      {% for r in runs %}
      <tr>
        <td>{{r[0]}}</td><td>{{r[1]}}</td><td>{{r[3]}}</td><td>{{r[4]}}</td><td>{{r[5]}}</td><td>{{r[6]}}</td><td>{{r[7]}}</td>
      </tr>
      {% endfor %}
    </table>
    """
    return render_template_string(html, api=API_NAME, q=qos, runs=runs)

@app.get("/health")
def health():
    return jsonify({"status": "running", "api": API_NAME})
