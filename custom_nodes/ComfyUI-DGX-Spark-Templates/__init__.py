"""DGX Spark bundled workflow templates.

Mostly example workflows tuned for DGX Spark (NVFP4 variants of bundled
templates), exposed through the template browser.

It also carries two memory management nodes. Several MiniMaxH3-Easy workflows
place a `RAMCleanup` node after their output; that node belongs to a pack whose
Linux path amounts to one `malloc_trim` call, so `memory_nodes` reimplements the
same interface here rather than pulling the pack in. See that module for detail.
"""

from .memory_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
