from __future__ import annotations

from xml.sax.saxutils import escape

from .base import MODELS_DIR, Backend

URL = "https://models.silero.ai/models/tts/ru/{version}.pt"


class SileroBackend(Backend):
    def _path(self):
        version = self.opts.get("version", "v5_6_ru")
        return version, MODELS_DIR / "silero" / f"{version}.pt"

    def download(self) -> None:
        import torch

        version, path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            self.log.info("Скачиваю %s → %s", URL.format(version=version), path)
            torch.hub.download_url_to_file(URL.format(version=version), str(path))
        self.load()

    def load(self) -> None:
        import torch

        version, path = self._path()
        if not path.exists():
            self.download()
            return
        torch.set_num_threads(int(self.opts.get("threads", 4)))
        self.model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
        self.model.to(torch.device("cpu"))
        self.version = version
        self.log.info("Silero %s загружена, голоса: %s", version, getattr(self.model, "speakers", "?"))

    def info(self):
        return {"voices": list(getattr(self.model, "speakers", []))}

    def synth(self, text, s):
        sr = int(s.get("sample_rate", 48000))
        speaker = s.get("speaker", "kseniya")
        rate, pitch = s.get("rate", "medium"), s.get("pitch", "medium")
        kw = dict(speaker=speaker, sample_rate=sr)
        kw.update(self.extra(s))
        if rate != "medium" or pitch != "medium":
            ssml = f'<speak><prosody rate="{rate}" pitch="{pitch}">{escape(text)}</prosody></speak>'
            audio = self.model.apply_tts(ssml_text=ssml, **kw)
        else:
            flags = {k: bool(s.get(k, True)) for k in ("put_accent", "put_yo", "put_stress_homo", "put_yo_homo")}
            try:
                audio = self.model.apply_tts(text=text, **flags, **kw)
            except TypeError:
                # старые версии без омографов
                audio = self.model.apply_tts(text=text, put_accent=flags["put_accent"], put_yo=flags["put_yo"], **kw)
        return self.to_np(audio), sr
