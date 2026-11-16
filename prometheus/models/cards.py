"""Model cards (M3.4): what each AI is good at, so each job goes to a model that fits it.

A card names one model exactly ("provider" or "provider:model") and says which kinds of job it suits, how strong it is
(1 light, 2 medium, 3 strong: Prometheus's own estimate), its price, and whether it is local or makes images. The
router ranks the cards that fit a job, best first; among equals the free one wins. A model that makes images only ever
gets image jobs. Private jobs (your files) only ever go to a local model.

The shipped cards are in prometheus/data/models.json. To change them, copy that file to Prometheus's data folder as
models.json and edit it: that copy wins, and updates never overwrite it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("chat", "plan", "research", "code", "vision", "images", "summarise", "private")
KIND_LABELS = {"chat": "quick talk", "plan": "planning (the main brain)", "research": "research and long reports",
               "code": "code", "vision": "looking at pictures and screens", "images": "making pictures",
               "summarise": "summarising and translating", "private": "your files (laptop only)"}
HEAVY = frozenset({"plan", "research", "code"})       # jobs a cheap paid model may take without asking (owner, 16 Nov)
CHEAP_IN, CHEAP_OUT = 1.0, 4.0                         # USD per million tokens: the owner's "not expensive" line
TASK_CAP = 0.10                                        # USD a task may spend on paid AI without asking
# which job each older route name stands for
PURPOSE_KIND = {"plan": "plan", "combine": "chat", "research": "research", "code": "code", "vision": "vision",
                "private": "private"}
SHIPPED = Path(__file__).resolve().parent.parent / "data" / "models.json"


@dataclass(frozen=True)
class Card:
    entry: str
    name: str
    kinds: tuple[str, ...]
    level: int = 2
    price_in: float | None = None      # None = free
    price_out: float | None = None
    local: bool = False
    makes_images: bool = False
    note: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def provider(self) -> str:
        return self.entry.split(":", 1)[0]

    @property
    def model(self) -> str:
        return self.entry.split(":", 1)[1] if ":" in self.entry else ""

    @property
    def free(self) -> bool:
        return self.local or self.price_in is None

    @property
    def cheap(self) -> bool:
        """Paid but under the owner's line (at most $1 in and $4 out per million tokens)."""
        return (not self.free and self.price_in is not None and self.price_out is not None
                and self.price_in <= CHEAP_IN and self.price_out <= CHEAP_OUT)

    def fits(self, kind: str) -> bool:
        if self.makes_images:
            return kind == "images"           # an image model never gets research, chat or anything else
        if kind == "private":
            return self.local and "private" in self.kinds
        return kind in self.kinds


def _card(raw: dict) -> Card | None:
    try:
        entry = str(raw["entry"]).strip()
        kinds = tuple(k for k in raw.get("kinds", []) if k in KINDS)
        if not entry or not kinds:
            return None
        price = raw.get("price")
        p_in = p_out = None
        if isinstance(price, (list, tuple)) and len(price) == 2:
            p_in, p_out = float(price[0]), float(price[1])
        return Card(entry=entry, name=str(raw.get("name") or entry), kinds=kinds,
                    level=max(1, min(3, int(raw.get("level", 2)))), price_in=p_in, price_out=p_out,
                    local=bool(raw.get("local", False)), makes_images=bool(raw.get("makes_images", False)),
                    note=str(raw.get("note") or ""))
    except (KeyError, TypeError, ValueError):
        return None


def load_cards(data_folder: Path | None = None) -> tuple[list[Card], str]:
    """The cards, and where they came from (or why the owner's copy was ignored)."""
    own = Path(data_folder) / "models.json" if data_folder else None
    problem = ""
    for path in ([own] if own else []) + [SHIPPED]:
        if path is None or not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cards = [c for c in (_card(r) for r in raw.get("cards", [])) if c]
        except (OSError, ValueError, AttributeError) as e:
            if path == own:
                problem = f"Your models.json couldn't be read ({e}); the shipped cards are used."
                continue
            return [], f"The shipped cards couldn't be read: {e}"
        source = "your models.json" if path == own else "the shipped cards"
        return cards, (problem or f"Using {source} ({len(cards)} models).")
    return [], problem or "No model cards found."


STEP_KINDS = ("chat", "plan", "research", "code", "vision", "summarise")   # what a step may be tagged


def rank(cards: list[Card], kind: str, usable) -> list[Card]:
    """Cards that fit this kind of job and are set up right now. Heavy jobs: best fit first (owner), free first when
    equal. Light jobs: free ones first, then the best (the owner's "free first" rule). `usable(entry)` says if one is
    set up."""
    fit = [c for c in cards if c.fits(kind) and usable(c.entry)]
    if kind in HEAVY:
        return sorted(fit, key=lambda c: (-c.level, not c.free, c.price_out or 0.0))
    return sorted(fit, key=lambda c: (not c.free, -c.level, c.price_out or 0.0))


def estimate_usd(card: Card, messages: list, system: str, max_tokens: int) -> float:
    """The most a call could cost on this card: its text in (about 4 characters a token) and its room out."""
    if card.free:
        return 0.0
    chars = len(system or "") + sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
    return (chars / 4 * (card.price_in or 0) + max_tokens * (card.price_out or 0)) / 1_000_000
