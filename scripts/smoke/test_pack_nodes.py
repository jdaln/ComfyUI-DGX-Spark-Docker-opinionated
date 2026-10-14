#!/usr/bin/env python3
"""Unit tests for the template pack's own nodes, without a running server.

The pack's modules are imported one by one with light stand-ins for the ComfyUI
modules that would start CUDA or a server, so the tests run in seconds and use
no GPU. They need the container's Python for torch, Pillow and av:

    docker cp scripts/smoke/test_pack_nodes.py comfyui:/tmp/
    docker exec comfyui python3 /tmp/test_pack_nodes.py

Rendered pages are left in /tmp/pack_node_tests/ to look at.
"""
import importlib
import os
import re
import shutil
import sys
import tempfile
import types
import unittest

COMFY = "/workspace/ComfyUI"
PACK = os.path.join(COMFY, "custom_nodes", "ComfyUI-DGX-Spark-Templates")
WORK = "/tmp/pack_node_tests"
FREE = {"bytes": 100 * 2**30}


def stub_comfy():
    """Stand-ins for the ComfyUI modules the pack imports, enough for these tests."""
    import av
    import torch

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
        return mod

    def save_image_path(prefix, output_dir, width=0, height=0):
        subfolder, name = os.path.split(prefix)
        folder = os.path.join(output_dir, subfolder)
        os.makedirs(folder, exist_ok=True)
        counters = [int(m.group(1)) for f in os.listdir(folder) if (m := re.match(re.escape(name) + r"_(\d+)", f))]
        return folder, name, max(counters, default=0) + 1, subfolder, prefix

    def load(path):
        with av.open(path) as container:
            stream = container.streams.audio[0]
            frames = [torch.from_numpy(f.to_ndarray()) for f in container.decode(stream)]
        wave = torch.cat(frames, dim=1)
        return (wave.float() / 32768.0 if wave.dtype == torch.int16 else wave.float()), stream.sample_rate

    module("folder_paths", get_input_directory=lambda: os.path.join(WORK, "input"),
           get_output_directory=lambda: os.path.join(WORK, "output"), get_save_image_path=save_image_path)
    comfy = module("comfy")
    comfy.model_management = module("comfy.model_management", current_loaded_models=[])
    comfy.system_memory = module("comfy.system_memory", virtual_memory_available=lambda: FREE["bytes"])
    extras = module("comfy_extras")
    extras.nodes_audio = module("comfy_extras.nodes_audio", load=load)
    module("server", PromptServer=None)
    sys.path.insert(0, COMFY)  # comfy_execution.graph_utils imports nothing else
    pack = module("pack")
    pack.__path__ = [PACK]


def load(name):
    return importlib.import_module(f"pack.{name}")




class Gates(unittest.TestCase):
    def test_draft_gate(self):
        gate = load("draft_nodes").DraftGate()
        blocker = sys.modules["comfy_execution.graph_utils"].ExecutionBlocker
        self.assertEqual(gate.check_lazy_status("", False), ["draft"])
        self.assertEqual(gate.check_lazy_status("Korjattu.", True), [])
        self.assertEqual(gate.check_lazy_status("  ", True), ["draft"])
        preview, text = gate.pick("", False, draft="Luonnos.")
        self.assertEqual(preview, "Luonnos.")
        self.assertIsInstance(text, blocker)
        self.assertEqual(gate.pick("Korjattu.", True), ("Korjattu.", "Korjattu."))
        self.assertEqual(gate.pick("", True, draft="Luonnos."), ("Luonnos.", "Luonnos."))

    def test_render_gate(self):
        gate = load("draft_nodes").RenderGate()
        blocker = sys.modules["comfy_execution.graph_utils"].ExecutionBlocker
        self.assertEqual(gate.check_lazy_status(False, 80.0), [])
        self.assertIsInstance(gate.gate(False, 80.0)[0], blocker)
        FREE["bytes"] = 40 * 2**30
        with self.assertRaisesRegex(RuntimeError, "Only 40 GiB"):
            gate.check_lazy_status(True, 80.0)
        self.assertEqual(gate.check_lazy_status(True, 80.0, value="done"), ["value"])
        self.assertEqual(gate.check_lazy_status(True, 0.0), ["value"])
        FREE["bytes"] = 100 * 2**30
        self.assertEqual(gate.check_lazy_status(True, 80.0), ["value"])
        self.assertEqual(gate.gate(True, 80.0, value="done"), ("done",))
if __name__ == "__main__":
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(os.path.join(WORK, "input"))
    tempfile.tempdir = WORK
    stub_comfy()
    unittest.main(verbosity=2)
