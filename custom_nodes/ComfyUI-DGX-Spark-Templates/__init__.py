"""DGX Spark bundled workflow templates.

Mostly example workflows tuned for DGX Spark (NVFP4 variants of bundled
templates), exposed through the template browser.

It also carries three nodes of its own. Several MiniMaxH3-Easy workflows place
a `RAMCleanup` node after their output; that node belongs to a pack whose Linux
path amounts to one `malloc_trim` call, so `memory_nodes` reimplements the same
interface here rather than pulling the pack in. See that module for detail.
`seed_nodes` holds the Seed List node the YuE2 best-of-8 template uses to turn
one run into eight takes.

`cpu_memory` starts a watcher that hands the CPU memory PyTorch has freed back
to the system once the queue has been idle for a minute.
"""

from . import cpu_memory, memory_nodes, seed_nodes

cpu_memory.start()

NODE_CLASS_MAPPINGS = {**memory_nodes.NODE_CLASS_MAPPINGS, **seed_nodes.NODE_CLASS_MAPPINGS}
NODE_DISPLAY_NAME_MAPPINGS = {**memory_nodes.NODE_DISPLAY_NAME_MAPPINGS,
                              **seed_nodes.NODE_DISPLAY_NAME_MAPPINGS}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
