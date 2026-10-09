"""Load a text-only open-weights model and tap its residual stream.

``load`` returns the model, tokenizer, and the list of decoder layers. ``ResidualTap`` registers a
forward hook on every decoder layer; the output of layer ``L`` is ``resid_post`` at ``L`` (the
convention Gemma Scope uses). Captured activations go straight to CPU so a teacher-forced pass over
a long transcript doesn't exhaust our 10 GB card, and the same hook can add a steering vector.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

GEMMA = "google/gemma-3-4b-it"
QWEN = "Qwen/Qwen3-4B-Instruct-2507"


@dataclass
class LoadedLM:
    model_id: str
    model: torch.nn.Module
    tok: object
    layers: torch.nn.ModuleList

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    @property
    def device(self) -> torch.device:
        return next(self.model.parameters()).device


def _decoder_layers(model: torch.nn.Module) -> torch.nn.ModuleList:
    inner = model.model
    inner = getattr(inner, "language_model", inner)  # Gemma 3 multimodal wrapper
    return inner.layers


def load(model_id: str = GEMMA, device: str = "cuda") -> LoadedLM:
    """Load in bf16, text-only. Gemma 3 4B ships as a vision-language checkpoint; we build it on
    CPU, drop the vision tower and projector (about 0.4B params we never use), then move to GPU."""
    tok = AutoTokenizer.from_pretrained(model_id)
    if model_id.startswith("google/gemma-3"):
        from transformers import Gemma3ForConditionalGeneration

        model = Gemma3ForConditionalGeneration.from_pretrained(model_id, dtype=torch.bfloat16)
        for name in ("vision_tower", "multi_modal_projector"):
            if hasattr(model.model, name):
                setattr(model.model, name, None)
        model = model.to(device)
    else:
        model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.bfloat16, device_map=device)
    model.eval()
    return LoadedLM(model_id, model, tok, _decoder_layers(model))


@dataclass
class ResidualTap:
    """Forward hooks on every decoder layer: capture ``resid_post`` to CPU and/or add steering.

    ``capture`` is the set of layers to record; ``steer`` maps layer -> (unit vector, alpha) and is
    added to the residual at every position.
    """

    lm: LoadedLM
    capture: set[int] = field(default_factory=set)
    steer: dict[int, tuple[torch.Tensor, float]] = field(default_factory=dict)
    acts: dict[int, list[torch.Tensor]] = field(default_factory=dict)
    _handles: list = field(default_factory=list)

    def _hook(self, layer: int):
        def hook(_mod, _inp, out):
            h = out[0] if isinstance(out, tuple) else out
            if layer in self.steer:
                vec, alpha = self.steer[layer]
                h = h + alpha * vec.to(h.device, h.dtype)
            if layer in self.capture:
                self.acts.setdefault(layer, []).append(h.detach()[0].float().cpu())
            return (h, *out[1:]) if isinstance(out, tuple) else h

        return hook

    def __enter__(self) -> ResidualTap:
        self._handles = [l.register_forward_hook(self._hook(i)) for i, l in enumerate(self.lm.layers)]
        return self

    def __exit__(self, *exc) -> None:
        for h in self._handles:
            h.remove()
        self._handles = []

    def stacked(self, layer: int) -> torch.Tensor:
        """All captured positions for ``layer`` as ``[seq, d_model]`` (float32, CPU)."""
        return torch.cat(self.acts[layer], dim=0)


@torch.no_grad()
def residuals(lm: LoadedLM, messages: list[dict], layers: set[int] | None = None) -> dict[int, torch.Tensor]:
    """One teacher-forced pass over a chat transcript; returns ``{layer: [seq, d_model]}``.

    ``use_cache=False`` because we only need activations, and the KV cache is what pushes a long
    transcript past our VRAM budget.
    """
    ids = lm.tok.apply_chat_template(messages, return_tensors="pt", return_dict=True)["input_ids"]
    with ResidualTap(lm, capture=set(range(lm.n_layers)) if layers is None else layers) as tap:
        lm.model(input_ids=ids.to(lm.device), use_cache=False, logits_to_keep=1)
    return {L: tap.stacked(L) for L in tap.acts}


@contextmanager
def steering(lm: LoadedLM, layer: int, vec: torch.Tensor, alpha: float):
    """Add ``alpha * vec/‖vec‖`` to the residual at ``layer`` for everything run inside the block."""
    with ResidualTap(lm, steer={layer: (vec / vec.norm(), alpha)}) as tap:
        yield tap
