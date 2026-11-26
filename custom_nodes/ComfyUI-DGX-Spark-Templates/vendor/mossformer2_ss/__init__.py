"""MossFormer2 speech separation model, vendored from ClearerVoice-Studio.

Source: https://github.com/modelscope/ClearerVoice-Studio, commit 6b3774d, clearvoice/models/mossformer2_ss/
(conv_module.py, fsmn.py, layer_norm.py, mossformer2_block.py, mossformer2.py). Copyright Alibaba / Speech Lab of
DAMO Academy, Apache License 2.0 (copy in licenses/ClearerVoice-LICENSE.txt). Changed: the unused torchinfo import
in mossformer2_block.py is removed. The model code is otherwise as published.
"""
