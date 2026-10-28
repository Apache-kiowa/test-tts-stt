"""Fish Audio S2 Pro через исходники fish-speech (vendor/fish-speech, ставит install.sh)."""
from __future__ import annotations

import sys

import numpy as np

from .base import MODELS_DIR, ROOT, Backend

VENDOR = ROOT / "vendor" / "fish-speech"
CKPT = MODELS_DIR / "fish" / "s2-pro"


class FishBackend(Backend):
    def download(self) -> None:
        from huggingface_hub import snapshot_download

        CKPT.mkdir(parents=True, exist_ok=True)
        self.log.info("Скачиваю fishaudio/s2-pro → %s", CKPT)
        snapshot_download("fishaudio/s2-pro", local_dir=str(CKPT))
        self.log.info("Веса Fish S2 Pro скачаны")

    def load(self) -> None:
        if not (CKPT / "codec.pth").exists():
            self.download()
        if str(VENDOR) not in sys.path:
            sys.path.insert(0, str(VENDOR))
        from tools.server.model_manager import ModelManager

        device = self.torch_device(self.opts.get("device", "auto"))
        self.log.info("Загружаю Fish S2 Pro на %s", device)
        self.mm = ModelManager(mode="tts", device=device, half=bool(self.opts.get("half", False)),
                               compile=bool(self.opts.get("compile", False)), llama_checkpoint_path=str(CKPT),
                               decoder_checkpoint_path=str(CKPT / "codec.pth"), decoder_config_name="modded_dac_vq")
        self.engine = self.mm.tts_inference_engine
        self._refs: dict[str, object] = {}

    def synth(self, text, s):
        from fish_speech.utils.schema import ServeReferenceAudio, ServeTTSRequest

        refs = []
        wav, ref_text = self.ref_paths(s.get("ref_voice"))
        if wav:
            with open(wav, "rb") as f:
                refs.append(ServeReferenceAudio(audio=f.read(), text=ref_text or ""))
        seed = s.get("seed")
        req = ServeTTSRequest(
            text=text, format="wav", references=refs, normalize=False, streaming=False,
            chunk_length=int(s.get("chunk_length", 200)), temperature=float(s.get("temperature", 0.7)),
            top_p=float(s.get("top_p", 0.7)), seed=int(seed) if seed is not None and int(seed) >= 0 else None,
            **self.extra(s),
        )
        sr, parts = 44100, []
        for r in self.engine.inference(req):
            if r.code == "error":
                raise RuntimeError(str(r.error))
            if r.code in ("segment", "final") and isinstance(r.audio, tuple):
                sr, a = r.audio
                if r.code == "final":
                    parts = [np.asarray(a, dtype=np.float32).reshape(-1)]
                    break
                parts.append(np.asarray(a, dtype=np.float32).reshape(-1))
        if not parts:
            raise RuntimeError("Fish не вернула аудио")
        return np.concatenate(parts), int(sr)
