"""Memory management nodes: VRAMCleanup and RAMCleanup.

Several MiniMaxH3-Easy workflows place a `RAMCleanup` node after their video
output. That node comes from LAOGOU-666/Comfyui-Memory_Cleanup (GPL-3.0), which
is most of a thousand lines of Windows API calls that do nothing on Linux: its
`clean_processes` and `clean_dlls` options are Windows-only branches, and the
only thing the Linux path does is call `malloc_trim`. Rather than ship that pack
for two small nodes, this reimplements the same interface.

The class names, input names, order and defaults match the original, so a
workflow saved against it loads here with its widget values intact.

On a DGX Spark the GPU shares host memory, so a finished video render keeps its
whole working set away from everything else until something unloads it. That is
what makes these worth having in a graph: VRAMCleanup is the in-workflow
equivalent of `POST /free {"unload_models": true, "free_memory": true}`.
"""

import ctypes
import gc
import logging
import platform
import time

import comfy.model_management
from server import PromptServer

try:
    import psutil
except ImportError:  # only used for reporting
    psutil = None

_log = logging.getLogger(__name__)


class AnyType(str):
    """Compares equal to every type, so `anything` accepts any wire."""

    def __eq__(self, _) -> bool:
        return True

    def __ne__(self, _) -> bool:
        return False


ANY = AnyType("*")


def _ram_usage():
    if psutil is None:
        return None, None
    memory = psutil.virtual_memory()
    return memory.percent, memory.available / (1024 * 1024)


class VRAMCleanup:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "offload_model": ("BOOLEAN", {"default": True,
                                              "tooltip": "Unload every loaded model."}),
                "offload_cache": ("BOOLEAN", {"default": True,
                                              "tooltip": "Collect garbage and empty the allocator cache."}),
            },
            "optional": {"anything": (ANY, {})},
            "hidden": {"unique_id": "UNIQUE_ID", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("output",)
    OUTPUT_NODE = True
    FUNCTION = "empty_cache"
    CATEGORY = "Memory Management"
    DESCRIPTION = ("Unload models and empty the cache, the in-workflow equivalent of "
                   "POST /free. On shared-memory hardware this is what gives the rest "
                   "of the box its memory back after a render.")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float(time.time())

    def empty_cache(self, offload_model, offload_cache, anything=None,
                    unique_id=None, extra_pnginfo=None):
        try:
            if offload_model:
                comfy.model_management.unload_all_models()
            if offload_cache:
                gc.collect()
                comfy.model_management.soft_empty_cache()
                PromptServer.instance.prompt_queue.set_flag("free_memory", True)
            _log.info("VRAM cleanup done (models=%s, cache=%s)", offload_model, offload_cache)
        except Exception as exc:
            _log.warning("VRAM cleanup failed: %s", exc)
        return (anything,)


class RAMCleanup:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clean_file_cache": ("BOOLEAN", {"default": True,
                                                 "tooltip": "Return free heap to the OS (malloc_trim)."}),
                "clean_processes": ("BOOLEAN", {"default": True,
                                                "tooltip": "Windows only; ignored on Linux."}),
                "clean_dlls": ("BOOLEAN", {"default": True,
                                           "tooltip": "Windows only; ignored on Linux."}),
                "retry_times": ("INT", {"default": 3, "min": 1, "max": 10, "step": 1}),
            },
            "optional": {"anything": (ANY, {})},
            "hidden": {"unique_id": "UNIQUE_ID", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("output",)
    OUTPUT_NODE = True
    FUNCTION = "clean_ram"
    CATEGORY = "Memory Management"
    DESCRIPTION = ("Return free heap to the OS. Only clean_file_cache does anything off "
                   "Windows; the other two switches are kept so workflows saved against "
                   "Comfyui-Memory_Cleanup load unchanged.")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float(time.time())

    def clean_ram(self, clean_file_cache, clean_processes, clean_dlls, retry_times,
                  anything=None, unique_id=None, extra_pnginfo=None):
        before_pct, before_avail = _ram_usage()
        system = platform.system()
        try:
            gc.collect()
            if clean_file_cache:
                for _ in range(retry_times):
                    if system == "Linux":
                        # Hand back arena memory the allocator is sitting on. The
                        # upstream node sleeps a second per retry; there is nothing
                        # to wait for, so we do not.
                        try:
                            ctypes.CDLL("libc.so.6").malloc_trim(0)
                        except (OSError, AttributeError) as exc:
                            _log.debug("malloc_trim unavailable: %s", exc)
                            break
                    elif system == "Windows":
                        try:
                            ctypes.windll.kernel32.SetSystemFileCacheSize(-1, -1, 0)
                        except Exception as exc:
                            _log.debug("SetSystemFileCacheSize failed: %s", exc)
                            break
                    else:
                        break
            if (clean_processes or clean_dlls) and system != "Windows":
                _log.debug("RAM cleanup: clean_processes/clean_dlls are Windows-only, ignored")

            after_pct, after_avail = _ram_usage()
            if before_avail is not None and after_avail is not None:
                _log.info("RAM cleanup done (%.1f%% -> %.1f%%, freed %.0f MB)",
                          before_pct, after_pct, after_avail - before_avail)
            else:
                _log.info("RAM cleanup done")
        except Exception as exc:
            _log.warning("RAM cleanup failed: %s", exc)
        return (anything,)


NODE_CLASS_MAPPINGS = {
    "VRAMCleanup": VRAMCleanup,
    "RAMCleanup": RAMCleanup,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "VRAMCleanup": "VRAM Cleanup",
    "RAMCleanup": "RAM Cleanup",
}
