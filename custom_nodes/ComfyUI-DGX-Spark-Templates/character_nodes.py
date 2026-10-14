"""Load Character and Save Character.

A character is a picture and a short recording of its voice, kept side by side
in input/cast/ as <Name>.png and <Name>.flac (.wav, .mp3, .ogg and .m4a load
too). Load Character lists the names that have both and returns the picture,
the voice and the name, so a workflow that makes someone speak or appear picks
them from one list instead of loading two files. Save Character adds one and
refuses to replace an existing character unless overwrite is on.

Names are limited to letters, digits, spaces, hyphens and apostrophes, so a name
can never point outside the folder. The list is read when the node definitions
load; a character saved since then validates and loads anyway, and shows in the
list after the page or the node definitions are refreshed.
"""

import os
import re

import av
import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths
from comfy_extras.nodes_audio import load as load_audio

FOLDER = "cast"
AUDIO_EXTENSIONS = (".flac", ".wav", ".mp3", ".ogg", ".m4a")
_NAME = re.compile(r"[^\W_][\w '\-]{0,59}")


def cast_dir():
    return os.path.join(folder_paths.get_input_directory(), FOLDER)


def characters():
    """Names that have both a picture and a voice sample, sorted."""
    if not os.path.isdir(cast_dir()):
        return []
    files = os.listdir(cast_dir())
    pictures = {name for name, ext in map(os.path.splitext, files) if ext.lower() == ".png"}
    voices = {name for name, ext in map(os.path.splitext, files) if ext.lower() in AUDIO_EXTENSIONS}
    return sorted(pictures & voices, key=str.casefold)


def character_path(name, extension):
    """input/cast/<name><extension>, after checking the name cannot leave the folder."""
    if not _NAME.fullmatch(name):
        raise ValueError(f"{name!r} is not a usable character name. Use letters, digits, spaces, hyphens and "
                         "apostrophes, up to 60 characters.")
    folder = os.path.realpath(cast_dir())
    path = os.path.realpath(os.path.join(folder, name + extension))
    if os.path.dirname(path) != folder:
        raise ValueError(f"{name!r} is not a usable character name.")
    return path


def voice_path(name):
    for extension in AUDIO_EXTENSIONS:
        path = character_path(name, extension)
        if os.path.exists(path):
            return path
    return None


class LoadCharacter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "character": (characters() or ["none saved yet"],
                              {"tooltip": "Characters saved in input/cast/ as a picture and a voice sample."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "STRING")
    RETURN_NAMES = ("picture", "voice", "name")
    FUNCTION = "load"
    CATEGORY = "utilities"
    DESCRIPTION = "Loads a character's picture and voice sample from input/cast/."

    @classmethod
    def VALIDATE_INPUTS(cls, character):
        if character not in characters():
            return f"No character named {character!r} in input/cast/: it needs a .png and a voice sample."
        return True

    @classmethod
    def IS_CHANGED(cls, character):
        paths = (character_path(character, ".png"), voice_path(character))
        return tuple(os.path.getmtime(p) for p in paths if p and os.path.exists(p))

    def load(self, character):
        with Image.open(character_path(character, ".png")) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            picture = torch.from_numpy(np.asarray(image).astype(np.float32) / 255.0)[None]
        waveform, sample_rate = load_audio(voice_path(character))
        return (picture, {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}, character)


class SaveCharacter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "name": ("STRING", {"default": "", "tooltip": "Letters, digits, spaces, hyphens and apostrophes."}),
                "overwrite": ("BOOLEAN", {"default": False,
                                          "tooltip": "Replace a character of the same name."}),
                # socket-only inputs after the widgets: workflows store widget values by position
                "picture": ("IMAGE",),
                "voice": ("AUDIO",),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("name",)
    OUTPUT_NODE = True
    FUNCTION = "save"
    CATEGORY = "utilities"
    DESCRIPTION = ("Saves a picture and a voice sample as a character in input/cast/, where Load Character "
                   "finds it.")

    def save(self, name, overwrite, picture, voice):
        name = " ".join(name.split())
        png = character_path(name, ".png")
        existing = [p for p in [png] + [character_path(name, e) for e in AUDIO_EXTENSIONS] if os.path.exists(p)]
        if existing and not overwrite:
            raise ValueError(f"A character named {name} already exists. Pick another name, or switch on "
                             "overwrite to replace it.")
        os.makedirs(cast_dir(), exist_ok=True)
        for path in existing:
            os.remove(path)
        pixels = np.clip(picture[0].cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
        Image.fromarray(pixels).save(png)
        self._write_flac(character_path(name, ".flac"), voice)
        return {"ui": {"images": [{"filename": os.path.basename(png), "subfolder": FOLDER, "type": "input"}]},
                "result": (name,)}

    @staticmethod
    def _write_flac(path, audio):
        waveform = audio["waveform"][0].float().cpu()
        layout = "mono" if waveform.shape[0] == 1 else "stereo"
        with av.open(path, mode="w", format="flac") as container:
            stream = container.add_stream("flac", rate=audio["sample_rate"], layout=layout)
            frame = av.AudioFrame.from_ndarray(waveform[:2].movedim(0, 1).reshape(1, -1).numpy(), format="flt",
                                               layout=layout)
            frame.sample_rate = audio["sample_rate"]
            frame.pts = 0
            container.mux(stream.encode(frame))
            container.mux(stream.encode(None))


NODE_CLASS_MAPPINGS = {
    "LoadCharacter": LoadCharacter,
    "SaveCharacter": SaveCharacter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadCharacter": "Load Character",
    "SaveCharacter": "Save Character",
}
