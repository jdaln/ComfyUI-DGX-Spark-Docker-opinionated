"""Seed List: one seed in, a list of consecutive seeds out.

When a node's input arrives as a list, ComfyUI runs that node once per item,
and every node downstream of it does the same. Wiring this into the seeds of a
generation graph turns one queued run into `count` independent takes. The YuE2
best-of-8 template uses it to re-roll the score, the music tokens and the audio
of each take, then saves all of them.
"""


class SeedList:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                 "control_after_generate": True}),
                "count": ("INT", {"default": 8, "min": 1, "max": 4096,
                                  "tooltip": "How many seeds, and so how many takes downstream."}),
            },
        }

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("seeds",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "seeds"
    CATEGORY = "utilities"
    DESCRIPTION = ("seed, seed + 1, ... as a list, so every node downstream runs once per "
                   "seed and one queued run produces count takes.")

    def seeds(self, seed, count):
        return ([(seed + i) % 0x10000000000000000 for i in range(count)],)


NODE_CLASS_MAPPINGS = {
    "SeedList": SeedList,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SeedList": "Seed List",
}
