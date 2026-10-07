#!/usr/bin/env python3
"""Build the bundled templates that wrap a ComfyUI blueprint.

A blueprint is a single subgraph node that ComfyUI lists only in the node
search box. Each template here copies one in and adds what it needs to open
and run from the template browser: loaders with sample inputs, any
preprocessing, a save node, the prompt the blueprint leaves empty, and a note.
The blueprint is copied, not referenced, so rebuild after a ComfyUI bump
changes one:

    python3 scripts/build_blueprint_templates.py           write the templates
    python3 scripts/build_blueprint_templates.py --check   exit 1 if any is stale

validate_manifest.py runs the check.
"""
import json
import os
import re
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLUEPRINTS = os.path.join(ROOT, "ComfyUI", "blueprints")
PACK = os.path.join(ROOT, "custom_nodes", "ComfyUI-DGX-Spark-Templates", "example_workflows")
SEED_WIDGETS = {"seed", "noise_seed"}  # followed by a control_after_generate value


class Template:
    def __init__(self, blueprint):
        with open(os.path.join(BLUEPRINTS, blueprint + ".json"), encoding="utf-8") as handle:
            self.wf = json.load(handle)
        assert len(self.wf["nodes"]) == 1, f"{blueprint}: expected one blueprint node"
        self.node = self.wf["nodes"][0]
        self.subgraphs = {sg["id"]: sg for sg in self.wf["definitions"]["subgraphs"]}
        for sg in self.subgraphs.values():
            defined = {link["id"] for link in sg.get("links", [])}
            for node in sg["nodes"]:
                # Without download links the bootstrap leaves these models to the
                # profiles, as it did when the blueprint was the only copy. A
                # Hugging Face link in a note counts as one, so notes keep the text.
                node.get("properties", {}).pop("models", None)
                if node["type"] in ("Note", "MarkdownNote") and node.get("widgets_values"):
                    text = re.sub(r"\[([^\]]*)\]\(https://huggingface\.co/[^)]*\)", r"\1", node["widgets_values"][0])
                    node["widgets_values"][0] = re.sub(r"https://huggingface\.co/(\S+)", r"\1", text)
                for out in node.get("outputs") or []:
                    out["links"] = [lid for lid in out.get("links") or [] if lid in defined] or None
                for inp in node.get("inputs") or []:
                    if inp.get("link") not in defined:
                        inp["link"] = None
        self.sub = self.subgraphs[self.node["type"]]
        self.x, self.y = self.node["pos"]
        self.width = self.node.get("size", [400, 300])[0]
        self.wf.setdefault("links", [])

    def add(self, kind, pos, values=(), inputs=(), outputs=(), size=(320, 120)):
        nid = max([self.wf.get("last_node_id", 0)] + [n["id"] for n in self.wf["nodes"]]) + 1
        self.wf["last_node_id"] = nid
        self.wf["nodes"].append({
            "id": nid, "type": kind, "pos": [self.x + pos[0], self.y + pos[1]], "size": list(size),
            "flags": {}, "order": nid, "mode": 0,
            "inputs": [{"name": name, "type": kind_, "link": None} for name, kind_ in inputs],
            "outputs": [{"name": name, "type": kind_, "links": None} for name, kind_ in outputs],
            "properties": {"Node name for S&R": kind}, "widgets_values": list(values)})
        return nid

    def connect(self, src, slot, dst, name):
        nodes = {n["id"]: n for n in self.wf["nodes"]}
        target = next(i for i, inp in enumerate(nodes[dst]["inputs"]) if inp["name"] == name)
        kind = nodes[src]["outputs"][slot]["type"]
        lid = max([self.wf.get("last_link_id", 0)] + [link[0] for link in self.wf["links"]]) + 1
        self.wf["last_link_id"] = lid
        self.wf["links"].append([lid, src, slot, dst, target, kind])
        nodes[dst]["inputs"][target]["link"] = lid
        nodes[src]["outputs"][slot]["links"] = (nodes[src]["outputs"][slot]["links"] or []) + [lid]

    def set(self, name, value, nth=0):
        """Set a promoted widget, by name and occurrence, wherever its value is
        read: the blueprint node when it stores values, and the inner widget the
        subgraph feeds, which the frontend shows and the smoke harness falls back to."""
        proxies = self.node["properties"]["proxyWidgets"]
        index = [i for i, proxy in enumerate(proxies) if proxy[1] == name][nth]
        if self.node.get("widgets_values"):
            self.node["widgets_values"][index] = value
        node_id, widget = proxies[index]
        if str(node_id) == "-1":
            for node, field in self._fed_by(self.sub, widget):
                self._set_widget(node, field, value)
        else:
            self._set_widget(next(n for n in self.sub["nodes"] if str(n["id"]) == str(node_id)), widget, value)

    def _fed_by(self, sub, input_name):
        slot = next(i for i, inp in enumerate(sub["inputs"]) if inp["name"] == input_name)
        nodes = {n["id"]: n for n in sub["nodes"]}
        return [(nodes[link["target_id"]], nodes[link["target_id"]]["inputs"][link["target_slot"]]["name"])
                for link in sub["links"] if link["origin_id"] == -10 and link["origin_slot"] == slot]

    def _set_widget(self, node, field, value):
        nested = self.subgraphs.get(node["type"])
        if nested:
            for inner, inner_field in self._fed_by(nested, field):
                self._set_widget(inner, inner_field, value)
            return
        index = 0
        for inp in node["inputs"]:
            name = (inp.get("widget") or {}).get("name")
            if name == field:
                node["widgets_values"][index] = value
                return
            if name:
                index += 2 if name in SEED_WIDGETS else 1
        raise KeyError(f"{node['type']} has no widget {field}")

    def note(self, text):
        self.add("MarkdownNote", (0, -380), [text], size=(max(self.width, 720), 330))

    def save(self, kind, values, output_type):
        node = self.add(kind, (self.width + 80, 0), values, inputs=[(INPUT_OF[kind], output_type)], size=(400, 400))
        self.connect(self.node["id"], 0, node, INPUT_OF[kind])


INPUT_OF = {"SaveImage": "images", "SaveVideo": "video", "SaveAudioMP3": "audio", "SaveGLB": "mesh"}
IMAGE_OUT = [("IMAGE", "IMAGE"), ("MASK", "MASK")]
VIDEO_PARTS = [("images", "IMAGE"), ("audio", "AUDIO"), ("fps", "FLOAT"), ("bit_depth", "COMBO"), ("color_space", "COMBO")]


def load_image(t, name, pos):
    return t.add("LoadImage", pos, [name, "image"], outputs=IMAGE_OUT, size=(320, 330))


def video_frames(t, name, pos, frames=121, size=(1280, 720)):
    """A sample video's first frames, scaled and centre-cropped: what the LTX 2.0 control blueprints take."""
    video = t.add("LoadVideo", pos, [name, "image"], outputs=[("VIDEO", "VIDEO")], size=(320, 330))
    parts = t.add("GetVideoComponents", (pos[0] + 360, pos[1]), inputs=[("video", "VIDEO")], outputs=VIDEO_PARTS)
    batch = t.add("ImageFromBatch", (pos[0] + 360, pos[1] + 160), [0, frames],
                  inputs=[("image", "IMAGE")], outputs=[("IMAGE", "IMAGE")])
    scale = t.add("ImageScale", (pos[0] + 720, pos[1]), ["lanczos", size[0], size[1], "center"],
                  inputs=[("image", "IMAGE")], outputs=[("IMAGE", "IMAGE")])
    t.connect(video, 0, parts, "video")
    t.connect(parts, 0, batch, "image")
    t.connect(batch, 0, scale, "image")
    return scale


def scaled_image(t, name, pos, size=(1280, 720)):
    image = load_image(t, name, pos)
    scale = t.add("ImageScale", (pos[0] + 360, pos[1]), ["lanczos", size[0], size[1], "center"],
                  inputs=[("image", "IMAGE")], outputs=[("IMAGE", "IMAGE")])
    t.connect(image, 0, scale, "image")
    return scale


def about(name, what, profile, credit=""):
    return "\n".join([
        "## About this workflow", "", what, "",
        f"The middle node is ComfyUI's **{name}** blueprint, the one in the node search box; double-click it "
        "to see inside." + (" " + credit if credit else ""), "",
        f"Asset profile: `{profile}`."])


def qwen_image(t):
    t.set("text", QWEN_IMAGE_PROMPT)
    t.save("SaveImage", ["qwen_image"], "IMAGE")
    t.note(about("Text to Image (Qwen-Image)",
                 "A picture from a prompt, with Qwen-Image and its 8-step Lightning LoRA. Qwen-Image writes legible "
                 "text into pictures, in English and Chinese, as the sample's shop signs show.",
                 "qwen-image-t2i-lightning-8step", "The sample prompt is from Comfy's `image_qwen_image` template (MIT)."))


def ideogram(t):
    t.save("SaveImage", ["ideogram4"], "IMAGE")
    t.note(about("Text to Image (Ideogram v4)",
                 "A picture from a prompt with Ideogram 4, the strongest model here at text and layout in posters "
                 "and graphics. The sample prompt is structured as JSON; keep its shape and change the descriptions.",
                 "ideogram-4"))


def qwen_edit(t):
    t.connect(load_image(t, "leather_sofa.png", (-400, 0)), 0, t.node["id"], "image1")
    t.connect(load_image(t, "texture_fur.png", (-400, 380)), 0, t.node["id"], "image2")
    t.set("prompt", "Change the leather of the sofa in image 1 to the fur material in image 2.")
    t.save("SaveImage", ["qwen_edit_2511"], "IMAGE")
    t.note(about("Image Edit (Qwen 2511)",
                 "Edits a picture from an instruction, with up to three pictures as input: here the sofa in the first "
                 "takes the fur of the second. Refer to them in the prompt as image 1, image 2 and image 3.",
                 "qwen-image-edit-2511-core",
                 "The sample pictures are from Comfy's `image_qwen_image_edit_2511` template (MIT)."))


def qwen_inpaint(t):
    image = load_image(t, "image_qwen_image_instantx_inpainting_controlnet_input_image.png", (-400, 0))
    t.connect(image, 0, t.node["id"], "image")
    t.connect(image, 1, t.node["id"], "mask")
    t.set("text", "The Queen, on a throne, surrounded by Knights, HD, Realistic, Octane Render, Unreal engine")
    t.save("SaveImage", ["qwen_inpaint"], "IMAGE")
    t.note(about("Image Inpainting (Qwen-image)",
                 "Repaints the masked part of a picture from a prompt. The sample's mask covers the throne. For your "
                 "own picture, right-click it in **Load Image** and open the mask editor to paint the part to change.",
                 "qwen-image-inpaint-lightning-4step",
                 "The sample picture and prompt are from Comfy's `image_qwen_image_instantx_inpainting_controlnet` "
                 "template (MIT)."))


def vace_inpaint(t):
    video = t.add("LoadVideo", (-400, 0), ["video_wan_vace_inpainting_input_video.mp4", "image"],
                  outputs=[("VIDEO", "VIDEO")], size=(320, 330))
    t.connect(video, 0, t.node["id"], "video")
    t.connect(load_image(t, "api_bfl_flux_1_kontext_multiple_images_input_dog.jpg", (-400, 380)), 0,
              t.node["id"], "reference_image_1")
    t.set("text", "A little boy and a dog are hiking in the forest. The puppy runs around the boy cheerfully.")
    t.set("text", "cat", nth=1)
    t.save("SaveVideo", ["video/wan_vace_inpaint", "auto", "auto"], "VIDEO")
    t.note(about("Video Inpainting (Wan2.1 VACE)",
                 "Replaces something in a video. SAM3 finds it from a word, here the cat, and Wan2.1 VACE paints the "
                 "reference picture's subject, here a dog, into its place, following the prompt.",
                 "wan2.1-vace-bundled",
                 "The sample video, picture, prompt and word are from Comfy's `video_wan_vace_inpainting` "
                 "template (MIT)."))


def ltx2_canny(t):
    frames = video_frames(t, "squirrel_in_flower_garden.mp4", (-1900, 0))
    half = t.add("ImageScaleBy", (-760, 0), ["lanczos", 0.5], inputs=[("image", "IMAGE")], outputs=[("IMAGE", "IMAGE")])
    edges = t.add("Canny", (-400, 0), [0.4, 0.8], inputs=[("image", "IMAGE")], outputs=[("IMAGE", "IMAGE")])
    t.connect(frames, 0, half, "image")
    t.connect(half, 0, edges, "image")
    t.connect(edges, 0, t.node["id"], "image")
    t.connect(scaled_image(t, "ltx_2_canny_1st_frame.png", (-760, 380)), 0, t.node["id"], "image_1")
    t.set("text", CANNY_PROMPT)
    t.save("SaveVideo", ["video/ltx2_canny", "auto", "auto"], "VIDEO")
    t.note(about("Canny to Video (LTX 2.0)",
                 "A new video that follows the edges of an existing one and starts from a picture you choose: the "
                 "first 121 frames of the source video, at 1280 x 720, and the edges found in them.",
                 "ltx-2.0-iclora-all-distilled",
                 "The sample video, first frame and prompt are from Comfy's `video_ltx2_canny_to_video` template (MIT)."))


def ltx2_depth(t):
    video = t.add("LoadVideo", (-400, 0), ["minimalist_sky_view_lounge.mp4", "image"],
                  outputs=[("VIDEO", "VIDEO")], size=(320, 330))
    t.connect(video, 0, t.node["id"], "video")
    t.connect(scaled_image(t, "minimalist_sky_view_lounge.png", (-760, 380)), 0, t.node["id"], "image_2")
    t.set("text", DEPTH_PROMPT)
    t.save("SaveVideo", ["video/ltx2_depth", "auto", "auto"], "VIDEO")
    t.note(about("Depth to Video (ltx 2.0)",
                 "A new video that follows the depth of an existing one and starts from a picture you choose. The "
                 "blueprint estimates the depth itself, with Lotus.",
                 "ltx-2.0-iclora-all-distilled-ref0.5",
                 "The sample video, first frame and prompt are from Comfy's `video_ltx2_depth_to_video` template (MIT)."))


def ltx2_pose(t):
    frames = video_frames(t, "dancer_field_pose.mp4", (-2300, 0))
    pose_model = t.add("CheckpointLoaderSimple", (-1180, 380), ["sdpose_wholebody_fp16.safetensors"],
                       outputs=[("MODEL", "MODEL"), ("CLIP", "CLIP"), ("VAE", "VAE")])
    keypoints = t.add("SDPoseKeypointExtractor", (-800, 0), [16],
                      inputs=[("model", "MODEL"), ("vae", "VAE"), ("image", "IMAGE")],
                      outputs=[("keypoints", "POSE_KEYPOINT")])
    drawn = t.add("SDPoseDrawKeypoints", (-400, 0), [True, True, True, True, 4, 2, 0.5, True],
                  inputs=[("keypoints", "POSE_KEYPOINT")], outputs=[("IMAGE", "IMAGE")], size=(320, 260))
    first = t.add("ImageFromBatch", (-400, 380), [0, 1], inputs=[("image", "IMAGE")], outputs=[("IMAGE", "IMAGE")])
    t.connect(pose_model, 0, keypoints, "model")
    t.connect(pose_model, 2, keypoints, "vae")
    t.connect(frames, 0, keypoints, "image")
    t.connect(keypoints, 0, drawn, "keypoints")
    t.connect(drawn, 0, t.node["id"], "image")
    t.connect(frames, 0, first, "image")
    t.connect(first, 0, t.node["id"], "image_1")
    t.set("text", "A woman in a flowing rust-red dress dances barefoot in a meadow of white wildflowers at golden "
                  "hour, the low sun behind her and trees in the background. Her movements are slow and graceful, "
                  "her dress and hair swaying with each turn.")
    t.save("SaveVideo", ["video/ltx2_pose", "auto", "auto"], "VIDEO")
    t.note(about("Pose to Video (LTX 2.0)",
                 "A new video in which someone moves like the person in a source video. SDPose draws the source "
                 "dancer's pose frame by frame, and LTX 2.0 renders the video from those poses and a first frame. "
                 "The sample uses the source's own first frame; put a picture of your character in a similar pose "
                 "into **Image From Batch**'s place to make them dance instead.",
                 "ltx-2.0-iclora-all-bundled",
                 "The sample video is from Comfy's `video_minimax_h3_fun_controlnet_union` template (MIT)."))


def ltx23_t2v(t):
    t.set("value", T2V_PROMPT)
    t.save("SaveVideo", ["video/ltx23_t2v", "auto", "auto"], "VIDEO")
    t.note(about("Text to Video (LTX-2.3)", "A five-second video with sound from a prompt, with LTX-2.3.",
                 "ltx-2.3-t2v-i2v-two-stage-distilled",
                 "The sample prompt is from Comfy's `video_ltx2_3_t2v` template (MIT)."))


def ltx23_i2v(t):
    t.connect(load_image(t, "egyptian_queen.png", (-400, 0)), 0, t.node["id"], "input")
    t.set("value", I2V_PROMPT)
    t.save("SaveVideo", ["video/ltx23_i2v", "auto", "auto"], "VIDEO")
    t.note(about("Image to Video (LTX-2.3)",
                 "A five-second video with sound that starts from a picture, with LTX-2.3. Spoken lines go in the "
                 "prompt in quotes.",
                 "ltx-2.3-t2v-i2v-single-stage-distilled-full",
                 "The sample picture and prompt are from Comfy's `video_ltx2_3_i2v` template (MIT)."))


def ace_step(t):
    t.set("tags", ACE_TAGS)
    t.set("lyrics", ACE_LYRICS)
    t.save("SaveAudioMP3", ["audio/ace_step", "V0"], "AUDIO")
    t.note(about("Text to Audio (ACE-Step 1.5)",
                 "A song from style tags and lyrics, with ACE-Step 1.5. Sections in square brackets, such as "
                 "[Verse] and [Chorus], shape the song.",
                 "ace-step-1.5-core",
                 "The sample tags and lyrics are from Comfy's `audio_ace_step_1_5_split_llm` template (MIT)."))


def lotus(t):
    t.connect(load_image(t, "image_lotus_depth_v1_1_input_image.png", (-400, 0)), 0, t.node["id"], "pixels")
    t.save("SaveImage", ["lotus_depth"], "IMAGE")
    t.note(about("Image Depth Estimation (Lotus Depth)",
                 "A depth map from a picture, with Lotus: near is light, far is dark. Use it as control input for "
                 "depth-guided image and video models.",
                 "lotus-depth-support",
                 "The sample picture is from Comfy's `image_lotus_depth_v1_1` template (MIT)."))


def hunyuan3d(t):
    image = load_image(t, "figurine_knight.jpg", (-1140, 0))
    model = t.add("LoadBackgroundRemovalModel", (-1140, 380), ["birefnet.safetensors"],
                  outputs=[("bg_model", "BACKGROUND_REMOVAL")], size=(320, 90))
    mask = t.add("RemoveBackground", (-780, 380), inputs=[("bg_removal_model", "BACKGROUND_REMOVAL"), ("image", "IMAGE")],
                 outputs=[("mask", "MASK")], size=(300, 80))
    crop = t.add("ImageCropToMask", (-420, 0), [1024, 1024, 1.1, 0, "#FFFFFF"],
                 inputs=[("images", "IMAGE"), ("masks", "MASK")], outputs=[("images", "IMAGE")], size=(340, 230))
    t.connect(image, 0, mask, "image")
    t.connect(model, 0, mask, "bg_removal_model")
    t.connect(image, 0, crop, "images")
    t.connect(mask, 0, crop, "masks")
    t.connect(crop, 0, t.node["id"], "image")
    t.save("SaveGLB", ["3d/hunyuan3d"], "MESH")
    t.note(about("Image to Model (Hunyuan3d 2.1)",
                 "One picture to an untextured 3D shape, saved as `.glb` in `output/3d/`, in about a minute; for "
                 "colour and textures use the Pixal3D templates. BiRefNet cuts the subject out and centres it on "
                 "white first. Without that step Hunyuan3D also models the picture's backdrop, as a wall behind the "
                 "object.",
                 "hunyuan3d-2.1-core",
                 "The sample picture, a knight figurine, ships with the template pack."))


QWEN_IMAGE_PROMPT = (
    "A vibrant, warm neon-lit street scene in Hong Kong at the afternoon, with a mix of colorful Chinese and English "
    "signs glowing brightly. The atmosphere is lively, cinematic, and rain-washed with reflections on the pavement. "
    "The colors are vivid, full of pink, blue, red, and green hues. Crowded buildings with overlapping neon signs. "
    "1980s Hong Kong style. Signs include:\n\"龍鳳冰室\" \"金華燒臘\" \"HAPPY HAIR\" \"鴻運茶餐廳\" \"EASY BAR\" "
    "\"永發魚蛋粉\" \"添記粥麵\" \"SUNSHINE MOTEL\" \"美都餐室\" \"富記糖水\" \"太平館\" \"雅芳髮型屋\" \"STAR KTV\" "
    "\"銀河娛樂城\" \"百樂門舞廳\" \"BUBBLE CAFE\" \"萬豪麻雀館\" \"CITY LIGHTS BAR\" \"瑞祥香燭莊\" \"文記文具\" "
    "\"GOLDEN JADE HOTEL\" \"LOVELY BEAUTY\" \"合興百貨\" \"興旺電器\" And the background is warm yellow street and "
    "with all stores' lights on.")
CANNY_PROMPT = (
    "A wide shot reveals a whimsical outdoor scene with a decorative tree-like structure crafted from brown branches. "
    "Golden spherical ornaments and delicate white artificial flowers adorn the branches, catching bright daylight. "
    "A small grey and white squirrel stands on a round, light-colored wooden platform at the base, its bushy tail "
    "slightly raised. The squirrel dips its head down, nibbling at something on the platform. The camera slowly "
    "pushes in, focusing on the squirrel's gentle movements. Soft forest sounds fill the air—rustling leaves, "
    "distant birdsong, and a gentle breeze. The golden ornaments shimmer in the sunlight as the squirrel continues "
    "to eat.")
DEPTH_PROMPT = (
    "An expansive, sunlit modern lounge perched high above the city, where floor-to-ceiling glass walls flood the "
    "open-concept space with natural light and frame sweeping panoramic views of the sprawling urban skyline below. "
    "Warm, rich wood flooring and matching wood-paneled ceilings anchor the design, accented by soft orange LED cove "
    "lighting that traces the ceiling edges, while exposed-bulb chandeliers and large black pendant lamps cast a cozy "
    "glow over the communal high-top wooden table, central workstations, and plant-adorned built-in shelves, "
    "blending sleek contemporary style with a relaxed, inviting atmosphere perfect for both work and leisure.")
T2V_PROMPT = (
    "Dynamic cinematic close-up of high-tech modular machinery self-assembling in midair, precision robotic parts, "
    "magnetic connectors, and glowing circuits clicking together, subtle smoke and light flares, extremely detailed "
    "titanium textures. The final product displays a clean, clear surface with large glowing engraved text "
    "“LTX-2.3” centered and unobstructed, dramatic lighting, photorealism, 8K, sharp focus.")
I2V_PROMPT = (
    "Egyptian royal in blue-and-gold headdress and high collar, white dress with golden embroidery and armbands, "
    "desert, robot soldiers in formation left and right. She walks steadily forward, head held level and gaze fixed "
    "ahead—no dipping or lowering of the head. The camera performs a single, smooth push-in only: starting in a "
    "wider shot of her, the robots, and the desert, it moves steadily forward until she is in a medium or "
    "medium-close frame, then holds. She stops, posture and head still upright, and says: “The old gods are silent. "
    "I am not.” Robot soldiers shift or march in place; sand and fabric move with the wind. No pull-back; the only "
    "camera move is the continuous push-in.")
ACE_TAGS = ("raw vocals, soulful vocals, Delta blues, country blues, haunting, longing, sorrowful, acoustic guitar, "
            "slide guitar, harmonica, upright bass, handclaps")
ACE_LYRICS = """[Intro]
Instrumental slide guitar and harmonica call

[Verse]
I woke up this mornin', devil was callin' my name
Crossroads wind was whisperin', never leave me the same
My heart's got a hole, baby, burned by a flame
Lookin' for redemption, but it ain't no easy game

[Chorus]
Oh, these blues got me shackled, chains around my soul
Playin' on six strings, tryin' to make me whole
Every note a confession, every tear a toll
Singin' for salvation as I lose control

[Verse]
Midnight train departed, left me standin' in the rain
Got nothin' but my shadow and this growin' pain
Devil keeps a-knockin', tellin' me to swear his name
But I keep strummin' truth to fight his claim

[Bridge]
Slide that bottle 'til the whole world fades
Drownin' in regret through these endless shades

[Guitar Solo]
Instrumental weepin' slide guitar, answered by mournful harmonica, rhythm slow and heavy

[Chorus]
Oh, these blues got me shackled, chains around my soul
Playin' on six strings, tryin' to make me whole
Every note a confession, every tear a toll
Singin' for salvation as I lose control

[Outro]
Sun's risin' slowly, but the pain stays near
Carryin' these blues down a lonesome frontier

[abrupt silence]"""

TEMPLATES = {
    "Text to Image (Qwen-Image)": qwen_image,
    "Text to Image (Ideogram v4)": ideogram,
    "Image Edit (Qwen 2511)": qwen_edit,
    "Image Inpainting (Qwen-image)": qwen_inpaint,
    "Video Inpainting (Wan2.1 VACE)": vace_inpaint,
    "Canny to Video (LTX 2.0)": ltx2_canny,
    "Depth to Video (ltx 2.0)": ltx2_depth,
    "Pose to Video (LTX 2.0)": ltx2_pose,
    "Text to Video (LTX-2.3)": ltx23_t2v,
    "Image to Video (LTX-2.3)": ltx23_i2v,
    "Text to Audio (ACE-Step 1.5)": ace_step,
    "Image Depth Estimation (Lotus Depth)": lotus,
    "Image to Model (Hunyuan3d 2.1)": hunyuan3d,
}


def build(name):
    t = Template(name)
    t.wf["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "ComfyUI-DGX-Spark-Templates/" + name))
    TEMPLATES[name](t)
    return json.dumps(t.wf, indent=2, ensure_ascii=False) + "\n"


def stale():
    """Names whose template on disk differs from what the current blueprints build."""
    out = []
    for name in TEMPLATES:
        path = os.path.join(PACK, name + ".json")
        current = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if current != build(name):
            out.append(name)
    return out


def main():
    if "--check" in sys.argv[1:]:
        names = stale()
        for name in names:
            print(f"{name}.json no longer matches its blueprint; run python3 scripts/build_blueprint_templates.py")
        return 1 if names else 0
    for name in TEMPLATES:
        with open(os.path.join(PACK, name + ".json"), "w", encoding="utf-8") as handle:
            handle.write(build(name))
        print("wrote", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
