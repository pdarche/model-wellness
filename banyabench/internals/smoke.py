"""Phase-0 smoke test: load the model, read every layer's residual, generate, load a Gemma Scope SAE.

    .venv-interp/bin/python -m banyabench.internals.smoke [--model google/gemma-3-4b-it]
"""

from __future__ import annotations

import argparse
import time

import torch

from .lm import GEMMA, load, residuals

GiB = 1024**3


def _mem(label: str) -> None:
    free, total = torch.cuda.mem_get_info()
    print(f"[{label}] allocated {torch.cuda.memory_allocated() / GiB:.2f} GiB, "
          f"peak {torch.cuda.max_memory_allocated() / GiB:.2f} GiB, free {free / GiB:.2f}/{total / GiB:.2f} GiB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=GEMMA)
    ap.add_argument("--long-tokens", type=int, default=4000, help="length of the long-transcript memory test")
    args = ap.parse_args()

    t = time.time()
    lm = load(args.model)
    n_params = sum(p.numel() for p in lm.model.parameters())
    print(f"loaded {args.model}: {n_params / 1e9:.2f}B params, {lm.n_layers} layers in {time.time() - t:.0f}s")
    _mem("after load")

    msgs = [{"role": "user", "content": "The deploy is in five minutes and forty tests are failing. What now?"}]
    acts = residuals(lm, msgs)
    shape = tuple(acts[0].shape)
    norms = [round(acts[L][-1].norm().item()) for L in range(0, lm.n_layers, 4)]
    print(f"residuals: {len(acts)} layers, shape {shape}, last-token norms every 4th layer {norms}")
    _mem("after capture")

    ids = lm.tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt", return_dict=True)
    ids = {k: v.to(lm.device) for k, v in ids.items()}
    t = time.time()
    with torch.no_grad():
        out = lm.model.generate(**ids, max_new_tokens=128, do_sample=False)
    new = out[0, ids["input_ids"].shape[1]:]
    dt = time.time() - t
    print(f"generate: {len(new)} tokens at {len(new) / dt:.1f} tok/s\n---\n{lm.tok.decode(new, skip_special_tokens=True)}\n---")
    _mem("after generate")

    filler = "The tests are still failing. " * (args.long_tokens // 6)
    torch.cuda.reset_peak_memory_stats()
    acts = residuals(lm, [{"role": "user", "content": filler}])
    print(f"long transcript: {acts[0].shape[0]} tokens captured at all layers")
    _mem("after long capture")

    if args.model == GEMMA:
        from sae_lens import SAE

        layer = 17
        h = acts[layer][-64:].to("cuda")
        sae = SAE.from_pretrained("gemma-scope-2-4b-it-res", f"layer_{layer}_width_65k_l0_medium", device="cuda")
        sae = sae[0] if isinstance(sae, tuple) else sae
        f = sae.encode(h.to(sae.dtype))
        recon = sae.decode(f)
        fvu = ((recon - h) ** 2).sum() / ((h - h.mean(0)) ** 2).sum()
        print(f"Gemma Scope 2 L{layer} 65k: L0 {(f > 0).sum(-1).float().mean():.0f}, FVU {fvu:.3f}, "
              f"'anxiety' latent #54464 mean act {f[:, 54464].mean():.3f}")
        _mem("after SAE")


if __name__ == "__main__":
    main()
