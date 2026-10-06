"""Dialogue and subtitle nodes.

Dialogue Script turns a script written as `Name: line` into two parallel lists,
the lines and the voice sample of whoever says each one. Wired into a text to
speech node, the lists make ComfyUI run that node once per line. Join Dialogue
puts the clips back together with a pause between them and writes the
subtitles from the script itself, so their timing is exact and the text needs
no speech recognition.

Word Timestamps to Subtitles is for audio that has no script: it groups the
per-word timings Whisper Speech to Text prints into subtitle cues. Burn In
Subtitles draws cues onto video frames and Save Subtitles writes an .srt file.
Subtitles travel between nodes as SRT text.

Burn In Subtitles draws with DejaVu Sans, which the image ships and which covers
Latin, Greek and Cyrillic. Pillow's built-in font, the fallback, has Latin only.
"""

import os
import re
import unicodedata

import numpy as np
import torch
import torchaudio
from PIL import Image, ImageDraw, ImageFont

import folder_paths

_NAMED_LINE = re.compile(r"^\s*([^:\n]{1,40}?)\s*:\s*(.+?)\s*$")
_WORD_STAMP = re.compile(r"^\s*\[\s*([0-9.]+)\s*-\s*([0-9.]+)\s*\]\s*(.*?)\s*$")
_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _wide(token):
    """True for text made only of full-width characters, such as Chinese or Japanese."""
    return bool(token) and all(unicodedata.east_asian_width(ch) in ("W", "F") for ch in token)


def _srt_timestamp(seconds):
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def format_srt(cues):
    """cues: iterable of (start_seconds, end_seconds, text)."""
    blocks = []
    for index, (start, end, text) in enumerate(cues, 1):
        blocks.append(f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text.strip()}\n")
    return "\n".join(blocks)


def parse_srt(text):
    """SRT text to a list of (start_seconds, end_seconds, text)."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if not match:
                continue
            h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in match.groups())
            start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
            end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
            caption = "\n".join(lines[i + 1:]).strip()
            if caption:
                cues.append((start, end, caption))
            break
    return cues


class DialogueScript:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "script": ("STRING", {
                    "multiline": True,
                    "default": "Anna: Hi! How are you?\nBen: Fine, thanks. And you?",
                    "tooltip": "One turn per line, written as Name: text. The first name to speak "
                               "uses voice_1, the second voice_2, up to four. A line without a name "
                               "is spoken by the previous speaker. Lines starting with # are skipped.",
                }),
                "voice_1": ("AUDIO", {"tooltip": "A clear sample of the first speaker's voice, 6 to 15 seconds."}),
            },
            "optional": {
                "voice_2": ("AUDIO",),
                "voice_3": ("AUDIO",),
                "voice_4": ("AUDIO",),
            },
        }

    RETURN_TYPES = ("STRING", "AUDIO", "STRING", "INT")
    RETURN_NAMES = ("lines", "voices", "speakers", "count")
    OUTPUT_IS_LIST = (True, True, True, False)
    FUNCTION = "split"
    CATEGORY = "audio/dialogue"
    DESCRIPTION = ("Splits a Name: line script into a list of lines and a matching list of voice "
                   "samples, so the text to speech node after it runs once per line.")

    def split(self, script, voice_1, voice_2=None, voice_3=None, voice_4=None):
        voices = [voice_1, voice_2, voice_3, voice_4]
        order, lines, speakers = [], [], []
        current = None
        for raw in script.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            match = _NAMED_LINE.match(raw)
            # "We leave at 10:30" is a line, not a speaker called "We leave at 10"
            if match and len(match.group(1).split()) <= 4 and not set(match.group(1)) & set(".!?,;\"0123456789"):
                current, text = match.group(1), match.group(2)
            else:
                text = raw.strip()
                if current is None:
                    current = "Speaker 1"
            if current not in order:
                order.append(current)
            lines.append(text)
            speakers.append(current)
        if not lines:
            raise ValueError("The script has no lines. Write one turn per line as Name: text.")
        if len(order) > 4:
            raise ValueError(f"The script has {len(order)} speakers ({', '.join(order)}); at most 4 are supported.")
        missing = [name for i, name in enumerate(order) if voices[i] is None]
        if missing:
            raise ValueError(f"No voice is connected for {', '.join(missing)}. Speakers take voice_1, "
                             f"voice_2 and so on in the order they first speak: {', '.join(order)}.")
        voice_list = [voices[order.index(name)] for name in speakers]
        return (lines, voice_list, speakers, len(lines))


class JoinDialogue:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO", {"tooltip": "One clip per line, in script order."}),
                "pause_seconds": ("FLOAT", {"default": 0.4, "min": 0.0, "max": 10.0, "step": 0.05,
                                            "tooltip": "Silence between two lines."}),
                "lead_in_seconds": ("FLOAT", {"default": 0.3, "min": 0.0, "max": 10.0, "step": 0.05,
                                              "tooltip": "Silence before the first line."}),
                "tail_seconds": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 10.0, "step": 0.05,
                                           "tooltip": "Silence after the last line."}),
                "names_in_subtitles": ("BOOLEAN", {"default": False,
                                                   "tooltip": "Start each subtitle with the speaker's name."}),
                # socket-only inputs after the widgets: workflows store widget values by position
                "lines": ("STRING", {"forceInput": True}),
                "speakers": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "FLOAT")
    RETURN_NAMES = ("audio", "subtitles", "seconds")
    FUNCTION = "join"
    CATEGORY = "audio/dialogue"
    DESCRIPTION = ("Joins one clip per line into a single track with pauses between them and "
                   "writes SRT subtitles timed to the clips.")

    def join(self, audio, lines, speakers, pause_seconds, lead_in_seconds, tail_seconds, names_in_subtitles):
        pause, lead_in, tail = pause_seconds[0], lead_in_seconds[0], tail_seconds[0]
        with_names = names_in_subtitles[0]
        if len(audio) != len(lines):
            raise ValueError(f"Got {len(audio)} clips for {len(lines)} lines; connect the lines output of "
                             "Dialogue Script to the text to speech node and its audio here.")
        rate = max(clip["sample_rate"] for clip in audio)
        waves = []
        for i, clip in enumerate(audio):
            wave = clip["waveform"][0].float().cpu()
            if wave.shape[-1] < 0.1 * clip["sample_rate"]:
                raise RuntimeError(f"Line {i + 1} came back empty ({lines[i][:60]!r}). The text to speech "
                                   "node failed on it. Chatterbox multilingual returns silence instead of an "
                                   "error; connect a Preview Any to its message output to read why.")
            # a model that misses its stop token talks on for up to 40 s, and the video would follow
            length = wave.shape[-1] / clip["sample_rate"]
            weight = sum(3 if _wide(ch) else 1 for ch in lines[i])
            if length > 2.0 + 0.2 * weight:
                raise RuntimeError(f"Line {i + 1} came back {length:.1f} s long for {len(lines[i])} characters "
                                   f"({lines[i][:60]!r}): the speech model kept talking after the line. Run "
                                   "again with another seed, or raise cfg_weight.")
            if clip["sample_rate"] != rate:
                wave = torchaudio.functional.resample(wave, clip["sample_rate"], rate)
            waves.append(wave)
        channels = max(w.shape[0] for w in waves)
        waves = [w.expand(channels, -1) if w.shape[0] == 1 else w for w in waves]

        def silence(seconds):
            return torch.zeros(channels, int(round(seconds * rate)))

        pieces, cues, clock = [silence(lead_in)], [], lead_in
        for i, wave in enumerate(waves):
            if i:
                pieces.append(silence(pause))
                clock += pause
            length = wave.shape[-1] / rate
            text = f"{speakers[i]}: {lines[i]}" if with_names else lines[i]
            cues.append((clock, clock + length, text))
            pieces.append(wave)
            clock += length
        pieces.append(silence(tail))
        joined = torch.cat(pieces, dim=-1)
        seconds = joined.shape[-1] / rate
        return ({"waveform": joined.unsqueeze(0), "sample_rate": rate}, format_srt(cues), seconds)


class TimestampsToSubtitles:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "max_characters": ("INT", {"default": 42, "min": 10, "max": 200,
                                           "tooltip": "Longest subtitle line before a new cue starts."}),
                "max_seconds": ("FLOAT", {"default": 6.0, "min": 0.5, "max": 30.0, "step": 0.5,
                                          "tooltip": "Longest time one cue stays on screen."}),
                "max_gap_seconds": ("FLOAT", {"default": 0.8, "min": 0.1, "max": 10.0, "step": 0.1,
                                              "tooltip": "A pause longer than this starts a new cue."}),
                "timestamps": ("STRING", {"forceInput": True,
                                          "tooltip": "Word timings, one per line as [start - end] word."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("subtitles",)
    FUNCTION = "group"
    CATEGORY = "audio/dialogue"
    DESCRIPTION = "Groups per-word timings from speech to text into SRT subtitle cues."

    def group(self, timestamps, max_characters, max_seconds, max_gap_seconds):
        words = []
        for line in timestamps.splitlines():
            match = _WORD_STAMP.match(line)
            if match and match.group(3):
                words.append((float(match.group(1)), float(match.group(2)), match.group(3)))
        if not words:
            raise ValueError("No word timings found. Connect the timestamps output of Whisper Speech to Text, "
                             "not its text output.")
        cues, text, start, end = [], "", None, None
        for w_start, w_end, word in words:
            joiner = "" if not text or (_wide(word) and _wide(text[-1])) else " "
            candidate = text + joiner + word
            if text and (len(candidate) > max_characters or w_end - start > max_seconds
                         or w_start - end > max_gap_seconds or text[-1] in ".!?。！？"):
                cues.append((start, end, text))
                text, start = word, w_start
            else:
                text = candidate
                start = w_start if start is None else start
            end = w_end
        cues.append((start, end, text))
        return (format_srt(cues),)


class BurnInSubtitles:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001}),
                "font_size": ("FLOAT", {"default": 4.5, "min": 1.0, "max": 20.0, "step": 0.5,
                                        "tooltip": "Text height as a percentage of the frame height."}),
                "position": (["bottom", "top"], {"default": "bottom"}),
                "offset_seconds": ("FLOAT", {"default": 0.0, "min": -600.0, "max": 600.0, "step": 0.05,
                                             "tooltip": "Shifts every cue; positive shows them later."}),
                "subtitles": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "burn"
    CATEGORY = "image/text"
    DESCRIPTION = "Draws SRT subtitles onto video frames, white with a black outline."

    @staticmethod
    def _font(size):
        for path in _FONTS:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default(size=size)

    @staticmethod
    def _wrap(text, font, max_width):
        lines = []
        for paragraph in text.split("\n"):
            current = ""
            for word in paragraph.split():
                candidate = f"{current} {word}" if current else word
                if font.getlength(candidate) <= max_width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = word
                # a run with no spaces to break at, as in Chinese, is cut where it overflows
                while len(current) > 1 and font.getlength(current) > max_width:
                    cut = len(current) - 1
                    while cut > 1 and font.getlength(current[:cut]) > max_width:
                        cut -= 1
                    lines.append(current[:cut])
                    current = current[cut:]
            if current:
                lines.append(current)
        return "\n".join(lines)

    @classmethod
    def _render(cls, width, height, text, position, font_size):
        """One cue as (rgb, alpha) tensors the size of the frame."""
        size = max(10, int(round(font_size / 100.0 * height)))
        font = cls._font(size)
        stroke = max(1, round(size * 0.08))
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        block = cls._wrap(text, font, int(width * 0.9))
        spacing = int(size * 0.2)
        box = draw.multiline_textbbox((0, 0), block, font=font, spacing=spacing, stroke_width=stroke, align="center")
        margin = int(height * 0.05)
        x = (width - (box[2] - box[0])) / 2 - box[0]
        y = height - margin - box[3] if position == "bottom" else margin - box[1]
        draw.multiline_text((x, y), block, font=font, fill=(255, 255, 255, 255), spacing=spacing,
                            stroke_width=stroke, stroke_fill=(0, 0, 0, 255), align="center")
        pixels = np.asarray(layer).astype(np.float32) / 255.0
        return torch.from_numpy(pixels[..., :3]), torch.from_numpy(pixels[..., 3:4])

    def burn(self, images, subtitles, fps, font_size, position, offset_seconds):
        out = images.clone()
        frames, height, width = images.shape[0], images.shape[1], images.shape[2]
        for start, end, text in parse_srt(subtitles):
            first = max(0, int(round((start + offset_seconds) * fps)))
            last = min(frames, int(round((end + offset_seconds) * fps)))
            if first >= last:
                continue
            rgb, alpha = self._render(width, height, text, position, font_size)
            rgb = rgb.to(device=out.device, dtype=out.dtype)
            alpha = alpha.to(device=out.device, dtype=out.dtype)
            span = out[first:last, ..., :3]
            out[first:last, ..., :3] = span * (1.0 - alpha) + rgb * alpha
        return (out,)


class SaveSubtitles:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filename_prefix": ("STRING", {"default": "subtitles/ComfyUI"}),
                "subtitles": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "audio/dialogue"
    DESCRIPTION = "Writes SRT subtitles to the output folder. Most players load an .srt next to the video."

    def save(self, subtitles, filename_prefix):
        folder, name, counter, _, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory())
        filename = f"{name}_{counter:05}_.srt"
        with open(os.path.join(folder, filename), "w", encoding="utf-8") as handle:
            handle.write(subtitles)
        return {"ui": {"text": [subtitles]}}


NODE_CLASS_MAPPINGS = {
    "DialogueScript": DialogueScript,
    "JoinDialogue": JoinDialogue,
    "TimestampsToSubtitles": TimestampsToSubtitles,
    "BurnInSubtitles": BurnInSubtitles,
    "SaveSubtitles": SaveSubtitles,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DialogueScript": "Dialogue Script",
    "JoinDialogue": "Join Dialogue",
    "TimestampsToSubtitles": "Word Timestamps to Subtitles",
    "BurnInSubtitles": "Burn In Subtitles",
    "SaveSubtitles": "Save Subtitles",
}
