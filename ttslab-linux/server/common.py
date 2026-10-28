from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"
REFS = ROOT / "refs"
VENVS = ROOT / "venvs"
for d in (LOGS, DATA, OUTPUTS, REFS, DATA / "defaults"):
    d.mkdir(parents=True, exist_ok=True)

CONFIG_DEFAULTS = {
    "PANEL_HOST": "0.0.0.0",
    "PANEL_PORT": "8800",
    "TTS_HOST": "0.0.0.0",
    "TTS_PORT": "8890",
    "TTS_API_KEY": "",
    "MODEL_ALIAS": "local-tts",
    "PANEL_PASSWORD": "",
    "AUTOSTART_LAST": "1",
}


def load_config() -> dict[str, str]:
    cfg = dict(CONFIG_DEFAULTS)
    f = ROOT / "config.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in cfg:
        if k in os.environ:
            cfg[k] = os.environ[k]
    return cfg


def setup_logging(name: str, filename: str, level: int = logging.INFO) -> logging.Logger:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "").endswith(filename) for h in root.handlers):
        fh = RotatingFileHandler(LOGS / filename, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "urllib3", "numba", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger(name)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    record.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        block, data = 8192, b""
        while size > 0 and data.count(b"\n") <= lines:
            step = min(block, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
    return data.decode("utf-8", "replace").splitlines()[-lines:]
