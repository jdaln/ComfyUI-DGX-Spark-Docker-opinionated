"""FAB Body Pose: FAB-L body estimation for core's SAM 3D Body nodes.

FAB (noahcao/FAB, Apache-2.0 code, weights for non-commercial research) is a
distilled SAM 3D Body with its own person detector. This node runs it on each
frame and returns core's MHR_POSE_DATA, so Smooth, Face Expression, Render and
Build Pose File work as with SAM 3D Body Predict, without SAM 3 tracking or a
separate detector. Core's loaded SAM 3D Body model gives the mesh faces, the
shader colours and the hand mask; it is not run.

FAB is not pip-installed: its package would add top-level `bench`, `train` and
`scripts` modules and pull a second OpenCV. The source is fetched once with git
at a pinned commit into models/fab/code/ and put at the end of sys.path when the
node first runs. pytorch_lightning is only FAB's base class and training
callbacks; a stand-in replaces it when the real one is missing. FAB loads its
checkpoints unrestricted; here every plain-data checkpoint is loaded with
weights_only=True. The detector keeps FAB's unpickler, which turns its
ultralytics classes into attribute records; the MHR model is TorchScript. The
G1 robot head is switched off.

Files, all pinned and checked (the asset profile fetches them in advance):
models/fab/weights/ (noahcao/FAB), models/fab/sam-3d-body-dinov3/ (config from
the gated facebook/sam-3d-body-dinov3, MHR model from Meta's MHR release),
models/body_models/mhr/ (MHR's SMPL conversion assets),
models/body_models/smpl/J_regressor_h36m.npy (SPIN's data release). SMPL 1.1.0
itself needs a registration and is placed by hand.
"""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import types
import urllib.request
import zipfile

import numpy as np
import torch

import comfy.model_management
import comfy.utils
import folder_paths

FAB_REPO = "https://github.com/noahcao/FAB.git"
FAB_COMMIT = "ec65caf40632b4d36eb48f58ab45ea56d3cba21a"
FAB_HF = "noahcao/FAB"
FAB_HF_REVISION = "f2e204f280aa27944b98fbf783f618a4bea2c223"
SAM3D_REPO = "facebook/sam-3d-body-dinov3"
SAM3D_REVISION = "11aaa346c7204874a1cbafe3d39a979080b2c55a"
MHR_ASSETS = "https://github.com/facebookresearch/MHR/releases/download/v1.0.1/assets.zip"
MHR_MODEL_SHA256 = "352e271a6c42729c68554ceaea0c955e866970160c31e35506d782dc0f7377bc"
MHR_RAW = "https://raw.githubusercontent.com/facebookresearch/MHR/85fc2cbaf77f606e41346cb791d8ad96a75e1a4b/tools/mhr_smpl_conversion/assets/"
MHR_FILES = {
    "head_hand_mask.npz": "60a64a305731dcc1c09f167dfa844246443b3115ae05abebe3813aa64a9ba2ae",
    "mhr2smpl_mapping.npz": "4bfe2fd6abb598f38ba3a3aa5506ac45b4efb3cdaed7fe4757cfbbf20bbe6ac7",
    "mhr2smplx_mapping.npz": "d6d023f9f483be73cd4491d0ddf217450627263405c9bc6bf43795dfff809af1",
    "mhr_face_mask.ply": "db8ed4fca24a744b1e1f4ed9562f55aff837c9f270debbb34a9d1537a3ac62e1",
    "smpl2mhr_mapping.npz": "84f908ad534f470dc5db072a4aeb9ccb4f9aa4484d2bce2558cc1a346df95c98",
    "smplx2mhr_mapping.npz": "94742d378ccbd6058ce8aa0221d16726d1e5def45490685c223b2e87c6070f97",
    "subsampled_vertex_indices.npy": "76008acafd4468dda802e3ee5523b29fef4de8408cc5e1c65351d4145efc01aa",
}
SPIN_DATA = "https://visiondata.cis.upenn.edu/spin/data.tar.gz"
J_REGRESSOR_SHA256 = "c655cd7013d7829eb9acbebf0e43f952a3fa0305a53c35880e39192bfb6444a0"
MODELS = {"FAB-L": "fab-l", "FAB-S": "fab-s"}
SHARED = ("detector.pt", "smpl_fitter.pt", "smpl_fitter_mesh.pt", "contact_video.pt")

FAB_DIR = os.path.join(folder_paths.models_dir, "fab")
CODE_DIR = os.path.join(FAB_DIR, "code", f"FAB-{FAB_COMMIT[:7]}")
WEIGHTS_DIR = os.path.join(FAB_DIR, "weights")
SAM3D_DIR = os.path.join(FAB_DIR, "sam-3d-body-dinov3")
BODY_DIR = os.path.join(folder_paths.models_dir, "body_models")
SMPL_DIR = os.path.join(BODY_DIR, "smpl")

_LOADED = {}
NUMPY_SAFE = [np._core.multiarray._reconstruct, np.ndarray, np.dtype] + [
    type(np.dtype(t)) for t in ("float16", "float32", "float64", "int32", "int64", "uint8", "bool")]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            h.update(block)
    return h.hexdigest()


def _download(url, path, sha256=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print(f"[fab] {path} is missing, downloading {url}")
    tmp = path + ".part"
    urllib.request.urlretrieve(url, tmp)
    if sha256 and _sha256(tmp) != sha256:
        os.remove(tmp)
        raise RuntimeError(f"{url}: sha256 mismatch")
    os.replace(tmp, path)


def _hf(repo, revision, name, folder):
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        from huggingface_hub import hf_hub_download
        print(f"[fab] {path} is missing, downloading it from {repo}")
        hf_hub_download(repo, name, revision=revision, local_dir=folder)
    return path


def _fetch_code():
    if os.path.exists(os.path.join(CODE_DIR, "fab", "predictor.py")):
        return
    print(f"[fab] fetching FAB's source at {FAB_COMMIT[:7]} into {CODE_DIR}")
    os.makedirs(CODE_DIR, exist_ok=True)
    git = ["git", "-C", CODE_DIR]
    subprocess.run(["git", "init", "-q", CODE_DIR], check=True)
    subprocess.run(git + ["fetch", "-q", "--depth", "1", FAB_REPO, FAB_COMMIT], check=True)
    subprocess.run(git + ["checkout", "-q", "FETCH_HEAD"], check=True)
    head = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    if head != FAB_COMMIT:
        raise RuntimeError(f"FAB source at {head}, expected {FAB_COMMIT}")


def _fetch_files(model):
    weights = MODELS[model]
    sums = _hf(FAB_HF, FAB_HF_REVISION, "SHA256SUMS", WEIGHTS_DIR)
    expected = {}
    for line in open(sums):
        digest, name = line.split()
        expected[name.lstrip("./")] = digest
    for name in [f"{weights}/model.pt", f"{weights}/config.json"] + [f"shared/{s}" for s in SHARED]:
        if os.path.exists(os.path.join(WEIGHTS_DIR, name)):
            continue
        path = _hf(FAB_HF, FAB_HF_REVISION, name, WEIGHTS_DIR)
        if name in expected and _sha256(path) != expected[name]:
            os.remove(path)
            raise RuntimeError(f"{path}: sha256 differs from {FAB_HF}'s SHA256SUMS")
    try:
        _hf(SAM3D_REPO, SAM3D_REVISION, "model_config.yaml", SAM3D_DIR)
    except Exception as e:
        raise RuntimeError(f"model_config.yaml could not be downloaded from {SAM3D_REPO} ({type(e).__name__}). The "
                           f"repository is gated: accept its licence on https://huggingface.co/{SAM3D_REPO} with the "
                           "account whose token is HF_TOKEN in .env.") from None
    mhr = os.path.join(SAM3D_DIR, "assets", "mhr_model.pt")
    if not os.path.exists(mhr):
        os.makedirs(os.path.dirname(mhr), exist_ok=True)
        print(f"[fab] {mhr} is missing, downloading MHR's assets.zip")
        data = urllib.request.urlopen(MHR_ASSETS).read()
        with zipfile.ZipFile(io.BytesIO(data)) as z, open(mhr + ".part", "wb") as out:
            out.write(z.read("assets/mhr_model.pt"))
        if _sha256(mhr + ".part") != MHR_MODEL_SHA256:
            os.remove(mhr + ".part")
            raise RuntimeError("mhr_model.pt: sha256 mismatch")
        os.replace(mhr + ".part", mhr)
    for name, digest in MHR_FILES.items():
        path = os.path.join(BODY_DIR, "mhr", name)
        if not os.path.exists(path):
            _download(MHR_RAW + name, path, digest)
    smpl = os.path.join(SMPL_DIR, "SMPL_NEUTRAL.pkl")
    if not os.path.exists(smpl):
        raise RuntimeError(f"{smpl} is missing. SMPL needs a registration: download SMPL 1.1.0 for Python from "
                           "https://smpl.is.tue.mpg.de and copy basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl there as "
                           "SMPL_NEUTRAL.pkl (male and female beside it as SMPL_MALE.pkl and SMPL_FEMALE.pkl).")
    regressor = os.path.join(SMPL_DIR, "J_regressor_h36m.npy")
    if not os.path.exists(regressor):
        print(f"[fab] {regressor} is missing, downloading SPIN's data.tar.gz")
        data = urllib.request.urlopen(SPIN_DATA).read()
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            member = tar.extractfile("data/J_regressor_h36m.npy").read()
        if hashlib.sha256(member).hexdigest() != J_REGRESSOR_SHA256:
            raise RuntimeError("J_regressor_h36m.npy: sha256 mismatch")
        with open(regressor, "wb") as out:
            out.write(member)


def _lightning_standin():
    try:
        import pytorch_lightning  # noqa: F401
        return
    except ImportError:
        pass

    class LightningModule(torch.nn.Module):
        def save_hyperparameters(self, *args, **kwargs):
            pass

        @property
        def device(self):
            p = next(self.parameters(), None)
            return p.device if p is not None else torch.device("cpu")

    pl = types.ModuleType("pytorch_lightning")
    pl.LightningModule = LightningModule
    pl.callbacks = types.SimpleNamespace(ModelCheckpoint=type("ModelCheckpoint", (), {}))
    loggers = types.ModuleType("pytorch_lightning.loggers")
    loggers.TensorBoardLogger = type("TensorBoardLogger", (), {})
    loggers.WandbLogger = type("WandbLogger", (), {})
    utilities = types.ModuleType("pytorch_lightning.utilities")
    utilities.rank_zero_only = lambda fn: fn
    pl.loggers, pl.utilities = loggers, utilities
    sys.modules.update({"pytorch_lightning": pl, "pytorch_lightning.loggers": loggers,
                        "pytorch_lightning.utilities": utilities})


def _safe_load(original):
    def load(f, *args, **kwargs):
        if kwargs.get("pickle_module") is not None or str(f).endswith("mhr_model.pt"):
            return original(f, *args, **kwargs)  # the detector's record unpickler; TorchScript
        kwargs["weights_only"] = True
        with torch.serialization.safe_globals(NUMPY_SAFE):
            return original(f, *args, **kwargs)
    return load


def _predictor(model):
    if _LOADED.get("model") == model:
        return _LOADED["predictor"]
    _unload()
    _fetch_code()
    _fetch_files(model)
    os.environ.update({"FAB_HOME": FAB_DIR, "FAB_WEIGHTS": WEIGHTS_DIR, "FAB_SAM3D": SAM3D_DIR,
                       "FAB_SMPL_DIR": SMPL_DIR})
    if CODE_DIR not in sys.path:
        sys.path.append(CODE_DIR)
    _lightning_standin()
    import fab.paths
    import fab.retarget.g1

    class NoG1:
        def __init__(self, *args, **kwargs):
            pass

        def to(self, *args, **kwargs):
            return self

    fab.retarget.g1.G1Kinematics = NoG1
    require = fab.paths.require
    fab.paths.require = lambda path, what, hint="": path if "G1" in what else require(path, what, hint)
    from fab.predictor import FABPredictor

    original = torch.load
    torch.load = _safe_load(original)
    try:
        predictor = FABPredictor(MODELS[model], fast=False,
                                 device=str(comfy.model_management.get_torch_device()))
    finally:
        torch.load = original
    predictor.kin = None
    _LOADED.update(model=model, predictor=predictor)
    return predictor


def _unload():
    if _LOADED.pop("predictor", None) is not None:
        _LOADED.pop("model", None)
        comfy.model_management.soft_empty_cache()


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _keep_order(people, previous):
    """People in the previous frame's order (box overlap), new ones after them."""
    if not previous:
        return sorted(people, key=lambda p: -(p["bbox"][2] - p["bbox"][0]) * (p["bbox"][3] - p["bbox"][1]))
    left, ordered = list(people), []
    for prev in previous:
        if not left:
            break
        best = max(left, key=lambda p: _iou(p["bbox"], prev["bbox"]))
        if _iou(best["bbox"], prev["bbox"]) > 0.1:
            ordered.append(best)
            left.remove(best)
    return ordered + left


class FABBodyPose:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "sam3d_body_model": ("SAM3D_BODY_MODEL", {"tooltip": "Core's SAM 3D Body model, for the mesh faces, "
                                                                 "colours and hand mask. It is not run."}),
            "image": ("IMAGE",),
            "model": (list(MODELS), {"default": "FAB-L", "tooltip": "FAB-L is the accurate one; FAB-S is smaller "
                                                                    "and faster."}),
            "fov": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 170.0, "step": 0.1,
                              "tooltip": "Vertical field of view in degrees, e.g. from MoGe. 0: FAB's default, a "
                                         "focal length of the image diagonal."}),
            "max_people": ("INT", {"default": 8, "min": 1, "max": 64}),
            "keep_model_loaded": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("MHR_POSE_DATA",)
    RETURN_NAMES = ("mhr_pose_data",)
    FUNCTION = "predict"
    CATEGORY = "image/detection"
    DESCRIPTION = ("Body pose of every person in each frame with FAB (a distilled SAM 3D Body with its own person "
                   "detector), as core's MHR pose data for Smooth, Face Expression, Render and Build Pose File. "
                   "Weights for non-commercial research.")

    def predict(self, sam3d_body_model, image, model, fov, max_people, keep_model_loaded):
        from comfy.ldm.sam3d_body.mhr.mhr_utils import compact_cont_to_model_params_body, mhr_param_hand_idxs
        from comfy_extras.sam3d_body.utils import compute_canonical_colors, compute_hand_vert_mask

        inner = sam3d_body_model.model
        frames, H, W = image.shape[0], int(image.shape[1]), int(image.shape[2])
        K = None
        if fov > 0:
            f = (H / 2) / np.tan(np.deg2rad(fov) / 2)
            K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]], np.float32)
        comfy.model_management.unload_all_models()
        out_frames, previous = [], []
        progress = comfy.utils.ProgressBar(frames)
        try:
            predictor = _predictor(model)
            captured = {}
            forward = predictor.model.forward

            def spy(*args, **kwargs):
                o = forward(*args, **kwargs)
                captured["o"] = o
                return o

            predictor.model.forward = spy
            try:
                for i in range(frames):
                    comfy.model_management.throw_exception_if_processing_interrupted()
                    rgb = (image[i].clamp(0, 1) * 255).round().to(torch.uint8).cpu().numpy()
                    captured.clear()
                    people = predictor.predict(rgb, K)
                    people = self._to_core(people, captured.get("o"), compact_cont_to_model_params_body,
                                           mhr_param_hand_idxs)
                    people = _keep_order(people, previous)[:max_people]
                    out_frames.append(people)
                    previous = people or previous
                    progress.update(1)
            finally:
                predictor.model.forward = forward
        finally:
            if not keep_model_loaded:
                _unload()
        comfy.model_management.load_models_gpu([sam3d_body_model])
        return ({"frames": out_frames, "faces": inner.head_pose.faces_np(), "image_size": (H, W),
                 "canonical_colors": compute_canonical_colors(inner), "hand_vert_mask": compute_hand_vert_mask(inner)},)

    @staticmethod
    def _to_core(people, o, cont_to_body, hand_idxs):
        """FAB's per-crop network outputs as core's per-person dicts (the fields SAM 3D Body Predict fills)."""
        if not people or o is None:
            return []
        f32 = {k: v.float().cpu().numpy() for k, v in o.items() if torch.is_tensor(v) and k != "faces"}
        raw = o["pred_pose_raw"].float()
        body = cont_to_body(raw[:, 6:])
        body[:, hand_idxs] = 0  # as SAM 3D Body's head: hands come from the hand parameters, no jaw
        body[:, -3:] = 0
        body = body.cpu().numpy()
        out = []
        for i, person in enumerate(people):
            out.append({
                "bbox": np.asarray(person["box"], np.float32),
                "focal_length": f32["focal_length"][i],
                "pred_keypoints_3d": f32["pred_keypoints_3d"][i],
                "pred_keypoints_2d": f32["pred_keypoints_2d"][i],
                "pred_vertices": f32["pred_vertices"][i],
                "pred_cam_t": f32["pred_cam_t"][i],
                "pred_pose_raw": f32["pred_pose_raw"][i],
                "global_rot": f32["global_rot"][i],
                "body_pose_params": body[i],
                "hand_pose_params": f32["hand"][i],
                "scale_params": f32["scale"][i],
                "shape_params": f32["shape"][i],
                "expr_params": f32["face"][i],
                "mask": None,
                "pred_joint_coords": f32["pred_joint_coords"][i],
                "pred_global_rots": f32["joint_global_rots"][i],
                "mhr_model_params": f32["mhr_model_params"][i],
                "pred_face_keypoints_3d": None,
                "pred_face_keypoints_2d": None,
            })
        return out


NODE_CLASS_MAPPINGS = {"FABBodyPose": FABBodyPose}
NODE_DISPLAY_NAME_MAPPINGS = {"FABBodyPose": "FAB Body Pose"}
