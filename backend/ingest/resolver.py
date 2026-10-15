"""Entity resolution: map names and PII tokens found in extracted text to graph entity ids.

Resolution order: (1) vault token -> owning client/account/counterparty (exact), (2) exact alias match on a
normalised key, (3) fuzzy alias match (difflib ratio >= 0.88, keys of 5+ chars). Topic phrases are resolved
separately by embedding similarity (pipeline.TopicIndex).
"""

from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict

TOKEN_RE = re.compile(r"⟨[A-Z]+(?:_[A-Z]+)*_\d{4}⟩")
_SUFFIXES = ("team", "system", "service", "platform", "desk", "department", "group")
HINT_LABEL = {"person": "Person", "team": "Team", "system": "System", "client": "Client", "account": "Account",
              "deal": "Deal", "counterparty": "Counterparty", "project": "Deal"}
ENTITY_LABEL = {"client": "Client", "account": "Account", "counterparty": "Counterparty"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").casefold())


def _variants(s: str) -> list[str]:
    words = re.sub(r"[^a-z0-9& ]+", " ", (s or "").casefold()).split()
    out = [norm(s)]
    if words and words[0] == "the":
        words = words[1:]
        out.append("".join(words))
    if len(words) > 1 and words[-1] in _SUFFIXES:
        out.append(norm(" ".join(words[:-1])))
    if words and words[0] == "project":
        out.append(norm(" ".join(words[1:])))
    return [v for v in dict.fromkeys(out) if v]


class Resolver:
    def __init__(self, world: dict, entity_tokens: dict[tuple[str, str], dict[str, str]]):
        self.alias: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.token_owners: dict[str, set[tuple[str, str]]] = defaultdict(set)  # all entities holding a token
        self.token_owner: dict[str, tuple[str, str]] = {}  # only tokens held by exactly one entity
        self.token_type: dict[str, str] = {}
        firsts = Counter(p["name"].split()[0].casefold() for p in world["people"])
        lasts = Counter(p["name"].split()[-1].casefold() for p in world["people"])
        for p in world["people"]:
            ref = ("Person", p["id"])
            self._add(p["name"], ref)
            self._add(p["email"].split("@")[0], ref)
            first, last = p["name"].split()[0], p["name"].split()[-1]
            if firsts[first.casefold()] == 1:
                self._add(first, ref)
            if lasts[last.casefold()] == 1:
                self._add(last, ref)
        for t in world["teams"]:
            ref = ("Team", t["id"])
            for a in (t["name"], t["id"], t["id"].removeprefix("team_")):
                self._add(a, ref)
        for s in world["systems"]:
            ref = ("System", s["id"])
            self._add(s["name"], ref)
            self._add(s["id"].removeprefix("sys_"), ref)
            # "PricingSvc" -> "Pricing Svc" / "Pricing Service"
            spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s["name"])
            self._add(spaced, ref)
            self._add(re.sub(r"\bSvc\b", "Service", spaced), ref)
        for d in world["deals"]:
            ref = ("Deal", d["id"])
            for a in (d["codename"], d["codename"].removeprefix("Project "), d.get("target") or ""):
                self._add(a, ref)
        for c in world["counterparties"]:
            self._add(c["name"], ("Counterparty", c["id"]))
        for (entity, eid), toks in entity_tokens.items():
            label = ENTITY_LABEL.get(entity)
            if not label:
                continue
            for typ, tok in toks.items():
                self.token_owners[tok].add((label, eid))
                self.token_type[tok] = typ
        # a shared identifier (e.g. one phone across a fraud ring) names no single entity; the graph models it as
        # one :PiiToken with several HAS_IDENTIFIER edges instead
        self.token_owner = {t: next(iter(o)) for t, o in self.token_owners.items() if len(o) == 1}
        self._keys = [k for k in self.alias if len(k) >= 5]
        self._memo: dict[tuple[str, str | None], tuple[str, str] | None] = {}

    def _add(self, text: str, ref: tuple[str, str]) -> None:
        k = norm(text)
        if len(k) >= 2:
            self.alias[k].add(ref)

    def by_token(self, text: str) -> tuple[str, str] | None:
        for tok in TOKEN_RE.findall(text or ""):
            if tok in self.token_owner:
                return self.token_owner[tok]
        return None

    @staticmethod
    def _pick(cands: set[tuple[str, str]], label: str | None) -> tuple[str, str] | None:
        if label:
            cands = {c for c in cands if c[0] == label}
        if len(cands) == 1:
            return next(iter(cands))
        return None

    def resolve(self, text: str, hint: str | None = None) -> tuple[str, str] | None:
        """Return (label, id) or None. `hint` is an extractor subject_type (person, system, ...)."""
        mk = (text, hint)
        if mk in self._memo:
            return self._memo[mk]
        label = HINT_LABEL.get(hint or "")
        hit = self.by_token(text)
        if hit is None:
            for v in _variants(text):
                if v in self.alias:
                    hit = self._pick(self.alias[v], label)
                    if hit:
                        break
        if hit is None:
            k = norm(text)
            if len(k) >= 5:
                for m in difflib.get_close_matches(k, self._keys, n=3, cutoff=0.88):
                    hit = self._pick(self.alias[m], label)
                    if hit:
                        break
        self._memo[mk] = hit
        return hit
