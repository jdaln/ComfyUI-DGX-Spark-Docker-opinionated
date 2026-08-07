# Workflow Catalogue

Every workflow this setup provisions, what it is for, and how to run it.

All 48 entries in the category tables below are verified end to end: the models
download, the workflow opens with no missing models, and it produces output. Run
times are measured on a DGX Spark at each workflow's default settings.

Newer additions that have not been through that on hardware yet are listed
separately under [Provisioned — not yet hardware-verified](#provisioned--not-yet-hardware-verified).

## How to use this

1. Pick a row and copy its profile name into `.env`:

   ```dotenv
   COMFY_ASSET_PROFILES=krea-2-turbo
   ```

   You can list several, comma-separated. Shared models (text encoders, VAEs)
   are downloaded once, so two profiles usually cost far less than the sum of
   their sizes.

2. Start the stack — the models download on first boot:

   ```bash
   docker compose up -d
   docker logs -f comfyui        # watch the download progress
   ```

3. Open <http://localhost:8188> and load the workflow named in the table:

   | Where it lives | How to open it |
   | --- | --- |
   | **Template** | Workflow → Browse Templates, search the name |
   | **Blueprint** | the node search box (they are subgraph blueprints) |
   | **Ours** | Browse Templates → `ComfyUI-DGX-Spark-Templates` |
   | **Node example** | Browse Templates → the custom node's section |

Some models are gated on Hugging Face (Krea 2, Ideogram 4, Gemma 3, a few LTX
LoRAs). Accept the licence on the model page and set `HF_TOKEN` in `.env`, or
those profiles will not download. See the README for the current gated list.

---

## Text to image

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Fastest general purpose image model | `z-image-turbo-core` | `image_z_image_turbo` | Template | 19 GB | 20 s |
| High-aesthetic 8-step distilled model | `krea-2-turbo` | `image_krea2_turbo_t2i` | Template | 17 GB | 35 s |
| Same, NVFP4 build — smaller and faster on Blackwell | `krea-2-turbo-nvfp4` | Text to Image (Krea 2 Turbo NVFP4) | Ours | 12 GB | 20 s |
| Krea 2 with nine style LoRAs (ink wash, retro anime, watercolour…) | `krea-2-turbo-styleloras` | Text to Image (Krea 2 Turbo Style LoRA) | Ours | 21 GB | 20 s |
| Krea 2 base model — full 52-step sampling, best for LoRA training and variety | `krea-2-raw` | Text to Image (Krea 2 RAW) | Ours | 17 GB | 90 s |
| Qwen-Image, 8-step Lightning LoRA | `qwen-image-t2i-lightning-8step` | Text to Image (Qwen-Image) | Blueprint | 30 GB | 115 s |
| Qwen-Image 2512, 4-step Lightning LoRA | `qwen-image-2512-t2i-lightning-4step` | `image_qwen_Image_2512` | Template | 30 GB | 230 s |
| Ideogram 4 — strongest text rendering in images | `ideogram-4` | Text to Image (Ideogram v4) | Blueprint | 27 GB | 55 s |
| Same, NVFP4 build | `ideogram-4-nvfp4` | Text to Image (Ideogram v4 NVFP4) | Ours | 20 GB | 70 s |

## Editing existing images

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Instruction-driven edits ("make it night", object swaps) | `qwen-image-edit-2511-core` | Image Edit (Qwen 2511) | Blueprint | 47 GB | 275 s |
| Inpainting and outpainting with a mask | `qwen-image-inpaint-lightning-4step` | Image Inpainting (Qwen-image) | Blueprint | 34 GB | 30 s |
| Split an image into editable layers | `qwen-image-layered-core` | `image_qwen_image_layered` | Template | 47 GB | 65 s |
| Compose from a control image (canny / depth / pose) | `z-image-turbo-union-control` | `image_z_image_turbo_fun_union_controlnet` | Template | 22 GB | 80 s |
| Instruction-driven edits with up to 16 reference images | `mage-flow-edit` | Image Edit (Mage-Flow) | Ours | 17 GB | 275 s |
| Same, 4-step distilled — about 7× faster | `mage-flow-edit-turbo` | Image Edit (Mage-Flow Turbo) | Ours | 17 GB | 25 s |

Microsoft's Mage-Flow-Edit. ComfyUI already supports it in core, so these
templates install no custom node at all — the three community node packs only
wrap loaders around what `TextEncodeMageFlowEdit` already does, or drag in
`diffusers` and Microsoft's pinned source. The two profiles share the 8.3 GB
text encoder and the VAE, so the second costs 7.7 GB.

There is **no NVFP4 build** for this one: the `ajh-code/Mage-Flow-NVFP4-*` repos
are standalone Diffusers pipelines shipping prebuilt CUDA kernels, not
safetensors ComfyUI can load. Comfy-Org's `int8_convrot` quants are half the
size but a straight quality loss with this much memory, so they are not
provisioned — the loader note says how to add one if you ever want it. Nothing
here is gated.

## Video

### Wan — the general purpose video models

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to video, 14B | `wan2.2-t2v-bundled` | `video_wan2_2_14B_t2v` | Template | 37 GB | 10 min |
| Animate a still image, 14B | `wan2.2-i2v-bundled` | `video_wan2_2_14B_i2v` | Template | 37 GB | 10 min |
| Replace or remove things inside a video | `wan2.1-vace-bundled` | Video Inpainting (Wan2.1 VACE) | Blueprint | 41 GB | 3 min |
| Drive a character with a reference video | `wananimate-preprocess` | WanAnimate_native_example_01 | Node example | 2 GB | 5 s |

`wananimate-preprocess` only ships the pose/detection/segmentation models — pair
it with a Wan animate checkpoint.

### LTX 2.0 — fast video, distilled or full quality

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to video, distilled (quickest) | `ltx-2.0-t2v-distilled` | `video_ltx2_t2v_distilled` | Template | 64 GB | 80 s |
| Image to video, distilled | `ltx-2.0-i2v-distilled` | `video_ltx2_i2v_distilled` | Template | 64 GB | 55 s |
| Text to video, full dev checkpoint | `ltx-2.0-t2v-full` | `video_ltx2_t2v` | Template | 71 GB | 165 s |
| Image to video, full dev checkpoint | `ltx-2.0-i2v-full` | `video_ltx2_i2v` | Template | 72 GB | 165 s |
| Refine an existing video (detailer LoRA) | `ltx-2.0-v2v-detailer` | `video_ltx2_i2v_lora` | Template | 74 GB | 6 min |
| Video guided by canny edges | `ltx-2.0-iclora-all-distilled` | Canny to Video (LTX 2.0) | Blueprint | 66 GB | 6 min |
| Video guided by a depth map | `ltx-2.0-iclora-all-distilled-ref0.5` | Depth to Video (ltx 2.0) | Blueprint | 66 GB | 5 min |
| Video guided by a pose sequence | `ltx-2.0-iclora-all-bundled` | Pose to Video (LTX 2.0) | Blueprint | 59 GB | 135 s |

### LTX 2.3 — newest generation, 22B

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to video, two-stage distilled | `ltx-2.3-t2v-i2v-two-stage-distilled` | Text to Video (LTX-2.3) | Blueprint | 60 GB | 140 s |
| Image to video, single stage | `ltx-2.3-t2v-i2v-single-stage-distilled-full` | Image to Video (LTX-2.3) | Blueprint | 59 GB | 105 s |
| Aligned control (canny / depth / pose) via IC-LoRA | `ltx-2.3-iclora-union-control-distilled` | `video_ltx2_3_ic_lora` | Template | 61 GB | 270 s |
| Transfer motion from a source video | `ltx-2.3-iclora-motion-track-distilled` | `video_ltx2_3_ic_lora` | Template | 60 GB | 160 s |
| HDR / relighting pass | `ltx-2.3-iclora-hdr-distilled` | `video_ltx2_3_ic_lora` | Template | 60 GB | 240 s |
| Lip-sync a face to audio | `ltx-2.3-iclora-lipdub-two-stage-distilled` | `video_ltx2_3_ic_lora` | Template | 63 GB | 260 s |

The four LTX 2.3 IC-LoRA profiles share one template — pick the LoRA in the
`ic_lora` widget to match the profile you provisioned.

### HunyuanVideo

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Image to video via the Leapfusion LoRA | `leapfusion-hunyuanvideo-i2v` | `leapfusion_hunyuuanvideo_i2v_native_testing` | Node example | 29 GB | 6 min |

### Video editing with LTX-2.3 task LoRAs

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Rewrite a clip from a plain instruction ("make it snow") | `bfs-ltx-2.3-edit-anything` | Video Edit Anything (LTX-2.3) | Ours | 62 GB | 255 s |
| Anime ⇄ live action on an existing clip | `bfs-ltx-2.3-style-swap` | Video Style Swap (LTX-2.3 Anime2Real) | Ours | 61 GB | 216 s |
| Repaint a masked region of a clip | `bfs-ltx-2.3-inpaint` | Video Inpainting (LTX-2.3 Masked) | Ours | 61 GB | 246 s |
| Same, driven by a reference image | `bfs-ltx-2.3-masked-ref-inpaint` | Video Inpainting (LTX-2.3 Masked) | Ours | 61 GB | 211 s |
| Swap the head in a clip, keeping the performance | `bfs-ltx-2.3-head-swap` | Video Head Swap (LTX-2.3) | Ours | 62 GB | 256 s |

These are the LTX-2.3 workflows from [ComfyUI-BFSNodes](https://github.com/alisson-anjos/ComfyUI-BFSNodes)
by Alisson Anjos, rebuilt on the same verified LTX-2.3 chain the
`ltx-2.3-t2v-i2v-two-stage-distilled` blueprint uses. That means they run on the
22 GB checkpoint you may already have rather than pulling a second 23 GB
transformer-only copy — the five profiles above share one base and differ only by
a 0.3–1.3 GB task LoRA, so the second one you provision costs about a gigabyte.

Where a task has more than one published capture, the profile provisions both and
the template loads the higher-fidelity one: head swap defaults to the
adaptive-rank extraction rather than the half-size rank-64 build, and Edit
Anything ships the AdamW and Prodigy runs side by side. Switch on the LoRA node.

A sixth profile, `bfs-ltx-2.3-multishot`, is still waiting on upstream weights —
see [Provisioned — not yet hardware-verified](#provisioned--not-yet-hardware-verified).

## Speech

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Conversations between up to 4 characters, voices cloned from samples | `vibevoice-large` | Text to Speech (Multi-Character Conversation) | Ours | 18 GB | 215 s |
| A voice described in words rather than sampled | `ltx-2.3-tts-prompted-voice` | Text to Speech (LTX-2.3 Prompted Voice) | Ours | 60 GB | 172 s |
| Both at once: describe one voice, clone the rest, run the conversation | `tts-prompted-conversation` | Text to Speech (Prompted Voices to Conversation) | Ours | 78 GB | 311 s |

Neither model does the whole job on its own, so all three templates ship.
[VibeVoice](https://github.com/Enemyx-net/VibeVoice-ComfyUI) does real
multi-speaker dialogue — `[1]:`/`[2]:` script, up to four voices, `[pause:800]`
tags — but every voice has to come from an audio sample. LTX-2.3's joint
audio/video latent path can be *told* what a voice sounds like ("low, hoarse,
soft Edinburgh accent") but renders one utterance at a time, at video-model
cost.

Both VibeVoice templates run at `diffusion_steps` 40 rather than the wrapper's
default 20 — the extra passes are cheap next to the LLM stage, and there is no
VRAM reason to economise here.

The third template joins them: LTX-2.3 renders speaker 1 from a description and
hands that clip to VibeVoice as the sample to clone, with speakers 2–4 on
ordinary Load Audio nodes. Every character can be sourced either way and
switching between them is unplugging one link. For a whole cast of described
voices, render them one at a time with the LTX template and reuse the files —
each prompted voice costs a full video render.

`ltx-2.3-tts-prompted-voice` adds no new weights: it is the same LTX-2.3 base
as `ltx-2.3-t2v-i2v-two-stage-distilled`, and the audio VAE is read out of that
checkpoint.

## Audio

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to music and song, with lyrics and style tags | `ace-step-1.5-core` | Text to Audio (ACE-Step 1.5) | Blueprint | 14 GB | 50 s |
| Full songs with lyrics and style tags, up to 5 minutes | `heartmula-oss-3b` | Text to Music (HeartMuLa 3B) | Ours | 21 GB | 145 s |
| Transcribe sung lyrics out of a track | `heartmula-transcribe` | Lyrics Transcription (HeartMuLa) | Ours | 3 GB | 10 s |

[HeartMuLa](https://huggingface.co/HeartMuLa) is a 3B music foundation model, a
step up from `ace-step-1.5-core` in vocal quality and song structure at about
1.5× the disk. Section markers (`[Verse]`, `[Chorus]`, …) shape the arrangement
and English, Chinese, Japanese, Korean and Spanish lyrics all work.

The transcription template needs a track you supply in `ComfyUI/input/` — the
repo ships no sample audio, so its Run time above was measured with an
externally supplied clip rather than a bundled default.

## 3D

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Turn one image into a textured 3D mesh (`.glb`) | `hunyuan3d-2.1-core` | Image to Model (Hunyuan3d 2.1) | Blueprint | 7 GB | 60 s |

## Utility

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Depth maps from images, for use as control input | `lotus-depth-support` | Image Depth Estimation (Lotus Depth) | Blueprint | 2 GB | 15 s |
| Background removal tuned for glass, glow, camouflage, text and print designs | `lucida-background-removal` | Remove Background (Lucida) | Ours | 0.9 GB | 5 s |

Lucida is a BiRefNet-HR fine-tune. It handles the mattes the bundled
`Remove Background (BiRefNet)` blueprint struggles with — semi-transparent
objects, camouflaged subjects, text and logos with shadows, illustrations,
stickers and tee designs. Same nodes, different checkpoint, so the two can live
side by side and you pick in the loader.

---

## Provisioned — not yet hardware-verified

These follow the same rules as everything above — pick the profile, start the
stack, open the workflow — but they have not yet had their smoke lane and
provisioning audit run on a DGX Spark, so the Run column is blank and the disk
figures come from the Hugging Face file sizes rather than a real download.

Promote a row into its category table once `run_lanes.py` and `audit_refs.py`
both pass for it and you have looked at the output. The README's *Verifying
Profiles* section has the commands.

### MiniMax H3 — video with its own soundtrack

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to video, with dialogue, effects and music generated with it | `minimax-h3-t2v` | Text to Video (MiniMax H3) | Ours | 42 GB | — |
| Animate a still image, same joint audio | `minimax-h3-i2v` | Image to Video (MiniMax H3) | Ours | 42 GB | — |
| Carry an identity, style, motion or voice over from references | `minimax-h3-ref2v` | Reference to Video (MiniMax H3) | Ours | 42 GB | — |

The first workflows here that produce sound. H3 models audio and video in one
forward pass rather than dubbing a track on afterwards, so speech lands in sync
with the mouth and effects land on the action. Output is 24 fps and about five
seconds at the shipped defaults; the Resolution Selector caps the short edge at
768 px.

Reference to video takes up to nine images, three videos (each able to carry its
own soundtrack) and three loose audio clips, and you address them from the prompt
by tag — `<Picture 1>`, `<Video 1>`, `<Audio 1>` — in the order you connected
them. It runs different weights (`ref2va`) from the other two (`fl2va`), so it
costs a second 21 GB model; all three together are 63 GB, since they share the
text encoder and both VAEs.

Nothing here is gated. Needs ComfyUI v0.30.0 or newer for the `MiniMaxH3*` nodes.

### Speech

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Same, on the small model | `vibevoice-1.5b` | Text to Speech (Multi-Character Conversation) | Ours | 5 GB | — |

Same template and pipeline as `vibevoice-large` (see the verified Speech
section above), just pointed at the 1.5B checkpoint — not yet run separately
on hardware.

### Video editing with LTX-2.3 task LoRAs

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Plan a multi-shot clip from one caption plus a keyframe per shot | `bfs-ltx-2.3-multishot` | Multishot ShotPlan (LTX-2.3) | Ours | 60 GB | — |

**Multishot is waiting on upstream weights.** The `LTX Multishot Prompt + Refs`
node landed on 2026-08-01 but its `multishot_strata_r128_v1` LoRA has not been
published, so that template reports one missing LoRA and has no smoke lane. The
profile still provisions the rest of the stack. Tracked in
`scripts/smoke/pending_models.json`.

---

## Choosing quickly

- **Just want a good picture, fast** → `z-image-turbo-core`, or `krea-2-turbo`
  for nicer aesthetics. On Blackwell prefer the `-nvfp4` variants.
- **Text inside the image** → `ideogram-4`.
- **Editing a photo you already have** → `qwen-image-edit-2511-core`.
- **First video** → `ltx-2.0-i2v-distilled` (55 s) before committing to Wan 2.2
  at ~10 minutes a clip.
- **Best video quality** → the LTX 2.3 pair, or Wan 2.2 for motion.
- **Tight on disk** → LTX profiles are 60–75 GB each; image profiles are 12–30 GB.

## Beyond this list

ComfyUI also ships hundreds of upstream templates and ~89 blueprints for models
this repo does not provision (API/cloud nodes, other checkpoints). They are
still visible in the template browser and work if you supply the models
yourself — the table above is the set that is provisioned and verified here.

To re-verify after changing profiles or updating ComfyUI, see the smoke lanes
and provisioning audit in the README.
