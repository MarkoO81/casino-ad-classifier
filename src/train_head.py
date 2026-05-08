"""Fine-tune a linear classification head on CLIP image embeddings.

Use this AFTER you have a labeled dataset of:
  - confirmed casino ads      (positives)
  - hard negatives            (sports betting that is licensed,
                               video games, lotteries, financial ads, etc.)

Typical lift over zero-shot: AUC 0.85 -> 0.97+, and importantly, you
can move the operating point to high precision without sacrificing recall.

Usage:
    python -m src.train_head --data data/labeled --out models/head.pt

Expects data/ structure:
    data/labeled/positive/*.jpg
    data/labeled/negative/*.jpg
"""

from __future__ import annotations
from pathlib import Path
import argparse
import json


def collect_embeddings(data_dir: Path, clip_model):
    """Walk data/{positive,negative}/*.{jpg,png}, return X, y, paths."""
    import torch
    X_list, y_list, paths = [], [], []
    for label, label_int in (("positive", 1), ("negative", 0)):
        for img_path in (data_dir / label).glob("*"):
            if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            try:
                emb = clip_model.embed(str(img_path)).squeeze(0)
                X_list.append(emb)
                y_list.append(label_int)
                paths.append(str(img_path))
            except Exception as e:
                print(f"skip {img_path}: {e}")
    X = torch.stack(X_list)
    y = torch.tensor(y_list, dtype=torch.float32)
    return X, y, paths


def train(X, y, epochs: int = 200, lr: float = 1e-3, weight_decay: float = 1e-4):
    """Train a linear head. Returns (model, metrics)."""
    import torch
    from torch import nn

    n, d = X.shape
    perm = torch.randperm(n)
    split = int(0.8 * n)
    train_idx, val_idx = perm[:split], perm[split:]
    X_tr, y_tr = X[train_idx], y[train_idx]
    X_va, y_va = X[val_idx], y[val_idx]

    head = nn.Linear(d, 1)
    opt = torch.optim.AdamW(head.parameters(), lr=lr, weight_decay=weight_decay)
    loss_fn = nn.BCEWithLogitsLoss()

    for ep in range(epochs):
        head.train()
        logits = head(X_tr).squeeze(-1)
        loss = loss_fn(logits, y_tr)
        opt.zero_grad(); loss.backward(); opt.step()

        if (ep + 1) % 50 == 0:
            head.eval()
            with torch.no_grad():
                val_logits = head(X_va).squeeze(-1)
                val_probs = torch.sigmoid(val_logits)
                val_pred = (val_probs > 0.5).float()
                acc = (val_pred == y_va).float().mean().item()
                print(f"ep {ep+1:3d}  loss {loss.item():.4f}  val_acc {acc:.3f}")

    # Final metrics
    head.eval()
    with torch.no_grad():
        val_logits = head(X_va).squeeze(-1)
        val_probs = torch.sigmoid(val_logits).cpu().numpy()
    metrics = {"val_size": int(len(y_va)),
               "val_pos_rate": float(y_va.mean()),
               "val_mean_prob": float(val_probs.mean())}
    try:
        from sklearn.metrics import roc_auc_score, average_precision_score
        metrics["val_auc"] = float(roc_auc_score(y_va.numpy(), val_probs))
        metrics["val_ap"] = float(average_precision_score(y_va.numpy(), val_probs))
    except ImportError:
        pass
    return head, metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path,
                        help="dir with positive/ and negative/ subfolders")
    parser.add_argument("--out", required=True, type=Path,
                        help="output path for head.pt")
    parser.add_argument("--model", default="ViT-L-14-quickgelu")
    parser.add_argument("--pretrained", default="openai")
    args = parser.parse_args()

    from .clip_classifier import CLIPGamblingClassifier
    import torch

    clip_model = CLIPGamblingClassifier(args.model, args.pretrained)
    print("collecting embeddings...")
    X, y, paths = collect_embeddings(args.data, clip_model)
    print(f"  {len(y)} samples ({int(y.sum())} positive)")

    print("training...")
    head, metrics = train(X, y)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": head.state_dict(),
                "in_features": X.shape[1],
                "model_name": args.model,
                "pretrained": args.pretrained,
                "metrics": metrics}, args.out)
    print(f"saved -> {args.out}")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
