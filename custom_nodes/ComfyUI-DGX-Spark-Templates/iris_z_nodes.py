"""Iris-Z small on core's FLUX.2 Klein 9B model: image to depth, depth to image, text to image and depth.

Iris-Z (Speridlabs, speridlabs/iris-z, arXiv 2611.16010) is FLUX.2 Klein base 9B trained to denoise an image and
its depth map in one sequence. Its transformer loads into core's Flux2 model unchanged; the one addition is
`modality_in`, an embedding added to every image token that says what the token is: a generated picture, a
generated depth map, a given picture or a given depth map. The depth map goes through the FLUX.2 VAE as a grey
picture holding log depth scaled per image to [-1, 1] (near -1).

Sampling runs on core's sampler. A given grid (the photo when estimating depth, the depth map when drawing a
picture) is a reference latent with core's `index_timestep_zero` method, which modulates it at timestep 0 as
Iris-Z does. `IrisZTask` adds the modality embedding to the tokens and puts the two grids at the time positions
Iris-Z was trained with: the picture at 0, the depth map at 1. For text to image and depth, the latent holds the
picture and the depth map side by side and both are denoised together.

Written anew for ComfyUI from the Iris-Z SDK (https://github.com/speridlabs/iris-z-sdk, commit 45ac2d2, Apache-2.0,
Copyright Speridlabs): `iris_z/model/model.py`, `iris_z/rgbd.py`, `iris_z/depth.py`, `iris_z/pipeline.py`. No SDK
code is imported. The weights are under the FLUX non-commercial licence.
"""
import json
import logging
import math
import os
import re

import numpy as np
import torch
from PIL import Image
from safetensors.torch import load_file

import comfy.model_management
import comfy.sd
import folder_paths

RGB, DEPTH, GIVEN_RGB, GIVEN_DEPTH = 0, 1, 2, 3
DEPTH_TIME = 1.0
TASKS = ("image to depth", "depth to image", "text to image and depth")
FIRST_SHARD = re.compile(r"-0*1-of-(\d+)\.safetensors$")
SMALL = {"base_model": "flux.2-klein-base-9b", "num_modalities": 4}
FAR_COLOUR_END = 0.85  # the SDK's colour scale stops at 85 % of turbo_r: its end is near-black indigo
DEPTH_FORMATS = ("disparity (bright = near)", "depth (bright = far)", "Iris-Z depth", "Iris-Z colours")


def first_shards():
    """First shards that have Iris-Z's empty_prompt.safetensors beside them."""
    names = folder_paths.get_filename_list("diffusion_models")
    folders = {os.path.dirname(n) for n in names if os.path.basename(n) == "empty_prompt.safetensors"}
    return [n for n in names if FIRST_SHARD.search(n) and os.path.dirname(n) in folders]


def shard_paths(first):
    """All shards of a Hugging Face sharded checkpoint, from the path of its first one."""
    match = FIRST_SHARD.search(first)
    if match is None:
        raise ValueError(f"{os.path.basename(first)} is not the first shard (name-00001-of-0000N.safetensors)")
    count, digits = int(match.group(1)), len(match.group(1))
    stem = first[:match.start()]
    paths = [f"{stem}-{k:0{digits}d}-of-{match.group(1)}.safetensors" for k in range(1, count + 1)]
    missing = [os.path.basename(p) for p in paths if not os.path.isfile(p)]
    if missing:
        raise FileNotFoundError(f"missing shards next to {os.path.basename(first)}: {', '.join(missing)}")
    return paths


def load(model_name):
    """MODEL with the modality table in its transformer options, and the empty-prompt conditioning."""
    first = folder_paths.get_full_path_or_raise("diffusion_models", model_name)
    folder = os.path.dirname(first)
    config_path = os.path.join(folder, "config.json")
    config = json.load(open(config_path)) if os.path.isfile(config_path) else SMALL
    if config.get("base_model") != SMALL["base_model"]:
        raise ValueError(f"{model_name}: base model {config.get('base_model')!r}; only Iris-Z small "
                         "(FLUX.2 Klein base 9B) is supported")
    sd = {}
    for path in shard_paths(first):
        sd.update(load_file(path))
    if "modality_in.weight" not in sd:
        raise ValueError(f"{model_name}: no modality_in.weight, not an Iris-Z checkpoint")
    modality = sd.pop("modality_in.weight")
    model = comfy.sd.load_diffusion_model_state_dict(sd)
    if model is None:
        raise RuntimeError(f"core did not recognise {model_name} as a FLUX.2 model")
    model.model_options.setdefault("transformer_options", {})["iris_z"] = {"modality": modality,
                                                                          "config": config}
    empty_path = os.path.join(folder, "empty_prompt.safetensors")
    if not os.path.isfile(empty_path):
        raise FileNotFoundError(f"empty_prompt.safetensors is missing next to {os.path.basename(first)}")
    empty = load_file(empty_path)["empty_prompt"]
    if empty.ndim == 2:
        empty = empty[None]
    return model, [[empty, {}]]


def make_patch(task, table):
    def patch(args):
        img, ids = args["img"], args["img_ids"].clone()
        refs = args["transformer_options"].get("reference_image_num_tokens", [])
        n = img.shape[1]
        modality = torch.empty(ids.shape[:2], dtype=torch.long, device=img.device)
        if task == "text to image and depth":
            if refs:
                raise ValueError("Iris-Z text to image and depth takes no reference latent")
            half = int(ids[0, :, 2].max().item() + 1) // 2  # picture left, depth map right
            depth = ids[..., 2] >= half
            ids[..., 2] -= depth.to(ids.dtype) * half
            ids[..., 0] = depth.to(ids.dtype) * DEPTH_TIME
            modality[:] = torch.where(depth, DEPTH, RGB)
        else:
            if len(refs) != 1:
                raise ValueError(f"Iris-Z {task} needs exactly one reference latent, got {len(refs)}")
            start = n - refs[0]
            if task == "image to depth":
                (target, target_time), (given, given_time) = (DEPTH, DEPTH_TIME), (GIVEN_RGB, 0.0)
            else:
                (target, target_time), (given, given_time) = (RGB, 0.0), (GIVEN_DEPTH, DEPTH_TIME)
            modality[:, :start], modality[:, start:] = target, given
            ids[:, :start, 0], ids[:, start:, 0] = target_time, given_time
        args["img"] = img + table.to(img.device, img.dtype)[modality]
        args["img_ids"] = ids
        return args

    return patch


def choose_resolution(source_width, source_height, target_side, multiple=16, area_tolerance=0.2):
    """About target_side^2 pixels, same aspect, multiples of 16, never larger than the source (as the SDK)."""
    source_aspect = source_width / source_height
    target_area = min(target_side * target_side, source_width * source_height)
    best = None
    for height in range(multiple, source_height + 1, multiple):
        for width in range(multiple, source_width + 1, multiple):
            area = width * height
            if abs(area - target_area) > area_tolerance * target_area:
                continue
            key = (round(abs(math.log((width / height) / source_aspect)), 6), abs(area - target_area))
            if best is None or key < best[0]:
                best = (key, width, height)
    if best is None:
        raise ValueError(f"no size near {target_side}^2 fits {source_width}x{source_height}")
    return best[1], best[2]


def enlarged_size(source_width, source_height, target_side):
    area = source_width * source_height
    if area < target_side * target_side:
        factor = (target_side * target_side / area) ** 0.5
        source_width, source_height = round(source_width * factor), round(source_height * factor)
    return choose_resolution(source_width, source_height, target_side)


def encode_log_depth(log_depth):
    """The 2nd to 98th percentile spans [-1, 1] (Iris-Z small has no sky ceiling)."""
    low, high = np.percentile(log_depth, [2, 98])
    return np.clip(2.0 * (log_depth - low) / max(high - low, 1e-6) - 1.0, -1.0, 1.0).astype(np.float32)


def colourize(log_depth):
    import matplotlib

    low, high = np.percentile(log_depth, [1, 99.5])
    normalized = np.clip((log_depth - low) / max(high - low, 1e-6), 0, 1) * FAR_COLOUR_END
    return matplotlib.colormaps["turbo_r"](normalized)[..., :3].astype(np.float32)


def read_coloured(rgb):
    import matplotlib
    from scipy.spatial import cKDTree

    positions = np.linspace(0.0, 1.0, 1024)
    scale = matplotlib.colormaps["turbo_r"](positions * FAR_COLOUR_END)[:, :3]
    _, nearest = cKDTree(scale).query(rgb.reshape(-1, 3))
    return positions[nearest].reshape(rgb.shape[:2])


class IrisZModelLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model_name": (first_shards(), {"tooltip": "The first transformer shard in the Iris-Z-small folder "
                                                       "of speridlabs/iris-z under diffusion_models; the other "
                                                       "shards and empty_prompt.safetensors sit next to it."}),
        }}

    RETURN_TYPES = ("MODEL", "CONDITIONING")
    RETURN_NAMES = ("model", "empty_prompt")
    FUNCTION = "load"
    CATEGORY = "advanced/loaders"
    DESCRIPTION = ("Loads Iris-Z small into core's FLUX.2 Klein 9B model. empty_prompt is Iris-Z's own encoding of "
                   "an empty prompt: image to depth needs no text encoder.")

    def load(self, model_name):
        return load(model_name)


class IrisZTask:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",),
            "task": (TASKS, {"tooltip": "image to depth: the photo is a reference latent, the latent is the depth "
                                        "map. depth to image: the depth map is the reference latent. text to image "
                                        "and depth: an empty latent twice as wide, picture left, depth map right."}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "patch"
    CATEGORY = "advanced/model"
    DESCRIPTION = "Sets an Iris-Z model up for one task: token modalities, grid positions, the given grid at t = 0."

    def patch(self, model, task):
        iris = model.model_options.get("transformer_options", {}).get("iris_z")
        if iris is None:
            raise ValueError("not an Iris-Z model: load it with the Iris-Z Model Loader")
        m = model.clone()
        m.set_model_post_input_patch(make_patch(task, iris["modality"]))
        m.add_object_patch("diffusion_model.params.default_ref_method", "index_timestep_zero")
        return (m,)


class IrisZPrepareImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "resolution": (["auto", "512", "1024"], {"tooltip": "Iris-Z was trained at 512 and 1024. auto: 512 "
                                                                "under 0.5 megapixels, else 1024. Never enlarges."}),
        }}

    RETURN_TYPES = ("IMAGE", "INT", "INT")
    RETURN_NAMES = ("image", "width", "height")
    FUNCTION = "prepare"
    CATEGORY = "image/transform"
    DESCRIPTION = "Resizes a photo the way Iris-Z's pipeline does (Lanczos, same aspect, multiples of 16)."

    def prepare(self, image, resolution):
        h, w = image.shape[1:3]
        side = (512 if w * h < 500_000 else 1024) if resolution == "auto" else int(resolution)
        width, height = choose_resolution(w, h, side)
        out = []
        for frame in image:
            pil = Image.fromarray((frame[..., :3].cpu().numpy() * 255).round().clip(0, 255).astype(np.uint8))
            out.append(torch.from_numpy(np.asarray(pil.resize((width, height), Image.LANCZOS),
                                                   dtype=np.float32) / 255.0))
        return torch.stack(out), width, height


class IrisZDepthDecode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "decoded": ("IMAGE", {"tooltip": "The VAE-decoded depth latent: a grey picture of log depth."}),
        }, "optional": {
            "match_size": ("IMAGE", {"tooltip": "Resize the depth to this image's size (the original photo)."}),
        }}

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("depth", "colours")
    FUNCTION = "decode"
    CATEGORY = "image/preprocessors"
    DESCRIPTION = ("Iris-Z depth as a picture: near bright, linear in log depth, like the other depth nodes here; "
                   "and Iris-Z's colouring (near red, far blue).")

    def decode(self, decoded, match_size=None):
        depths, colours = [], []
        for frame in decoded:
            log_depth = (frame[..., :3].float().mean(dim=-1) * 2.0 - 1.0).clamp(-1, 1).cpu().numpy()
            if match_size is not None:
                h, w = match_size.shape[1:3]
                log_depth = np.asarray(Image.fromarray(log_depth.astype(np.float32)).resize((w, h), Image.BILINEAR))
            near_bright = (1.0 - log_depth) / 2.0
            depths.append(torch.from_numpy(np.repeat(near_bright[..., None], 3, axis=-1).astype(np.float32)))
            colours.append(torch.from_numpy(colourize(log_depth)))
        return torch.stack(depths), torch.stack(colours)


class IrisZDepthEncode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "depth": ("IMAGE",),
            "depth_format": (DEPTH_FORMATS, {"tooltip": "disparity: bright = near, as Depth Anything, Lotus and "
                                                        "MoGe here give it. Iris-Z depth: the depth output of "
                                                        "Iris-Z Depth Decode. Iris-Z colours: its colours output."}),
            "resolution": ([1024, 512], {"tooltip": "Side of the square of the same area; small maps are enlarged."}),
        }}

    RETURN_TYPES = ("IMAGE", "INT", "INT")
    RETURN_NAMES = ("encoded", "width", "height")
    FUNCTION = "encode"
    CATEGORY = "image/preprocessors"
    DESCRIPTION = "Turns a depth map into Iris-Z's grey log-depth picture, ready for VAE Encode."

    def encode(self, depth, depth_format, resolution):
        frame = depth[0, ..., :3].float().cpu().numpy()
        h, w = frame.shape[:2]
        width, height = enlarged_size(w, h, int(resolution))
        if depth_format == "Iris-Z colours":
            values = read_coloured(frame.astype(np.float64))  # 0 near, 1 far: already log-depth ordered
        else:
            values = frame.mean(axis=-1).astype(np.float64)
        values = np.asarray(Image.fromarray(values.astype(np.float32)).resize((width, height), Image.BILINEAR),
                            dtype=np.float64)
        if depth_format.startswith("disparity"):
            log_depth = -np.log(np.maximum(values, 1e-3))
        elif depth_format.startswith("depth"):
            log_depth = np.log(np.maximum(values, 1e-3))
        elif depth_format == "Iris-Z depth":
            log_depth = 1.0 - 2.0 * values  # near bright, linear in log depth
        else:
            log_depth = values
        encoded = encode_log_depth(log_depth)
        grey = torch.from_numpy((encoded + 1.0) / 2.0)[None, ..., None].expand(1, height, width, 3).contiguous()
        if depth.shape[0] > 1:
            logging.warning("Iris-Z Depth Encode: using the first of %d depth maps", depth.shape[0])
        return grey, width, height


class IrisZEmptyLatent:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "width": ("INT", {"default": 1024, "min": 256, "max": 2048, "step": 16,
                              "tooltip": "Of the picture; the latent holds the depth map beside it. Trained at "
                                         "512 and 1024 square."}),
            "height": ("INT", {"default": 1024, "min": 256, "max": 2048, "step": 16}),
            "batch_size": ("INT", {"default": 1, "min": 1, "max": 16}),
        }}

    RETURN_TYPES = ("LATENT", "INT", "INT")
    RETURN_NAMES = ("latent", "width", "height")
    FUNCTION = "make"
    CATEGORY = "latent"
    DESCRIPTION = ("An empty latent for text to image and depth: picture left, depth map right. width and height "
                   "are the picture's, for Flux2Scheduler.")

    def make(self, width, height, batch_size):
        latent = torch.zeros([batch_size, 128, height // 16, 2 * (width // 16)],
                             device=comfy.model_management.intermediate_device())
        return {"samples": latent}, width, height


class IrisZSplit:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"latent": ("LATENT",)}}

    RETURN_TYPES = ("LATENT", "LATENT")
    RETURN_NAMES = ("picture", "depth")
    FUNCTION = "split"
    CATEGORY = "latent"
    DESCRIPTION = "Cuts a text to image and depth latent into the picture (left) and the depth map (right)."

    def split(self, latent):
        samples = latent["samples"]
        if samples.shape[-1] % 2:
            raise ValueError("not an Iris-Z picture-and-depth latent: odd width")
        half = samples.shape[-1] // 2
        return {"samples": samples[..., :half].clone()}, {"samples": samples[..., half:].clone()}


NODE_CLASS_MAPPINGS = {
    "IrisZModelLoader": IrisZModelLoader,
    "IrisZTask": IrisZTask,
    "IrisZPrepareImage": IrisZPrepareImage,
    "IrisZDepthDecode": IrisZDepthDecode,
    "IrisZDepthEncode": IrisZDepthEncode,
    "IrisZEmptyLatent": IrisZEmptyLatent,
    "IrisZSplit": IrisZSplit,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "IrisZModelLoader": "Iris-Z Model Loader",
    "IrisZTask": "Iris-Z Task",
    "IrisZPrepareImage": "Iris-Z Prepare Image",
    "IrisZDepthDecode": "Iris-Z Depth Decode",
    "IrisZDepthEncode": "Iris-Z Depth Encode",
    "IrisZEmptyLatent": "Iris-Z Empty Latent (picture + depth)",
    "IrisZSplit": "Iris-Z Split Picture and Depth",
}
