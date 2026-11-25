"""MovieRoll long text to video on core's HunyuanVideo 1.5 model.

MovieRoll (Tencent, yunyangge/MovieRoll) is a HunyuanVideo 1.5 transformer distilled to roll a video out in
chunks: a window of five chunks at five noise levels moves forward one chunk per model call, and finished
chunks go into a key-value cache the next ones attend to. Its weights load into core's HunyuanVideo 1.5 class
unchanged after renaming; this module only adds the sampling loop and the cached attention.

The loop follows MovieRoll's own distilled sampler (upstream commit 0b5f73e, `movieroll_dmd_pipeline.py`,
`movieroll_pipeline.py`, `movieroll_transformer.py`) for its released distilled text-to-video settings:
chunks of 4 latent frames (the first 5), step list 1000/800/600/400/200 with shift 5, context noise 0.15,
blocks 0-5 attending to the full history and blocks 6-53 to the first and the latest chunk, the history
re-encoded once it holds 18 chunks. Noise is drawn from one CPU generator in upstream's order, so a seed starts
as upstream's video does; rounding then moves the motion over long videos.

Licence: derived from MovieRoll (https://github.com/Tencent-Hunyuan/MovieRoll, Copyright (C) 2026 Tencent), so
this file is under the Tencent Hunyuan Community License Agreement (licenses/MovieRoll-LICENSE.txt, notice in
licenses/MovieRoll-NOTICE.txt), which does not apply in the European Union, the United Kingdom and South Korea.
Changed from upstream: the sampler and the cached attention are written anew on ComfyUI core's modules; no
upstream code is imported or copied verbatim.
"""
import logging
import re

import torch

import comfy.model_detection
import comfy.model_management
import comfy.model_patcher
import comfy.supported_models
import comfy.utils
import folder_paths
from comfy.ldm.flux.layers import timestep_embedding
from comfy.ldm.flux.math import apply_rope1
from comfy.ldm.modules.attention import optimized_attention

log = logging.getLogger(__name__)

# distilled t2v config.json of yunyangge/MovieRoll
CHUNK = 4
WINDOW = 5
STEPS = (1000, 800, 600, 400, 200)
SHIFT = 5.0
FULL_HISTORY_BLOCKS = 6
RECOMPUTE_AT = 18
RECOMPUTE_RECENT = 3
SCALE_FACTOR = 1.03682  # VAE scaling; core's HunyuanVideo15 latent format uses the same

# The system text of MovieRoll's video template. The original is a Python string with backslash line breaks,
# so each break became nine spaces; core's HunyuanVideo 1.5 template has newlines there.
_SYSTEM = ("You are a helpful assistant. Describe the video by detailing the following aspects:         "
           "1. The main content and theme of the video.         "
           "2. The color, shape, size, texture, quantity, text, and spatial relationships of the objects.         "
           "3. Actions, events, behaviors temporal relationships, physical movement changes of the objects.         "
           "4. background environment, light, style and atmosphere.         "
           "5. camera angles, movements, and transitions used in the video.")
TEMPLATE = "<|im_start|>system\n" + _SYSTEM + "<|im_end|>\n<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n"

# original HunyuanVideo key names -> core's, applied in order
_RENAMES = (
    ("_attn_qkv.", "_attn.qkv."), ("_attn_proj.", "_attn.proj."),
    ("_attn_q_norm.", "_attn.norm.query_norm."), ("_attn_k_norm.", "_attn.norm.key_norm."),
    ("self_attn_qkv.", "self_attn.qkv."), ("self_attn_proj.", "self_attn.proj."),
    ("_mod.linear.", "_mod.lin."), ("mlp.fc1.", "mlp.0."), ("mlp.fc2.", "mlp.2."),
    ("time_in.mlp.0.", "time_in.in_layer."), ("time_in.mlp.2.", "time_in.out_layer."),
    ("t_embedder.mlp.0.", "t_embedder.in_layer."), ("t_embedder.mlp.2.", "t_embedder.out_layer."),
    ("c_embedder.linear_1.", "c_embedder.in_layer."), ("c_embedder.linear_2.", "c_embedder.out_layer."),
)


def rename_key(key):
    for old, new in _RENAMES:
        key = key.replace(old, new)
    return key


def load_model(path, dtype=torch.bfloat16):
    """MovieRoll's transformer as core's HunyuanVideo 1.5 model. The fp32 file is cast tensor by tensor."""
    from safetensors import safe_open
    sd = {}
    with safe_open(path, "pt", device="cpu") as f:
        for key in f.keys():
            sd[rename_key(key)] = f.get_tensor(key).to(dtype)
    if "double_blocks.0.img_attn.qkv.weight" not in sd or "byt5_in.fc1.weight" not in sd:
        raise ValueError(f"{path} is not a MovieRoll / HunyuanVideo 1.5 transformer")
    # MovieRoll's t2v weights have no vision_in (image prompt) layer, so core's detection takes them for the
    # HunyuanImage 2.1 refiner; the rest of the layout is HunyuanVideo 1.5's.
    unet_config = comfy.model_detection.detect_unet_config(sd, "")
    model_config = comfy.supported_models.HunyuanVideo15(unet_config)
    load_device = comfy.model_management.get_torch_device()
    offload_device = comfy.model_management.unet_offload_device()
    manual_cast = comfy.model_management.unet_manual_cast(dtype, load_device, model_config.supported_inference_dtypes)
    model_config.set_inference_dtype(dtype, manual_cast, device=load_device)
    model = model_config.get_model(sd, "")
    patcher = comfy.model_patcher.CoreModelPatcher(model, load_device=load_device, offload_device=offload_device)
    if not comfy.model_management.is_device_cpu(offload_device):
        model.to(offload_device)
    model.load_model_weights(sd, "", assign=patcher.is_dynamic())
    return patcher


def chunk_sizes(latent_frames):
    if latent_frames < CHUNK + 1 or (latent_frames - 1) % CHUNK:
        raise ValueError(f"{latent_frames} latent frames: MovieRoll needs 1 + a multiple of {CHUNK}")
    return [CHUNK + 1] + [CHUNK] * ((latent_frames - 1) // CHUNK - 1)


def sigmas():
    s = [t / 1000 for t in STEPS]
    return [SHIFT * x / (1 + (SHIFT - 1) * x) for x in s] + [0.0]


class _Cache:
    """Pre-RoPE image keys and values per block: a list of (k, v, frames) entries, oldest first."""

    def __init__(self, blocks):
        self.layers = [[] for _ in range(blocks)]

    def frames(self, i):
        return sum(f for _, _, f in self.layers[i])


class _Runner:
    """MovieRoll's streaming forward on core's HunyuanVideo modules."""

    def __init__(self, dm, height, width):
        self.dm, self.H, self.W = dm, height, width
        self.heads = dm.num_heads
        self.dtype = next(dm.parameters()).dtype if dm.dtype is None else dm.dtype
        self.device = comfy.model_management.get_torch_device()
        self.cache = _Cache(len(dm.double_blocks))
        self._pe = {}

    # positions -------------------------------------------------------------
    def pe(self, start, frames):
        key = (start, frames)
        if key not in self._pe:
            t = torch.arange(start, start + frames, device=self.device, dtype=torch.float32)
            h = torch.arange(self.H, device=self.device, dtype=torch.float32)
            w = torch.arange(self.W, device=self.device, dtype=torch.float32)
            ids = torch.stack(torch.meshgrid(t, h, w, indexing="ij"), dim=-1).reshape(1, -1, 3)
            self._pe[key] = self.dm.pe_embedder(ids).to(self.device)
            if len(self._pe) > 64:
                self._pe.pop(next(iter(self._pe)))
        return self._pe[key]

    def cached_keys(self, i):
        """Block i's cached keys with RoPE, its values, and the frame where new tokens start."""
        entries = self.cache.layers[i]
        if not entries:
            return None, None, 0
        ks, vs, offset = [], [], 0
        for k, v, f in entries:  # positions re-packed from 0 in cache order
            ks.append(apply_rope1(k, self.pe(offset, f)))
            vs.append(v)
            offset += f
        return torch.cat(ks, dim=2), torch.cat(vs, dim=2), offset

    # helpers ---------------------------------------------------------------
    def time_vec(self, t):
        return self.dm.time_in(timestep_embedding(t.flatten(), 256, time_factor=1.0).to(self.dtype))

    def mod_norm(self, x, norm, mod, frames):
        # LayerNorm and modulation in fp32, then the model dtype for the next linear: what upstream's bf16
        # autocast does, and measurably closer to it than doing them in bf16
        b, n, d = x.shape
        y = norm(x.float()).view(b, frames, n // frames, d)
        y = y * (1 + mod.scale[:, :, None].float()) + mod.shift[:, :, None].float()
        return y.view(b, n, d).to(self.dtype)

    @staticmethod
    def gated(x, gate, frames):
        b, n, d = x.shape
        return (x.view(b, frames, n // frames, d) * gate[:, :, None]).view(b, n, d)

    def qkv(self, attn, x):
        b, n, _ = x.shape
        q, k, v = attn.qkv(x).view(b, n, 3, self.heads, -1).permute(2, 0, 3, 1, 4)
        q, k = attn.norm(q, k, v)
        return q, k, v

    def attend(self, q, k, v):
        return optimized_attention(q, k, v, self.heads, skip_reshape=True)

    def embed(self, latents):
        b, c, t, h, w = latents.shape
        x = torch.cat([latents, torch.zeros((b, c + 1, t, h, w), device=latents.device, dtype=latents.dtype)], 1)
        return self.dm.img_in(x.to(self.dtype))

    # image-only pass that writes the cache ---------------------------------
    def encode(self, latents):
        """Add a finished chunk (already context-noised) to every block's cache, at timestep 0, no text."""
        frames = latents.shape[2]
        img = self.embed(latents.to(self.device))
        vec = self.time_vec(torch.zeros(1, device=self.device)).view(1, 1, -1)
        for i, blk in enumerate(self.dm.double_blocks):
            if i >= FULL_HISTORY_BLOCKS:
                del self.cache.layers[i][1:]  # sink-recent blocks keep the first chunk only
            m1, m2 = blk.img_mod(vec)
            q, k, v = self.qkv(blk.img_attn, self.mod_norm(img, blk.img_norm1, m1, 1))
            ck, cv, start = self.cached_keys(i)
            pe = self.pe(start, frames)
            kr = apply_rope1(k, pe)
            keys = kr if ck is None else torch.cat([ck, kr], dim=2)
            values = v if cv is None else torch.cat([cv, v], dim=2)
            out = self.attend(apply_rope1(q, pe), keys, values)
            self.cache.layers[i].append((k.to(torch.bfloat16), v.to(torch.bfloat16), frames))
            if i == len(self.dm.double_blocks) - 1:
                break  # the last block's output is not used
            img = img + self.gated(blk.img_attn.proj(out), m1.gate, 1)
            img = img + self.gated(blk.img_mlp(self.mod_norm(img, blk.img_norm2, m2, 1)), m2.gate, 1)

    # window forward --------------------------------------------------------
    def velocity(self, latents, t_frames, txt, byt5):
        """Flow velocity for the window (oldest chunk first), t_frames: [frames] timesteps."""
        b, c, frames, h, w = latents.shape
        img = self.embed(latents)
        vec = self.time_vec(t_frames).view(1, frames, -1)
        t_txt = t_frames[:1]
        vec_txt = self.time_vec(t_txt)
        dm = self.dm
        text = dm.txt_in(txt.to(self.device, self.dtype), t_txt, None)
        # upstream keeps cond_type_embedding in fp32, which turns the text stream into fp32 from here on
        if dm.cond_type_embedding is not None:
            text = text.float() + dm.cond_type_embedding(torch.zeros(text.shape[:2], device=text.device,
                                                                     dtype=torch.long)).float()
        if byt5 is not None and dm.byt5_in is not None:
            glyph = dm.byt5_in(byt5.to(self.device, self.dtype))
            glyph = glyph.float() + dm.cond_type_embedding(torch.ones(glyph.shape[:2], device=glyph.device,
                                                                      dtype=torch.long)).float()
            text = torch.cat([glyph, text.float()], dim=1)
        for i, blk in enumerate(dm.double_blocks):
            m1, m2 = blk.img_mod(vec)
            t1, t2 = blk.txt_mod(vec_txt)
            q, k, v = self.qkv(blk.img_attn, self.mod_norm(img, blk.img_norm1, m1, frames))
            tx = self.mod_norm(text, blk.txt_norm1, t1, 1)
            tq, tk, tv = self.qkv(blk.txt_attn, tx)
            ck, cv, start = self.cached_keys(i)
            pe = self.pe(start, frames)
            q, k = apply_rope1(q, pe), apply_rope1(k, pe)
            img_keys = [k, tk] if ck is None else [ck, k, tk]
            img_values = [v, tv] if cv is None else [cv, v, tv]
            img_out = self.attend(q, torch.cat(img_keys, dim=2), torch.cat(img_values, dim=2))
            txt_out = self.attend(tq, torch.cat([k, tk], dim=2), torch.cat([v, tv], dim=2))  # text skips the cache
            img = img + self.gated(blk.img_attn.proj(img_out), m1.gate, frames)
            img = img + self.gated(blk.img_mlp(self.mod_norm(img, blk.img_norm2, m2, frames)), m2.gate, frames)
            text = text + blk.txt_attn.proj(txt_out) * t1.gate
            text = text + blk.txt_mlp(self.mod_norm(text, blk.txt_norm2, t2, 1)) * t2.gate
        shift, scale = dm.final_layer.adaLN_modulation(vec).chunk(2, dim=-1)
        out = dm.final_layer.norm_final(img.float()).view(b, frames, h * w, -1)
        out = out * (1 + scale[:, :, None].float()) + shift[:, :, None].float()
        out = dm.final_layer.linear(out.view(b, frames * h * w, -1).to(img.dtype))
        return out.view(b, frames, h, w, -1).permute(0, 4, 1, 2, 3)


def sample(dm, txt, byt5, width, height, length, seed, context_noise=0.15, progress=None, anchor=None):
    """MovieRoll rolling sampling. Returns latents [1, 32, T, H/16, W/16] in model space (fp32, on the CPU).

    txt: [1, L, 3584] Qwen2.5-VL states of the valid tokens; byt5: [1, L2, 1472] or None.
    anchor: image to video, the start image's latent [1, 32, 1, H/16, W/16] in model space. It becomes the first
    frame and goes into the cache before the first round; the generated chunks are then all 4 frames.
    """
    H, W = height // 16, width // 16
    latent_frames = (length - 1) // 4 + 1
    sizes = chunk_sizes(latent_frames)
    if anchor is not None:
        sizes = [CHUNK] * ((latent_frames - 1) // CHUNK)  # [1 (the anchor), 4, 4, ...]
    sig = sigmas()
    ts = [1000 * s for s in sig[:-1]]
    runner = _Runner(dm, H, W)
    device = runner.device
    txt = txt.to(device)
    byt5 = None if byt5 is None else byt5.to(device)
    g = torch.Generator("cpu").manual_seed(seed)

    def noise(frames):  # upstream's randn_tensor: bf16 on the CPU generator, then to the GPU
        return torch.randn((1, 32, frames, H, W), generator=g, device="cpu", dtype=torch.bfloat16).to(device)

    def context(clean):  # drawn in the clean latents' dtype, as upstream; at 0 upstream draws nothing
        if context_noise <= 0:
            return clean
        eps = torch.randn(clean.shape, generator=g, device="cpu", dtype=clean.dtype)
        return (1 - context_noise) * clean + context_noise * eps

    queue = []  # [latents, round pushed]
    finished = []
    if anchor is not None:
        anchor = anchor.to(torch.bfloat16).cpu()  # upstream casts it to the run's dtype
        finished.append(anchor)
        runner.encode(context(anchor))
    total = len(finished) + len(sizes)
    n = len(sizes)
    for r in range(n + WINDOW):
        comfy.model_management.throw_exception_if_processing_interrupted()
        if r >= WINDOW:
            done = queue.pop(0)[0].cpu()
            finished.append(done)
            if len(finished) == total:
                break
            count = len(finished)
            pick = sorted({0, (count - 1) // 2, *range(count - RECOMPUTE_RECENT, count)})
            if len(runner.cache.layers[1]) >= RECOMPUTE_AT and len(pick) < count:
                noised = context(torch.cat([finished[j] for j in pick], dim=2))
                runner.cache = _Cache(len(dm.double_blocks))
                start = 0
                for j in pick:
                    f = finished[j].shape[2]
                    runner.encode(noised[:, :, start:start + f])
                    start += f
            else:
                runner.encode(context(done))
        frames = sizes[r] if r < n else sizes[-1]  # past the end: filler chunks of noise
        queue.append([noise(frames), r])
        window = torch.cat([c[0] for c in queue], dim=2)
        steps = [r - p for _, p in queue]
        t_frames = torch.cat([torch.full((c[0].shape[2],), ts[i], device=device) for c, i in zip(queue, steps)])
        v = runner.velocity(window, t_frames, txt, byt5).float()
        start = 0
        for c, i in zip(queue, steps):
            f = c[0].shape[2]
            c[0] = c[0].float() + v[:, :, start:start + f] * (sig[i + 1] - sig[i])
            start += f
        if progress is not None:
            progress(r + 1, n + WINDOW)
    return torch.cat(finished, dim=2)


def start_latent(vae, image, width, height):
    """The start image as MovieRoll's i2v anchor: scaled to cover width x height with Lanczos, centre-cropped
    (upstream's PIL / torchvision steps), VAE-encoded to one latent frame and scaled into model space."""
    import numpy as np
    from PIL import Image
    pixels = (image[0].cpu().numpy() * 255).round().clip(0, 255).astype(np.uint8)
    picture = Image.fromarray(pixels)
    scale = max(width / picture.width, height / picture.height)
    picture = picture.resize((int(round(picture.width * scale)), int(round(picture.height * scale))), Image.LANCZOS)
    left, top = int(round((picture.width - width) / 2.0)), int(round((picture.height - height) / 2.0))
    picture = picture.crop((left, top, left + width, top + height))
    pixels = torch.from_numpy(np.asarray(picture).copy()).float().div(255)[None]
    latent = vae.encode(pixels)
    if latent.ndim != 5 or latent.shape[2] != 1:
        raise ValueError(f"expected a one-frame video latent from the VAE, got {tuple(latent.shape)}: "
                         "use the HunyuanVideo 1.5 VAE")
    return latent * SCALE_FACTOR


def glyph_texts(text):
    """MovieRoll's byT5 texts: ASCII or curly double quotes, in order, without repeats."""
    found = [a or b for a, b in re.findall(r'\"(.*?)\"|“(.*?)”', text)]
    return list(dict.fromkeys(found)) if len(found) > 1 else found


class MovieRollModelLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model_name": (folder_paths.get_filename_list("diffusion_models"),
                           {"tooltip": "MovieRoll's distilled transformer, the fp32 model.safetensors of "
                                       "yunyangge/MovieRoll distilled/t2v or distilled/i2v. Loaded in bf16."}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "load"
    CATEGORY = "advanced/loaders"
    DESCRIPTION = "Loads MovieRoll's distilled transformer into core's HunyuanVideo 1.5 model, in bf16."

    def load(self, model_name):
        return (load_model(folder_paths.get_full_path_or_raise("diffusion_models", model_name)),)


class MovieRollTextEncode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "clip": ("CLIP", {"tooltip": "HunyuanVideo 1.5 text encoders: Qwen2.5-VL 7B and the Glyph byT5."}),
            "text": ("STRING", {"multiline": True, "dynamicPrompts": True}),
        }}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "encode"
    CATEGORY = "conditioning"
    DESCRIPTION = ("Text conditioning with MovieRoll's own video template. Text in double quotes is also read "
                   "letter by letter (byT5), for writing in the video.")

    def encode(self, clip, text):
        tokens = clip.tokenize(text, llama_template=TEMPLATE)
        glyphs = glyph_texts(text)
        if glyphs:  # MovieRoll's quote rules, not core's
            byt5 = clip.tokenizer.byt5
            tokens["byt5"] = byt5.tokenize_with_weights("".join(f'Text "{g}". ' for g in glyphs))
        else:
            tokens.pop("byt5", None)
        return (clip.encode_from_tokens_scheduled(tokens),)


class MovieRollSampler:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL", {"tooltip": "From MovieRoll Model Loader."}),
            "positive": ("CONDITIONING", {"tooltip": "From MovieRoll Text Encode."}),
            "width": ("INT", {"default": 864, "min": 256, "max": 1920, "step": 16,
                              "tooltip": "MovieRoll was trained at 864x480."}),
            "height": ("INT", {"default": 480, "min": 256, "max": 1920, "step": 16}),
            "length": ("INT", {"default": 721, "min": 17, "max": 1441, "step": 16,
                               "tooltip": "Frames at 24 fps: 17 + a multiple of 16. 721 is 30 s."}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
        }, "optional": {
            "context_noise": ("FLOAT", {"default": 0.15, "min": 0.0, "max": 1.0, "step": 0.01,
                                        "tooltip": "Noise on finished chunks before the next ones read them."}),
            "vae": ("VAE", {"tooltip": "Image to video: the HunyuanVideo 1.5 VAE, to encode the start image."}),
            "start_image": ("IMAGE", {"tooltip": "Image to video: the first frame. Needs the i2v model and the "
                                                 "VAE. Scaled to cover width x height and centre-cropped."}),
        }}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "generate"
    CATEGORY = "sampling"
    DESCRIPTION = ("MovieRoll's rolling sampler: the video comes out chunk by chunk, each chunk reading the "
                   "finished ones. 5 steps per chunk, no CFG. With a start image, image to video.")

    def generate(self, model, positive, width, height, length, seed, context_noise=0.15, vae=None,
                 start_image=None):
        if (length - 1) % 16:
            raise ValueError(f"length {length}: MovieRoll needs 17 + a multiple of 16 frames")
        anchor = None
        if start_image is not None:
            if vae is None:
                raise ValueError("a start image needs the vae input (HunyuanVideo 1.5 VAE)")
            anchor = start_latent(vae, start_image, width, height)
        cond, extra = positive[0][0], positive[0][1]
        mask = extra.get("attention_mask")
        if mask is not None:  # padded batch: keep the valid tokens, as MovieRoll packs them
            cond = cond[:, mask[0].bool()]
        comfy.model_management.load_models_gpu([model])
        dm = model.model.diffusion_model
        bar = comfy.utils.ProgressBar(1)

        def progress(done, total):
            bar.total = total
            bar.update_absolute(done, total)

        with torch.inference_mode():
            latents = sample(dm, cond, extra.get("conditioning_byt5small"), width, height, length, seed,
                             context_noise, progress, anchor)
        return ({"samples": latents / SCALE_FACTOR},)


NODE_CLASS_MAPPINGS = {
    "MovieRollModelLoader": MovieRollModelLoader,
    "MovieRollTextEncode": MovieRollTextEncode,
    "MovieRollSampler": MovieRollSampler,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MovieRollModelLoader": "MovieRoll Model Loader",
    "MovieRollTextEncode": "MovieRoll Text Encode",
    "MovieRollSampler": "MovieRoll Sampler",
}
