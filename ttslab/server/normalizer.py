"""Нормализация русского текста перед синтезом речи.

Порядок:
  1. пользовательский словарь (data/dictionary.txt)
  2. время (17:00 -> «семнадцать ноль ноль»)
  3. инициалы (Дорохов С. А. -> «Дорохов эс а» / «Дорохов» / как есть)
  4. деньги, «№»
  5. ru-normalizr (числа, даты, сокращения, телефоны), если установлен
  6. запасной вариант: оставшиеся цифры -> слова через num2words
  7. ударения (silero-stress или ruaccent), если модель их понимает

Модуль не зависит от torch/mlx: тяжёлые акцентуаторы грузятся лениво.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("ttslab.normalizer")

ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = ROOT / "data" / "dictionary.txt"

DEFAULT_OPTIONS: dict[str, Any] = {
    "normalize": True,          # включить всю нормализацию
    "time_style": "short",      # short | full | hyphen | off
    "initials": "letters",      # letters | drop | keep
    "use_ru_normalizr": True,   # числа, даты, сокращения, телефоны
    "use_dictionary": True,     # data/dictionary.txt
    "stress": "none",           # none | silero | ruaccent
    "stress_format": "plus",    # plus (+а) | acute (а́)
}

try:
    from num2words import num2words as _n2w
except Exception:  # pragma: no cover
    _n2w = None


# ---------------------------------------------------------------- helpers
def num_words(n: int, gender: str = "m") -> str:
    if _n2w is None:
        return str(n)
    s = _n2w(n, lang="ru")
    if gender == "f":
        last2 = n % 100
        if not 11 <= last2 <= 19:
            if n % 10 == 1:
                s = re.sub(r"один$", "одна", s)
            elif n % 10 == 2:
                s = re.sub(r"два$", "две", s)
    return s


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


LETTER_NAMES = {
    "А": "а", "Б": "бэ", "В": "вэ", "Г": "гэ", "Д": "дэ", "Е": "е", "Ё": "ё",
    "Ж": "жэ", "З": "зэ", "И": "и", "Й": "и краткое", "К": "ка", "Л": "эль",
    "М": "эм", "Н": "эн", "О": "о", "П": "пэ", "Р": "эр", "С": "эс", "Т": "тэ",
    "У": "у", "Ф": "эф", "Х": "ха", "Ц": "цэ", "Ч": "че", "Ш": "ша", "Щ": "ща",
    "Э": "э", "Ю": "ю", "Я": "я",
}

UP = "А-ЯЁ"
LOW = "а-яё"


# ---------------------------------------------------------------- dictionary
_dict_cache: tuple[float, list[tuple[re.Pattern, str]]] | None = None


def load_dictionary() -> list[tuple[re.Pattern, str]]:
    """Строки вида `что => как читать`. `re:` в начале — регулярное выражение."""
    global _dict_cache
    if not DICT_FILE.exists():
        return []
    mtime = DICT_FILE.stat().st_mtime
    if _dict_cache and _dict_cache[0] == mtime:
        return _dict_cache[1]
    rules: list[tuple[re.Pattern, str]] = []
    for raw in DICT_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=>" not in line:
            continue
        src, dst = (p.strip() for p in line.split("=>", 1))
        try:
            if src.startswith("re:"):
                pat = re.compile(src[3:].strip())
            else:
                pat = re.compile(r"(?<![\w])" + re.escape(src) + r"(?![\w])", re.IGNORECASE)
            rules.append((pat, dst))
        except re.error as e:
            log.warning("Словарь: ошибка в строке %r: %s", raw, e)
    _dict_cache = (mtime, rules)
    return rules


def apply_dictionary(text: str) -> str:
    for pat, dst in load_dictionary():
        text = pat.sub(dst, text)
    return text


# ---------------------------------------------------------------- time
_TIME_COLON = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_TIME_DOT = re.compile(r"(\b(?:в|с|до|к|на|по|около)\s)([01]?\d|2[0-3])\.([0-5]\d)(?![.\d])", re.IGNORECASE)


def time_words(h: int, m: int, style: str) -> str:
    hw = num_words(h)
    if style == "full":
        res = f"{hw} {plural(h, 'час', 'часа', 'часов')}"
        if m:
            res += f" {num_words(m, 'f')} {plural(m, 'минута', 'минуты', 'минут')}"
        return res
    if m == 0:
        mins = "ноль-ноль" if style == "hyphen" else "ноль ноль"
    elif m < 10:
        mins = f"ноль {num_words(m)}"
    else:
        mins = num_words(m)
    return f"{hw} {mins}"


def replace_times(text: str, style: str) -> str:
    if style == "off":
        return text
    fn = lambda mt: time_words(int(mt.group(1)), int(mt.group(2)), style)  # noqa: E731
    text = _TIME_COLON.sub(fn, text)
    text = _TIME_DOT.sub(lambda mt: mt.group(1) + time_words(int(mt.group(2)), int(mt.group(3)), style), text)
    return text


# ---------------------------------------------------------------- initials
_SURNAME = rf"[{UP}][{LOW}]+(?:-[{UP}][{LOW}]+)?"
# Фамилия И. О.  /  Фамилия И.О.  /  Фамилия И.
_SURNAME_INITIALS = re.compile(rf"\b({_SURNAME})\s+([{UP}])\.(?:\s?([{UP}])\.)?")
# И. О. Фамилия  /  И.О. Фамилия  /  И. Фамилия
_INITIALS_SURNAME = re.compile(rf"(?<![{UP}{LOW}])([{UP}])\.\s?(?:([{UP}])\.\s?)?({_SURNAME})\b")


def _sentence_end_after(text: str, pos: int) -> bool:
    rest = text[pos:]
    return rest == "" or rest.startswith("\n") or bool(re.match(rf"\s+[{UP}«\"]", rest))


def replace_initials(text: str, mode: str) -> str:
    if mode == "keep":
        return text

    def spoken(letters: list[str]) -> str:
        if mode == "drop":
            return ""
        return " ".join(LETTER_NAMES.get(ch, ch.lower()) for ch in letters)

    out, pos = [], 0
    for mt in _SURNAME_INITIALS.finditer(text):
        letters = [g for g in (mt.group(2), mt.group(3)) if g]
        # «Иванов А.» в конце предложения — тоже инициал; «Вчера Я.» почти не встречается
        out.append(text[pos:mt.start()])
        rep = mt.group(1)
        sp = spoken(letters)
        if sp:
            rep += " " + sp
        if _sentence_end_after(text, mt.end()):
            rep += "."
        out.append(rep)
        pos = mt.end()
    out.append(text[pos:])
    text = "".join(out)

    def rep2(mt: re.Match) -> str:
        letters = [g for g in (mt.group(1), mt.group(2)) if g]
        sp = spoken(letters)
        return f"{sp} {mt.group(3)}" if sp else mt.group(3)

    return _INITIALS_SURNAME.sub(rep2, text)


# ---------------------------------------------------------------- money, misc
_MONEY = re.compile(r"(\d[\d ]*\d|\d)\s?(?:₽|руб(?:лей|ля|ль)?\.?|р\.)(?![{LOW}])".replace("{LOW}", LOW))
_KOP = re.compile(r"(\d+)\s?коп(?:еек|ейки|ейка)?\.?(?![а-яё])")


def replace_money(text: str) -> str:
    def rub(mt: re.Match) -> str:
        n = int(mt.group(1).replace(" ", ""))
        return f"{num_words(n)} {plural(n, 'рубль', 'рубля', 'рублей')}"

    def kop(mt: re.Match) -> str:
        n = int(mt.group(1))
        return f"{num_words(n, 'f')} {plural(n, 'копейка', 'копейки', 'копеек')}"

    text = _MONEY.sub(rub, text)
    return _KOP.sub(kop, text)


def replace_misc(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"№\s?", "номер ", text)
    text = text.replace("…", "...")
    return text


_DIGITS = re.compile(r"\d+")


def digits_fallback(text: str) -> str:
    return _DIGITS.sub(lambda m: num_words(int(m.group(0))) if len(m.group(0)) < 13 else " ".join(num_words(int(c)) for c in m.group(0)), text)


# ---------------------------------------------------------------- ru-normalizr
_rn_lock = threading.Lock()
_rn_func = None
_rn_failed = False


def _ru_normalizr(text: str) -> str:
    global _rn_func, _rn_failed
    if _rn_failed:
        return digits_fallback(text)
    with _rn_lock:
        if _rn_func is None:
            try:
                from ru_normalizr import normalize as rn

                _rn_func = rn
            except Exception as e:
                log.warning("ru-normalizr недоступен (%s), числа будут прочитаны через num2words", e)
                _rn_failed = True
                return digits_fallback(text)
    try:
        return _rn_func(text)
    except Exception as e:
        log.warning("ru-normalizr упал на тексте (%s), использую запасной вариант", e)
        return digits_fallback(text)


# ---------------------------------------------------------------- stress
_accentors: dict[str, Any] = {}
_acc_lock = threading.Lock()


def _get_accentor(kind: str):
    with _acc_lock:
        if kind in _accentors:
            return _accentors[kind]
        acc = None
        try:
            if kind == "silero":
                from silero_stress import load_accentor

                acc = load_accentor(lang="ru")
            elif kind == "ruaccent":
                from ruaccent import RUAccent

                acc = RUAccent()
                try:
                    acc.load(omograph_model_size="turbo3.1", use_dictionary=True)
                except Exception:
                    acc.load()
        except Exception as e:
            log.warning("Акцентуатор %s недоступен: %s", kind, e)
            acc = None
        _accentors[kind] = acc
        return acc


def add_stress(text: str, kind: str, fmt: str = "plus") -> str:
    if kind in ("none", "", None):
        return text
    acc = _get_accentor(kind)
    if acc is None:
        return text
    try:
        out = acc(text) if kind == "silero" else acc.process_all(text)
    except Exception as e:
        log.warning("Ошибка расстановки ударений: %s", e)
        return text
    if fmt == "acute":
        out = re.sub(r"\+([аеёиоуыэюяАЕЁИОУЫЭЮЯ])", "\\1\u0301", out)
    return out


# ---------------------------------------------------------------- main
def tidy(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ([,.!?;:])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\.\s*\.(?!\.)", ".", text)
    return text.strip()


def normalize_text(text: str, opts: dict[str, Any] | None = None) -> tuple[str, list[dict[str, str]]]:
    """Возвращает (текст для синтеза, шаги для отладки)."""
    o = {**DEFAULT_OPTIONS, **(opts or {})}
    steps: list[dict[str, str]] = []

    def step(name: str, new: str):
        nonlocal text
        if new != text:
            steps.append({"step": name, "text": new})
        text = new

    if not o.get("normalize", True):
        step("ударения", add_stress(text, o.get("stress", "none"), o.get("stress_format", "plus")))
        return text, steps

    step("очистка", replace_misc(text))
    if o.get("use_dictionary", True):
        step("словарь", apply_dictionary(text))
    step("время", replace_times(text, o.get("time_style", "short")))
    step("инициалы", replace_initials(text, o.get("initials", "letters")))
    step("деньги", replace_money(text))
    if o.get("use_ru_normalizr", True):
        step("ru-normalizr", _ru_normalizr(text))
    if _DIGITS.search(text):
        step("цифры", digits_fallback(text))
    step("оформление", tidy(text))
    step("ударения", add_stress(text, o.get("stress", "none"), o.get("stress_format", "plus")))
    return text, steps


# ---------------------------------------------------------------- chunking
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def split_chunks(text: str, max_chars: int) -> list[str]:
    """Режет по предложениям, длинные предложения — по запятым/пробелам."""
    if max_chars <= 0 or len(text) <= max_chars:
        return [text] if text.strip() else []
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in parts:
        while len(p) > max_chars:
            cut = max(p.rfind(", ", 0, max_chars), p.rfind(" ", 0, max_chars))
            cut = cut if cut > max_chars // 3 else max_chars
            head, p = p[: cut + 1].strip(), p[cut + 1 :].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
        if not cur:
            cur = p
        elif len(cur) + 1 + len(p) <= max_chars:
            cur += " " + p
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


if __name__ == "__main__":  # быстрый ручной тест: python -m server.normalizer "текст"
    import sys

    logging.basicConfig(level=logging.INFO)
    sample = " ".join(sys.argv[1:]) or "Записываю: 17:00, врач Дорохов С. А., кабинет № 204, стоимость 1500 руб."
    res, st = normalize_text(sample)
    for s in st:
        print(f"[{s['step']}] {s['text']}")
    print("=>", res)
