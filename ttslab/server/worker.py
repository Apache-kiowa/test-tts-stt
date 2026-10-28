"""OpenAI-совместимый TTS-сервер для одной модели.

Запуск (это делает панель):
    venvs/<группа>/bin/python -m server.worker --model silero-v5 --port 8890

Эндпоинты:
    GET  /health                 состояние (loading / ready / error)
    GET  /v1/models              список моделей в формате OpenAI
    POST /v1/audio/speech        синтез (как у OpenAI) — сюда ходит LiteLLM
    POST /v1/normalize           посмотреть, что уйдёт в модель после нормализации
    GET  /v1/info                описание модели и настроек
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import traceback
import urllib.parse
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .common import DATA, LOGS, append_jsonl, load_config, read_json, setup_logging
from .registry import coerce, defaults, get_model, load_defaults

OPENAI_VOICES = {"alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer", "verse"}


class State:
    model_id = ""
    spec: dict[str, Any] = {}
    load_opts: dict[str, Any] = {}
    backend = None
    status = "starting"
    error = ""
    started = time.time()
    loaded_at = 0.0
    warmup_s = None
    requests = 0
    lock = threading.Lock()


S = State()


def saved_defaults(model_id: str) -> dict[str, Any]:
    """Настройки «по умолчанию для API», сохранённые с фронта (data/defaults/<id>.json)."""
    return read_json(DATA / "defaults" / f"{model_id}.json", {})


def merge_settings(body: dict[str, Any]) -> dict[str, Any]:
    spec = S.spec
    keys = {f["key"] for f in spec["settings"] + spec["common"]}
    raw: dict[str, Any] = dict(saved_defaults(S.model_id))

    # 1) лишние поля тела запроса (LiteLLM пропускает их через extra_body)
    for k, v in body.items():
        if k in keys:
            raw[k] = v
    # 2) явный словарь settings
    if isinstance(body.get("settings"), dict):
        raw.update(body["settings"])
    # 3) instructions: JSON с настройками или текст-инструкция
    instr = body.get("instructions")
    if isinstance(instr, str) and instr.strip():
        st = instr.strip()
        if st.startswith("ttslab:"):
            st = st[len("ttslab:"):].strip()
        if st.startswith("{"):
            try:
                raw.update(json.loads(st))
            except json.JSONDecodeError:
                pass
        elif "instruct" in keys:
            raw["instruct"] = instr
    # 4) voice: если такой голос у модели есть
    voice = body.get("voice")
    if isinstance(voice, str) and voice and voice.lower() not in OPENAI_VOICES:
        for key in ("speaker", "voice"):
            f = next((f for f in spec["settings"] if f["key"] == key), None)
            if f and voice in [o["value"] for o in f.get("options", [])]:
                raw[key] = voice
        if voice.startswith("ref:"):
            raw["ref_voice"] = voice[4:]
    # 5) speed
    if body.get("speed") not in (None, 1, 1.0) and "speed" in keys:
        raw["speed"] = body["speed"]
    return coerce(spec, raw)


def synthesize(text: str, s: dict[str, Any], log) -> dict[str, Any]:
    import numpy as np

    from . import audio as A
    from .normalizer import normalize_text, split_chunks

    t_norm0 = time.perf_counter()
    norm, steps = normalize_text(text, s)
    chunks = split_chunks(norm, int(s.get("max_chunk_chars", 400)))
    t_norm = time.perf_counter() - t_norm0
    if not chunks:
        raise ValueError("Пустой текст после нормализации")
    parts, per_chunk = [], []
    t_wait0 = time.perf_counter()
    with S.lock:  # модель одна: параллельные запросы ждут очереди
        t_wait = time.perf_counter() - t_wait0
        t0 = time.perf_counter()
        S.backend.seed_all(s.get("seed"))
        for i, ch in enumerate(chunks):
            t1 = time.perf_counter()
            a, sr = S.backend.synth(ch, s)
            dt = time.perf_counter() - t1
            per_chunk.append({"chars": len(ch), "synth": round(dt, 3), "audio": round(len(a) / sr, 3)})
            log.info("кусок %d/%d: %d симв. → %.2f с аудио за %.2f с", i + 1, len(chunks), len(ch), len(a) / sr, dt)
            parts.append((a, sr))
        gen = time.perf_counter() - t0
    t_post0 = time.perf_counter()
    wav, sr = A.join(parts, int(s.get("pause_ms", 200)))
    if s.get("peak_normalize", True):
        wav = A.peak_normalize(wav)
    t_post = time.perf_counter() - t_post0
    return {"audio": wav.astype(np.float32), "sr": sr, "normalized": norm, "steps": steps, "chunks": len(chunks), "gen_time": gen,
            "duration": len(wav) / sr if sr else 0,
            "timing": {"normalize": round(t_norm, 4), "queue": round(t_wait, 4), "synth": round(gen, 4),
                       "first_chunk": per_chunk[0]["synth"] if per_chunk else None, "join": round(t_post, 4), "chunks": per_chunk}}


def mem_stats() -> dict[str, Any]:
    """Память процесса и ускорителя (МБ)."""
    import resource
    import sys

    out: dict[str, Any] = {}
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out["rss_peak_mb"] = round(rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024)
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                out["cuda_peak_mb"] = round(torch.cuda.max_memory_allocated() / 2**20)
                out["cuda_now_mb"] = round(torch.cuda.memory_allocated() / 2**20)
        except Exception:
            pass
    mx = sys.modules.get("mlx.core")
    if mx is not None:
        for fn in ("get_peak_memory", "get_active_memory"):
            f = getattr(mx, fn, None) or getattr(getattr(mx, "metal", None), fn, None)
            if f:
                try:
                    out["mlx_" + fn.split("_")[1] + "_mb"] = round(f() / 2**20)
                except Exception:
                    pass
    return out


def create_app(api_key: str, log):
    from . import audio as A

    app = FastAPI(title=f"TTS Lab worker: {S.model_id}")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-TTS-Model", "X-Gen-Time", "X-Audio-Duration", "X-Normalized-Text", "X-Chunks", "X-Format",
                                       "X-Server-Time", "X-Timing"])

    from fastapi.exceptions import HTTPException as _HE

    @app.exception_handler(_HE)
    async def openai_errors(_req: Request, exc: _HE):
        # формат ошибок как у OpenAI: {"error": {...}} — так LiteLLM покажет понятный текст
        d = exc.detail
        body = d if isinstance(d, dict) and "error" in d else {"error": {"message": str(d), "type": "invalid_request_error"}}
        return JSONResponse(body, status_code=exc.status_code)

    def check_key(req: Request):
        if not api_key or api_key.lower() == "none":
            return
        auth = req.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else req.headers.get("x-api-key", "")
        if token != api_key:
            raise HTTPException(401, detail={"error": {"message": "Invalid API key", "type": "invalid_request_error"}})

    @app.get("/health")
    def health():
        return {"status": S.status, "model": S.model_id, "title": S.spec.get("title"), "error": S.error,
                "uptime_s": round(time.time() - S.started), "load_time_s": round(S.loaded_at - S.started, 1) if S.loaded_at else None, "warmup_s": S.warmup_s,
                "requests": S.requests, "load_options": S.load_opts, "pid": os.getpid(),
                "device": str(getattr(S.backend, "device", "mlx" if S.spec.get("group") == "mlx" else "cpu")) if S.backend else None,
                "memory": mem_stats()}

    @app.get("/v1/models")
    def models(request: Request):
        check_key(request)
        cfg = load_config()
        names = [S.model_id, cfg.get("LITELLM_MODEL_NAME", "mac-tts"), "tts-1"]
        return {"object": "list", "data": [{"id": n, "object": "model", "owned_by": "ttslab", "created": int(S.started)} for n in dict.fromkeys(names)]}

    @app.get("/v1/info")
    def info():
        extra = {}
        if S.backend is not None and S.status == "ready":
            try:
                extra = S.backend.info()
            except Exception:
                pass
        return {"model": S.model_id, "spec": S.spec, "defaults": defaults(S.spec), "saved_defaults": saved_defaults(S.model_id),
                "status": S.status, "info": extra, "ffmpeg": A.has_ffmpeg()}

    @app.post("/v1/normalize")
    async def normalize(request: Request):
        from .normalizer import normalize_text

        body = await request.json()
        s = merge_settings(body)
        norm, steps = normalize_text(body.get("input", ""), s)
        return {"normalized": norm, "steps": steps}

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        check_key(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, detail={"error": {"message": "Ожидается JSON"}})
        text = body.get("input") or ""
        if not text.strip():
            raise HTTPException(400, detail={"error": {"message": "Поле input пустое"}})
        if S.status != "ready":
            raise HTTPException(503, detail={"error": {"message": f"Модель {S.model_id} ещё не готова: {S.status} {S.error}"}})
        s = merge_settings(body)
        fmt = body.get("response_format", "mp3")
        rec: dict[str, Any] = {"model": S.model_id, "client": request.client.host if request.client else "", "chars": len(text),
                               "input": text[:2000], "format": fmt, "settings": s,
                               "via": request.headers.get("user-agent", "")[:80]}
        S.requests += 1
        t_req0 = time.perf_counter()
        try:
            from starlette.concurrency import run_in_threadpool

            res = await run_in_threadpool(synthesize, text, s, log)
        except Exception as e:
            log.error("Ошибка синтеза: %s\n%s", e, traceback.format_exc())
            rec.update(ok=False, error=str(e))
            append_jsonl(LOGS / "requests.jsonl", rec)
            return JSONResponse({"error": {"message": f"{type(e).__name__}: {e}", "type": "server_error"}}, status_code=500)
        t_enc0 = time.perf_counter()
        data, real_fmt, ctype = A.encode(res["audio"], res["sr"], fmt)
        timing = {**res["timing"], "encode": round(time.perf_counter() - t_enc0, 4)}
        server_time = time.perf_counter() - t_req0
        timing["server"] = round(server_time, 4)
        rec.update(ok=True, timing=timing, server_time=round(server_time, 3), normalized=res["normalized"][:2000], gen_time=round(res["gen_time"], 3),
                   audio_s=round(res["duration"], 2), chunks=res["chunks"], rtf=round(res["gen_time"] / max(res["duration"], 1e-3), 3),
                   out_format=real_fmt, bytes=len(data))
        append_jsonl(LOGS / "requests.jsonl", rec)
        log.info("OK %s: %d симв., аудио %.1f с, синтез %.2f с (RTF %.2f), %s", S.model_id, len(text), res["duration"], res["gen_time"],
                 rec["rtf"], real_fmt)
        headers = {
            "X-TTS-Model": S.model_id,
            "X-Gen-Time": f"{res['gen_time']:.3f}",
            "X-Audio-Duration": f"{res['duration']:.2f}",
            "X-Chunks": str(res["chunks"]),
            "X-Format": real_fmt,
            "X-Server-Time": f"{server_time:.3f}",
            "X-Timing": json.dumps({k: v for k, v in timing.items() if k != "chunks"}),
            "X-Normalized-Text": urllib.parse.quote(res["normalized"][:3000]),
        }
        return Response(content=data, media_type=ctype, headers=headers)

    return app


def load_in_background(log):
    from .backends.base import get_backend_class

    try:
        S.status = "loading"
        log.info("Загружаю модель %s с параметрами %s", S.model_id, S.load_opts)
        cls = get_backend_class(S.model_id)
        S.backend = cls(S.spec, S.load_opts, log)
        S.backend.load()
        S.loaded_at = time.time()
        # прогрев: первый вызов на MPS/MLX всегда медленный
        try:
            from .registry import defaults as d

            # прогрев нормализатора и акцентуатора: иначе первый запрос ждёт загрузку словарей (~0.5 с и больше)
            from .normalizer import normalize_text

            tn = time.perf_counter()
            normalize_text("Запись на 17:00, врач Иванов И. И., кабинет № 204, 1500 руб., 05.11.2026.", {**d(S.spec), **saved_defaults(S.model_id)})
            log.info("Нормализатор прогрет за %.2f с", time.perf_counter() - tn)
            tw = time.perf_counter()
            S.backend.synth("Проверка связи.", {**d(S.spec), **saved_defaults(S.model_id)})
            S.warmup_s = round(time.perf_counter() - tw, 2)
            log.info("Прогрев занял %.2f с", S.warmup_s)
        except Exception as e:
            log.warning("Прогрев не удался (это не критично): %s", e)
        S.status = "ready"
        log.info("Модель %s готова за %.1f с", S.model_id, time.time() - S.started)
    except Exception as e:
        S.status = "error"
        S.error = f"{type(e).__name__}: {e}"
        log.error("Не удалось загрузить модель: %s\n%s", e, traceback.format_exc())


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--host", default=cfg["TTS_HOST"])
    ap.add_argument("--port", type=int, default=int(cfg["TTS_PORT"]))
    ap.add_argument("--api-key", default=cfg["TTS_API_KEY"])
    ap.add_argument("--load-json", default="{}", help="параметры загрузки JSON")
    ap.add_argument("--download-only", action="store_true")
    a = ap.parse_args()

    log = setup_logging("ttslab.worker", "worker.log")
    S.model_id = a.model
    S.spec = get_model(a.model)
    S.load_opts = {**load_defaults(S.spec), **json.loads(a.load_json or "{}")}

    if a.download_only:
        from .backends.base import get_backend_class

        log.info("Скачиваю веса %s %s", a.model, S.load_opts)
        b = get_backend_class(a.model)(S.spec, S.load_opts, log)
        b.download()
        inst = read_json(DATA / "installed.json", {})
        inst[a.model] = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "load_options": S.load_opts}
        from .common import write_json

        write_json(DATA / "installed.json", inst)
        log.info("Готово: %s", a.model)
        return

    import uvicorn

    threading.Thread(target=load_in_background, args=(log,), daemon=True).start()
    app = create_app(a.api_key, log)
    log.info("TTS API для %s слушает http://%s:%d/v1 (ключ: %s)", a.model, a.host, a.port, "задан" if a.api_key else "нет")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
