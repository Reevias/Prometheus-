"""Picks a model per purpose and switches by itself. Enforces privacy, budget and the kill switch.

A route is an ordered list of entries, each "provider" or "provider:model". The router tries them in order,
skips any that are unavailable or resting after a failure, and drops to free/local ones when the cap is reached.
"""
from __future__ import annotations

import re
import contextlib
import contextvars
import time
from typing import Callable

from prometheus.config import Settings
from prometheus.models.anthropic_provider import AnthropicProvider
from prometheus.models.base import (PERSONAL_CLOSE, PERSONAL_OPEN, PaidNeedsYes, PrivacyViolation, Provider,
                                    ProviderUnavailable, Reply)
from prometheus.catalog import load_catalog
from prometheus.models import cards as _cards
from prometheus.models.compat import OpenAICompatProvider
from prometheus.models.ollama import OllamaProvider
from prometheus.models.openrouter import OpenRouterProvider
from prometheus.safety.budget import Budget, BudgetExceeded
from prometheus.safety.killswitch import KillSwitch

REST_SECONDS = 300
BAD_MODEL = re.compile(r"model name|model .*(doesn'?t exist|not found|unknown|invalid)|no such model|"
                       r"(unknown|invalid) model", re.I)
HEAD_PURPOSES = ("plan", "combine")       # the lead AI: plans tasks, answers simple questions, writes final answers


def split_entry(entry: str) -> tuple[str, str | None]:
    name, sep, model = entry.partition(":")
    return name, (model or None) if sep else None


_PAID_OK: contextvars.ContextVar[bool] = contextvars.ContextVar("prometheus_paid_ok", default=False)
# the kind of job the current step is (the planner tags steps; "" = use the purpose's own kind)
_KIND: contextvars.ContextVar[str] = contextvars.ContextVar("prometheus_job_kind", default="")
# what paid AI this task has spent so far (None = not inside a task: no paid AI without asking)
_TASK_SPEND: contextvars.ContextVar[list | None] = contextvars.ContextVar("prometheus_task_spend", default=None)
# inside this, only free or local AI is used (the front door's quick check never costs money)
_FREE_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar("prometheus_free_only", default=False)


class ModelRouter:
    def __init__(self, providers: dict[str, Provider], routes: dict[str, list[str]], budget: Budget,
                 killswitch: KillSwitch, clock: Callable[[], float] = time.time):
        self.providers = providers
        self.routes = routes
        self.budget = budget
        self.killswitch = killswitch
        self.clock = clock
        self._resting: dict[str, tuple[float, str]] = {}
        self.head = ""          # the lead AI chosen by hand ("" = follow the routes)
        self.paid_needs_yes = False   # paid AI only after the owner says yes (a paid Brain picked by hand is a yes)
        self.cards: list = []         # model cards (M3.4): which AI suits which job
        self.paid_heavy = False       # cheap paid AI may take heavy jobs without asking (Features switch)
        self.pinned: dict[str, str] = {}   # purpose -> the owner's own pick (research brain...): always tried first
        self.cards_off: set[str] = set()   # job kinds where cards aren't used (coding brain switched off)
        for entry in routes.get("private", []):
            p = providers.get(split_entry(entry)[0])
            if p is None or not p.is_local:
                raise PrivacyViolation(f"'{entry}' is not a local model; personal files may only use local models.")

    def clear_rest(self) -> None:
        """Wake every resting model (called when a key is added or changed)."""
        self._resting.clear()

    def status(self) -> dict[str, bool]:
        return {name: p.available() for name, p in self.providers.items()}

    def status_detail(self) -> list[dict]:
        now = self.clock()
        out = []
        for name, p in self.providers.items():
            until, reason = self._resting.get(name, (0.0, ""))
            resting = until > now
            out.append({"name": name, "available": p.available(), "local": p.is_local, "resting": resting,
                        "reason": reason if resting else "", "minutes_left": int((until - now) // 60) + 1 if resting else 0})
        return out

    def is_free(self, entry: str) -> bool:
        name, model = split_entry(entry)
        p = self.providers.get(name)
        if p is None:
            return False
        model = model or getattr(p, "model", "") or ""
        return bool(p.is_local or getattr(p, "is_free", False) or (name == "openrouter" and model.endswith(":free")))

    # -- model cards (M3.4) -------------------------------------------------------------------------------------
    def kind_for(self, purpose: str) -> str:
        """The kind of job: private and vision always stay what they are; otherwise the step's tag, else the
        purpose's own kind."""
        if purpose in ("private", "vision"):
            return purpose
        return _KIND.get() or _cards.PURPOSE_KIND.get(purpose, "")

    def _usable(self, entry: str, kind: str = "") -> bool:
        name, _ = split_entry(entry)
        p = self.providers.get(name)
        if p is None or getattr(getattr(p, "info", None), "pick_only", False):
            return False                      # only when the owner picks it (Coarena)
        if kind == "private" and not p.is_local:
            return False
        try:
            return bool(p.available())
        except Exception:
            return False

    def card_for(self, entry: str):
        """The card for this exact model; a provider-wide card only when no card names the model."""
        name, model = split_entry(entry)
        if not model:
            model = getattr(self.providers.get(name), "model", "") or ""
        for c in self.cards:
            if c.entry == entry or (model and c.entry == f"{name}:{model}"):
                return c
        return next((c for c in self.cards if c.entry == name and not c.model), None)

    def card_entries(self, kind: str) -> list[str]:
        if not self.cards or not kind or kind in self.cards_off:
            return []
        return [c.entry for c in _cards.rank(self.cards, kind, lambda e: self._usable(e, kind))]

    def heavy_ok(self, entry: str, kind: str) -> bool:
        """A cheap paid model may take a heavy job without asking: switch on, cheap card, task under its cap."""
        if not self.paid_heavy or kind not in _cards.HEAVY:
            return False
        spent = _TASK_SPEND.get()
        card = self.card_for(entry)
        return spent is not None and card is not None and card.cheap and spent[0] < _cards.TASK_CAP

    @contextlib.contextmanager
    def free_only(self):
        token = _FREE_ONLY.set(True)
        try:
            yield
        finally:
            _FREE_ONLY.reset(token)

    @contextlib.contextmanager
    def step_kind(self, kind: str):
        token = _KIND.set(kind if kind in _cards.STEP_KINDS else "")     # never "private" or "images" from a tag
        try:
            yield
        finally:
            _KIND.reset(token)

    def _fits_task_cap(self, entry: str, messages, system, max_tokens) -> bool:
        """This call's most possible cost still fits in the task's limit (so one big call can't jump past it)."""
        spent, card = _TASK_SPEND.get(), self.card_for(entry)
        if spent is None or card is None:
            return False
        return spent[0] + _cards.estimate_usd(card, messages, system, max_tokens) <= _cards.TASK_CAP

    @contextlib.contextmanager
    def task_spending(self):
        """One task: paid AI it uses without asking is counted against the per-task limit."""
        token = _TASK_SPEND.set([0.0])
        try:
            yield
        finally:
            _TASK_SPEND.reset(token)

    def entries_for(self, purpose: str) -> list[str]:
        """The order to try: the cards that fit the job first (best first, free before paid when equal), then the
        purpose's own route as the fallback. With a hand-picked lead AI, planning and answers use it first and then
        only free or local models, so a choice made to save money never falls back to a paid one."""
        route = list(self.routes.get(purpose, []))
        kind = self.kind_for(purpose)
        cards = self.card_entries(kind)
        if kind not in _cards.HEAVY:
            # light jobs: free cards, then the owner's route, then paid cards (the "free first" rule)
            free_cards = [e for e in cards if self.is_free(e)]
            cards = free_cards + [e for e in route if e not in free_cards] + [e for e in cards if e not in free_cards]
        pin = self.pinned.get(purpose)
        fitted = list(dict.fromkeys(([pin] if pin else []) + cards + route))
        if kind != "images":                     # a picture-making AI never gets anything else, routes included
            fitted = [e for e in fitted if not getattr(self.card_for(e), "makes_images", False)]
        if purpose == "private":
            fitted = [e for e in fitted if self._usable(e, "private") or e in route]
        if self.head and purpose in HEAD_PURPOSES:
            return [self.head] + [e for e in fitted if e != self.head and self.is_free(e)]
        return fitted

    @contextlib.contextmanager
    def paid_allowed(self):
        """Inside this block (one request the owner said yes to), paid AI may be used."""
        token = _PAID_OK.set(True)
        try:
            yield
        finally:
            _PAID_OK.reset(token)

    def _order(self, purpose: str) -> list[str]:
        """Free AIs first while paid ones wait for a yes, so a yes is only asked when nothing free could answer."""
        entries = self.entries_for(purpose)
        if not self.paid_needs_yes or _PAID_OK.get():
            return entries
        kind = self.kind_for(purpose)
        ok = [e for e in entries if self.is_free(e) or e == self.head or self.heavy_ok(e, kind)]
        return ok + [e for e in entries if e not in ok]

    def stream(self, purpose: str, messages: list[dict], system: str, on_text, max_tokens: int = 4096) -> Reply:
        """Like complete, but the answer comes word by word through on_text (M3.5). on_text(None) means: start
        again (the first AI failed part way and the next one is answering)."""
        return self.complete(purpose, messages, system, max_tokens=max_tokens, on_text=on_text)

    def complete(self, purpose: str, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096, skip: frozenset[str] = frozenset(), on_text=None) -> Reply:
        self.killswitch.check()
        errors: list[str] = []
        empty: list[str] = []
        waiting_yes: list[str] = []
        over_budget = False
        for entry in self._order(purpose):
            name, model = split_entry(entry)
            provider = self.providers.get(name)
            if provider is None or not provider.available() or name in skip:
                continue
            if purpose == "private" and not provider.is_local:
                raise PrivacyViolation(f"'{name}' is not local.")
            used_model = model or getattr(provider, "model", "") or ""
            rest_key = next((k for k in (name, f"{name}/{used_model}")
                             if self._resting.get(k, (0.0, ""))[0] > self.clock()), None)
            if rest_key:
                errors.append(f"{rest_key}: resting ({self._resting[rest_key][1]})")
                continue
            free = (provider.is_local or getattr(provider, "is_free", False)
                    or (name == "openrouter" and used_model.endswith(":free")))
            if not free and _FREE_ONLY.get():
                continue
            if not free and self.budget.state().stopped:
                over_budget = True   # cap reached: skip paid models, keep looking for a free or local one
                continue
            heavy = (not free and self.heavy_ok(entry, self.kind_for(purpose))
                     and self._fits_task_cap(entry, messages, system, max_tokens))
            if not free and self.paid_needs_yes and not _PAID_OK.get() and entry != self.head and not heavy:
                waiting_yes.append(f"{name} · {used_model}" if used_model else name)
                continue             # paid: only after the owner's yes
            keyless = bool(getattr(provider, "keyless", False) or getattr(getattr(provider, "info", None), "keyless", False))
            sent = personal_for(messages, share=not keyless)    # a keyless provider never gets memories (M3 B2)
            try:
                if on_text is not None and json_schema is None and hasattr(provider, "stream"):
                    on_text(None)                 # a fresh start (an earlier AI may have failed part way)
                    reply = provider.stream(sent, system, on_text=on_text, max_tokens=max_tokens, model=model)
                else:
                    reply = provider.complete(sent, system, json_schema=json_schema, max_tokens=max_tokens,
                                              model=model)
            except ProviderUnavailable as e:
                # a wrong model name rests only that model; the provider's other models keep working
                key = f"{name}/{used_model}" if BAD_MODEL.search(str(e)) else name
                self._resting[key] = (self.clock() + REST_SECONDS, str(e))
                errors.append(f"{name}: {e}")
                continue
            self._resting.pop(name, None)
            if not provider.is_local:
                self.budget.record(reply.provider, reply.model, reply.usd, reply.tokens_in, reply.tokens_out)
                spent = _TASK_SPEND.get()
                if spent is not None and not free:
                    spent[0] += float(reply.usd or 0.0)
            if not (reply.text or "").strip():
                # an empty answer (a thinking model that used all its room, a free model that timed out) is a
                # failure: try the next AI rather than hand back nothing
                empty.append(f"{reply.provider} · {reply.model}")
                errors.append(f"{name}: sent back an empty answer")
                continue
            return reply
        if waiting_yes:
            names = ", ".join(dict.fromkeys(waiting_yes))
            raise PaidNeedsYes(
                f"No free AI could answer this right now ({'; '.join(errors) or 'none is set up for it'}). A paid one "
                f"could: {names}. It usually costs well under a cent. Reply “yes, use paid” to use it for this "
                "request, or try again later.", list(dict.fromkeys(waiting_yes)))
        if empty:
            raise ProviderUnavailable(
                f"The AI ({', '.join(empty)}) sent back an empty answer. Try again in a moment, or pick another Brain "
                "in Settings → AI." + (f" Others: {'; '.join(e for e in errors if 'empty answer' not in e)}."
                                       if len(errors) > len(empty) else ""))
        if over_budget:
            self.budget.ensure_can_spend()
            raise BudgetExceeded("Spending cap reached and no free model is available.")
        message = f"No AI model is available for '{purpose}'. Start Ollama or add an API key in Settings."
        if errors:
            message += " (" + "; ".join(errors) + ")"
        raise ProviderUnavailable(message)


CLOUD_PURPOSES = ("plan", "combine", "research")
CLAUDE_CHOICES = (("claude-opus-5-5", "Claude Opus 5.5 (smartest, $4/$20 per million tokens)"),
                  ("claude-sonnet-5-5", "Claude Sonnet 5.5 ($2/$10 per million tokens)"),
                  ("claude-haiku-5-5", "Claude Haiku 5.5 (cheap, $0.10/$0.50 per million tokens)"))


def head_choices(router: "ModelRouter") -> list[tuple[str, str]]:
    """Lead AIs that can be picked right now: (route entry, label). Only ones that are set up appear."""
    out = []
    for name, p in router.providers.items():
        if not p.available():
            continue
        model = getattr(p, "model", "") or ""
        entry = f"{name}:{model}" if model else name
        if name == "anthropic":
            out += [(f"anthropic:{m}", label) for m, label in CLAUDE_CHOICES]
        elif name == "ollama":
            out.append((entry, f"On this laptop: {model or 'local model'} (free, private, slower)"))
        elif name == "openrouter":
            out.append((entry, f"OpenRouter: {model or 'default'}"))
            out += [(f"openrouter:{m['id']}", f"OpenRouter: {m['name']} ({m['maker']}, {price_text(m['price'])})")
                    for m in openrouter_models() if f"openrouter:{m['id']}" != entry]
        elif name == "ollama-cloud":
            info = getattr(p, "info", None)
            for m in (info.models if info else (model,)):
                out.append((f"ollama-cloud:{m}", f"Ollama cloud: {m} (free plan with limits; not for private files)"))
        elif getattr(getattr(p, "info", None), "pick_only", False):
            out += [(f"{name}:{m}", f"{p.info.name}: {m} (free daily use)") for m in p.info.models]
        else:
            info = getattr(p, "info", None)
            label = info.name if info else name
            out.append((entry, f"{label}: {model} (free)" if model else f"{label} (free)"))
    return out


def _personal_split(text: str):
    """The text with the personal block (memories, facts, project notes) taken out, and the text with it kept."""
    out, keep, i = [], [], 0
    while True:
        a = text.find(PERSONAL_OPEN, i)
        if a < 0:
            out.append(text[i:])
            keep.append(text[i:])
            break
        b = text.find(PERSONAL_CLOSE, a)
        if b < 0:                          # an unclosed block is treated as personal, to the end
            out.append(text[i:a])
            keep.append(text[i:a] + text[a + len(PERSONAL_OPEN):])
            break
        out.append(text[i:a])
        keep.append(text[i:a] + text[a + len(PERSONAL_OPEN):b])
        i = b + len(PERSONAL_CLOSE)
    return "".join(out), "".join(keep)


def personal_for(messages: list[dict], share: bool) -> list[dict]:
    """The messages as one AI should get them: the personal block kept (share) or left out (a provider that needs no
    key: its owner is unknown, so it only ever gets the request and the recent turns). The markers never go out."""
    done = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str) and PERSONAL_OPEN in c:
            m = {**m, "content": _personal_split(c)[1 if share else 0]}
        done.append(m)
    return done


def provider_on(info, settings: Settings) -> bool:
    """A catalog provider is used when it is supported, not switched off, and either has a key (checked when it is
    called) or needs none and free no-key providers are allowed."""
    if not info.supported or info.id in settings.disabled_providers:
        return False
    return not info.keyless or settings.free_no_key or info.id in settings.enabled_providers


# Which AI writes research reports (Settings → AI → Research brain). Kimi runs on Moonshot's servers via OpenRouter.
def price_text(price: str) -> str:
    return "free, with daily limits" if str(price).strip().lower() == "free" else f"${price} per M tokens"


def live_openrouter_prices() -> dict | None:
    """OpenRouter's own current prices, USD per million tokens {model id: (in, out)}; None if it can't be reached.
    The list is public (no key) and is only asked for when the owner presses "Check cards"."""
    import httpx

    try:
        resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=8)
        if resp.status_code != 200:
            return None
        found = {}
        for m in resp.json().get("data", []):
            pricing = m.get("pricing") or {}
            try:
                found[str(m["id"])] = (float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6)
            except (KeyError, TypeError, ValueError):
                continue
        return found or None
    except (httpx.HTTPError, ValueError):
        return None


def openrouter_models() -> list[dict]:
    """A hand-picked list of strong models on OpenRouter (prometheus/data/openrouter_models.json)."""
    import json as _json
    from pathlib import Path as _Path

    try:
        return _json.loads((_Path(__file__).resolve().parent.parent / "data" / "openrouter_models.json")
                           .read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError):
        return []


RESEARCH_BRAINS = {"auto": "", "claude": "anthropic"}
RESEARCH_LABELS = {"auto": "Automatic (as before: the cheapest good model)", "claude": "Claude"}
for _m in openrouter_models():
    RESEARCH_BRAINS[_m["id"]] = f"openrouter:{_m['id']}"
    RESEARCH_LABELS[_m["id"]] = f"{_m['name']} ({_m['maker']}; {_m['about']}; {price_text(_m['price'])})"


CODE_BRAINS = {"auto": "openrouter:ricursiveai/ripple-65b-preview", "off": ""}


def build_routes(settings: Settings) -> dict[str, list[str]]:
    """The user's routes, plus every free provider that is on: ones with a key first, no-key ones after them,
    the local model last. Never added to the private route."""
    from prometheus.config import get_secret

    routes = {purpose: list(entries) for purpose, entries in settings.routes.items()}
    if settings.openrouter_fast_model and "research" in routes:
        routes["research"] = [f"openrouter:{settings.openrouter_fast_model}" if e == "openrouter" else e
                              for e in routes["research"]]
    brain = getattr(settings, "research_brain", "auto")
    if brain in RESEARCH_BRAINS and RESEARCH_BRAINS[brain] and "research" in routes:
        first = RESEARCH_BRAINS[brain]
        # the chosen research brain goes first; the old route stays behind it as the fallback
        routes["research"] = [first] + [e for e in routes["research"] if e != first]
    on = [p for p in load_catalog() if provider_on(p, settings) and not p.pick_only]   # pick-only: never by itself
    keyed = [p for p in on if not p.keyless and get_secret(p.key_name)]
    keyless = [p for p in on if p.keyless]
    for info in keyed + keyless:
        model = settings.provider_models.get(info.id, "")
        entry = f"{info.id}:{model}" if model else info.id
        for purpose in CLOUD_PURPOSES:
            names = routes.setdefault(purpose, [])
            if not any(split_entry(e)[0] == info.id for e in names):
                local = [e for e in names if split_entry(e)[0] == "ollama"]
                names[:] = [e for e in names if e not in local] + [entry] + local   # local stays the last resort
    code = CODE_BRAINS.get(getattr(settings, "code_brain", "auto"), getattr(settings, "code_brain", ""))
    if code and "plan" in routes:
        # coding requests are planned by the coding brain first; the normal planning route stays behind it
        routes["code"] = [code] + [e for e in routes["plan"] if e != code]
    return routes


def build_router(settings: Settings, budget: Budget, killswitch: KillSwitch) -> ModelRouter:
    providers: dict[str, Provider] = {
        "openrouter": OpenRouterProvider(model=settings.openrouter_model),
        "anthropic": AnthropicProvider(model=settings.anthropic_model),
        "ollama": OllamaProvider(settings.ollama_model, settings.ollama_url),
    }
    for info in load_catalog():
        providers[info.id] = OpenAICompatProvider(info, model=settings.provider_models.get(info.id, ""),
                                                  enabled=provider_on(info, settings))
    return ModelRouter(providers, build_routes(settings), budget, killswitch)
