# Workflow Catalogue

Every workflow this setup provisions, what it is for, and how to run it.

All 35 entries in the category tables below are verified end to end: the models
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

## Audio

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to music and song, with lyrics and style tags | `ace-step-1.5-core` | Text to Audio (ACE-Step 1.5) | Blueprint | 14 GB | 50 s |

## 3D

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Turn one image into a textured 3D mesh (`.glb`) | `hunyuan3d-2.1-core` | Image to Model (Hunyuan3d 2.1) | Blueprint | 7 GB | 60 s |

## Utility

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Depth maps from images, for use as control input | `lotus-depth-support` | Image Depth Estimation (Lotus Depth) | Blueprint | 2 GB | 15 s |

---

## Provisioned — not yet hardware-verified

These follow the same rules as everything above — pick the profile, start the
stack, open the workflow — but they have not yet had their smoke lane and
provisioning audit run on a DGX Spark, so the Run column is blank and the disk
figures come from the Hugging Face file sizes rather than a real download.

Promote a row into its category table once `run_lanes.py` and `audit_refs.py`
both pass for it and you have looked at the output. The README's *Verifying
Profiles* section has the commands.

### Utility

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Background removal tuned for glass, glow, camouflage, text and print designs | `lucida-background-removal` | Remove Background (Lucida) | Ours | 0.9 GB | — |

Lucida is a BiRefNet-HR fine-tune. It handles the mattes the bundled
`Remove Background (BiRefNet)` blueprint struggles with — semi-transparent
objects, camouflaged subjects, text and logos with shadows, illustrations,
stickers and tee designs. Same nodes, different checkpoint, so the two can live
side by side and you pick in the loader.

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
