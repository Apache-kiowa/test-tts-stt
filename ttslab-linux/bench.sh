#!/bin/bash
# Замер скорости из терминала (то же, что страница /bench).
#   ./bench.sh                                 — все установленные модели, короткие и средние фразы, 3 повтора
#   ./bench.sh silero-v5 omnivoice             — только эти модели
#   SETS=short,medium,long REPEATS=5 ./bench.sh
cd "$(dirname "$0")"
PY="venvs/core/bin/python"; [ -x "$PY" ] || { echo "Сначала ./install.sh"; exit 1; }
get() { grep "^$1=" config.env 2>/dev/null | tail -1 | cut -d= -f2-; }
PORT="$(get PANEL_PORT)"; PORT="${PORT:-8800}"
AUTH=""; PW="$(get PANEL_PASSWORD)"; [ -n "$PW" ] && AUTH="admin:$PW"
curl -sf ${AUTH:+-u "$AUTH"} "http://127.0.0.1:$PORT/health" >/dev/null || { echo "Панель не запущена: ./start.sh"; exit 1; }

exec "$PY" - "$PORT" "$AUTH" "${SETS:-short,medium}" "${REPEATS:-3}" "$@" <<'EOF'
import json, sys, time, urllib.request, base64

port, auth, sets, repeats, *models = sys.argv[1:]
base = f"http://127.0.0.1:{port}"

def call(path, data=None):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

if not models:
    models = [m["id"] for m in call("/api/models") if m["installed"]["env"] and m["installed"]["weights"] and m["id"] != "test-tone"]
print("Модели:", ", ".join(models))
run = call("/api/bench/start", {"models": models, "sets": sets.split(","), "repeats": int(repeats), "restore": True})
seen = 0
while True:
    s = call("/api/bench/status")
    for line in s["log"][seen:] if len(s["log"]) >= seen else s["log"]:
        print(line, flush=True)
    seen = len(s["log"])
    if not s["running"]:
        break
    time.sleep(2)

rows = call(f"/api/bench/runs/{run['id']}")["summary"]
f = lambda v: "—" if v is None else (f"{v:.3f}" if v < 0.1 else f"{v:.2f}")
head = ["Модель", "Загрузка", "Первый", "Короткие", "Средние", "Длинные", "p90", "RTF", "Симв/с"]
print()
print(" | ".join(f"{h:>9}" if i else f"{h:<28}" for i, h in enumerate(head)))
for r in rows:
    vals = [r["load_s"], r["first_s"], r["short_median"], r["medium_median"], r["long_median"], r["p90"], r["rtf_median"], r["chars_per_s"]]
    print(f"{r['title'][:28]:<28} | " + " | ".join(f"{f(v):>9}" for v in vals) + (f"   ошибка: {r['error']}" if r.get("error") else ""))
print(f"\nCSV: {base}/api/bench/runs/{run['id']}/csv")
EOF
