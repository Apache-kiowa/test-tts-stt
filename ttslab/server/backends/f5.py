from __future__ import annotations

import re

from ..normalizer import add_stress
from .base import Backend

MODEL_CFG = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)


def _step_num(name: str) -> int:
    nums = re.findall(r"(\d+)", name.rsplit("/", 1)[-1])
    return int(nums[-1]) if nums else 0


class F5Backend(Backend):
    sample_rate = 24000

    def _resolve_files(self):
        from huggingface_hub import hf_hub_download, list_repo_files

        if self.spec["id"] == "espeech":
            repo = self.opts.get("repo", "ESpeech/ESpeech-TTS-1_RL-V2")
            files = list_repo_files(repo)
            weights = [f for f in files if f.endswith((".safetensors", ".pt")) and "/" not in f.strip("/")] or \
                      [f for f in files if f.endswith((".safetensors", ".pt"))]
            weights.sort(key=lambda f: (f.endswith(".safetensors"), _step_num(f)), reverse=True)
            vocab = next((f for f in files if f.endswith("vocab.txt")), None)
        else:
            repo = "Misha24-10/F5-TTS_RUSSIAN"
            folder = self.opts.get("checkpoint", "F5TTS_v1_Base_v4_winter")
            files = list_repo_files(repo)
            weights = [f for f in files if f.startswith(folder + "/") and f.endswith((".safetensors", ".pt"))]
            if not weights:
                raise RuntimeError(f"В {repo} нет папки {folder}. Есть: {sorted({f.split('/')[0] for f in files if '/' in f})}")
            weights.sort(key=lambda f: (f.endswith(".safetensors"), "inference" in f, _step_num(f)), reverse=True)
            vocab = next((f for f in files if f == f"{folder}/vocab.txt"), None) or next((f for f in files if f == "vocab.txt"), None)
        if not weights or not vocab:
            raise RuntimeError(f"Не нашёл веса/vocab.txt в {repo}: {files}")
        self.log.info("Чекпоинт: %s / %s, словарь: %s", repo, weights[0], vocab)
        return hf_hub_download(repo, weights[0]), hf_hub_download(repo, vocab)

    def load(self) -> None:
        import torch  # noqa: F401
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        from f5_tts.model import DiT

        self.device = self.torch_device(self.opts.get("device", "auto"))
        ckpt, vocab = self._resolve_files()
        self.model = load_model(DiT, MODEL_CFG, ckpt, vocab_file=vocab, device=self.device)
        self.vocoder = load_vocoder(device=self.device)
        self.log.info("F5 загружена на %s", self.device)

    def synth(self, text, s):
        from f5_tts.infer.utils_infer import infer_process, preprocess_ref_audio_text

        wav, ref_text = self.ref_paths(s.get("ref_voice") or "default_female")
        if not ref_text:
            raise ValueError("Для F5 нужен образец с расшифровкой (refs/<имя>.txt)")
        ref_text = add_stress(ref_text, s.get("stress", "none"))
        ref_audio, ref_text = preprocess_ref_audio_text(wav, ref_text, show_info=self.log.debug)
        kw = dict(
            nfe_step=int(s.get("nfe_step", 32)),
            cfg_strength=float(s.get("cfg_strength", 2.0)),
            sway_sampling_coef=float(s.get("sway_sampling_coef", -1.0)),
            speed=float(s.get("speed", 1.0)),
            cross_fade_duration=float(s.get("cross_fade_duration", 0.15)),
        )
        kw.update(self.extra(s))
        wave, sr, _ = infer_process(ref_audio, ref_text, text, self.model, self.vocoder,
                                    show_info=self.log.debug, device=self.device, **kw)
        return self.to_np(wave), int(sr)
