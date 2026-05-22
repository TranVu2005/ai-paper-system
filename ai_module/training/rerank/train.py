from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import torch
from torch import nn
from torch.utils.data import DataLoader

from ai_module.rerank.dataset import make_dataset
from ai_module.rerank.features import FEATURE_NAMES
from ai_module.rerank.model import MlpReranker, save_checkpoint


def _load_config(path: str) -> Dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _resolve_path(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def _eval_loss(model: nn.Module, loader: DataLoader, device: torch.device, criterion: nn.Module) -> float:
    model.eval()
    total = 0.0
    steps = 0
    with torch.no_grad():
        for batch in loader:
            x = batch["x"].to(device)
            y = batch["y"].to(device)
            logits = model(x)
            loss = criterion(logits, y)
            total += float(loss.item())
            steps += 1
    return total / max(1, steps)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train rerank model")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg_base = cfg_path.parent
    cfg = _load_config(str(cfg_path))

    train_path = _resolve_path(cfg_base, str(cfg["train_path"]))
    val_path = _resolve_path(cfg_base, str(cfg["val_path"]))
    model_dir = _resolve_path(cfg_base, str(cfg["model_dir"]))
    output_dir = _resolve_path(cfg_base, str(cfg.get("output_dir", str(model_dir))))

    train_ds, stats = make_dataset(str(train_path))
    val_ds, _ = make_dataset(str(val_path), feature_min=stats["min"], feature_max=stats["max"])

    batch_size = int(cfg.get("batch_size", 32))
    epochs = int(cfg.get("epochs", 8))
    lr = float(cfg.get("lr", 1e-3))
    weight_decay = float(cfg.get("weight_decay", 1e-4))
    hidden_dim = int(cfg.get("hidden_dim", 64))
    dropout = float(cfg.get("dropout", 0.1))
    seed = int(cfg.get("seed", 42))

    torch.manual_seed(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = MlpReranker(input_dim=train_ds.x.size(1), hidden_dim=hidden_dim, dropout=dropout).to(device)
    optim = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    criterion = nn.BCEWithLogitsLoss()

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

    best_val = float("inf")
    best_state = None
    history: List[Dict[str, float]] = []

    for ep in range(1, epochs + 1):
        model.train()
        total = 0.0
        steps = 0
        for batch in train_loader:
            x = batch["x"].to(device)
            y = batch["y"].to(device)
            optim.zero_grad(set_to_none=True)
            logits = model(x)
            loss = criterion(logits, y)
            loss.backward()
            optim.step()
            total += float(loss.item())
            steps += 1

        train_loss = total / max(1, steps)
        val_loss = _eval_loss(model, val_loader, device, criterion)
        history.append({"epoch": float(ep), "train_loss": train_loss, "val_loss": val_loss})
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

        print(json.dumps({"epoch": ep, "train_loss": train_loss, "val_loss": val_loss}))

    if best_state is None:
        raise RuntimeError("No model state was saved")

    model_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = model_dir / "rerank_mlp.pt"
    save_checkpoint(
        str(ckpt_path),
        {
            "state_dict": best_state,
            "input_dim": int(train_ds.x.size(1)),
            "hidden_dim": hidden_dim,
            "dropout": dropout,
            "feature_names": FEATURE_NAMES,
            "feature_min": stats["min"],
            "feature_max": stats["max"],
            "best_val_loss": best_val,
        },
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "train_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

    print(json.dumps({"status": "ok", "checkpoint": str(ckpt_path), "best_val_loss": best_val}))


if __name__ == "__main__":
    main()
