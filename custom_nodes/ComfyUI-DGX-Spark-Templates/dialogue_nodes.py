"""Dialogue and subtitle nodes.

Dialogue Script turns a script written as `Name: line` into two parallel lists,
the lines and the voice sample of whoever says each one. Wired into a text to
speech node, the lists make ComfyUI run that node once per line. Join Dialogue
puts the clips back together with a pause between them and writes the
subtitles from the script itself, so their timing is exact and the text needs
no speech recognition.

Join With Pauses does the same for listening exercises: a pause to repeat
each line, dictation with every line read twice, or time to answer.

Segments from Subtitles plans MiniMax H3 Easy video for such a track. H3 makes
only certain frame counts, so it lengthens pauses until every cut falls in a
pause and every shot has one of those counts; the video then stays in sync
with the speech for its whole length.

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
LEVEL_TOOLTIP = "Plays every line at the same loudness, whatever the volume of each voice sample."


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
            "optional": {
                "level_voices": ("BOOLEAN", {"default": True, "tooltip": LEVEL_TOOLTIP}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "FLOAT")
    RETURN_NAMES = ("audio", "subtitles", "seconds")
    FUNCTION = "join"
    CATEGORY = "audio/dialogue"
    DESCRIPTION = ("Joins one clip per line into a single track with pauses between them, every line at the "
                   "same loudness, and writes SRT subtitles timed to the clips.")

    def join(self, audio, lines, speakers, pause_seconds, lead_in_seconds, tail_seconds, names_in_subtitles,
             level_voices=None):
        pause, lead_in, tail = pause_seconds[0], lead_in_seconds[0], tail_seconds[0]
        with_names = names_in_subtitles[0]
        if len(audio) != len(lines):
            raise ValueError(f"Got {len(audio)} clips for {len(lines)} lines; connect the lines output of "
                             "Dialogue Script to the text to speech node and its audio here.")
        waves, rate, channels = _clips(audio, lines, lines)
        if level_voices is None or level_voices[0]:
            waves = level(waves, rate)

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


def _clips(audio, lines, spoken):
    """The clips as waveforms at one sample rate and channel count, after checking each against its line.

    spoken is the text that was read for each line, which can be longer than the line itself when numbers were
    written out for speech; the length check uses it."""
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
        weight = sum(3 if _wide(ch) else 1 for ch in spoken[i])
        if length > 2.0 + 0.2 * weight:
            raise RuntimeError(f"Line {i + 1} came back {length:.1f} s long for {len(spoken[i])} characters "
                               f"({lines[i][:60]!r}): the speech model kept talking after the line. Run "
                               "again with another seed, or raise cfg_weight.")
        if clip["sample_rate"] != rate:
            wave = torchaudio.functional.resample(wave, clip["sample_rate"], rate)
        waves.append(wave)
    channels = max(w.shape[0] for w in waves)
    return [w.expand(channels, -1) if w.shape[0] == 1 else w for w in waves], rate, channels


def level(waves, rate, target_db=-20.0):
    """Each clip scaled to the same speech level, its peaks kept below -1 dBFS.

    The level is the mean power of the clip's 50 ms frames that are within 30 dB of its loudest frame, so
    pauses inside a line do not count. Voice samples recorded at different volumes then give lines that
    play equally loud."""
    frame = max(1, int(0.05 * rate))
    out = []
    for wave in waves:
        mono = wave.mean(0)
        frames = mono.shape[-1] // frame
        if not frames:
            out.append(wave)
            continue
        power = mono[:frames * frame].reshape(frames, frame).pow(2).mean(1)
        if float(power.max()) <= 0.0:
            out.append(wave)
            continue
        active = power[power >= power.max() * 1e-3]
        gain = 10 ** ((target_db - 10 * float(torch.log10(active.mean()))) / 20)
        gain = min(gain, 0.89 / float(wave.abs().max()))
        out.append(wave * gain)
    return out


PAUSE_MODES = ("fixed gap", "listen and repeat", "dictation", "answer gap")


class JoinWithPauses:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (list(PAUSE_MODES), {"tooltip": "fixed gap: the same pause after every line. listen and "
                                                        "repeat: a pause as long as the line times repeat_factor, "
                                                        "for saying it after the speaker. dictation: every line "
                                                        "twice, then a pause for writing. answer gap: gap_seconds "
                                                        "of silence after every line, for answering."}),
                "gap_seconds": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 60.0, "step": 0.1,
                                          "tooltip": "The pause in fixed gap and answer gap, and the pause "
                                                     "between the two readings when a line is read twice."}),
                "repeat_factor": ("FLOAT", {"default": 1.5, "min": 0.5, "max": 6.0, "step": 0.1,
                                            "tooltip": "Listen and repeat, dictation: pause length as a multiple "
                                                       "of the line's length."}),
                "read_twice": ("BOOLEAN", {"default": False,
                                           "tooltip": "Listen and repeat: read every line twice. Dictation always "
                                                      "does."}),
                "lead_in_seconds": ("FLOAT", {"default": 0.3, "min": 0.0, "max": 10.0, "step": 0.05}),
                "tail_seconds": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 30.0, "step": 0.05}),
                "level_voices": ("BOOLEAN", {"default": True, "tooltip": LEVEL_TOOLTIP}),
                # socket-only inputs after the widgets: workflows store widget values by position
                "audio": ("AUDIO", {"tooltip": "One clip per line, in order."}),
                "lines": ("STRING", {"forceInput": True, "tooltip": "The text of each line, for the subtitles."}),
            },
            "optional": {
                "spoken_lines": ("STRING", {"forceInput": True,
                                            "tooltip": "What was actually read for each line, when it differs from "
                                                       "the line, such as numbers written out in words."}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "FLOAT")
    RETURN_NAMES = ("audio", "subtitles", "seconds")
    FUNCTION = "join"
    CATEGORY = "audio/dialogue"
    DESCRIPTION = ("Joins one clip per line into one track with pauses for listening exercises: a fixed gap, "
                   "listen and repeat, dictation or a gap for answering. Writes SRT subtitles timed to the clips.")

    def join(self, mode, gap_seconds, repeat_factor, read_twice, lead_in_seconds, tail_seconds, level_voices, audio,
             lines, spoken_lines=None):
        mode, gap, factor, twice = mode[0], gap_seconds[0], repeat_factor[0], read_twice[0] or mode[0] == "dictation"
        if len(audio) != len(lines):
            raise ValueError(f"Got {len(audio)} clips for {len(lines)} lines.")
        waves, rate, channels = _clips(audio, lines, spoken_lines or lines)
        if level_voices[0]:
            waves = level(waves, rate)

        def silence(seconds):
            return torch.zeros(channels, int(round(seconds * rate)))

        pieces, cues, clock = [silence(lead_in_seconds[0])], [], lead_in_seconds[0]
        for i, wave in enumerate(waves):
            length = wave.shape[-1] / rate
            readings = 2 if twice and mode in ("listen and repeat", "dictation") else 1
            for reading in range(readings):
                cues.append((clock, clock + length, lines[i]))
                pieces.append(wave)
                clock += length
                if reading + 1 < readings:
                    pause = gap if mode == "dictation" else factor * length
                elif mode in ("listen and repeat", "dictation"):
                    pause = factor * length
                elif i + 1 < len(waves) or mode == "answer gap":
                    pause = gap
                else:
                    pause = 0.0
                pieces.append(silence(pause))
                clock += pause
        pieces.append(silence(tail_seconds[0]))
        joined = torch.cat(pieces, dim=-1)
        return ({"waveform": joined.unsqueeze(0), "sample_rate": rate}, format_srt(cues), joined.shape[-1] / rate)


H3_FPS = 24
SHOT_MODES = ("turns", "speak and listen")
RUN_MODES = ("one run per shot", "context segments")
SPEAK_PROMPT = ("integrated_multimodal_description: [Shot 1] Live-action, natural light. A medium close-up of "
                "{picture}. The person (S1) looks at the camera and says in a calm, even voice: <d>[{language}] "
                "{line}</d> The face stays relaxed, with small natural mouth movements and few expressions. The "
                "head and shoulders stay almost still, with no hand gestures.\n\n"
                "overall_soundscape: Quiet room tone.\n\nnon_diegetic_music: N/A")
LISTEN_PROMPT = ("integrated_multimodal_description: [Shot 1] Live-action, natural light. A medium close-up of "
                 "{picture}. The person listens quietly with the mouth closed, looking at the camera, breathing "
                 "calmly and blinking now and then. The face stays still and relaxed.\n\n"
                 "overall_soundscape: Quiet room tone.\n\nnon_diegetic_music: N/A")


class SegmentsFromSubtitles:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "shots": (list(SHOT_MODES), {"tooltip": "turns: one shot per speaker turn, showing whoever speaks. "
                                                        "speak and listen: a speaking shot for each line and a "
                                                        "listening shot for each long pause, for a character who "
                                                        "waits for an answer."}),
                "runs": (list(RUN_MODES), {"tooltip": "one run per shot: each shot is its own MiniMax H3 Easy run, "
                                                      "joined with cuts; needed when shots show different people. "
                                                      "context segments: one Context Segments run, a continuous "
                                                      "take of one person."}),
                "speak_prompt": ("STRING", {"multiline": True, "default": SPEAK_PROMPT,
                                            "tooltip": "Prompt of a speaking shot. {picture} becomes the speaker's "
                                                       "<Picture N> tag, {line} the words, {speaker} the name and "
                                                       "{language} the language."}),
                "listen_prompt": ("STRING", {"multiline": True, "default": LISTEN_PROMPT,
                                             "tooltip": "Prompt of a listening shot, with the same placeholders."}),
                "language": ("STRING", {"default": "Finnish", "tooltip": "Fills {language} in the prompts."}),
                "lead_seconds": ("FLOAT", {"default": 0.25, "min": 0.0, "max": 2.0, "step": 0.05,
                                           "tooltip": "Silence kept at the start of a shot before its first word."}),
                "trail_seconds": ("FLOAT", {"default": 0.25, "min": 0.0, "max": 2.0, "step": 0.05,
                                            "tooltip": "Silence kept after a shot's last word before the cut."}),
                "listen_min_seconds": ("FLOAT", {"default": 2.0, "min": 1.0, "max": 30.0, "step": 0.1,
                                                 "tooltip": "speak and listen: a pause at least this long gets its "
                                                            "own listening shot."}),
                "max_shot_seconds": ("FLOAT", {"default": 15.0, "min": 3.0, "max": 30.0, "step": 0.5,
                                               "tooltip": "A speaker turn longer than this is split between two "
                                                          "lines."}),
                # socket-only inputs after the widgets: workflows store widget values by position
                "audio": ("AUDIO", {"tooltip": "The whole track, from Join Dialogue or Join With Pauses."}),
                "subtitles": ("STRING", {"forceInput": True, "tooltip": "The track's SRT subtitles."}),
            },
            "optional": {
                "speakers": ("STRING", {"forceInput": True,
                                        "tooltip": "Who says each line, as Dialogue Script lists them. Speakers "
                                                   "take <Picture 1>, <Picture 2> and so on in the order they "
                                                   "first speak."}),
                "picture_1": ("IMAGE", {"tooltip": "The first speaker, for the shot_picture output."}),
                "picture_2": ("IMAGE",),
                "picture_3": ("IMAGE",),
                "picture_4": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING", "STRING", "FLOAT", "INT", "AUDIO", "FLOAT", "STRING", "IMAGE")
    RETURN_NAMES = ("audio", "subtitles", "segment_seconds", "prompt", "seconds", "count",
                    "shot_audio", "shot_seconds", "shot_prompt", "shot_picture")
    OUTPUT_IS_LIST = (False, False, False, False, False, False, True, True, True, True)
    FUNCTION = "plan"
    CATEGORY = "audio/dialogue"
    DESCRIPTION = ("Plans MiniMax H3 Easy shots from a speech track and its subtitles. Lengthens pauses where "
                   "needed so that every cut falls in a pause and every shot has a frame count H3 makes exactly; "
                   "the video then stays in sync with the speech. Outputs the adjusted track and subtitles, the "
                   "Context Segments lengths and prompts, and per shot lists for one run per shot.")

    def plan(self, shots, runs, speak_prompt, listen_prompt, language, lead_seconds, trail_seconds,
             listen_min_seconds, max_shot_seconds, audio, subtitles, speakers=None,
             picture_1=None, picture_2=None, picture_3=None, picture_4=None):
        track = audio[0]
        wave = track["waveform"][0].float().cpu()
        rate = track["sample_rate"]
        cues = parse_srt(subtitles[0])
        speakers = speakers or []
        if speakers and len(speakers) != len(cues):
            raise ValueError(f"Got {len(speakers)} speakers for {len(cues)} subtitle lines.")
        plan, inserts = plan_shots(cues, wave.shape[-1], rate, speakers, shots[0], runs[0], lead_seconds[0],
                                   trail_seconds[0], listen_min_seconds[0], max_shot_seconds[0])
        out = insert_silence(wave, inserts)
        frames = sum(shot["frames"] for shot in plan)
        out = _fit_length(out, round(frames * rate / H3_FPS))

        def moved(seconds, end=False):
            # silence inserted where a cue ends goes after it, where a cue starts before it
            sample = round(seconds * rate)
            return (sample + sum(count for at, count in inserts if at < sample or (at == sample and not end))) / rate

        new_cues = [(moved(start), moved(stop, end=True), text) for start, stop, text in cues]
        order = list(dict.fromkeys(speakers))
        pictures = [picture_1, picture_2, picture_3, picture_4]
        prompts, clips, lengths, shot_pictures, clock = [], [], [], [], 0
        for shot in plan:
            name = shot["speaker"]
            number = order.index(name) + 1 if name in order else 1
            template = speak_prompt[0] if shot["kind"] == "speak" else listen_prompt[0]
            line = " ".join(cues[i][2].replace("\n", " ") for i in shot["lines"]) if shot["kind"] == "speak" else ""
            prompts.append(template.replace("{picture}", f"<Picture {number}>").replace("{line}", line)
                           .replace("{speaker}", name or "").replace("{language}", language[0]))
            first, last = round(clock * rate / H3_FPS), round((clock + shot["frames"]) * rate / H3_FPS)
            clips.append({"waveform": out[:, first:last].unsqueeze(0), "sample_rate": rate})
            lengths.append(shot["frames"] / H3_FPS)
            clock += shot["frames"]
            if any(p is not None for p in pictures):
                if number > 4 or pictures[number - 1] is None:
                    raise ValueError(f"No picture is connected for {name or 'speaker ' + str(number)}; connect "
                                     f"picture_{number}.")
                shot_pictures.append(pictures[number - 1][0])
        print(f"[segments] {len(plan)} shots, {frames} frames: "
              + ", ".join(f"{s['kind']} {s['frames']}" for s in plan)
              + f"; {sum(c for _, c in inserts) / rate:.2f} s of silence added")
        return ({"waveform": out.unsqueeze(0), "sample_rate": rate}, format_srt(new_cues),
                ",".join(f"{length:.4f}" for length in lengths), "\n---\n".join(prompts), frames / H3_FPS,
                len(plan), clips, lengths, prompts, shot_pictures)


def _fit_length(wave, samples):
    if wave.shape[-1] >= samples:
        return wave[:, :samples]
    return torch.cat([wave, torch.zeros(wave.shape[0], samples - wave.shape[-1])], dim=-1)


def insert_silence(wave, inserts):
    """wave with count zero samples inserted before sample index at, for each (at, count)."""
    pieces, done = [], 0
    for at, count in sorted(inserts):
        pieces += [wave[:, done:at], torch.zeros(wave.shape[0], count)]
        done = at
    pieces.append(wave[:, done:])
    return torch.cat(pieces, dim=-1)


def plan_shots(cues, samples, rate, speakers, shots, runs, lead, trail, listen_min, max_shot, fps=H3_FPS):
    """Groups the lines into shots and gives each shot a frame count MiniMax H3 makes exactly.

    H3 makes 17k+5 frames for a shot that starts a run and 17k frames for a later Context Segment. Cuts go
    in pauses, at least lead seconds before the next line and trail seconds after the last one. When no
    allowed frame count puts a cut there, the pause is lengthened. Returns the shots as dicts (kind,
    speaker, lines, frames) and the silence to insert, as (sample index, sample count) pairs."""
    if not cues:
        raise ValueError("The subtitles have no cues.")
    starts = [min(round(start * rate), samples) for start, _, _ in cues]
    ends = [min(max(round(end * rate), starts[i]), samples) for i, (_, end, _) in enumerate(cues)]
    lead, trail = round(lead * rate), round(trail * rate)
    block = round(17 * rate / fps)

    def speaker(i):
        return speakers[i] if i < len(speakers) else ""

    groups = []
    for i in range(len(cues)):
        last = groups[-1] if groups else None
        if (last and last["kind"] == "speak" and speaker(last["lines"][-1]) == speaker(i)
                and ends[i] - starts[last["lines"][0]] <= max_shot * rate):
            last["lines"].append(i)
        else:
            groups.append({"kind": "speak", "speaker": speaker(i), "lines": [i]})
        pause = (starts[i + 1] if i + 1 < len(cues) else samples) - ends[i]
        if shots == "speak and listen" and pause >= max(listen_min * rate, lead + trail + 2 * block):
            groups.append({"kind": "listen", "speaker": speaker(i), "lines": [i]})

    def next_speech(k):
        later = [g for g in groups[k + 1:] if g["kind"] == "speak"]
        return starts[later[0]["lines"][0]] if later else samples

    inserts, shift, clock, cut = [], 0, 0, 0
    for k, group in enumerate(groups):
        following = groups[k + 1] if k + 1 < len(groups) else None
        speech = next_speech(k)
        quiet_from = ends[group["lines"][-1]] if group["kind"] == "speak" else cut
        if following is None:
            low = high = prefer = samples
        elif group["kind"] == "speak" and following["kind"] == "speak":
            low, high = ends[group["lines"][-1]] + trail, speech - lead
            prefer = (low + high) // 2
        elif group["kind"] == "speak":
            # cut soon after the line, leaving the listening shot at least one block
            low, high = ends[group["lines"][-1]] + trail, speech - lead - block
            prefer = low
        else:
            low, high = cut, speech - lead
            prefer = high
        base = 5 if k == 0 or runs == "one run per shot" else 17

        def source(frames):
            """Where a cut after this many frames falls on the input track."""
            return round((clock + frames) * rate / fps) - shift

        frames = base
        while source(frames) < low:
            frames += 17
        fitting = []
        while source(frames) <= high:
            fitting.append(frames)
            frames += 17
        if fitting:
            frames = min(fitting, key=lambda f: abs(source(f) - prefer))
        else:
            frames = base
            while source(frames) < low:
                frames += 17
            # lengthen the pause so the next line starts lead after the cut; the silence goes inside the pause
            added = source(frames) - high
            inserts.append((min(max(high, quiet_from), speech), added))
            shift += added
        if frames > 30 * fps:
            raise ValueError(f"Shot {k + 1} would be {frames / fps:.1f} s long; H3 makes at most 30 s. Split "
                             "the long line or lower max_shot_seconds.")
        cut = source(frames)
        clock += frames
        group["frames"] = frames
    return groups, inserts


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
    "JoinWithPauses": JoinWithPauses,
    "SegmentsFromSubtitles": SegmentsFromSubtitles,
    "TimestampsToSubtitles": TimestampsToSubtitles,
    "BurnInSubtitles": BurnInSubtitles,
    "SaveSubtitles": SaveSubtitles,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DialogueScript": "Dialogue Script",
    "JoinDialogue": "Join Dialogue",
    "JoinWithPauses": "Join With Pauses",
    "SegmentsFromSubtitles": "Segments from Subtitles",
    "TimestampsToSubtitles": "Word Timestamps to Subtitles",
    "BurnInSubtitles": "Burn In Subtitles",
    "SaveSubtitles": "Save Subtitles",
}
