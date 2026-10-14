"""Draft Gate and Render Gate: two-pass workflows.

A two-pass workflow writes a draft first, quickly, and renders it in a second
run once someone has read and corrected it. Draft Gate picks the text that
goes on: the draft, or a corrected copy pasted into checked_text, in which case
the draft is not computed again.

Render Gate sits in front of each output of the slow branch. Its value input is
lazy, so with render off nothing upstream of it runs, loaders included. Draft
Gate's blocked text output alone would not do that: ComfyUI queues every
non-lazy upstream node of an output before running any of them, and an
ExecutionBlocker only stops the node that receives it, so the slow branch's
model loaders would still load in the first pass.

Render Gate can also refuse to start a heavy branch when the machine is short
of memory. On a DGX Spark the GPU shares the host's memory, and two servers
rendering at once end in a hard reboot rather than an error, so a short message
is the better outcome.
"""

import torch

import comfy.model_management
import comfy.system_memory
from comfy_execution.graph_utils import ExecutionBlocker

from .memory_nodes import ANY


def freeable_gib():
    """Memory free now plus what this server could release: its loaded models
    and the part of its allocator cache that holds no tensors."""
    free = comfy.system_memory.virtual_memory_available()
    free += sum(m.model_loaded_memory() for m in comfy.model_management.current_loaded_models
                if m.model is not None)
    if torch.cuda.is_available():
        stats = torch.cuda.memory_stats()
        free += stats.get("reserved_bytes.all.current", 0) - stats.get("active_bytes.all.current", 0)
    return free / 2**30


class DraftGate:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "checked_text": ("STRING", {"multiline": True, "default": "",
                                            "tooltip": "The corrected text. Leave empty to use the draft as it is."}),
                "render": ("BOOLEAN", {"default": False,
                                       "tooltip": "Off: show the draft and stop. On: send the checked text, or "
                                                  "the draft when checked_text is empty, to the rest of the "
                                                  "workflow."}),
                # socket-only input after the widgets: workflows store widget values by position
                "draft": ("STRING", {"forceInput": True, "lazy": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("preview", "text")
    FUNCTION = "pick"
    CATEGORY = "utilities"
    DESCRIPTION = ("Picks the text for the second pass of a two-pass workflow. preview always shows the text in "
                   "use; text goes on only when render is on. With render on and checked_text filled, the draft "
                   "is not computed.")

    def check_lazy_status(self, checked_text, render, draft=None):
        if render and checked_text.strip():
            return []
        return ["draft"]

    def pick(self, checked_text, render, draft=None):
        if render and checked_text.strip():
            return (checked_text, checked_text)
        if not render:
            return (draft, ExecutionBlocker(None))
        return (draft, draft)


class RenderGate:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "render": ("BOOLEAN", {"default": True}),
                "min_free_gib": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1024.0, "step": 1.0,
                                           "tooltip": "Stop with a message instead of starting when less memory "
                                                      "than this is free, counting what this server could "
                                                      "unload. 0 skips the check."}),
                "value": (ANY, {"lazy": True}),
            },
        }

    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("value",)
    FUNCTION = "gate"
    CATEGORY = "utilities"
    DESCRIPTION = ("Passes value on only when render is on. With render off nothing upstream of value runs, "
                   "and every node after the gate is skipped.")

    def check_lazy_status(self, render, min_free_gib, value=None):
        if not render:
            return []
        # value is already computed when ComfyUI asks again after evaluating it
        if value is None and min_free_gib > 0:
            free = freeable_gib()
            if free < min_free_gib:
                raise RuntimeError(f"Only {free:.0f} GiB of memory is free and this needs about "
                                   f"{min_free_gib:.0f} GiB. Another job is probably running on this machine; "
                                   "run again once it has finished.")
        return ["value"]

    def gate(self, render, min_free_gib, value=None):
        if not render:
            return (ExecutionBlocker(None),)
        return (value,)


NODE_CLASS_MAPPINGS = {
    "DraftGate": DraftGate,
    "RenderGate": RenderGate,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DraftGate": "Draft Gate",
    "RenderGate": "Render Gate",
}
