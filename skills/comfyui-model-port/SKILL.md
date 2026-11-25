---
name: comfyui-model-port
description: 'Port a model that has no ComfyUI support onto ComfyUI core models in this DGX Spark repo, and prove the port matches upstream. Use when: a new model is a fine-tune or variant of an architecture core already runs (HunyuanVideo 1.5, FLUX.2, MiniMax H3, ...), when upstream code cannot be imported (pinned torch or transformers), or when checking that a port, a quantized build or a new sampler gives the same results as the reference. Covers the load spike, model-call parity with a noise floor, end-to-end checks with shared noise, and what to vendor.'
---

# Port a model onto core (this repo)

MovieRoll (on core's HunyuanVideo 1.5) and Iris-Z small (on core's FLUX.2 Klein
9B) were done this way. Everything runs in locked containers
(`scripts/tools/locked_run.sh`), one GPU job at a time, under the memory guard.
Upstream code goes in `tmp/<name>/` as a read-only reference: read it, run it
as the reference in tests, never import it from the pack.

## 1. Spike: does it load into core, strictly?

Read the weight headers only (safetensors `get_slice`), build meta tensors, and
let core pick the config: `comfy.model_detection.model_config_from_unet(meta, "")`.
Then rename with **core's own load path**, `model_config.process_unet_state_dict`
(BFL `_norm.scale` becomes `.weight`, and so on), build the model on the meta
device and compare keys and shapes both ways. Finish with a real
`load_state_dict(..., strict=True, assign=True)`. Pass: the only keys left over
are the ones the variant adds (Iris-Z: `modality_in.weight`). Report and wait
for the owner's go before porting.

If detection picks the wrong family (MovieRoll's weights lack `vision_in`, so
core saw HunyuanImage 2.1 Refiner), build the right `supported_models` class
explicitly.

## 2. Look for what core already does

Before writing a forward pass, read core's model for hooks that cover the
variant: per-token timestep modulation (`timestep_zero_index`,
`modulation_dims`), reference latents and their methods
(`index_timestep_zero`), `post_input` patches (add an embedding, rewrite
position ids), wrappers. Iris-Z needed no forward of its own: a reference
latent at timestep 0 plus a `post_input` patch, and core's sampler does the rest.
Less code, and every standard sampler and scheduler node works.

## 3. Parity of one model call, with a floor

Same inputs to upstream and to the port (random latents, a real context, one
timestep), compare outputs as relative error. A number means nothing alone:

- **Control:** the port with the key piece switched off (no patch, wrong
  method). It must be far off (Iris-Z: 16-19 % against 2 %).
- **Floor:** upstream against itself with only rounding changed: upstream in
  fp32 (cast the same bf16 weights up), or another attention kernel
  (`torch.nn.attention.sdpa_kernel([SDPBackend.MATH])`). A port within the floor
  is as close as upstream is to itself (Iris-Z: port 1.1 % from fp32, upstream's
  own bf16 2.2 %).

Upstream code that needs a kernel the image lacks (FA3) can get a stand-in on
SDPA for an exact reference. Capture upstream's inputs at the call you compare
rather than rebuilding them by hand.

## 4. End to end, with shared noise

Reproduce upstream's starting noise exactly (same generator, device, dtype and
draw order) and pass it to core's sampler, so the two runs differ only by the
port. Short samplers (4 steps) should agree closely (Iris-Z depth: 0.1-0.6 % of
the range). Long CFG runs (50 steps) diverge chaotically: compare against the
floor from step 3 run over the same steps (Iris-Z: port 0.32-0.51 latent rel err,
upstream against itself 0.34), and look at the pictures: same scene, drifting
details is a pass.

Check the codec separately (VAE encode and decode against upstream's). A large
difference may be upstream's own rounding: the SDK's bf16 autocast was 18 % off
its own fp32 on grey depth maps, while core's fp16 VAE matched fp32 to 0.3 %.

## 5. What goes into the pack

- A module of nodes written anew on core's classes, with a docstring naming the
  upstream commit and files it follows, the licence, and what was changed.
- Code copied verbatim (model definitions) goes in `vendor/<name>/` with the
  upstream licence in `licenses/` and the changes listed; pin any small pip
  dependency in the pack's `requirements.txt`.
- Weights load as plain data: safetensors, or `torch.load(weights_only=True)`.
  Check every pickle loads that way before anything else touches it. Load
  strictly; upstream loaders that skip mismatched keys hide errors.
- Unit tests in `scripts/smoke/test_pack_nodes.py` for the logic that is not
  the network (patches, stitching, resizing rules), with stand-ins for models.
- Then the normal onboarding: the `comfyui-workflow-integrate` skill.

## Memory

Add up what upstream keeps resident at once, not one file's size. The machine
has 121.7 GiB shared by CPU and GPU, and running out means a hard reboot. Decode
long videos one latent frame at a time if the VAE needs it; unload upstream
helpers (text encoders) before the big model loads.
