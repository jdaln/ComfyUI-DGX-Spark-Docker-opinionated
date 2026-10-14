"""DGX Spark bundled workflow templates.

Mostly example workflows tuned for DGX Spark (NVFP4 variants of bundled
templates), exposed through the template browser.

It also carries nodes of its own. Several MiniMaxH3-Easy workflows place
a `RAMCleanup` node after their output; that node belongs to a pack whose Linux
path amounts to one `malloc_trim` call, so `memory_nodes` reimplements the same
interface here rather than pulling the pack in. See that module for detail.
`seed_nodes` holds the Seed List node the YuE2 best-of-8 template uses to turn
one run into eight takes. `dialogue_nodes` splits a script into one speech clip
per line, joins the clips back, with pauses for listening exercises if wanted,
and writes, burns in and saves subtitles, for the dialogue and subtitle
templates. `speech_nodes` adds Whisper large-v3 speech to text with word
timings, and Chatterbox Speech, which runs the multilingual Chatterbox or
Finnish-NLP's Finnish fine-tune of it.

`draft_nodes` holds Draft Gate and Render Gate, which split a workflow into a
quick draft pass and a slow render pass. `text_nodes` turns a dropdown value
into table cells and pipe-separated lines into lists. `character_nodes` keeps a
picture and a voice sample together as a named character in input/cast/.
`page_nodes` lays out Markdown and pictures as printable A4 pages and a PDF,
which the browser extension in `web/` downloads when the node finishes.
`speech_text_nodes` cuts text into sentences and writes out numbers in Finnish
words for speech. `language_model_nodes` runs chat models that core's Generate
Text does not, such as the Finnish-trained Poro 2, through transformers.

`cpu_memory` starts a watcher that hands the CPU memory PyTorch has freed back
to the system once the queue has been idle for a minute. `example_inputs` copies
sample files that ship with the pack, the InfiniteTalk speaker masks, into the
input folder when they are missing.
"""

from . import (character_nodes, cpu_memory, dialogue_nodes, draft_nodes, example_inputs, language_model_nodes,
               memory_nodes, page_nodes, seed_nodes, speech_nodes, speech_text_nodes, text_nodes)

cpu_memory.start()
example_inputs.install()

_MODULES = (memory_nodes, seed_nodes, dialogue_nodes, speech_nodes, draft_nodes, text_nodes, character_nodes,
            page_nodes, speech_text_nodes, language_model_nodes)
NODE_CLASS_MAPPINGS = {name: node for module in _MODULES for name, node in module.NODE_CLASS_MAPPINGS.items()}
NODE_DISPLAY_NAME_MAPPINGS = {name: title for module in _MODULES
                              for name, title in module.NODE_DISPLAY_NAME_MAPPINGS.items()}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
