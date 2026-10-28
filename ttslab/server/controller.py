"""Панель управления TTS Lab.

    venvs/core/bin/python -m server.controller

http://<мак>:8800/        — страница тестирования (ключ LiteLLM, текст, настройки, прослушивание, оценки)
http://<мак>:8800/admin   — выбор и запуск модели, логи, словарь, голоса-образцы
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from .common import (DATA, LOGS, OUTPUTS, REFS, ROOT, VENVS, append_jsonl, load_config, read_json, setup_logging, tail,
                     write_json)
from .normalizer import DICT_FILE, normalize_text
from .registry import GROUPS, all_models, coerce, defaults, get_model, load_defaults

log = setup_logging("ttslab.panel", "panel.log")
CFG = load_config()
STATE_FILE = DATA / "state.json"
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="TTS Lab")
_downloads: dict[str, subprocess.Popen] = {}


# ------------------------------------------------------------------ auth
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    pwd = CFG.get("PANEL_PASSWORD", "")
    if pwd and not request.url.path.startswith("/health"):
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.lower().startswith("basic "):
            try:
                _, p = base64.b64decode(auth[6:]).decode().split(":", 1)
                ok = secrets.compare_digest(p, pwd)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="TTS Lab"'})
    return await call_next(request)


# ------------------------------------------------------------------ helpers
def venv_python(group: str) -> Path | None:
    if group == "core":
        return Path(sys.executable)
    p = VENVS / group / "bin" / "python"
    return p if p.exists() else None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:  # если это наш завершившийся дочерний процесс — забираем его, иначе он висит «зомби»
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/stat") as f:
            if f.read().rsplit(")", 1)[-1].split()[0] == "Z":
                return False
    except OSError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def port_pids(port: int) -> list[int]:
    if not shutil.which("lsof"):
        return []
    try:
        out = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def lan_ips() -> list[str]:
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def get_state() -> dict[str, Any]:
    st = read_json(STATE_FILE, {})
    if st and not pid_alive(st.get("pid")):
        st["running"] = False
    elif st:
        st["running"] = True
    return st


def kill_group(pid: int, timeout: float = 10) -> None:
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        t0 = time.time()
        while time.time() - t0 < timeout:
            if not pid_alive(pid):
                return
            time.sleep(0.2)


def worker_url(path: str = "") -> str:
    return f"http://127.0.0.1:{CFG['TTS_PORT']}{path}"


def model_installed(m: dict[str, Any]) -> dict[str, Any]:
    inst = read_json(DATA / "installed.json", {})
    py = venv_python(m["group"])
    return {"env": py is not None, "weights": m["id"] in inst or m["id"] == "test-tone", "installed_at": inst.get(m["id"], {}).get("ts")}


# ------------------------------------------------------------------ pages
@app.get("/", response_class=HTMLResponse)
def page_user():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/admin", response_class=HTMLResponse)
def page_admin():
    return (STATIC / "admin.html").read_text(encoding="utf-8")


@app.get("/static/{name}")
def static_file(name: str):
    p = (STATIC / name).resolve()
    if p.parent != STATIC.resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/health")
def health():
    return {"ok": True}


# ------------------------------------------------------------------ config / models
@app.get("/api/config")
def api_config():
    return {
        "panel_port": int(CFG["PANEL_PORT"]),
        "tts_port": int(CFG["TTS_PORT"]),
        "tts_api_key": CFG["TTS_API_KEY"],
        "litellm_url": CFG["LITELLM_URL"],
        "litellm_model_name": CFG["LITELLM_MODEL_NAME"],
        "lan_ips": lan_ips(),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "caffeinate": shutil.which("caffeinate") is not None,
        "groups": {g: {**v, "installed": venv_python(g) is not None} for g, v in GROUPS.items()},
    }


@app.get("/api/models")
def api_models():
    out = []
    for m in all_models():
        m["installed"] = model_installed(m)
        m["saved_defaults"] = read_json(DATA / "defaults" / f"{m['id']}.json", {})
        out.append(m)
    return out


# ------------------------------------------------------------------ worker control
@app.get("/api/status")
def api_status():
    st = get_state()
    out: dict[str, Any] = {"bench": BENCH.busy(), "state": st, "worker": None, "port_busy": port_busy(int(CFG["TTS_PORT"]))}
    if st.get("running"):
        try:
            r = httpx.get(worker_url("/health"), timeout=1.5)
            out["worker"] = r.json()
        except Exception as e:
            out["worker"] = {"status": "starting", "error": f"нет ответа: {type(e).__name__}"}
    elif out["port_busy"]:
        out["foreign_pids"] = port_pids(int(CFG["TTS_PORT"]))
    return out


@app.post("/api/start")
async def api_start(request: Request):
    body = await request.json()
    return start_worker(body.get("model"), body.get("load_options") or {}, bool(body.get("force")))


def start_worker(model_id: str, load_options: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """Остановить текущую модель и запустить model_id. Используют кнопка «Запустить», автозапуск и замер скорости."""
    try:
        spec = get_model(model_id)
    except KeyError:
        raise HTTPException(404, f"Нет модели {model_id}")
    py = venv_python(spec["group"])
    if py is None:
        raise HTTPException(400, f"Окружение «{spec['group']}» не установлено. Запустите: ./install.sh {model_id}")
    load_opts = {**load_defaults(spec), **(load_options or {})}

    # на Маке работает только одна модель: сначала гасим текущую
    st = get_state()
    if st.get("running"):
        log.info("Останавливаю %s (pid %s)", st.get("model"), st.get("pid"))
        kill_group(st["pid"])
    port = int(CFG["TTS_PORT"])
    if port_busy(port):
        if force:
            for pid in port_pids(port):
                log.warning("Порт %d занят процессом %d — завершаю", port, pid)
                kill_group(pid)
            time.sleep(1)
        if port_busy(port):
            pids = port_pids(port)
            raise HTTPException(409, f"Порт {port} занят другим процессом {pids}. Нажмите «Освободить порт» или смените TTS_PORT в config.env")

    cmd = [str(py), "-m", "server.worker", "--model", model_id, "--host", CFG["TTS_HOST"], "--port", str(port),
           "--api-key", CFG["TTS_API_KEY"], "--load-json", json.dumps(load_opts, ensure_ascii=False)]
    if CFG.get("USE_CAFFEINATE", "1") == "1" and shutil.which("caffeinate"):
        cmd = ["caffeinate", "-i", *cmd]
    out = open(LOGS / "worker.out", "ab")
    out.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start {model_id} {load_opts}\n".encode())
    out.flush()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "TOKENIZERS_PARALLELISM": "false", "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT, start_new_session=True, env=env)
    st = {"model": model_id, "title": spec["title"], "pid": proc.pid, "port": port, "started": time.time(),
          "started_str": time.strftime("%Y-%m-%d %H:%M:%S"), "load_options": load_opts, "cmd": " ".join(cmd)}
    write_json(STATE_FILE, st)
    log.info("Запущен воркер %s pid=%d порт=%d", model_id, proc.pid, port)
    return {"ok": True, "state": st}


@app.post("/api/stop")
def api_stop():
    return stop_worker()


def stop_worker() -> dict[str, Any]:
    st = get_state()
    if st.get("running"):
        kill_group(st["pid"])
        log.info("Воркер %s остановлен", st.get("model"))
    if st:
        st["running"] = False
        st["stopped"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json(STATE_FILE, st)
    return {"ok": True}


@app.post("/api/free-port")
def api_free_port():
    port = int(CFG["TTS_PORT"])
    pids = port_pids(port)
    for pid in pids:
        kill_group(pid)
    return {"killed": pids, "busy": port_busy(port)}


@app.post("/api/download")
async def api_download(request: Request):
    body = await request.json()
    spec = get_model(body.get("model"))
    py = venv_python(spec["group"])
    if py is None:
        raise HTTPException(400, f"Окружение «{spec['group']}» не установлено. Запустите в терминале: ./install.sh {spec['id']}")
    if spec["id"] in _downloads and _downloads[spec["id"]].poll() is None:
        return {"ok": True, "running": True}
    load_opts = {**load_defaults(spec), **(body.get("load_options") or {})}
    out = open(LOGS / "install.log", "ab")
    out.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} download {spec['id']}\n".encode())
    out.flush()
    _downloads[spec["id"]] = subprocess.Popen(
        [str(py), "-m", "server.worker", "--model", spec["id"], "--download-only", "--load-json", json.dumps(load_opts)],
        cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    return {"ok": True, "running": True}


@app.get("/api/downloads")
def api_downloads():
    return {k: ("running" if p.poll() is None else f"exit {p.returncode}") for k, p in _downloads.items()}


# ------------------------------------------------------------------ logs
LOG_SOURCES = {"worker": "worker.log", "worker_raw": "worker.out", "panel": "panel.log", "install": "install.log",
               "requests": "requests.jsonl"}


@app.get("/api/logs")
def api_logs(source: str = "worker", lines: int = 300):
    fn = LOG_SOURCES.get(source)
    if not fn:
        raise HTTPException(404)
    return {"source": source, "lines": tail(LOGS / fn, max(10, min(lines, 5000)))}


@app.get("/api/logs/download")
def api_logs_download(source: str = "worker"):
    fn = LOG_SOURCES.get(source)
    if not fn or not (LOGS / fn).exists():
        raise HTTPException(404)
    return FileResponse(LOGS / fn, filename=fn)


@app.delete("/api/logs")
def api_logs_clear(source: str = "worker"):
    fn = LOG_SOURCES.get(source)
    if fn and (LOGS / fn).exists():
        (LOGS / fn).write_text("")
    return {"ok": True}


# ------------------------------------------------------------------ dictionary / defaults / refs
@app.get("/api/dictionary", response_class=PlainTextResponse)
def api_dict_get():
    return DICT_FILE.read_text(encoding="utf-8") if DICT_FILE.exists() else ""


@app.put("/api/dictionary")
async def api_dict_put(request: Request):
    DICT_FILE.write_text((await request.body()).decode("utf-8"), encoding="utf-8")
    return {"ok": True}


@app.get("/api/defaults/{model_id}")
def api_defaults_get(model_id: str):
    return read_json(DATA / "defaults" / f"{model_id}.json", {})


@app.put("/api/defaults/{model_id}")
async def api_defaults_put(model_id: str, request: Request):
    spec = get_model(model_id)
    data = coerce(spec, await request.json())
    write_json(DATA / "defaults" / f"{model_id}.json", data)
    log.info("Сохранены настройки по умолчанию для %s", model_id)
    return {"ok": True, "saved": data}


@app.delete("/api/defaults/{model_id}")
def api_defaults_delete(model_id: str):
    p = DATA / "defaults" / f"{model_id}.json"
    if p.exists():
        p.unlink()
    return {"ok": True}


@app.get("/api/refs")
def api_refs():
    out = []
    for w in sorted(REFS.glob("*.wav")):
        t = w.with_suffix(".txt")
        out.append({"name": w.stem, "text": t.read_text(encoding="utf-8") if t.exists() else "", "size": w.stat().st_size})
    return out


@app.get("/api/refs/{name}.wav")
def api_ref_audio(name: str):
    p = REFS / f"{Path(name).name}.wav"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/wav")


@app.post("/api/refs")
async def api_ref_upload(name: str = Form(...), text: str = Form(""), file: UploadFile = File(...)):
    safe = "".join(c for c in name if c.isalnum() or c in "-_").strip("-_")
    if not safe:
        raise HTTPException(400, "Имя только из букв, цифр, - и _")
    raw = await file.read()
    dst = REFS / f"{safe}.wav"
    if raw[:4] == b"RIFF":
        dst.write_bytes(raw)
    elif shutil.which("ffmpeg"):
        tmp = REFS / f".{safe}.upload"
        tmp.write_bytes(raw)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-ac", "1", "-ar", "24000", str(dst)], check=True)
        tmp.unlink(missing_ok=True)
    else:
        raise HTTPException(400, "Нужен WAV (или установите ffmpeg: brew install ffmpeg)")
    (REFS / f"{safe}.txt").write_text(text.strip(), encoding="utf-8")
    return {"ok": True, "name": safe}


@app.delete("/api/refs/{name}")
def api_ref_delete(name: str):
    for ext in (".wav", ".txt"):
        (REFS / f"{Path(name).name}{ext}").unlink(missing_ok=True)
    return {"ok": True}


# ------------------------------------------------------------------ normalize preview
@app.post("/api/normalize")
async def api_normalize(request: Request):
    body = await request.json()
    settings = body.get("settings") or {}
    model_id = body.get("model")
    if model_id:
        try:
            settings = coerce(get_model(model_id), settings)
        except KeyError:
            pass
    # в окружении панели нет акцентуаторов: ударения показывает только запущенная модель
    preview_settings = {**settings, "stress": "none"}
    norm, steps = normalize_text(body.get("input", ""), preview_settings)
    stressed = None
    if settings.get("stress", "none") != "none":
        try:
            r = httpx.post(worker_url("/v1/normalize"), json={"input": body.get("input", ""), "settings": settings}, timeout=30)
            stressed = r.json().get("normalized")
        except Exception:
            stressed = None
    return {"normalized": norm, "steps": steps, "stressed": stressed}


# ------------------------------------------------------------------ synthesis proxy
def build_speech_body(p: dict[str, Any]) -> dict[str, Any]:
    settings = p.get("settings") or {}
    transport = p.get("transport", "instructions")
    voice = p.get("voice") or settings.get("speaker") or settings.get("voice") or "alloy"
    body: dict[str, Any] = {"model": p.get("model") or CFG["LITELLM_MODEL_NAME"], "input": p.get("input", ""),
                            "voice": voice, "response_format": p.get("response_format", "wav")}
    if transport in ("instructions", "both"):
        body["instructions"] = "ttslab:" + json.dumps(settings, ensure_ascii=False)
    if transport in ("body", "both"):
        body["settings"] = settings
    if transport == "none":
        pass
    return body


@app.post("/api/litellm-check")
async def api_litellm_check(request: Request):
    """Проверка ключа и поиска модели в LiteLLM: GET /v1/models."""
    p = await request.json()
    base = (p.get("base_url") or CFG["LITELLM_URL"]).rstrip("/")
    url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {p.get('api_key', '')}"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Нет связи с {url}: {e}"}, status_code=502)
    if r.status_code != 200:
        return JSONResponse({"ok": False, "error": f"HTTP {r.status_code}: {r.text[:500]}"}, status_code=502)
    ids = [m.get("id") for m in r.json().get("data", [])]
    want = p.get("model") or CFG["LITELLM_MODEL_NAME"]
    return {"ok": True, "models": ids, "found": want in ids, "want": want}


@app.post("/api/speech")
async def api_speech(request: Request):
    """Синтез через LiteLLM (или напрямую в воркер) от имени браузера: без CORS, с логом и сохранением."""
    p = await request.json()
    mode = p.get("mode", "litellm")
    body = build_speech_body(p)
    if mode == "direct":
        url = worker_url("/v1/audio/speech")
        key = CFG["TTS_API_KEY"]
    else:
        base = (p.get("base_url") or CFG["LITELLM_URL"]).rstrip("/")
        url = base + ("/audio/speech" if base.endswith("/v1") else "/v1/audio/speech")
        key = p.get("api_key") or ""
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as c:
            r = await c.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        log.error("Запрос %s не прошёл: %s", url, e)
        return JSONResponse({"error": f"Нет связи с {url}: {type(e).__name__}: {e}"}, status_code=502)
    latency = time.time() - t0
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("audio"):
        text = r.text[:3000]
        log.warning("%s ответил %s: %s", url, r.status_code, text[:500])
        append_jsonl(LOGS / "requests.jsonl", {"via": f"panel→{mode}", "url": url, "ok": False, "status": r.status_code,
                                               "error": text[:1000], "input": body["input"][:500]})
        return JSONResponse({"error": f"{mode}: HTTP {r.status_code}", "detail": text, "url": url, "sent": body}, status_code=502)

    st = get_state()
    out_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    ext = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/flac": "flac",
           "audio/aac": "aac"}.get(ctype.split(";")[0], "bin")
    (OUTPUTS / f"{out_id}.{ext}").write_bytes(r.content)
    import urllib.parse

    meta = {
        "id": out_id, "file": f"{out_id}.{ext}", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
        "model": r.headers.get("x-tts-model") or st.get("model"), "litellm_model": body["model"],
        "input": body["input"], "settings": p.get("settings") or {},
        "normalized": urllib.parse.unquote(r.headers.get("x-normalized-text", "")) or None,
        "gen_time": float(r.headers["x-gen-time"]) if r.headers.get("x-gen-time") else None,
        "audio_s": float(r.headers["x-audio-duration"]) if r.headers.get("x-audio-duration") else None,
        "latency": round(latency, 3), "bytes": len(r.content), "score": None, "comment": "",
        "timing": json.loads(r.headers["x-timing"]) if r.headers.get("x-timing") else None,
        "load_options": st.get("load_options"),
    }
    write_json(OUTPUTS / f"{out_id}.json", meta)
    hdr = {"X-Output-Id": out_id, "X-Latency": f"{latency:.3f}", "Access-Control-Expose-Headers": "*"}
    for h in ("x-tts-model", "x-gen-time", "x-audio-duration", "x-normalized-text", "x-chunks", "x-format", "x-timing", "x-server-time"):
        if r.headers.get(h):
            hdr[h] = r.headers[h]
    return Response(content=r.content, media_type=ctype, headers=hdr)


# ------------------------------------------------------------------ outputs / ratings
def all_outputs(limit: int = 500) -> list[dict[str, Any]]:
    metas = sorted(OUTPUTS.glob("*.json"), reverse=True)[:limit]
    return [m for m in (read_json(p, None) for p in metas) if m]


@app.get("/api/outputs")
def api_outputs(limit: int = 100):
    return all_outputs(limit)


@app.get("/api/outputs/{out_id}/audio")
def api_output_audio(out_id: str):
    meta = read_json(OUTPUTS / f"{Path(out_id).name}.json", None)
    if not meta:
        raise HTTPException(404)
    return FileResponse(OUTPUTS / meta["file"])


@app.delete("/api/outputs/{out_id}")
def api_output_delete(out_id: str):
    meta = read_json(OUTPUTS / f"{Path(out_id).name}.json", None)
    if meta:
        (OUTPUTS / meta["file"]).unlink(missing_ok=True)
        (OUTPUTS / f"{meta['id']}.json").unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/outputs/{out_id}/rate")
async def api_rate(out_id: str, request: Request):
    p = await request.json()
    path = OUTPUTS / f"{Path(out_id).name}.json"
    meta = read_json(path, None)
    if not meta:
        raise HTTPException(404)
    meta["score"] = p.get("score")
    meta["comment"] = p.get("comment", "")
    meta["tags"] = p.get("tags", [])
    write_json(path, meta)
    append_jsonl(DATA / "ratings.jsonl", {k: meta.get(k) for k in ("id", "model", "score", "comment", "tags", "input", "normalized",
                                                                    "gen_time", "audio_s", "latency", "settings")})
    return {"ok": True}


@app.get("/api/summary")
def api_summary():
    agg: dict[str, dict[str, Any]] = {}
    for m in all_outputs(5000):
        a = agg.setdefault(m.get("model") or "?", {"model": m.get("model") or "?", "n": 0, "rated": 0, "score_sum": 0.0,
                                                  "gen": 0.0, "audio": 0.0, "lat": 0.0, "issues": {}})
        a["n"] += 1
        a["lat"] += m.get("latency") or 0
        a["gen"] += m.get("gen_time") or 0
        a["audio"] += m.get("audio_s") or 0
        if m.get("score"):
            a["rated"] += 1
            a["score_sum"] += float(m["score"])
        for t in m.get("tags") or []:
            a["issues"][t] = a["issues"].get(t, 0) + 1
    out = []
    for a in agg.values():
        out.append({"model": a["model"], "outputs": a["n"], "rated": a["rated"],
                    "avg_score": round(a["score_sum"] / a["rated"], 2) if a["rated"] else None,
                    "avg_latency": round(a["lat"] / a["n"], 2) if a["n"] else None,
                    "rtf": round(a["gen"] / a["audio"], 3) if a["audio"] else None, "issues": a["issues"]})
    return sorted(out, key=lambda x: -(x["avg_score"] or 0))


# ------------------------------------------------------------------ замер скорости
from .bench import BenchRunner, PHRASES, SET_TITLES, list_runs, run_csv, summarize  # noqa: E402

BENCH = BenchRunner(lambda mid, lo: start_worker(mid, lo), get_state, worker_url, CFG["TTS_API_KEY"], log)


@app.get("/bench", response_class=HTMLResponse)
def page_bench():
    return (STATIC / "bench.html").read_text(encoding="utf-8")


@app.get("/api/bench/phrases")
def api_bench_phrases():
    return {"sets": {k: {"title": SET_TITLES[k], "phrases": v, "chars": [len(x) for x in v]} for k, v in PHRASES.items()}}


@app.post("/api/bench/start")
async def api_bench_start(request: Request):
    p = await request.json()
    ids = p.get("models") or []
    if not ids:
        raise HTTPException(400, "Выберите хотя бы одну модель")
    models = []
    for mid in ids:
        try:
            spec = get_model(mid)
        except KeyError:
            continue
        if venv_python(spec["group"]) is None:
            continue
        models.append({"id": mid, "title": spec["title"], "load_options": (p.get("load_options") or {}).get(mid) or {}})
    if not models:
        raise HTTPException(400, "Ни одна из выбранных моделей не установлена")
    try:
        run = BENCH.start(models, p.get("sets") or ["short", "medium"], int(p.get("repeats") or 3), bool(p.get("restore", True)),
                          float(p.get("timeout") or 1800))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "id": run["id"]}


@app.post("/api/bench/stop")
def api_bench_stop():
    BENCH.stop()
    return {"ok": True}


@app.get("/api/bench/status")
def api_bench_status():
    r = BENCH.run
    if not r:
        return {"running": False}
    return {"running": BENCH.busy(), "id": r["id"], "status": r["status"], "progress": r["progress"], "current": r["current"],
            "log": r["log"][-60:], "summary": summarize(r)}


@app.get("/api/bench/runs")
def api_bench_runs():
    return list_runs()


@app.get("/api/bench/runs/{run_id}")
def api_bench_run(run_id: str):
    from .bench import BENCH_DIR

    r = read_json(BENCH_DIR / f"{Path(run_id).name}.json", None)
    if not r:
        raise HTTPException(404)
    r["summary"] = summarize(r)
    return r


@app.get("/api/bench/runs/{run_id}/csv")
def api_bench_csv(run_id: str):
    from .bench import BENCH_DIR

    r = read_json(BENCH_DIR / f"{Path(run_id).name}.json", None)
    if not r:
        raise HTTPException(404)
    return Response(run_csv(r), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=tts_speed_{r['id']}.csv"})


@app.delete("/api/bench/runs/{run_id}")
def api_bench_delete(run_id: str):
    from .bench import BENCH_DIR

    (BENCH_DIR / f"{Path(run_id).name}.json").unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/ratings.csv")
def api_ratings_csv():
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["время", "модель", "оценка", "проблемы", "комментарий", "текст", "что прочитала модель", "синтез, с", "аудио, с",
                "задержка, с", "настройки"])
    for m in all_outputs(10000):
        w.writerow([m.get("ts"), m.get("model"), m.get("score") or "", ",".join(m.get("tags") or []), m.get("comment", ""),
                    m.get("input"), m.get("normalized") or "", m.get("gen_time") or "", m.get("audio_s") or "", m.get("latency") or "",
                    json.dumps(m.get("settings"), ensure_ascii=False)])
    return Response(("﻿" + buf.getvalue()).encode("utf-8"), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tts_ratings.csv"})


def main():
    import uvicorn

    log.info("Панель: http://%s:%s  (тестирование: /, управление: /admin)", CFG["PANEL_HOST"], CFG["PANEL_PORT"])
    for ip in lan_ips():
        log.info("  в сети: http://%s:%s", ip, CFG["PANEL_PORT"])
    uvicorn.run(app, host=CFG["PANEL_HOST"], port=int(CFG["PANEL_PORT"]), log_level="warning")


if __name__ == "__main__":
    main()
