"""Separate Two Speakers: two people talking over each other, split into one track each.

Runs MossFormer2_SS_16K (ClearerVoice-Studio, Alibaba, Apache-2.0; model code vendored in vendor/mossformer2_ss).
The model separates two voices in 16 kHz speech. It was trained on short mixtures and a whole long file at once
comes out much worse (tested: SI-SDR 1.9 dB against 11.4 dB), so the recording is cut into 20 s windows with 4 s
of overlap. Each window's two tracks are scaled to fit the mixture, matched to the previous window's tracks by
correlation over the overlap so that a speaker keeps its track, and crossfaded.

Measured on 3 minutes of Finnish read speech with an English speaker over it at the same level: SI-SDR of the
Finnish track from -4.4 dB (the mix) to 11.4 dB, the Finnish voice on the same track in every 10 s span; Nemotron's
character errors from 74 % to 28 %, Whisper's from 82 % to 21 % (clean: 6 % and 3 %). It does not separate three
or more voices, and the output is 16 kHz.
"""
import gc
import hashlib
import os

import numpy as np
import torch
import torchaudio

import comfy.model_management
import folder_paths

REPO = "alibabasglab/MossFormer2_SS_16K"
REVISION = "407cb030cd66340918ebb6c8cc63b18f8592cdbe"
SHA256 = "00a3a48bda492db1e829b85dd443f8f43a43039a3e90f1a24962ea9caf14a11a"
MODEL_PATH = os.path.join(folder_paths.models_dir, "speech_separation", "mossformer2_ss_16k.pt")
RATE = 16000
_LOADED = {}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


def _checkpoint():
    if not os.path.exists(MODEL_PATH):
        from huggingface_hub import hf_hub_download
        print(f"[separation] {MODEL_PATH} is missing, downloading it from {REPO}")
        folder = os.path.dirname(MODEL_PATH)
        os.makedirs(folder, exist_ok=True)
        fetched = hf_hub_download(REPO, "last_best_checkpoint.pt", revision=REVISION, local_dir=folder)
        if _sha256(fetched) != SHA256:
            os.remove(fetched)
            raise RuntimeError(f"{REPO} checkpoint failed its sha256 check")
        os.replace(fetched, MODEL_PATH)
    return MODEL_PATH


def _model():
    if "mossformer2" not in _LOADED:
        from .vendor.mossformer2_ss.mossformer2 import MossFormer
        model = MossFormer(in_channels=512, out_channels=512, num_blocks=24, kernel_size=16, norm="ln", num_spks=2,
                           skip_around_intra=True, use_global_pos_enc=True, max_length=20000)
        state = torch.load(_checkpoint(), map_location="cpu", weights_only=True)["model"]  # plain data only
        state = {k.removeprefix("module."): v for k, v in state.items()}
        model.load_state_dict(state, strict=True)
        _LOADED["mossformer2"] = model.to(comfy.model_management.get_torch_device()).eval()
    return _LOADED["mossformer2"]


def _unload():
    if _LOADED.pop("mossformer2", None) is not None:
        gc.collect()
        comfy.model_management.soft_empty_cache()


def _fit(tracks, mix):
    """Scale the two tracks so that their sum fits the mixture best (least squares)."""
    a = tracks @ tracks.T + 1e-6 * np.eye(2)
    gains = np.linalg.solve(a, tracks @ mix)
    return tracks * gains[:, None]


def separate(model, mix, window_seconds, overlap_seconds):
    """mix: 1-D float32 at 16 kHz -> (2, T) float32."""
    device = comfy.model_management.get_torch_device()
    w, o = int(window_seconds * RATE), int(overlap_seconds * RATE)
    if len(mix) <= w:
        with torch.inference_mode():
            y = torch.stack([t[0] for t in model(torch.from_numpy(mix)[None].to(device))])
        return _fit(y.float().cpu().numpy()[:, :len(mix)], mix)
    out = np.zeros((2, len(mix)), np.float32)
    fade = np.linspace(0, 1, o, dtype=np.float32)
    prev, start = None, 0
    while True:
        comfy.model_management.throw_exception_if_processing_interrupted()
        seg = mix[start:start + w]
        with torch.inference_mode():
            y = torch.stack([t[0] for t in model(torch.from_numpy(seg)[None].to(device))])
        y = _fit(y.float().cpu().numpy()[:, :len(seg)], seg)
        if prev is None:
            out[:, :len(seg)] = y
        else:
            a, b = prev[:, -o:], y[:, :o]
            if np.dot(a[0], b[1]) + np.dot(a[1], b[0]) > np.dot(a[0], b[0]) + np.dot(a[1], b[1]):
                y = y[::-1].copy()  # the speaker who carries on keeps its track
            out[:, start:start + o] = out[:, start:start + o] * (1 - fade) + y[:, :o] * fade
            out[:, start + o:start + len(seg)] = y[:, o:]
        prev = y
        if start + w >= len(mix):
            return out
        start += w - o


class SeparateTwoSpeakers:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "window_seconds": ("FLOAT", {"default": 20.0, "min": 4.0, "max": 60.0, "step": 1.0,
                                         "tooltip": "Length of each piece the model sees. It was trained on short "
                                                    "mixtures; 20 s worked best in the test."}),
            "overlap_seconds": ("FLOAT", {"default": 4.0, "min": 1.0, "max": 10.0, "step": 0.5,
                                          "tooltip": "Overlap between pieces, used to keep each speaker on the "
                                                     "same track and to crossfade."}),
            "keep_model_loaded": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("AUDIO", "AUDIO")
    RETURN_NAMES = ("speaker_1", "speaker_2")
    FUNCTION = "separate"
    CATEGORY = "audio/speech"
    DESCRIPTION = ("Splits a recording of two people talking over each other into one track per speaker, with "
                   "MossFormer2 (ClearerVoice-Studio, Apache-2.0). Output at 16 kHz. Listen and keep the track you "
                   "need; which speaker lands on track 1 depends on the recording. Not for three or more voices.")

    def separate(self, audio, window_seconds, overlap_seconds, keep_model_loaded):
        if not audio or "waveform" not in audio:
            raise ValueError("There is no sound to separate: connect a recording.")
        if overlap_seconds >= window_seconds / 2:
            raise ValueError("overlap_seconds must be under half of window_seconds")
        rate = audio["sample_rate"]
        try:
            model = _model()
            first, second = [], []
            for wave_ in audio["waveform"]:
                mono = wave_.float().mean(0)
                if rate != RATE:
                    mono = torchaudio.functional.resample(mono, rate, RATE)
                tracks = separate(model, mono.cpu().numpy().astype(np.float32), window_seconds, overlap_seconds)
                peak = max(np.abs(tracks).max(), 1e-6)
                if peak > 0.99:  # keep both tracks at the same scale, below clipping
                    tracks = tracks * (0.99 / peak)
                first.append(torch.from_numpy(tracks[0]).reshape(1, -1))
                second.append(torch.from_numpy(tracks[1]).reshape(1, -1))
        finally:
            if not keep_model_loaded:
                _unload()
        return ({"waveform": torch.stack(first), "sample_rate": RATE},
                {"waveform": torch.stack(second), "sample_rate": RATE})


NODE_CLASS_MAPPINGS = {"SeparateTwoSpeakers": SeparateTwoSpeakers}
NODE_DISPLAY_NAME_MAPPINGS = {"SeparateTwoSpeakers": "Separate Two Speakers (MossFormer2)"}
