"""Copies sample inputs that ship with this pack into ComfyUI's input folder.

Most templates get their sample files through the asset profiles, which
download them. The InfiniteTalk speaker masks, the Iris-Z sample depth map
(the taxi photo's depth, for Depth to Image) and the two-speaker sample (a FLEURS
Finnish recording, CC BY 4.0, over a LibriVox reading) were made for this repo and live
in example_inputs/, so they are copied over at startup when missing; anything
already there, file or link, is left alone. A symlink would not do: ComfyUI
refuses input files that resolve outside the input folder.
"""

import os
import shutil

import folder_paths

SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_inputs")


def install():
    if not os.path.isdir(SOURCE):
        return
    target = folder_paths.get_input_directory()
    for name in sorted(os.listdir(SOURCE)):
        dest = os.path.join(target, name)
        if os.path.lexists(dest):
            continue
        try:
            shutil.copyfile(os.path.join(SOURCE, name), dest)
        except OSError as exc:
            print(f"[DGX-Spark-Templates] could not copy {name} into {target}: {exc}")
