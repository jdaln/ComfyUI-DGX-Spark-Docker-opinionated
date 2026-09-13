# Workflow catalogue

Every workflow this setup provisions, what it is for, and how to run it.

`asset-profiles.json` defines 57 profiles. The 55 in the category tables below
are verified end to end on a DGX Spark: the models download, the workflow opens
with no missing models, and it produces output. Run times are measured at each
workflow's default settings. The remaining 2 are listed under
[Provisioned, not yet hardware-verified](#provisioned-not-yet-hardware-verified).

Most of those 55 have an automated smoke lane. Three do not, because their
workflow needs an audio file the repo does not ship, so they were checked by
hand instead: `vibevoice-large`, `heartmula-transcribe` and
`tts-prompted-conversation`. They are marked below.

## How to use this

1. Copy a row's profile name into `.env`:

   ```dotenv
   COMFY_ASSET_PROFILES=krea-2-turbo
   ```

   List several comma-separated. Shared models such as text encoders and VAEs
   download once, so two profiles usually cost far less than the sum of their
   sizes.

2. Start the stack. Models download on first boot, before the server starts:

   ```bash
   docker compose up -d
   docker logs -f comfyui
   ```

3. Open <http://localhost:8188> and load the workflow named in the table:

   | Type | Where to find it |
   | --- | --- |
   | **Template** | Workflow → Browse Templates, search the name |
   | **Blueprint** | the node search box; they are subgraph blueprints |
   | **Ours** | Browse Templates → `ComfyUI-DGX-Spark-Templates` |
   | **Node example** | Browse Templates → the custom node's section |

Krea 2, Ideogram 4, Gemma 3 and two LTX LoRAs are gated on Hugging Face. Accept
the licence on the model page and set `HF_TOKEN` in `.env`, or those profiles
download nothing. See [models.md](models.md) for the current gated list.

---

## Text to image

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Fastest general purpose image model | `z-image-turbo-core` | `image_z_image_turbo` | Template | 19 GB | 20 s |
| High-aesthetic 8-step distilled model | `krea-2-turbo` | `image_krea2_turbo_t2i` | Template | 17 GB | 35 s |
| Same, NVFP4 build; smaller and faster on Blackwell | `krea-2-turbo-nvfp4` | Text to Image (Krea 2 Turbo NVFP4) | Ours | 12 GB | 20 s |
| Krea 2 with nine style LoRAs (ink wash, retro anime, watercolour) | `krea-2-turbo-styleloras` | Text to Image (Krea 2 Turbo Style LoRA) | Ours | 21 GB | 20 s |
| Krea 2 base model, full 52-step sampling; best for LoRA training and variety | `krea-2-raw` | Text to Image (Krea 2 RAW) | Ours | 17 GB | 90 s |
| Qwen-Image, 8-step Lightning LoRA | `qwen-image-t2i-lightning-8step` | Text to Image (Qwen-Image) | Blueprint | 30 GB | 115 s |
| Qwen-Image 2512, 4-step Lightning LoRA | `qwen-image-2512-t2i-lightning-4step` | `image_qwen_Image_2512` | Template | 30 GB | 230 s |
| Ideogram 4; strongest text rendering in images | `ideogram-4` | Text to Image (Ideogram v4) | Blueprint | 27 GB | 55 s |
| Same, NVFP4 build | `ideogram-4-nvfp4` | Text to Image (Ideogram v4 NVFP4) | Ours | 20 GB | 70 s |

## Editing existing images

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Instruction-driven edits ("make it night", object swaps) | `qwen-image-edit-2511-core` | Image Edit (Qwen 2511) | Blueprint | 47 GB | 275 s |
| Inpainting and outpainting with a mask | `qwen-image-inpaint-lightning-4step` | Image Inpainting (Qwen-image) | Blueprint | 34 GB | 30 s |
| Split an image into editable layers | `qwen-image-layered-core` | `image_qwen_image_layered` | Template | 47 GB | 65 s |
| Compose from a control image (canny, depth, pose) | `z-image-turbo-union-control` | `image_z_image_turbo_fun_union_controlnet` | Template | 22 GB | 80 s |
| Instruction-driven edits with up to 16 reference images | `mage-flow-edit` | Image Edit (Mage-Flow) | Ours | 17 GB | 275 s |
| Same, 4-step distilled, about 7x faster | `mage-flow-edit-turbo` | Image Edit (Mage-Flow Turbo) | Ours | 17 GB | 25 s |

The two Mage-Flow profiles share an 8.3 GB text encoder and a VAE, so whichever
you add second costs 7.7 GB. ComfyUI supports Mage-Flow in core, so neither
template installs a custom node. There is no NVFP4 build that ComfyUI can load.

## Video

### Wan, the general purpose video models

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to video, 14B | `wan2.2-t2v-bundled` | `video_wan2_2_14B_t2v` | Template | 37 GB | 10 min |
| Animate a still image, 14B | `wan2.2-i2v-bundled` | `video_wan2_2_14B_i2v` | Template | 37 GB | 10 min |
| Replace or remove things inside a video | `wan2.1-vace-bundled` | Video Inpainting (Wan2.1 VACE) | Blueprint | 41 GB | 3 min |
| Drive a character with a reference video | `wananimate-preprocess` | WanAnimate_native_example_01 | Node example | 2 GB | 5 s |

`wananimate-preprocess` ships only the pose, detection and segmentation models.
Pair it with a Wan animate checkpoint. Its smoke lane covers the preprocessing
branch alone, which is why it runs in seconds; the animate checkpoint, LoRAs,
text encoder and VAE the rest of that workflow loads come from you.

### MiniMax H3, video with its own soundtrack

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to video, with dialogue, effects and music generated with it | `minimax-h3-t2v` | Text to Video (MiniMax H3) | Ours | 42 GB | 291 s |
| Animate a still image, same joint audio | `minimax-h3-i2v` | Image to Video (MiniMax H3) | Ours | 42 GB | 276 s |
| Carry an identity, style, motion or voice over from references | `minimax-h3-ref2v` | Reference to Video (MiniMax H3) | Ours | 42 GB | 316 s |

The first workflows here that produce sound. H3 models audio and video in one
forward pass instead of dubbing a track on afterwards, so speech lands in sync
with the mouth and effects land on the action. Output is 24 fps and about five
seconds at the shipped defaults; the Resolution Selector caps the short edge at
768 px.

Reference to video takes up to nine images, three videos (each able to carry its
own soundtrack) and three loose audio clips, addressed from the prompt by tag
(`<Picture 1>`, `<Video 1>`, `<Audio 1>`) in the order you connected them. It
runs the `ref2va` weights rather than the `fl2va` the other two share, so it
costs a second 21 GB model; all three together are 63 GB.

Core ships its own `video_minimax_h3_t2v` / `_i2v` / `_r2v` templates, loading
the same four files these profiles provision. The bundled copies stay anyway:
core's i2v and r2v default to sample images that are not published anywhere
fetchable (`transparent_rgb_gaming_mouse.png`, `red_superboy_on_city_roof.png`),
so they cannot run unattended, and the exports shared one workflow id. Ours
point at `example.png` and carry distinct ids, which is what makes them smoke
testable. Do not delete them as duplicates.

Each run peaks around 40 GB resident on top of whatever else is on the box, not
the 53 GiB core's template metadata advertises. All three produce 864x480 or
640x640 at 24 fps, 5.2 s, with a stereo AAC track at roughly -14 dB mean, so the
joint audio path really is generating sound rather than padding silence.

Nothing here is gated. Needs ComfyUI v0.30.0 or newer for the `MiniMaxH3*`
nodes.

### LTX 2.0, fast video, distilled or full quality

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

### LTX 2.3, newest generation, 22B

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to video, two-stage distilled | `ltx-2.3-t2v-i2v-two-stage-distilled` | Text to Video (LTX-2.3) | Blueprint | 60 GB | 140 s |
| Image to video, single stage | `ltx-2.3-t2v-i2v-single-stage-distilled-full` | Image to Video (LTX-2.3) | Blueprint | 59 GB | 105 s |
| Aligned control (canny, depth, pose) via IC-LoRA | `ltx-2.3-iclora-union-control-distilled` | `video_ltx2_3_ic_lora` | Template | 61 GB | 270 s |
| Transfer motion from a source video | `ltx-2.3-iclora-motion-track-distilled` | `video_ltx2_3_ic_lora` | Template | 60 GB | 160 s |
| HDR and relighting pass | `ltx-2.3-iclora-hdr-distilled` | `video_ltx2_3_ic_lora` | Template | 60 GB | 240 s |
| Lip-sync a face to audio | `ltx-2.3-iclora-lipdub-two-stage-distilled` | `video_ltx2_3_ic_lora` | Template | 63 GB | 260 s |

The four IC-LoRA profiles share one template. Select the LoRA in the `ic_lora`
widget to match the profile you provisioned.

### LTX 2.5, 22B, joint audio and video

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text, image or first/last frame to video, with audio | `ltx-2.5-distilled` | `video_ltx2_5_t2v`, `_i2v`, `_flf2v` | Template | 41 GB | 107 s |
| Same, NVFP4 transformer, 3 GB smaller on disk | `ltx-2.5-distilled-nvfp4` | Text to Video (LTX-2.5 NVFP4) | Ours | 38 GB | 116 s |
| Upscale any existing video 2x, no generator needed | `ltx-2.5-latent-upscale` | Video Upscale (LTX-2.5 Latent 2x) | Ours | 2.3 GB | 105 s |
| Timeline editor: multi-shot sequencing, per-segment prompts | `ltx-2.5-distilled-nvfp4` | LTX Director 2 (LTX-2.5) | Ours | 38 GB | GUI only |
| Place up to 50 keyframes at chosen frames | `ltx-2.5-sequencer` | Shot Sequencer (LTX-2.5) | Ours | 41 GB | 120 s |

LTX 2.5 conditions on Gemma 4, so it needs ComfyUI 0.35.0 or newer. Core ships
the three templates and matching blueprints, all pointing at the int8-convrot
transformer, which is what `ltx-2.5-distilled` provisions. The NVFP4 build is
17.4 GB against 20.0 GB and no core template references it, so it gets a bundled
copy with the loader switched, the same arrangement as Krea 2 and Ideogram
NVFP4. Both profiles share the Gemma 4 encoder, both VAEs, the prompt enhancer
and the spatial upscaler, so the second costs only its transformer.

Output is 1280x704 at 24 fps, about 5 seconds, with a stereo track, so it is
both higher resolution and roughly half the time of MiniMax H3.

NVFP4 saves 3 GB on disk but measured slightly slower than int8 here, 116 s
against 107 s, so pick it for space rather than speed. Only the transformer
differs.

`LTX Director 2 (LTX-2.5)` is the timeline editor from
[WhatDreamsCost-ComfyUI](https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI)
(GPL-3.0), converted off LTX 2.3: `DualCLIPLoader` becomes a single `CLIPLoader`
because 2.5 folds the text projection into its Gemma 4 encoder, the transformer,
both VAEs and the upscaler move to 2.5 builds, and the KJNodes latent-preview
pair is gone because it needed a 2.3-only tiny VAE. The derived file is GPL-3.0
and carries its attribution in a note inside the workflow.

`Shot Sequencer (LTX-2.5)` is core's own `video_ltx2_5_i2v` with one node
swapped: the stage-1 `LTXVImgToVideoInplace` becomes `LTXSequencer` from the same
GPL-3.0 pack, which places up to 50 keyframes into the video latent at chosen
frames. It ships with a single keyframe at frame 0, behaving like plain image to
video; raise `num_images` and set `insert_frame_N` to sequence shots. Everything
else is core's verified two-stage chain, so it uses the int8 transformer and
needs no assets beyond `ltx-2.5-distilled`; `ltx-2.5-sequencer` is an alias of
that profile.

Two constraints are worth knowing before rewiring it. The Sequencer must sit
before `LTXVConcatAVLatent`, because 2.5's combined audio+video latent is a
NestedTensor and the node calls `.clone()`. And `LTXDirectorCropGuides` has to
run after each sampler stage: the Sequencer appends keyframe tokens to the
latent, the stage-2 upscale changes the spatial resolution, and without cropping
the sampler rejects the token count.

`LTX Director 2 (LTX-2.5)` has no smoke lane. `LTXDirector` carries 23 widget values across 11 required
and 12 optional inputs, several link-converted, so the headless UI-to-API
conversion in `wf_smoke.py` maps them positionally and shifts. The upstream
workflow fails the same way for the same reason, so this is the node's design
rather than something the conversion introduced. The ComfyUI frontend builds the
prompt from its own widget model and is unaffected, which is why the row reads
GUI only.

`ltx-2.5-latent-upscale` is the cheap one: the video VAE and the spatial
upscaler only, 2.3 GB and no transformer. It takes a video file, snaps it to a
multiple of 32, upscales the latent 2x and re-muxes the original audio, so it
works on output from any model. Aimed at MiniMax H3, whose `ref2v` carries
identity and voice from references and has no LTX equivalent, but whose 864x480
is below what LTX 2.5 generates natively. Adapted from
[Peter Duncan's MiniMax H3 + LTX 2.5 upscaler workflow](https://github.com/peterducan-hub/PeterDuncan_Comfyui),
rebuilt on core nodes because the original needs eleven node types this repo
does not install.

This is the heaviest workflow in the catalogue for memory: a 15 GB text encoder
and a 21 GB transformer put the floor at 13.9 GB free during VAE decode, against
20.8 GB for MiniMax H3. Give it a quiet machine, and see
[troubleshooting.md](troubleshooting.md#memory-stays-used-after-a-run).

Weights are [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5),
which is gated. Accept the licence on that page with the `HF_TOKEN` account or
every file 403s. Approval is instant, but it is not automatic.

### HunyuanVideo

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Image to video via the Leapfusion LoRA | `leapfusion-hunyuanvideo-i2v` | `leapfusion_hunyuuanvideo_i2v_native_testing` | Node example | 29 GB | 6 min |

### Video editing with LTX-2.3 task LoRAs

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Rewrite a clip from a plain instruction ("make it snow") | `bfs-ltx-2.3-edit-anything` | Video Edit Anything (LTX-2.3) | Ours | 62 GB | 255 s |
| Anime to live action and back, on an existing clip | `bfs-ltx-2.3-style-swap` | Video Style Swap (LTX-2.3 Anime2Real) | Ours | 61 GB | 216 s |
| Repaint a masked region of a clip | `bfs-ltx-2.3-inpaint` | Video Inpainting (LTX-2.3 Masked) | Ours | 61 GB | 246 s |
| Same, driven by a reference image | `bfs-ltx-2.3-masked-ref-inpaint` | Video Inpainting (LTX-2.3 Masked) | Ours | 61 GB | 211 s |
| Swap the head in a clip, keeping the performance | `bfs-ltx-2.3-head-swap` | Video Head Swap (LTX-2.3) | Ours | 62 GB | 256 s |

Rebuilt from [ComfyUI-BFSNodes](https://github.com/alisson-anjos/ComfyUI-BFSNodes)
by Alisson Anjos onto the same LTX-2.3 chain as
`ltx-2.3-t2v-i2v-two-stage-distilled`, so all five share one base checkpoint and
differ only by a 0.3–1.3 GB task LoRA. Where a task has more than one published
capture the profile provisions both and the template loads the higher-fidelity
one; switch on the LoRA node.

A sixth profile, `bfs-ltx-2.3-multishot`, is waiting on upstream weights.

## Speech

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Conversations between up to 4 characters, voices cloned from samples [^h] | `vibevoice-large` | Text to Speech (Multi-Character Conversation) | Ours | 18 GB | 215 s |
| A voice described in words rather than sampled | `ltx-2.3-tts-prompted-voice` | Text to Speech (LTX-2.3 Prompted Voice) | Ours | 60 GB | 172 s |
| Both at once: describe one voice, clone the rest, run the conversation [^h] | `tts-prompted-conversation` | Text to Speech (Prompted Voices to Conversation) | Ours | 78 GB | 311 s |

Pick by what you have. [VibeVoice](https://github.com/Enemyx-net/VibeVoice-ComfyUI)
does real multi-speaker dialogue (`[1]:`/`[2]:` script, up to four voices,
`[pause:800]` tags) but every voice needs an audio sample. LTX-2.3 can be told
what a voice sounds like ("low, hoarse, soft Edinburgh accent") but renders one
utterance at a time at video-model cost. The third template renders speaker 1
with LTX-2.3 and hands that clip to VibeVoice as the clone source, with speakers
2 to 4 on ordinary Load Audio nodes.

`ltx-2.3-tts-prompted-voice` adds no new weights. It reuses the
`ltx-2.3-t2v-i2v-two-stage-distilled` base and reads the audio VAE out of that
checkpoint.

## Audio

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Text to music and song, with lyrics and style tags | `ace-step-1.5-core` | Text to Audio (ACE-Step 1.5) | Blueprint | 14 GB | 50 s |
| Full songs with lyrics and style tags, up to 5 minutes | `heartmula-oss-3b` | Text to Music (HeartMuLa 3B) | Ours | 21 GB | 145 s |
| Transcribe sung lyrics out of a track [^h] | `heartmula-transcribe` | Lyrics Transcription (HeartMuLa) | Ours | 3 GB | 10 s |

[HeartMuLa](https://huggingface.co/HeartMuLa) is a 3B music model, better than
`ace-step-1.5-core` at vocals and song structure for about 1.5x the disk.
Section markers (`[Verse]`, `[Chorus]`) shape the arrangement; English, Chinese,
Japanese, Korean and Spanish lyrics all work.

The transcription template needs a track you put in `ComfyUI/input/`. The repo
ships no sample audio, so its run time was measured with a supplied clip.

## 3D

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Turn one image into a textured 3D mesh (`.glb`) | `hunyuan3d-2.1-core` | Image to Model (Hunyuan3d 2.1) | Blueprint | 7 GB | 60 s |

## Utility

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Depth maps from images, for use as control input | `lotus-depth-support` | Image Depth Estimation (Lotus Depth) | Blueprint | 2 GB | 15 s |
| Background removal tuned for glass, glow, camouflage, text and print designs | `lucida-background-removal` | Remove Background (Lucida) | Ours | 0.9 GB | 5 s |

Lucida is a BiRefNet-HR fine-tune for the mattes the bundled
`Remove Background (BiRefNet)` blueprint struggles with: semi-transparent
objects, camouflaged subjects, text and logos with shadows, illustrations,
stickers, tee designs. Same nodes, different checkpoint, so both can sit side by
side and you pick in the loader.

[^h]: Checked by hand, not by an automated lane. These workflows need an audio
    file the repo does not ship, so there is nothing for `run_lanes.py` to run
    unattended.

---

## Provisioned, not yet hardware-verified

These follow the same rules as everything above: pick the profile, start the
stack, open the workflow. They have not had their smoke lane and provisioning
audit run on a DGX Spark, so the Run column is blank and the disk figures come
from Hugging Face file sizes rather than a real download.

Promote a row into its category table once `run_lanes.py` and `audit_refs.py`
both pass and the output looks right. Commands in [verifying.md](verifying.md).

### Speech

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Same as `vibevoice-large`, on the small model | `vibevoice-1.5b` | Text to Speech (Multi-Character Conversation) | Ours | 5 GB | — |

### Video editing with LTX-2.3 task LoRAs

| What you get | Profile | Workflow | Type | Disk | Run |
| --- | --- | --- | --- | ---: | ---: |
| Plan a multi-shot clip from one caption plus a keyframe per shot | `bfs-ltx-2.3-multishot` | Multishot ShotPlan (LTX-2.3) | Ours | 60 GB | — |

The `LTX Multishot Prompt + Refs` node landed on 2026-08-01 but its
`multishot_strata_r128_v1` LoRA is not published, so the template reports one
missing LoRA and has no smoke lane. The profile provisions the rest of the
stack. Tracked in `scripts/smoke/pending_models.json`.

---

## Choosing quickly

- **A good picture, fast**: `z-image-turbo-core`, or `krea-2-turbo` for nicer
  aesthetics. Prefer the `-nvfp4` variants on Blackwell.
- **Text inside the image**: `ideogram-4`.
- **Editing a photo you already have**: `qwen-image-edit-2511-core`.
- **First video**: `ltx-2.0-i2v-distilled` at 55 s, before committing to Wan 2.2
  at around 10 minutes a clip.
- **Best video quality**: the LTX 2.3 pair, or Wan 2.2 for motion.
- **Tight on disk**: image profiles are 12–30 GB, LTX profiles 60–75 GB each.

## Beyond this list

ComfyUI also ships hundreds of upstream templates and 90 blueprints for models
this repo does not provision, including API and cloud nodes. They stay visible
in the template browser and work if you supply the models yourself. The tables
above are the set that is provisioned and verified here.

To re-verify after changing profiles or updating ComfyUI, see
[verifying.md](verifying.md).
