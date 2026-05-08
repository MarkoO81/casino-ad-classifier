"""CLIP zero-shot image classifier for gambling ad detection.

Uses open_clip (https://github.com/mlfoundations/open_clip).

Two modes:
  1. zero_shot_score(image)  -- prompts vs image, returns prob in [0, 1]
  2. embed(image)            -- returns image embedding for downstream
                                fine-tuned head (see train_head.py)

The zero-shot mode is good for v1 deployment. As you collect labeled
examples (confirmed casino ads vs hard negatives from your alerts),
collect their embeddings via embed() and train a linear head — this
typically lifts AUC from ~0.85 to ~0.97+ on this task.
"""

from __future__ import annotations
from pathlib import Path
from typing import Union

# Lazy imports inside methods so this module loads even without torch/open_clip
# installed (useful for testing the rule-based pieces in isolation).


class CLIPGamblingClassifier:
    """Wraps an open_clip model for zero-shot + embedding."""

    def __init__(self, model_name: str = "ViT-L-14-quickgelu",
                 pretrained: str = "openai", device: str = None):
        self.model_name = model_name
        self.pretrained = pretrained
        self.device = device
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._text_features = None  # cached prompt embeddings
        self._positive_indices = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        import torch
        import open_clip
        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model, _, self._preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.pretrained, device=self.device
        )
        self._model.eval()
        self._tokenizer = open_clip.get_tokenizer(self.model_name)

    def _ensure_prompts(self):
        if self._text_features is not None:
            return
        import torch
        from .prompts import build_prompt_set
        all_prompts, positive_indices = build_prompt_set()
        self._positive_indices = positive_indices
        tokens = self._tokenizer(all_prompts).to(self.device)
        with torch.no_grad():
            text_feats = self._model.encode_text(tokens)
            text_feats = text_feats / text_feats.norm(dim=-1, keepdim=True)
        self._text_features = text_feats

    def _load_image(self, image: Union[str, Path, "PIL.Image.Image"]):
        from PIL import Image
        if isinstance(image, (str, Path)):
            return Image.open(image).convert("RGB")
        return image.convert("RGB")

    def embed(self, image) -> "torch.Tensor":
        """Return L2-normalized image embedding."""
        self._ensure_loaded()
        import torch
        img = self._load_image(image)
        x = self._preprocess(img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            feat = self._model.encode_image(x)
            feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat.cpu()

    def zero_shot_score(self, image) -> float:
        """Probability that image is a gambling ad creative, in [0, 1].

        Computes softmax over (positives + negatives) prompts and sums
        probability mass on the positive class.
        """
        self._ensure_loaded()
        self._ensure_prompts()
        import torch
        img = self._load_image(image)
        x = self._preprocess(img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            img_feat = self._model.encode_image(x)
            img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)
            # Cosine similarity * temperature, then softmax
            logits = (img_feat @ self._text_features.T) * 100.0
            probs = logits.softmax(dim=-1).squeeze(0).cpu().tolist()
        return float(sum(probs[i] for i in self._positive_indices))

    def zero_shot_score_batch(self, images) -> list:
        """Score a batch of images. Faster than calling one at a time."""
        self._ensure_loaded()
        self._ensure_prompts()
        import torch
        tensors = [self._preprocess(self._load_image(im)) for im in images]
        x = torch.stack(tensors).to(self.device)
        with torch.no_grad():
            img_feat = self._model.encode_image(x)
            img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)
            logits = (img_feat @ self._text_features.T) * 100.0
            probs = logits.softmax(dim=-1).cpu().tolist()
        return [float(sum(p[i] for i in self._positive_indices)) for p in probs]
