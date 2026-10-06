"""Speech to text and text to speech for the dialogue and subtitle templates.

Whisper Speech to Text runs OpenAI's Whisper large-v3 through transformers and
prints one `[start - end] word` line per word, the format Word Timestamps to
Subtitles reads. Whisper times words in all of its 100 languages, Finnish
included.

Chatterbox Speech runs either Resemble AI's multilingual Chatterbox or
Finnish-NLP's Finnish fine-tune of it. The fine-tune is run the way its model
card does: its T3 over the multilingual vocabulary, text without a language tag
or lowercasing, and the base model's S3Gen, voice encoder and default voice.
The model classes come from filliptm's ComfyUI_Fill-ChatterBox. Its own nodes
sample without upstream Chatterbox's alignment check, and the multilingual
model then talks on after the text in every language tried here, so this node
samples itself, with that check (see _StopGuard).

Both read their files from models/ and download any that are missing from
Hugging Face on first use; the asset profiles provision them in advance.
"""

import gc
import os
import sys
import tempfile
import wave

import numpy as np
import torch
import torch.nn.functional as F
import torchaudio

import comfy.model_management
import comfy.utils
import folder_paths

WHISPER_REPO = "openai/whisper-large-v3"
WHISPER_DIR = os.path.join(folder_paths.models_dir, "stt", "whisper", "whisper-large-v3")
WHISPER_FILES = ("config.json", "generation_config.json", "preprocessor_config.json", "model.safetensors",
                 "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt", "normalizer.json",
                 "added_tokens.json", "special_tokens_map.json")
CHATTERBOX_REPO = "ResembleAI/chatterbox"
CHATTERBOX_DIR = os.path.join(folder_paths.models_dir, "chatterbox", "chatterbox_multilingual")
CHATTERBOX_FILES = ("ve.pt", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json", "conds.pt")
MULTILINGUAL_FILES = ("t3_mtl23ls_v2.safetensors", "Cangjie5_TC.json")
FINNISH_REPO = "Finnish-NLP/Chatterbox-Finnish"
FINNISH_DIR = os.path.join(folder_paths.models_dir, "chatterbox", "chatterbox_finnish")
FINNISH_FILE = "models/best_finnish_multilingual_cp986.safetensors"
FINNISH = "Finnish (Finnish-NLP fine-tune)"
MULTILINGUAL = "Multilingual (Resemble AI)"
LANGUAGES = ("Arabic (ar)", "Chinese (zh)", "Danish (da)", "Dutch (nl)", "English (en)", "Finnish (fi)", "French (fr)",
             "German (de)", "Greek (el)", "Hebrew (he)", "Hindi (hi)", "Italian (it)", "Japanese (ja)", "Korean (ko)",
             "Malay (ms)", "Norwegian (no)", "Polish (pl)", "Portuguese (pt)", "Russian (ru)", "Spanish (es)",
             "Swahili (sw)", "Swedish (sv)", "Turkish (tr)")
SPEECH_VOCAB = 6561  # S3 speech tokens; ids at or above it are control tokens
MAX_SPEECH_TOKENS = 1000  # 40 s at 25 tokens a second

_LOADED = {}


def _fetch(repo_id, filenames, folder):
    """Local paths of the files, downloading the missing ones into folder."""
    paths = []
    for name in filenames:
        path = os.path.join(folder, os.path.basename(name))
        if not os.path.exists(path):
            from huggingface_hub import hf_hub_download
            print(f"[speech] {path} is missing, downloading it from {repo_id}")
            fetched = hf_hub_download(repo_id, name, local_dir=folder)
            if os.path.abspath(fetched) != os.path.abspath(path):
                os.replace(fetched, path)
        paths.append(path)
    return paths


def _unload(key):
    if _LOADED.pop(key, None) is not None:
        gc.collect()
        comfy.model_management.soft_empty_cache()


def _whisper_languages():
    from transformers.models.whisper.tokenization_whisper import LANGUAGES as WHISPER_LANGUAGES
    return ["auto"] + sorted(f"{name.title()} ({code})" for code, name in WHISPER_LANGUAGES.items())


class WhisperSpeechToText:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "language": (_whisper_languages(), {"default": "auto",
                                                    "tooltip": "The spoken language. auto detects it in every "
                                                               "30-second stretch."}),
                "keep_model_loaded": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "timestamps")
    OUTPUT_IS_LIST = (True, True)
    FUNCTION = "transcribe"
    CATEGORY = "audio/speech"
    DESCRIPTION = ("Writes down speech with Whisper large-v3. timestamps holds one [start - end] word line per "
                   "word, for Word Timestamps to Subtitles. Given a list of clips, it loads the model once for "
                   "all of them.")

    @staticmethod
    def _run(audio, language):
        if "whisper" not in _LOADED:
            from transformers import WhisperForConditionalGeneration, WhisperProcessor, pipeline
            _fetch(WHISPER_REPO, WHISPER_FILES, WHISPER_DIR)
            device = comfy.model_management.get_torch_device()
            # word timings come from cross-attention weights, which only the eager attention returns
            model = WhisperForConditionalGeneration.from_pretrained(
                WHISPER_DIR, dtype=torch.float16, attn_implementation="eager").to(device)
            processor = WhisperProcessor.from_pretrained(WHISPER_DIR)
            _LOADED["whisper"] = pipeline("automatic-speech-recognition", model=model, tokenizer=processor.tokenizer,
                                          feature_extractor=processor.feature_extractor, chunk_length_s=30,
                                          dtype=torch.float16, device=device)
        speech = audio["waveform"][0].float().mean(0).cpu()
        if audio["sample_rate"] != 16000:
            speech = torchaudio.functional.resample(speech, audio["sample_rate"], 16000)
        options = {"task": "transcribe"}
        if language != "auto":
            options["language"] = language.rsplit("(", 1)[1].rstrip(")")
        return _LOADED["whisper"]({"raw": speech.numpy(), "sampling_rate": 16000}, return_timestamps="word",
                                  generate_kwargs=options, batch_size=8)

    @staticmethod
    def _word_lines(result):
        words = [chunk for chunk in result.get("chunks", []) if chunk["text"].strip()]
        lines = []
        for i, chunk in enumerate(words):
            start, end = chunk["timestamp"]
            if start is None:
                continue
            if end is None:  # the last word of a chunk can come back open-ended
                end = words[i + 1]["timestamp"][0] if i + 1 < len(words) else None
                end = end if end is not None and end > start else start + 0.5
            lines.append(f"[{start:.2f} - {end:.2f}] {chunk['text'].strip()}")
        return "\n".join(lines)

    def transcribe(self, audio, language, keep_model_loaded):
        texts, stamps = [], []
        progress = comfy.utils.ProgressBar(len(audio))
        try:
            for clip in audio:
                comfy.model_management.throw_exception_if_processing_interrupted()
                result = self._run(clip, language[0])
                texts.append(result["text"].strip())
                stamps.append(self._word_lines(result))
                progress.update(1)
        finally:
            if not keep_model_loaded[0]:
                _unload("whisper")
        return (texts, stamps)


def _chatterbox_modules():
    """The tts, mtl_tts and t3 modules of the Chatterbox code ComfyUI_Fill-ChatterBox loaded."""
    import nodes
    fl_node = nodes.NODE_CLASS_MAPPINGS.get("FL_ChatterboxMultilingualTTS")
    if fl_node is None:
        raise RuntimeError("Chatterbox Speech uses the Chatterbox code in filliptm's ComfyUI_Fill-ChatterBox, "
                           "which is not installed.")
    fl = sys.modules[fl_node.__module__]
    tts = sys.modules[fl.ChatterboxTTS.__module__]
    return tts, sys.modules[fl.ChatterboxMultilingualTTS.__module__], sys.modules[tts.T3.__module__]


def _load_chatterbox(finnish):
    from safetensors.torch import load_file
    tts, mtl, t3_module = _chatterbox_modules()
    _fetch(CHATTERBOX_REPO, CHATTERBOX_FILES, CHATTERBOX_DIR)
    if finnish:
        t3_path = _fetch(FINNISH_REPO, (FINNISH_FILE,), FINNISH_DIR)[0]
    else:
        t3_path = _fetch(CHATTERBOX_REPO, MULTILINGUAL_FILES, CHATTERBOX_DIR)[0]
    device = comfy.model_management.get_torch_device()

    def base(name):
        return os.path.join(CHATTERBOX_DIR, name)

    ve = tts.VoiceEncoder()
    ve.load_state_dict(torch.load(base("ve.pt"), weights_only=True, map_location="cpu"))
    t3 = tts.T3(t3_module.T3Config.multilingual())
    t3.load_state_dict({k[3:] if k.startswith("t3.") else k: v for k, v in load_file(t3_path).items()})
    s3gen = tts.S3Gen()
    s3gen.load_state_dict(torch.load(base("s3gen.pt"), weights_only=True, map_location="cpu"))
    for part in (ve, t3, s3gen):
        part.to(device).eval()
    vocab = base("grapheme_mtl_merged_expanded_v1.json")
    tokenizer = tts.EnTokenizer(vocab) if finnish else mtl.MTLTokenizer(vocab)
    conds = tts.Conditionals.load(base("conds.pt"), map_location="cpu").to(device)
    return tts.ChatterboxTTS(t3, s3gen, ve, tokenizer, device, conds=conds)


class _StopGuard:
    """Ends the speech when the text has been read, as upstream Chatterbox does.

    Three attention heads of the T3 track where in the text it is. Until the
    last text tokens are reached the stop token is suppressed. After that,
    attention that stays on the end (a held sound) or goes back into the text (a
    repeat) forces it, as does one speech token ten times in a row. Adapted from
    Resemble AI's AlignmentStreamAnalyzer (MIT), with the ten-token limit
    Finnish-NLP chose for Finnish long vowels. The heads only report their
    weights under eager attention, so the T3 runs eager while the guard watches.
    """

    HEADS = ((12, 15), (13, 11), (9, 2))  # (layer, head)

    def __init__(self, tfmr, text_start, text_end, eos):
        self.start, self.end, self.eos = text_start, text_end, eos
        self.alignment = torch.zeros(0, text_end - text_start)
        self.frame = 0
        self.position = 0
        self.completed_at = None
        self.tokens = []
        self.weights = [None] * len(self.HEADS)
        self.handles = [tfmr.layers[layer].self_attn.register_forward_hook(self._spy(k, head))
                        for k, (layer, head) in enumerate(self.HEADS)]

    def _spy(self, k, head):
        def hook(module, args, output):
            if isinstance(output, tuple) and len(output) > 1 and output[1] is not None:
                self.weights[k] = output[1][0, head].float().cpu()  # the conditional row of the CFG pair
        return hook

    def close(self):
        for handle in self.handles:
            handle.remove()

    def step(self, logits, last_token):
        if any(weights is None for weights in self.weights):
            raise RuntimeError("Chatterbox Speech could not read the T3's attention weights, which it needs to end "
                               "the speech on time.")
        attention = torch.stack(self.weights).mean(0)
        i, j = self.start, self.end
        # the first pass covers the whole prompt; after that the cache gives one row per step
        chunk = (attention[j:, i:j] if self.frame == 0 else attention[:, i:j]).clone()
        chunk[:, self.frame + 1:] = 0
        self.alignment = torch.cat((self.alignment, chunk), dim=0)
        steps, length = self.alignment.shape
        position = int(chunk[-1].argmax())
        if -4 < position - self.position < 7:
            self.position = position
        if self.completed_at is None and self.position >= length - 3:
            self.completed_at = steps
        self.tokens = (self.tokens + [last_token])[-10:]
        stop = len(self.tokens) == 10 and len(set(self.tokens)) == 1
        if self.completed_at is not None:
            tail = self.alignment[self.completed_at:]
            stop = stop or bool(tail[:, -3:].sum(dim=0).max() >= 5) or bool(tail[:, :-5].max(dim=1).values.sum() > 5)
        if position < length - 3 and length > 5:
            logits[..., self.eos] = -2 ** 15
        if stop:
            logits = torch.full_like(logits, -2 ** 15)
            logits[..., self.eos] = 2 ** 15
        self.frame += 1
        return logits


def _sample(model, t3_module, tokens, temperature, cfg_weight, repetition_penalty, min_p, top_p):
    """Speech tokens for one line: ComfyUI_Fill-ChatterBox's sampling loop with the stop guard."""
    from transformers.generation.logits_process import (MinPLogitsWarper, RepetitionPenaltyLogitsProcessor,
                                                        TopPLogitsWarper)
    t3, hp = model.t3, model.t3.hp
    tokens = tokens.long()  # the logits processors index with these
    start = hp.start_speech_token * torch.ones_like(tokens[:, :1])
    embeds, cond_length = t3.prepare_input_embeds(t3_cond=model.conds.t3, text_tokens=tokens, speech_tokens=start,
                                                  cfg_weight=cfg_weight)
    backend = t3_module.T3HuggingfaceBackend(config=t3.cfg, llama=t3.tfmr, speech_enc=t3.speech_emb,
                                             speech_head=t3.speech_head, alignment_stream_analyzer=None)
    bos = start[:1]
    bos_embed = t3.speech_emb(bos) + t3.speech_pos_emb.get_fixed_embedding(0)
    inputs = torch.cat([embeds, torch.cat([bos_embed, bos_embed])], dim=1)
    processors = (RepetitionPenaltyLogitsProcessor(penalty=float(repetition_penalty)), MinPLogitsWarper(min_p=min_p),
                  TopPLogitsWarper(top_p=top_p))
    guard = _StopGuard(t3.tfmr, cond_length, cond_length + tokens.size(-1), hp.stop_speech_token)
    config = t3.tfmr.config
    attention = config._attn_implementation
    config._attn_implementation = "eager"
    generated = bos.clone()
    try:
        output = backend(inputs_embeds=inputs, past_key_values=None, use_cache=True, output_attentions=True,
                         output_hidden_states=True, return_dict=True)
        for i in range(MAX_SPEECH_TOKENS):
            logits = output.logits[:, -1, :]
            # rows: with the text, and with it zeroed; cfg_weight 0 leaves the first
            logits = logits[0:1] + cfg_weight * (logits[0:1] - logits[1:2])
            logits = guard.step(logits, int(generated[0, -1]))
            if temperature != 1.0:
                logits = logits / temperature
            for processor in processors:
                logits = processor(generated, logits)
            token = torch.multinomial(torch.softmax(logits, dim=-1), num_samples=1)
            generated = torch.cat([generated, token], dim=1)
            if int(token) == hp.stop_speech_token:
                break
            embed = t3.speech_emb(token) + t3.speech_pos_emb.get_fixed_embedding(i + 1)
            output = backend(inputs_embeds=torch.cat([embed, embed]), past_key_values=output.past_key_values,
                             output_attentions=True, output_hidden_states=True, return_dict=True)
    finally:
        guard.close()
        config._attn_implementation = attention
    return generated[0, 1:]


def _write_wav(path, audio):
    mono = audio["waveform"][0].float().mean(0).clamp(-1.0, 1.0).cpu().numpy()
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(audio["sample_rate"]))
        handle.writeframes((mono * 32767.0).astype("<i2").tobytes())


class ChatterboxSpeech:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "voice": ("AUDIO", {"tooltip": "A clear sample of the voice to copy, 6 to 15 seconds."}),
                "text": ("STRING", {"multiline": True,
                                    "default": "Hei! Tämä on suomenkielistä puhetta hienosäädetyllä Chatterbox-mallilla."}),
                "model": ([FINNISH, MULTILINGUAL], {"default": FINNISH,
                                                    "tooltip": "The Finnish fine-tune speaks only Finnish. The "
                                                               "multilingual model speaks the language set below."}),
                "language": (list(LANGUAGES), {"default": "English (en)",
                                               "tooltip": "The text's language, for the multilingual model."}),
                "exaggeration": ("FLOAT", {"default": 0.5, "min": 0.25, "max": 2.0, "step": 0.05,
                                           "tooltip": "Above 0.5 the delivery gets more expressive."}),
                "cfg_weight": ("FLOAT", {"default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05,
                                         "tooltip": "How closely the pacing follows the voice sample. With a sample "
                                                    "in another language than the text, 0 keeps its accent out."}),
                "temperature": ("FLOAT", {"default": 0.8, "min": 0.05, "max": 2.0, "step": 0.05}),
                "repetition_penalty": ("FLOAT", {"default": 1.5, "min": 1.0, "max": 3.0, "step": 0.1}),
                "min_p": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 1.0, "step": 0.01}),
                "top_p": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF, "control_after_generate": True}),
                "keep_model_loaded": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "speak"
    CATEGORY = "audio/speech"
    DESCRIPTION = ("Speech in the voice of a sample with Chatterbox: Finnish-NLP's Finnish fine-tune, or Resemble "
                   "AI's multilingual model in 23 languages. Given a list of lines, as Dialogue Script makes, it "
                   "speaks each one with its own voice sample.")

    def speak(self, voice, text, model, language, exaggeration, cfg_weight, temperature, repetition_penalty, min_p,
              top_p, seed, keep_model_loaded):
        if len(voice) not in (1, len(text)):
            raise ValueError(f"Got {len(voice)} voice samples for {len(text)} lines.")
        settings = dict(exaggeration=exaggeration[0], cfg_weight=cfg_weight[0], temperature=temperature[0],
                        repetition_penalty=repetition_penalty[0], min_p=min_p[0], top_p=top_p[0], seed=seed[0])
        try:
            clips = self._speak_all(voice, text, model[0] == FINNISH, language[0].rsplit("(", 1)[1].rstrip(")"),
                                    **settings)
        finally:
            if not keep_model_loaded[0]:
                _unload("chatterbox")
        return (clips,)

    @staticmethod
    def _speak_all(voice, text, finnish, language, exaggeration, cfg_weight, temperature, repetition_penalty, min_p,
                   top_p, seed):
        if _LOADED.get("chatterbox", (None,))[0] != finnish:
            _unload("chatterbox")
            _LOADED["chatterbox"] = (finnish, _load_chatterbox(finnish))
        model = _LOADED["chatterbox"][1]
        tts, mtl, t3_module = _chatterbox_modules()
        progress = comfy.utils.ProgressBar(len(text))
        clips = []
        handle, sample_path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
        try:
            for i, line in enumerate(text):
                comfy.model_management.throw_exception_if_processing_interrupted()
                if not line.strip():
                    raise ValueError(f"Line {i + 1} is empty.")
                _write_wav(sample_path, voice[min(i, len(voice) - 1)])
                torch.manual_seed(seed)
                model.prepare_conditionals(sample_path, exaggeration=exaggeration)
                if finnish:
                    tokens = model.tokenizer.text_to_tokens(tts.punc_norm(line))
                else:
                    tokens = model.tokenizer.text_to_tokens(mtl.punc_norm(line), language_id=language)
                tokens = torch.cat([tokens, tokens], dim=0).to(model.device)  # the CFG pair
                tokens = F.pad(tokens, (1, 0), value=model.t3.hp.start_text_token)
                tokens = F.pad(tokens, (0, 1), value=model.t3.hp.stop_text_token)
                with torch.inference_mode():
                    speech = _sample(model, t3_module, tokens, temperature, cfg_weight, repetition_penalty, min_p,
                                     top_p)
                    speech = tts.drop_invalid_tokens(speech)
                    speech = speech[speech < SPEECH_VOCAB].to(model.device)
                    wav, _ = model.s3gen.inference(speech_tokens=speech, ref_dict=model.conds.gen)
                wav = wav.squeeze(0).detach().cpu().numpy()
                if not finnish:  # the multilingual model can end on noise; ComfyUI_Fill-ChatterBox trims it
                    wav = mtl.ChatterboxMultilingualTTS._trim_trailing_silence(model, wav)
                if model.watermarker is not None:
                    wav = model.watermarker.apply_watermark(wav, sample_rate=model.sr)
                clip = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32)).reshape(1, 1, -1)
                clips.append({"waveform": clip, "sample_rate": model.sr})
                progress.update(1)
        finally:
            os.remove(sample_path)
        return clips


NODE_CLASS_MAPPINGS = {
    "WhisperSpeechToText": WhisperSpeechToText,
    "ChatterboxSpeech": ChatterboxSpeech,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "WhisperSpeechToText": "Whisper Speech to Text",
    "ChatterboxSpeech": "Chatterbox Speech",
}
