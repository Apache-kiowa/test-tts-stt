"""Список моделей, их группы окружений, настройки для фронта.

Файл без тяжёлых зависимостей: его читает и панель управления, и воркер.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def sel(key, label, options, default, help="", group="model"):
    opts = [o if isinstance(o, dict) else {"value": o, "label": str(o)} for o in options]
    return {"key": key, "label": label, "type": "select", "options": opts, "default": default, "help": help, "group": group}


def num(key, label, default, mn=None, mx=None, step=None, help="", group="model"):
    return {"key": key, "label": label, "type": "number", "default": default, "min": mn, "max": mx, "step": step, "help": help, "group": group}


def flag(key, label, default, help="", group="model"):
    return {"key": key, "label": label, "type": "bool", "default": default, "help": help, "group": group}


def text(key, label, default="", help="", group="model", multiline=False):
    return {"key": key, "label": label, "type": "textarea" if multiline else "text", "default": default, "help": help, "group": group}


def ref(key="ref_voice", label="Голос-образец", default="none", help="", group="model"):
    # options подставляются на фронте из списка refs/*.wav
    return {"key": key, "label": label, "type": "ref", "default": default, "help": help, "group": group}


def common(stress="none", stress_choices=("none", "silero", "ruaccent"), chunk=400, pause=200):
    names = {"none": "нет", "silero": "silero-stress (+)", "ruaccent": "RUAccent (+)"}
    return [
        flag("normalize", "Нормализация текста", True, "Время, числа, инициалы, сокращения, телефоны → слова", "text"),
        sel("time_style", "Время 17:00", [
            {"value": "short", "label": "семнадцать ноль ноль"},
            {"value": "full", "label": "семнадцать часов"},
            {"value": "hyphen", "label": "семнадцать ноль-ноль"},
            {"value": "off", "label": "не трогать"},
        ], "short", group="text"),
        sel("initials", "Инициалы «Дорохов С. А.»", [
            {"value": "letters", "label": "буквами: «Дорохов эс а»"},
            {"value": "drop", "label": "убрать: «Дорохов»"},
            {"value": "keep", "label": "оставить как есть"},
        ], "letters", group="text"),
        flag("use_ru_normalizr", "ru-normalizr (даты, телефоны, сокращения)", True, group="text"),
        flag("use_dictionary", "Свой словарь произношений", True, "Файл data/dictionary.txt, правится в панели", "text"),
        sel("stress", "Ударения", [{"value": s, "label": names[s]} for s in stress_choices], stress,
            "Ставит «+» перед ударной гласной. Включайте только для моделей, которые это понимают", "text"),
        num("max_chunk_chars", "Макс. символов в куске", chunk, 50, 2000, 10, "Длинный текст режется по предложениям", "text"),
        num("pause_ms", "Пауза между кусками, мс", pause, 0, 2000, 10, group="text"),
        flag("peak_normalize", "Выровнять громкость", True, group="text"),
        num("seed", "Seed (-1 = случайно)", -1, -1, 2**31 - 1, 1, "Одинаковый seed — одинаковый результат", "text"),
        text("extra_json", "Доп. параметры (JSON)", "{}", "Передаются в вызов модели как есть", "text", multiline=True),
    ]


DEV = lambda default="auto": sel("device", "Устройство", ["auto", "cuda", "cpu"], default, "auto — видеокарта NVIDIA, если есть, иначе процессор")

MODELS: list[dict[str, Any]] = [
    {
        "id": "silero-v5",
        "title": "Silero v5 (ru)",
        "group": "silero",
        "rank": 1,
        "license": "CC BY-NC-SA 4.0 — только некоммерческое",
        "size": "~150 МБ",
        "gpu": "не нужна",
        "summary": "Родная русская модель: 5 готовых голосов, ударения и омографы встроены, очень быстрая на CPU.",
        "load_options": [
            sel("version", "Версия", ["v5_6_ru", "v5_5_ru", "v5_ru"], "v5_6_ru", "v5_6 умеет вопросы и восклицания"),
            sel("threads", "Потоков CPU", [2, 4, 6, 8, 12, 16], 4),
        ],
        "settings": [
            sel("speaker", "Голос", ["kseniya", "xenia", "baya", "aidar", "eugene"], "kseniya"),
            sel("sample_rate", "Частота", [48000, 24000, 8000], 48000),
            flag("put_accent", "Авто-ударения", True),
            flag("put_yo", "Авто-«ё»", True),
            flag("put_stress_homo", "Омографы (ударение)", True),
            flag("put_yo_homo", "Омографы («ё»)", True),
            sel("rate", "Темп (SSML)", ["medium", "x-slow", "slow", "fast", "x-fast"], "medium"),
            sel("pitch", "Высота (SSML)", ["medium", "x-low", "low", "high", "x-high"], "medium"),
        ],
        "common": common(stress="none", stress_choices=("none", "silero"), chunk=800),
    },
    {
        "id": "espeech",
        "title": "ESpeech TTS-1 (ru, F5)",
        "group": "f5",
        "rank": 2,
        "license": "см. карточку модели (у SFT-256K — Apache 2.0)",
        "size": "~1.4 ГБ",
        "gpu": "желательна, от 4 ГБ",
        "summary": "Русская модель на базе F5-TTS. Нужен голос-образец: по умолчанию берётся сгенерированный при установке.",
        "load_options": [
            sel("repo", "Чекпоинт", ["ESpeech/ESpeech-TTS-1_RL-V2", "ESpeech/ESpeech-TTS-1_RL-V1", "ESpeech/ESpeech-TTS-1_SFT-256K"], "ESpeech/ESpeech-TTS-1_RL-V2"),
            DEV(),
        ],
        "settings": [
            ref(default="default_female", help="Голос, которым будет говорить модель (refs/*.wav)"),
            num("nfe_step", "Шаги (NFE)", 32, 8, 64, 1, "Больше — качественнее и медленнее"),
            num("cfg_strength", "CFG", 2.0, 0.5, 5, 0.1),
            num("sway_sampling_coef", "Sway sampling", -1.0, -1, 1, 0.1),
            num("speed", "Скорость", 1.0, 0.5, 2.0, 0.05),
            num("cross_fade_duration", "Склейка кусков, с", 0.15, 0, 1, 0.01),
        ],
        "common": common(stress="ruaccent", stress_choices=("none", "ruaccent", "silero"), chunk=250),
    },
    {
        "id": "qwen3-voicedesign",
        "title": "Qwen3-TTS 1.7B VoiceDesign",
        "group": "qwen",
        "rank": 3,
        "license": "Apache 2.0",
        "size": "~4.5 ГБ",
        "gpu": "желательна, от 8 ГБ (на CPU медленно)",
        "summary": "Голос по текстовому описанию (описание пишите по-английски).",
        "load_options": [
            sel("repo", "Веса", ["Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"], "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"),
            DEV(),
            sel("dtype", "Точность", ["auto", "bfloat16", "float16", "float32"], "auto", "auto: bfloat16 на GPU, float32 на CPU"),
        ],
        "settings": [
            text("instruct", "Описание голоса (EN)", "A warm, calm adult female voice of a clinic receptionist. Clear diction, friendly and unhurried, medium pitch.", multiline=True),
            sel("language", "Язык", ["Russian", "Auto"], "Russian"),
            num("temperature", "Temperature", 0.9, 0.1, 1.5, 0.05),
            num("top_p", "Top-p", 1.0, 0.1, 1.0, 0.05),
            num("top_k", "Top-k", 50, 1, 200, 1),
            num("repetition_penalty", "Repetition penalty", 1.05, 1.0, 2.0, 0.01),
        ],
        "common": common(chunk=300),
    },
    {
        "id": "f5-russian",
        "title": "F5-TTS Russian (Misha24-10)",
        "group": "f5",
        "rank": 4,
        "license": "CC BY-NC 4.0 — некоммерческое",
        "size": "~1.4 ГБ",
        "gpu": "желательна, от 4 ГБ (на CPU ~минута на фразу)",
        "summary": "Дообученная F5-TTS на 5000 ч русской речи, понимает ударения «+». Нужен голос-образец.",
        "load_options": [
            sel("checkpoint", "Версия", ["F5TTS_v1_Base_v4_winter", "F5TTS_v1_Base_v2", "F5TTS_v1_Base_accent_tune", "F5TTS_v1_Base"], "F5TTS_v1_Base_v4_winter"),
            DEV(),
        ],
        "settings": [
            ref(default="default_female"),
            num("nfe_step", "Шаги (NFE)", 32, 8, 64, 1),
            num("cfg_strength", "CFG", 2.0, 0.5, 5, 0.1),
            num("sway_sampling_coef", "Sway sampling", -1.0, -1, 1, 0.1),
            num("speed", "Скорость", 1.0, 0.5, 2.0, 0.05),
            num("cross_fade_duration", "Склейка кусков, с", 0.15, 0, 1, 0.01),
        ],
        "common": common(stress="ruaccent", stress_choices=("none", "ruaccent", "silero"), chunk=250),
    },
    {
        "id": "voxcpm2",
        "title": "VoxCPM2 2B",
        "group": "voxcpm",
        "rank": 5,
        "license": "Apache 2.0",
        "size": "~5 ГБ",
        "gpu": "желательна, ~8 ГБ",
        "summary": "30 языков, 48 кГц, голос по описанию или по образцу.",
        "load_options": [
            sel("repo", "Веса", ["openbmb/VoxCPM2"], "openbmb/VoxCPM2"),
            DEV(),
            flag("optimize", "torch.compile", False, "Быстрее после долгого первого запуска, только на GPU"),
        ],
        "settings": [
            text("instruct", "Описание голоса", "A calm adult woman, warm and clear voice, polite and unhurried", "Пусто — голос выберет модель", multiline=True),
            ref(help="Если выбран образец, описание игнорируется"),
            num("cfg_value", "CFG", 2.0, 1.0, 5.0, 0.1),
            num("inference_timesteps", "Шаги диффузии", 10, 4, 30, 1),
        ],
        "common": common(chunk=300),
    },
    {
        "id": "omnivoice",
        "title": "OmniVoice (k2-fsa)",
        "group": "omnivoice",
        "rank": 6,
        "license": "Apache 2.0",
        "size": "~2.5 ГБ",
        "gpu": "желательна, ~8 ГБ",
        "summary": "646 языков, русский в числе лучших. Быстрая, 0.6B.",
        "load_options": [
            sel("repo", "Веса", ["k2-fsa/OmniVoice"], "k2-fsa/OmniVoice"),
            DEV(),
            sel("dtype", "Точность", ["auto", "float16", "bfloat16", "float32"], "auto"),
        ],
        "settings": [
            text("instruct", "Голос (теги через запятую)", "female, young adult, moderate pitch",
                 "Только из списка: male/female; child, teenager, young adult, middle-aged, elderly; very low … very high pitch; whisper. Пусто — авто"),
            ref(),
            num("num_step", "Шаги", 32, 4, 64, 1),
            num("guidance_scale", "CFG", 2.0, 0, 5, 0.1),
            num("class_temperature", "Temperature", 0.0, 0, 2, 0.05),
            num("position_temperature", "Position temperature", 5.0, 0, 10, 0.1),
            num("speed", "Скорость (0 = авто)", 0, 0, 2, 0.05),
        ],
        "common": common(chunk=300),
    },
    {
        "id": "fish-s2-pro",
        "title": "Fish Audio S2 Pro",
        "group": "fish",
        "rank": 7,
        "license": "Fish Audio Research — некоммерческое",
        "size": "~11 ГБ",
        "gpu": "NVIDIA от 24 ГБ (на CPU очень медленно)",
        "summary": "Топ-2 открытых в слепых тестах, 80+ языков, теги [pause] [whisper] прямо в тексте.",
        "load_options": [
            DEV(),
            flag("half", "float16 (для старых GPU без bf16)", False),
            flag("compile", "torch.compile", False, "Ускоряет на GPU, первый запуск дольше"),
        ],
        "settings": [
            ref(help="Без образца модель выберет голос сама и он будет меняться"),
            num("temperature", "Temperature", 0.7, 0.1, 1.0, 0.05),
            num("top_p", "Top-p", 0.7, 0.1, 1.0, 0.05),
            num("chunk_length", "Длина куска (внутр.)", 200, 100, 1000, 10),
        ],
        "common": common(chunk=500),
    },
    {
        "id": "chatterbox-mtl",
        "title": "Chatterbox Multilingual",
        "group": "chatterbox",
        "rank": 8,
        "license": "MIT (+ водяной знак PerTh)",
        "size": "~3 ГБ",
        "gpu": "желательна, ~6 ГБ",
        "summary": "23 языка, контроль эмоциональности. В тестах на русском ошибается в ударениях.",
        "load_options": [DEV()],
        "settings": [
            ref(help="Без образца — встроенный (англоязычный) голос"),
            num("exaggeration", "Эмоциональность", 0.5, 0, 2, 0.05),
            num("cfg_weight", "CFG weight", 0.5, 0, 1, 0.05),
            num("temperature", "Temperature", 0.8, 0.05, 2, 0.05),
            num("repetition_penalty", "Repetition penalty", 2.0, 1, 3, 0.05),
            num("min_p", "Min-p", 0.05, 0, 1, 0.01),
            num("top_p", "Top-p", 1.0, 0, 1, 0.05),
        ],
        "common": common(chunk=250),
    },
    {
        "id": "qwen3-customvoice",
        "title": "Qwen3-TTS 1.7B CustomVoice",
        "group": "qwen",
        "rank": 9,
        "license": "Apache 2.0",
        "size": "~4.5 ГБ",
        "gpu": "желательна, от 8 ГБ",
        "summary": "9 готовых голосов (родные — китайский/английский, в русском слышен акцент) + инструкция стиля.",
        "load_options": [
            sel("repo", "Веса", ["Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"], "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"),
            DEV(),
            sel("dtype", "Точность", ["auto", "bfloat16", "float16", "float32"], "auto"),
        ],
        "settings": [
            sel("voice", "Голос", ["serena", "vivian", "ono_anna", "sohee", "ryan", "aiden", "dylan", "eric", "uncle_fu"], "serena"),
            text("instruct", "Стиль (EN, необязательно)", "Speak calmly and clearly, like a polite receptionist.", multiline=True),
            sel("language", "Язык", ["Russian", "Auto"], "Russian"),
            num("temperature", "Temperature", 0.9, 0.1, 1.5, 0.05),
            num("top_p", "Top-p", 1.0, 0.1, 1.0, 0.05),
            num("top_k", "Top-k", 50, 1, 200, 1),
            num("repetition_penalty", "Repetition penalty", 1.05, 1.0, 2.0, 0.01),
        ],
        "common": common(chunk=300),
    },
    {
        "id": "xtts-v2",
        "title": "XTTS-v2 (Coqui)",
        "group": "xtts",
        "rank": 10,
        "license": "CPML — некоммерческое, лицензию купить негде",
        "size": "~1.9 ГБ",
        "gpu": "желательна, ~4 ГБ",
        "summary": "Старый мультиязычный стандарт, для сравнения. Есть встроенные голоса.",
        "load_options": [DEV()],
        "settings": [
            sel("speaker", "Голос", ["Ana Florence", "Claribel Dervla", "Daisy Studious", "Gracie Wise", "Tammie Ema", "Sofia Hellen",
                                    "Andrew Chipper", "Craig Gutsy", "Damien Black", "Viktor Eka"], "Ana Florence"),
            ref(help="Если выбран образец, встроенный голос игнорируется"),
            num("temperature", "Temperature", 0.75, 0.05, 1.5, 0.05),
            num("length_penalty", "Length penalty", 1.0, 0, 3, 0.1),
            num("repetition_penalty", "Repetition penalty", 5.0, 1, 20, 0.5),
            num("top_k", "Top-k", 50, 1, 200, 1),
            num("top_p", "Top-p", 0.85, 0.05, 1, 0.05),
            num("speed", "Скорость", 1.0, 0.5, 2, 0.05),
        ],
        "common": common(chunk=220),
    },
]

GROUPS = {
    "silero": {"python": "3.12", "title": "Silero"},
    "f5": {"python": "3.11", "title": "F5-TTS: ESpeech, F5 Russian"},
    "qwen": {"python": "3.12", "title": "Qwen3-TTS"},
    "voxcpm": {"python": "3.12", "title": "VoxCPM2"},
    "omnivoice": {"python": "3.12", "title": "OmniVoice"},
    "fish": {"python": "3.12", "title": "Fish Audio S2 Pro"},
    "chatterbox": {"python": "3.11", "title": "Chatterbox"},
    "xtts": {"python": "3.11", "title": "XTTS-v2"},
}

# Тестовая модель без нейросети — для проверки цепочки панель → API → клиенты
FAKE = {
    "id": "test-tone",
    "title": "Тест: синус без нейросети",
    "group": "core",
    "rank": 99,
    "license": "—",
    "size": "0",
    "gpu": "не нужна",
    "summary": "Проверка связи панель → API → клиенты в сети: вместо речи пищит тон, длина зависит от текста.",
    "load_options": [],
    "settings": [num("freq", "Частота тона, Гц", 440, 100, 2000, 10)],
    "common": common(chunk=400),
}


def all_models(include_fake: bool = True) -> list[dict[str, Any]]:
    out = deepcopy(sorted(MODELS, key=lambda m: m["rank"]))
    if include_fake:
        out.append(deepcopy(FAKE))
    return out


def get_model(model_id: str) -> dict[str, Any]:
    for m in all_models():
        if m["id"] == model_id:
            return m
    raise KeyError(model_id)


def defaults(spec: dict[str, Any]) -> dict[str, Any]:
    return {f["key"]: f["default"] for f in spec["settings"] + spec["common"]}


def load_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    return {f["key"]: f["default"] for f in spec.get("load_options", [])}


def coerce(spec: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Значения из запроса -> правильные типы; неизвестные ключи сохраняются."""
    fields = {f["key"]: f for f in spec["settings"] + spec["common"]}
    out = defaults(spec)
    for k, v in (raw or {}).items():
        f = fields.get(k)
        if f is None:
            out[k] = v
            continue
        try:
            if f["type"] == "number":
                v = float(v)
                if f.get("step") and float(f["step"]).is_integer() and float(f["default"]).is_integer():
                    v = int(v)
            elif f["type"] == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif f["type"] == "select":
                vals = [o["value"] for o in f["options"]]
                if vals and isinstance(vals[0], int):
                    v = int(v)
            else:
                v = "" if v is None else str(v)
        except (TypeError, ValueError):
            continue
        out[k] = v
    return out
