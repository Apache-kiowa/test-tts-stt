"""Замер скорости ответа моделей.

Модели запускаются по очереди (на машине всегда одна модель), для каждой:
  1. время загрузки до готовности (включая скачивание весов, если их нет, и прогрев);
  2. первый запрос после загрузки («холодный»);
  3. короткие / средние / длинные фразы по N раз.
Запросы идут прямо в API модели — как у боевых клиентов, с сохранёнными «настройками для API».
"""
from __future__ import annotations

import csv
import io
import json
import os
import platform
import shutil
import statistics
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

import httpx

from .common import DATA, read_json, write_json

BENCH_DIR = DATA / "bench"
BENCH_DIR.mkdir(parents=True, exist_ok=True)

PHRASES: dict[str, list[str]] = {
    "short": [
        "Записываю вас на 17:00. Верно?",
        "Врач Дорохов С. А. принимает в кабинете 204.",
        "Здравствуйте! Чем могу помочь?",
    ],
    "medium": [
        "Свободное время на завтра: 09:00, 10:30 и 14:05. Терапевт Кузнецова Е.В. принимает в кабинете № 204, "
        "приходите за десять минут до начала приёма.",
        "Вы записаны к стоматологу на четверг, 12 ноября, в 11:20. Возьмите с собой паспорт и полис ОМС, "
        "а если планы изменятся, перезвоните нам.",
    ],
    "long": [
        "Здравствуйте! Вы позвонили в медицинский центр «Здоровье». Сейчас я помогу вам записаться на приём. "
        "На ближайшую неделю свободны терапевт Кузнецова Елена Викторовна, кардиолог Дорохов Сергей Анатольевич "
        "и невролог Петрова Анна Сергеевна. У терапевта есть окна в понедельник в 09:00 и 16:45, у кардиолога — "
        "во вторник в 11:20 и в четверг в 14:05, у невролога — в среду в 10:30. Стоимость первичной консультации "
        "1500 руб., повторной — 1200 руб. Анализы сдаются натощак с 07:30 до 10:00 в процедурном кабинете на втором "
        "этаже. Если планы изменятся, перезвоните нам по номеру +7 (495) 123-45-07 или напишите в чат на сайте. "
        "Скажите, пожалуйста, к какому врачу и на какое время вас записать?",
    ],
}
SET_TITLES = {"short": "короткие", "medium": "средние", "long": "длинные"}


def system_info() -> dict[str, Any]:
    info: dict[str, Any] = {"os": f"{platform.system()} {platform.release()}", "arch": platform.machine(),
                            "python": platform.python_version(), "host": platform.node(), "cpus": os.cpu_count()}
    try:
        if platform.system() == "Darwin":
            cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=3).stdout.strip()
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3).stdout.strip() or 0)
            info["cpu"] = cpu
            info["ram_gb"] = round(mem / 2**30)
        else:
            with open("/proc/cpuinfo") as f:
                info["cpu"] = next((ln.split(":", 1)[1].strip() for ln in f if ln.startswith("model name")), platform.processor())
            with open("/proc/meminfo") as f:
                info["ram_gb"] = round(int(next(ln.split()[1] for ln in f if ln.startswith("MemTotal"))) / 2**20)
    except Exception:
        pass
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            info["gpu"] = out.replace("\n", "; ")
        except Exception:
            pass
    return info


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 3)


def summarize(run: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for mid in run.get("models", []):
        rows = [r for r in run.get("results", []) if r["model"] == mid]
        meta = run.get("model_meta", {}).get(mid, {})
        ok = [r for r in rows if r.get("ok") and not r.get("first")]
        first = next((r for r in rows if r.get("first") and r.get("ok")), None)
        item: dict[str, Any] = {
            "model": mid, "title": meta.get("title", mid), "status": meta.get("status"), "error": meta.get("error"),
            "load_s": meta.get("load_s"), "weights_s": meta.get("weights_s"), "warmup_s": meta.get("warmup_s"),
            "device": meta.get("device"), "memory": meta.get("memory", {}),
            "first_s": first["total"] if first else None, "requests": len(ok), "errors": sum(1 for r in rows if not r.get("ok")),
        }
        for st in PHRASES:
            vals = [r["total"] for r in ok if r["set"] == st]
            item[f"{st}_median"] = pct(vals, 0.5)
            item[f"{st}_p90"] = pct(vals, 0.9)
        totals = [r["total"] for r in ok]
        item["median"] = pct(totals, 0.5)
        item["p90"] = pct(totals, 0.9)
        rtf = [r["rtf"] for r in ok if r.get("rtf") is not None]
        item["rtf_median"] = pct(rtf, 0.5)
        synth = sum(r.get("synth") or 0 for r in ok)
        item["chars_per_s"] = round(sum(r["chars"] for r in ok) / synth, 1) if synth else None
        ovh = [r["total"] - (r.get("synth") or 0) for r in ok if r.get("synth") is not None]
        item["overhead_median"] = pct(ovh, 0.5)
        fc = [r["first_chunk"] for r in ok if r.get("first_chunk") is not None]
        item["first_chunk_median"] = pct(fc, 0.5)
        out.append(item)
    return out


class BenchRunner:
    def __init__(self, start_worker: Callable, get_state: Callable, worker_url: Callable[[str], str], api_key: str, log):
        self.start_worker = start_worker
        self.get_state = get_state
        self.worker_url = worker_url
        self.api_key = api_key
        self.log = log
        self.run: dict[str, Any] | None = None
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()

    # ------------------------------------------------------------ helpers
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _say(self, text: str) -> None:
        assert self.run is not None
        line = f"{time.strftime('%H:%M:%S')}  {text}"
        self.run["log"].append(line)
        self.run["log"] = self.run["log"][-400:]
        self.log.info("Замер: %s", text)

    def _save(self) -> None:
        if self.run:
            write_json(BENCH_DIR / f"{self.run['id']}.json", self.run)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key and self.api_key.lower() != "none" else {}

    def _wait_ready(self, timeout: float) -> dict[str, Any]:
        t0 = time.time()
        last = {}
        while time.time() - t0 < timeout and not self.stop_flag.is_set():
            try:
                last = httpx.get(self.worker_url("/health"), timeout=3).json()
                if last.get("status") in ("ready", "error"):
                    return last
            except Exception:
                pass
            time.sleep(1)
        last.setdefault("status", "timeout")
        return last

    def _request(self, client: httpx.Client, text: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        r = client.post(self.worker_url("/v1/audio/speech"), headers=self._headers(),
                        json={"model": "bench", "input": text, "voice": "alloy", "response_format": "wav"})
        total = time.perf_counter() - t0
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            return {"ok": False, "total": round(total, 3), "error": msg[:500]}
        timing = {}
        try:
            timing = json.loads(r.headers.get("x-timing", "{}"))
        except Exception:
            pass
        audio = float(r.headers.get("x-audio-duration") or 0)
        synth = timing.get("synth")
        return {
            "ok": True, "total": round(total, 3), "server": timing.get("server"), "synth": synth,
            "normalize": timing.get("normalize"), "encode": timing.get("encode"), "queue": timing.get("queue"),
            "first_chunk": timing.get("first_chunk"), "audio_s": audio,
            "rtf": round(synth / audio, 3) if synth and audio else None,
            "chunks": int(r.headers.get("x-chunks") or 1), "bytes": len(r.content),
        }

    # ------------------------------------------------------------ main loop
    def start(self, models: list[dict[str, Any]], sets: list[str], repeats: int, restore: bool, timeout: float) -> dict[str, Any]:
        if self.busy():
            raise RuntimeError("Замер уже идёт")
        sets = [s for s in sets if s in PHRASES] or ["short"]
        repeats = max(1, min(int(repeats), 20))
        st = self.get_state()
        self.run = {
            "id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "finished": None, "status": "running", "system": system_info(), "models": [m["id"] for m in models],
            "sets": sets, "repeats": repeats, "model_meta": {}, "results": [], "log": [], "current": "",
            "progress": {"done": 0, "total": len(models) * (1 + repeats * sum(len(PHRASES[s]) for s in sets))},
            "restore": st.get("model") if restore and st.get("running") else None,
            "restore_options": st.get("load_options") if restore else None,
        }
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._loop, args=(models, timeout), daemon=True)
        self.thread.start()
        return self.run

    def _loop(self, models: list[dict[str, Any]], timeout: float) -> None:
        run = self.run
        assert run is not None
        try:
            with httpx.Client(timeout=httpx.Timeout(1800, connect=10)) as client:
                for m in models:
                    if self.stop_flag.is_set():
                        break
                    mid = m["id"]
                    meta: dict[str, Any] = {"title": m.get("title", mid)}
                    run["model_meta"][mid] = meta
                    run["current"] = f"{meta['title']}: загрузка"
                    self._say(f"Запускаю {meta['title']}")
                    t0 = time.time()
                    try:
                        self.start_worker(mid, m.get("load_options") or {})
                    except Exception as e:
                        meta.update(status="error", error=getattr(e, "detail", str(e)))
                        self._say(f"{meta['title']}: не запустилась — {meta['error']}")
                        run["progress"]["done"] += 1 + run["repeats"] * sum(len(PHRASES[s]) for s in run["sets"])
                        self._save()
                        continue
                    h = self._wait_ready(timeout)
                    meta.update(load_s=round(time.time() - t0, 1), weights_s=h.get("load_time_s"), warmup_s=h.get("warmup_s"),
                                device=h.get("device"), status=h.get("status"))
                    if h.get("status") != "ready":
                        meta["error"] = h.get("error") or h.get("status")
                        self._say(f"{meta['title']}: не готова ({meta['error']})")
                        run["progress"]["done"] += 1 + run["repeats"] * sum(len(PHRASES[s]) for s in run["sets"])
                        self._save()
                        continue
                    self._say(f"{meta['title']}: готова за {meta['load_s']} с (веса {meta['weights_s']} с, прогрев {meta['warmup_s']} с)")

                    plan = [("short", 0, PHRASES["short"][0], 0, True)]
                    for rep in range(run["repeats"]):
                        for st in run["sets"]:
                            for i, ph in enumerate(PHRASES[st]):
                                plan.append((st, i, ph, rep, False))
                    for st, i, ph, rep, first in plan:
                        if self.stop_flag.is_set():
                            break
                        run["current"] = f"{meta['title']}: {'первый запрос' if first else SET_TITLES[st]} {i + 1}, повтор {rep + 1}"
                        res = self._request(client, ph)
                        res.update(model=mid, set=st, phrase=i, rep=rep, first=first, chars=len(ph))
                        if res.get("ok") and res.get("synth"):
                            res["chars_per_s"] = round(len(ph) / res["synth"], 1)
                        run["results"].append(res)
                        run["progress"]["done"] += 1
                        if not res["ok"]:
                            self._say(f"{meta['title']}: ошибка — {res['error']}")
                        elif first:
                            self._say(f"{meta['title']}: первый запрос {res['total']} с")
                        self._save()
                    try:
                        h = httpx.get(self.worker_url("/health"), timeout=3).json()
                        meta["memory"] = h.get("memory", {})
                    except Exception:
                        pass
                    s = next((x for x in summarize(run) if x["model"] == mid), {})
                    self._say(f"{meta['title']}: медиана ответа {s.get('median')} с, RTF {s.get('rtf_median')}")
                    self._save()
            if run.get("restore") and not self.stop_flag.is_set():
                self._say(f"Возвращаю модель {run['restore']}")
                try:
                    self.start_worker(run["restore"], run.get("restore_options") or {})
                except Exception as e:
                    self._say(f"Не удалось вернуть модель: {e}")
            run["status"] = "stopped" if self.stop_flag.is_set() else "done"
        except Exception as e:
            run["status"] = "error"
            self._say(f"Замер прерван ошибкой: {type(e).__name__}: {e}")
        finally:
            run["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            run["current"] = ""
            self._say("Замер окончен" if run["status"] == "done" else f"Замер: {run['status']}")
            self._save()

    def stop(self) -> None:
        self.stop_flag.set()


def list_runs() -> list[dict[str, Any]]:
    out = []
    for p in sorted(BENCH_DIR.glob("*.json"), reverse=True):
        r = read_json(p, None)
        if r:
            out.append({k: r.get(k) for k in ("id", "started", "finished", "status", "system", "models", "sets", "repeats")})
    return out


def run_csv(run: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["модель", "набор", "фраза", "повтор", "первый", "символов", "ответ, с", "сервер, с", "синтез, с", "нормализация, с",
                "кодирование, с", "первый кусок, с", "аудио, с", "RTF", "симв/с", "ошибка"])
    for r in run.get("results", []):
        w.writerow([r["model"], SET_TITLES.get(r["set"], r["set"]), r["phrase"] + 1, r["rep"] + 1, "да" if r.get("first") else "",
                    r["chars"], r.get("total"), r.get("server"), r.get("synth"), r.get("normalize"), r.get("encode"),
                    r.get("first_chunk"), r.get("audio_s"), r.get("rtf"), r.get("chars_per_s"), r.get("error", "")])
    buf.write("\n")
    w.writerow(["Итоги"])
    w.writerow(["модель", "загрузка, с", "веса, с", "прогрев, с", "первый запрос, с", "медиана, с", "p90, с", "короткие, с",
                "средние, с", "длинные, с", "RTF", "симв/с", "устройство", "память", "ошибок"])
    for s in summarize(run):
        w.writerow([s["title"], s["load_s"], s["weights_s"], s["warmup_s"], s["first_s"], s["median"], s["p90"], s["short_median"],
                    s["medium_median"], s["long_median"], s["rtf_median"], s["chars_per_s"], s["device"],
                    json.dumps(s["memory"], ensure_ascii=False), s["errors"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")
