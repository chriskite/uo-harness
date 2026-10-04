"""Fine-tune Laya's English checkpoint on the speech-triage set (triage_data.py)
for triage.py's two noul questions. Runs in .venv-laya (torch + laya), on the
GPU next to the client:

  .venv-laya/Scripts/python.exe harness/triage_train.py [--data harness/data/triage/train.jsonl]
      [--out models/laya-triage] [--epochs 3] [--micro-batch 8] [--grad-accum 2]
      [--train-layers 4] [--mem-gb 3.0]

Laya's supported path (notebooks/laya_finetune_typed_decisions_mps.py in the
laya repo): the RLCD loss (proper-scoring-rule reward, GRPO-style policy
gradient on Gaussian logit noise, sigma 0.4 -> 0.1) plus cross-entropy, AdamW
2.5e-5 encoder / 1e-4 head with cosine decay, gradient checkpointing on encoder
and head, one temperature per question type fitted after training on a
calibration split, fp16 weights in the export. Our changes, for 8 GB shared
with the client and a ~1k-row set: only the top --train-layers encoder layers
plus the decision head train (embeddings and lower layers frozen and kept in
fp16, as shipped, so the export leaves them bit-identical; act head untouched:
we don't use it), bf16 autocast, a --mem-gb cap on this process's VRAM, and
only the noul temperature is refitted (the other types' are kept).

Sequences are built by laya's own Agent._encode_state from the exact
questions triage.QUESTIONS sends, so training sees what laya-serve sees.
Writes the checkpoint (rl_agent_config.json, model.safetensors, encoder/,
tokenizer/) and train_log.json (loss, wall time, peak VRAM) to --out.
"""
import argparse
import json
import math
import os
import random
import shutil
import sys
import time
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from safetensors.torch import save_file

from laya.agent import Agent
from laya.common import proper_reward

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import triage  # noqa: E402  (stdlib only: QUESTIONS)

BASE_REPO = "convaiinnovations/laya"
BASE_REVISION = triage.STOCK_REVISION      # the stock checkpoint the baseline was measured on
QIDS = ("check", "direct")
SEED = 20261004


def base_dir() -> str:
    """The pinned base checkpoint: from the HF cache laya-serve filled, else downloaded."""
    kw = {"revision": BASE_REVISION,
          "allow_patterns": ["rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*"]}
    try:
        return snapshot_download(BASE_REPO, local_files_only=True, **kw)
    except Exception:  # noqa: BLE001  (huggingface_hub raises several types for "not cached")
        return snapshot_download(BASE_REPO, **kw)


def items_for(agent, rows):
    """One training item per (row, question) with a label; `direct` null = check only."""
    internal = {q: Agent._to_internal(triage.QUESTIONS[q]) for q in QIDS}
    out = []
    for i, r in enumerate(rows):
        ids = [q for q in QIDS if r[q] is not None]
        for q, it in zip(ids, agent._encode_state(r["state"], ids, internal)):
            y = float(r[q])
            out.append({"ids": it["ids"], "markers": it["markers"], "qtype": it["qtype"],
                        "target": [1.0 - y, y], "row": i, "q": q})
    return out


def collate(items, pad_id):
    n, seq = len(items), max(len(it["ids"]) for it in items)
    ids = torch.full((n, seq), pad_id, dtype=torch.long)
    att = torch.zeros((n, seq), dtype=torch.long)
    pos = torch.zeros((n, 2), dtype=torch.long)
    mask = torch.ones((n, 2), dtype=torch.bool)
    target = torch.tensor([it["target"] for it in items], dtype=torch.float32)
    for i, it in enumerate(items):
        ids[i, :len(it["ids"])] = torch.tensor(it["ids"])
        att[i, :len(it["ids"])] = 1
        pos[i] = torch.tensor(it["markers"])
    return ids, att, pos, mask, target, torch.tensor([it["qtype"] for it in items], dtype=torch.long)


def fit_temperature(logits, targets):
    """The notebook's per-type fit: one scalar T minimising cross-entropy."""
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=100)

    def closure():
        opt.zero_grad()
        loss = -(targets * torch.log_softmax(logits / log_t.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    opt.step(closure)
    return float(torch.clamp(log_t.exp(), 0.1, 10.0).item())


@torch.no_grad()
def raw_logits(model, items, pad_id, device, bs=16):
    model.eval()
    out = []
    for s in range(0, len(items), bs):
        ids, att, pos, mask, _, qt = collate(items[s:s + bs], pad_id)
        with torch.autocast("cuda", dtype=torch.float16, enabled=device.type == "cuda"):
            lg, _ = model(ids.to(device), att.to(device), pos.to(device), mask.to(device), qt.to(device))
        out.append(lg.float().cpu())
    return torch.cat(out) if out else torch.zeros((0, 2))


def report(logits, items, temp):
    """Per question: accuracy at p=0.5 and the score range of each label (calibration split)."""
    p = torch.softmax(logits / temp, -1)[:, 1]
    res = {}
    for q in QIDS:
        idx = [i for i, it in enumerate(items) if it["q"] == q]
        if not idx:
            continue
        y = torch.tensor([items[i]["target"][1] for i in idx])
        pq = p[idx]
        res[q] = {"n": len(idx), "acc": round(float(((pq >= 0.5).float() == y).float().mean()), 4),
                  "pos_min": round(float(pq[y == 1].min()), 4) if (y == 1).any() else None,
                  "neg_max": round(float(pq[y == 0].max()), 4) if (y == 0).any() else None}
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=os.path.join(HERE, "data", "triage", "train.jsonl"))
    ap.add_argument("--out", default=os.path.join(ROOT, "models", "laya-triage"))
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--micro-batch", type=int, default=8)
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--train-layers", type=int, default=4, help="top encoder layers to train (of 28)")
    ap.add_argument("--calib-pct", type=int, default=10, help="rows held out of training to fit the temperature")
    ap.add_argument("--mem-gb", type=float, default=3.0, help="VRAM cap for this process")
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args()

    t0 = time.time()
    random.seed(SEED)
    torch.manual_seed(SEED)
    device = torch.device(a.device if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        total = torch.cuda.get_device_properties(device).total_memory
        torch.cuda.set_per_process_memory_fraction(min(1.0, a.mem_gb * 2 ** 30 / total), device.index or 0)
    src = base_dir()
    agent = Agent(src, device="cpu")
    tok, model = agent.tok, agent.model
    with open(os.path.join(src, "rl_agent_config.json")) as f:
        cfg = json.load(f)

    with open(a.data, encoding="utf-8") as f:
        rows = [json.loads(ln) for ln in f]
    order = list(range(len(rows)))
    random.Random(SEED).shuffle(order)
    n_cal = len(rows) * a.calib_pct // 100
    cal_rows = [rows[i] for i in sorted(order[:n_cal])]
    train_rows = [rows[i] for i in sorted(order[n_cal:])]
    train_items, cal_items = items_for(agent, train_rows), items_for(agent, cal_rows)

    n_layers = len(model.encoder.layers)
    trainable = {f"encoder.layers.{i}." for i in range(n_layers - a.train_layers, n_layers)} | {
        "encoder.final_norm.", "head.", "scorer.", "type_emb."}
    for name, p in model.named_parameters():
        p.requires_grad = any(name.startswith(t) for t in trainable)
        # parameters only: RoPE buffers stay fp32
        p.data = p.data.float() if p.requires_grad else p.data.half()
    model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.head_checkpointing = True
    model.to(device).train()
    enc = [p for n, p in model.named_parameters() if p.requires_grad and n.startswith("encoder.")]
    head = [p for n, p in model.named_parameters() if p.requires_grad and not n.startswith("encoder.")]
    opt = torch.optim.AdamW([{"params": enc, "lr": 2.5e-5}, {"params": head, "lr": 1e-4}], weight_decay=0.01)
    updates = max(1, math.ceil(len(train_items) / a.micro_batch / a.grad_accum) * a.epochs)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=updates, eta_min=1e-6)
    n_train = sum(p.numel() for p in enc + head)
    print(f"device {device}; rows train {len(train_rows)} / calib {len(cal_rows)}; items {len(train_items)} / "
          f"{len(cal_items)}; trainable {n_train / 1e6:.1f}M of {sum(p.numel() for p in model.parameters()) / 1e6:.1f}M;"
          f" {updates} updates", flush=True)

    log = {"args": vars(a), "base": f"{BASE_REPO}@{BASE_REVISION}", "rows": {"train": len(train_rows),
           "calib": len(cal_rows)}, "items": {"train": len(train_items), "calib": len(cal_items)},
           "trainable_params": n_train, "epochs": [], "steps": []}
    t_train = time.time()
    step = 0
    for epoch in range(a.epochs):
        random.Random(42 + epoch).shuffle(train_items)
        sigma = 0.4 + (0.1 - 0.4) * epoch / max(1, a.epochs - 1)
        total, n_batches = 0.0, 0
        opt.zero_grad(set_to_none=True)
        model.train()
        for s in range(0, len(train_items), a.micro_batch):
            ids, att, pos, mask, target, qt = (x.to(device) for x in collate(train_items[s:s + a.micro_batch],
                                                                                tok.pad_token_id))
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                logits, _ = model(ids, att, pos, mask, qt)
            logits = logits.float()
            k = mask.sum(-1, keepdim=True).float()
            eps = torch.randn((4,) + logits.shape, device=device) * sigma * mask
            eps = (eps - eps.sum(-1, keepdim=True) / k) * mask
            noisy = logits.detach().unsqueeze(0) + eps
            probs = torch.softmax(noisy.masked_fill(~mask, -1e4), -1)
            with torch.no_grad():
                reward = proper_reward(probs, target.unsqueeze(0), qt, mask, w_sph=0.75, w_rps=1.0)
                adv = reward - reward.mean(0, keepdim=True)
                adv = adv / (adv.std() + 1e-6)
            logp = -(((noisy - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma ** 2)
            loss_rl = -(adv * logp).mean()
            loss_ce = -(target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1).mean()
            loss = loss_rl + loss_ce
            (loss / a.grad_accum).backward()
            n_batches += 1
            total += loss_ce.item()
            if n_batches % a.grad_accum == 0 or s + a.micro_batch >= len(train_items):
                torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
                opt.step()
                sched.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                if step % 20 == 0:
                    log["steps"].append({"step": step, "ce": round(loss_ce.item(), 4), "rl": round(loss_rl.item(), 4)})
                    print(f"epoch {epoch + 1} step {step}/{updates} ce {loss_ce.item():.4f} rl {loss_rl.item():.4f}",
                          flush=True)
        cal = raw_logits(model, cal_items, tok.pad_token_id, device)
        cal_ce = float(-(torch.tensor([it["target"] for it in cal_items]) * torch.log_softmax(cal, -1)).sum(-1).mean())
        ep = {"epoch": epoch + 1, "train_ce": round(total / max(1, n_batches), 4), "calib_ce_T1": round(cal_ce, 4),
              "calib": report(cal, cal_items, 1.0), "wall_s": round(time.time() - t_train, 1)}
        log["epochs"].append(ep)
        print(json.dumps(ep), flush=True)

    t_noul = fit_temperature(cal, torch.tensor([it["target"] for it in cal_items]))
    log["temperature_noul"] = t_noul
    log["calib_at_T"] = report(cal, cal_items, t_noul)
    log["train_wall_s"] = round(time.time() - t_train, 1)
    if device.type == "cuda":
        log["peak_vram_mib"] = round(torch.cuda.max_memory_allocated(device) / 2 ** 20)
        log["peak_reserved_mib"] = round(torch.cuda.max_memory_reserved(device) / 2 ** 20)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    save_file({n: v.detach().half().cpu().contiguous() for n, v in model.state_dict().items()},
              str(out / "model.safetensors"))
    for sub in ("encoder", "tokenizer"):      # unchanged: byte-identical to the base checkpoint's
        shutil.copytree(os.path.join(src, sub), out / sub, dirs_exist_ok=True)
    cfg["temperature"][2] = t_noul
    cfg.setdefault("temperature_by_options", {})["noul:2"] = t_noul
    cfg.update({"fine_tuned": True, "model_name": "laya-triage",
                "training": {**cfg.get("training", {}), "triage": {
                    "base": log["base"], "data": os.path.relpath(a.data, ROOT), "rows": log["rows"],
                    "epochs": a.epochs, "train_layers": a.train_layers, "temperature_noul": t_noul}}})
    with open(out / "rl_agent_config.json", "w") as f:
        json.dump(cfg, f, indent=2)
    log["total_wall_s"] = round(time.time() - t0, 1)
    with open(out / "train_log.json", "w") as f:
        json.dump(log, f, indent=1)
    print(json.dumps({k: log[k] for k in ("temperature_noul", "calib_at_T", "train_wall_s", "total_wall_s")
                      + (("peak_vram_mib", "peak_reserved_mib") if device.type == "cuda" else ())}), flush=True)


if __name__ == "__main__":
    main()
