"""Создаёт голоса-образцы refs/default_female и refs/default_male через Silero.

Они нужны моделям, которые без образца не говорят (ESpeech, F5): так вы получаете
готовый голос без записи своего. Свои образцы можно загрузить в панели.
"""
from __future__ import annotations

import logging

from . import audio as A
from .common import REFS, setup_logging
from .registry import get_model, load_defaults

VOICES = {
    "default_female": ("kseniya", "Здравствуйте! Вы позвонили в медицинский центр. Меня зовут Ксения, я помогу вам записаться на приём к врачу."),
    "default_male": ("eugene", "Добрый день! Вы позвонили в медицинский центр. Я помогу подобрать удобное время и записать вас к специалисту."),
}


def main():
    log = setup_logging("ttslab.refs", "install.log")
    from .backends.silero import SileroBackend

    spec = get_model("silero-v5")
    b = SileroBackend(spec, load_defaults(spec), logging.getLogger("ttslab.refs"))
    b.load()
    for name, (speaker, text) in VOICES.items():
        wav = REFS / f"{name}.wav"
        if wav.exists():
            log.info("Образец %s уже есть", name)
            continue
        audio, sr = b.synth(text, {"speaker": speaker, "sample_rate": 24000, "rate": "medium", "pitch": "medium"})
        wav.write_bytes(A.to_wav_bytes(A.peak_normalize(audio), sr))
        (REFS / f"{name}.txt").write_text(text, encoding="utf-8")
        log.info("Создан образец %s (%s, %.1f с)", name, speaker, len(audio) / sr)


if __name__ == "__main__":
    main()
