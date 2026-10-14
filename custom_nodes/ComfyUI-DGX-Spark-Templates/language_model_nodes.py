"""Generate Text (Transformers): chat models that ComfyUI's own text nodes do not run.

Core's Generate Text runs the language models ComfyUI implements itself, such
as Gemma 4 and Qwen 3. This node runs other chat models through the transformers
library ComfyUI already installs. It was added for LumiOpen's Poro 2 8B
Instruct, Llama 3.1 trained further on Finnish, which wrote correct Finnish in
tests where Gemma 4 12B made several mistakes in every text.

A model is a folder in models/LLM/ holding the usual Hugging Face files:
config.json, the tokenizer files and safetensors weights. It loads on first use
and is unloaded after the run unless keep_model_loaded is on.
"""

import gc
import os

import torch

import comfy.model_management
import comfy.utils
import folder_paths

MODELS_DIR = os.path.join(folder_paths.models_dir, "LLM")
_LOADED = {}


def models():
    if not os.path.isdir(MODELS_DIR):
        return []
    return sorted(name for name in os.listdir(MODELS_DIR)
                  if os.path.isfile(os.path.join(MODELS_DIR, name, "config.json")))


def _unload():
    if _LOADED.pop("model", None) is not None:
        gc.collect()
        comfy.model_management.soft_empty_cache()


def _load(name):
    if _LOADED.get("model", (None,))[0] != name:
        _unload()
        from transformers import AutoModelForCausalLM, AutoTokenizer
        path = os.path.join(MODELS_DIR, name)
        device = comfy.model_management.get_torch_device()
        tokenizer = AutoTokenizer.from_pretrained(path)
        model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16, device_map=str(device)).eval()
        _LOADED["model"] = (name, tokenizer, model)
    return _LOADED["model"][1:]


def _progress(total):
    """A stopping criterion that moves the progress bar and stops generating when the run is cancelled."""
    from transformers import StoppingCriteria, StoppingCriteriaList

    class Progress(StoppingCriteria):
        def __init__(self):
            self.bar = comfy.utils.ProgressBar(total)

        def __call__(self, input_ids, scores, **kwargs):
            self.bar.update(1)
            stop = comfy.model_management.processing_interrupted()
            return torch.full((input_ids.shape[0],), stop, dtype=torch.bool, device=input_ids.device)

    return StoppingCriteriaList([Progress()])


class TransformersTextGenerate:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (models() or ["none in models/LLM"], {"tooltip": "A folder in models/LLM/ with a Hugging Face "
                                                                          "chat model."}),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "max_new_tokens": ("INT", {"default": 512, "min": 1, "max": 8192}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05,
                                          "tooltip": "0 always picks the most likely word."}),
                "top_p": ("FLOAT", {"default": 0.95, "min": 0.0, "max": 1.0, "step": 0.01}),
                "top_k": ("INT", {"default": 64, "min": 0, "max": 1000}),
                "repetition_penalty": ("FLOAT", {"default": 1.05, "min": 1.0, "max": 3.0, "step": 0.01}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF, "control_after_generate": True}),
                "keep_model_loaded": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "system_prompt": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "generate"
    CATEGORY = "text"
    DESCRIPTION = ("Writes text with a Hugging Face chat model from models/LLM/, run through transformers, for models "
                   "that ComfyUI's own Generate Text does not run, such as LumiOpen's Finnish-trained Poro 2.")

    @classmethod
    def VALIDATE_INPUTS(cls, model):
        if model not in models():
            return f"No chat model named {model!r} in models/LLM/."
        return True

    def generate(self, model, prompt, max_new_tokens, temperature, top_p, top_k, repetition_penalty, seed,
                 keep_model_loaded, system_prompt=None):
        try:
            tokenizer, network = _load(model)
            messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
            messages.append({"role": "user", "content": prompt})
            ids = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt")
            ids = ids.to(network.device)
            torch.manual_seed(seed)
            sampling = {"do_sample": False}
            if temperature > 0:
                sampling = {"do_sample": True, "temperature": temperature, "top_p": top_p, "top_k": top_k}
            with torch.inference_mode():
                out = network.generate(ids, attention_mask=torch.ones_like(ids), max_new_tokens=max_new_tokens,
                                       repetition_penalty=repetition_penalty, pad_token_id=tokenizer.eos_token_id,
                                       stopping_criteria=_progress(max_new_tokens), **sampling)
            comfy.model_management.throw_exception_if_processing_interrupted()
            text = tokenizer.decode(out[0, ids.shape[1]:], skip_special_tokens=True).strip()
        finally:
            if not keep_model_loaded:
                _unload()
        return (text,)


NODE_CLASS_MAPPINGS = {
    "TransformersTextGenerate": TransformersTextGenerate,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TransformersTextGenerate": "Generate Text (Transformers)",
}
