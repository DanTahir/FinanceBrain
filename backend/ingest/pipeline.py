"""Ingestion pipeline: structured records + redacted documents -> the brain__<tenant> graph.

The same `Ingestor.ingest_docs` path serves the initial seed (backend.ingest.seed) and live-feed events:
extraction (cache or live LLM) -> entity resolution -> topic canonicalisation by embedding similarity ->
batched UNWIND/MERGE writes (nodes first, then relationships) -> embeddings (vecf32) -> fact supersession
(bitemporal valid_from/valid_to + SUPERSEDES) -> incremental KNOWS_ABOUT weights with recency decay.

No raw PII enters the graph: document text is the redacted text, and clients/accounts/counterparties carry
only vault tokens (linked via HAS_IDENTIFIER to shared :PiiToken nodes).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from backend.config import get_settings
from backend.graph import queries as Q
from backend.graph.db import brain_graph, get_db
from backend.ingest.extract import Extraction, Extractor, XFact
from backend.ingest.resolver import Resolver, norm
from backend.ingest.sources import cutoff, load_decision_log, load_entity_tokens, load_redacted_docs, load_world
from backend.llm.embeddings import embed_cached

log = logging.getLogger(__name__)

REF_DATE = date(2026, 10, 15)       # "today" in the synthetic timeline; recency decay is measured from here
HALF_LIFE_DAYS = 45.0
# Derived-expertise weight per participation role. Intent matters more than volume: answering someone else's
# question or writing documentation shows knowledge; asking a question shows need (a heavy *user* of a system asks
# about it constantly); being asked shows the organisation treats you as the expert.
KNOW_W = {"documented": 2.0, "answered": 2.0, "assignee": 1.2, "author": 1.0, "asked_of": 1.0, "attendee": 0.5,
          "asked": 0.3, "mentioned": 0.3, "recipient": 0.15}
KNOW_COUNTERS = ("answered", "asked", "documented")  # kept per KNOWS_ABOUT edge so who_knows can explain itself
AUTHORITATIVE_DOCS = {"runbook", "architecture", "model_card", "methodology", "guide", "postmortem", "procedure"}
KNOWS_TARGETS = ("System", "Client", "Deal", "Topic", "Counterparty")
TOPIC_SIM = 0.86                    # cosine threshold for merging a new topic phrase into an existing topic
FUNCTIONAL = {"owned_by", "maintained_by", "status", "assigned_to", "deadline", "role"}  # one value at a time
WRITE_BATCH = {"Document": 150}
DEFAULT_BATCH = 1000
VEC_BATCH = 100
EMBED_CHARS = 12000
MENTION_LABEL = {"people": "Person", "systems": "System", "clients": "Client", "accounts": "Account",
                 "deals": "Deal", "counterparties": "Counterparty", "transactions": "Transaction"}
ENTITY_LABEL = {"client": "Client", "account": "Account", "counterparty": "Counterparty"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sha(s: str, n: int = 12) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def clean_props(d: dict) -> dict:
    """FalkorDB-safe properties: drop nulls/empties, JSON-encode maps (map-valued properties are rejected)."""
    out = {}
    for k, v in d.items():
        if v is None or (isinstance(v, (list, tuple, str)) and len(v) == 0 and k != "text"):
            continue
        if isinstance(v, dict):
            v = json.dumps(v, ensure_ascii=False, sort_keys=True)
        elif isinstance(v, (list, tuple)):
            v = json.dumps(list(v), ensure_ascii=False) if any(isinstance(x, (dict, list)) for x in v) else list(v)
        out[k] = v
    return out


def decay(ts: str) -> float:
    try:
        d = date.fromisoformat(ts[:10])
    except ValueError:
        return 1.0
    return 0.5 ** (max(0, (REF_DATE - d).days) / HALF_LIFE_DAYS)


def team_id(x: str) -> str:
    return x if x.startswith("team_") else f"team_{x}"


def normalize_topic(t: str) -> str:
    t = re.sub(r"\s+", " ", (t or "").strip().strip(".").casefold())
    return t[:60] if len(t) >= 3 else ""


def is_question(d: dict) -> bool:
    return d.get("kind") in ("slack", "email") and "?" in (d.get("text") or "")


def participation(d: dict, mentioned: set[str], msg_meta: dict[str, tuple[str | None, bool]]) -> dict[str, str]:
    """Each person's strongest participation role in one document (see KNOW_W).

    `msg_meta` maps doc_id -> (author_id, is_question) so a reply can be recognised as an answer to someone
    else's question even when the question was ingested in an earlier batch.
    """
    roles: dict[str, str] = {}

    def give(pid: str | None, role: str) -> None:
        if pid and KNOW_W[role] > KNOW_W.get(roles.get(pid, ""), 0.0):
            roles[pid] = role

    author, kind, q = d.get("author_id"), d.get("kind"), is_question(d)
    parent = msg_meta.get(d.get("reply_to") or "")
    if kind == "doc" and d.get("doc_type") in AUTHORITATIVE_DOCS:
        give(author, "documented")
    elif parent and parent[1] and parent[0] and parent[0] != author:
        give(author, "answered")
    elif q:
        give(author, "asked")
    else:
        give(author, "author")
    give((d.get("ticket") or {}).get("assignee_id"), "assignee")
    for a in d.get("attendees") or []:
        give(a, "attendee")
    for r in (d.get("recipients") or []) + (d.get("cc") or []):
        give(r, "asked_of" if q and kind == "email" else "recipient")
    for m in mentioned:
        if m != author:
            give(m, "asked_of" if q else "mentioned")
    return roles


def supersession_plan(groups: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Pure core of fact supersession.

    `groups` maps (subject_id, predicate) -> [(valid_from, fact_id, object_key, current valid_to)]. A fact is
    closed by the first strictly later fact with a different object (restatements of the same value do not close
    it). Returns (valid_to updates that differ from the stored value, [(newer_id, older_id)] SUPERSEDES pairs).
    """
    updates: list[dict] = []
    supersedes: list[tuple[str, str]] = []
    for facts in groups.values():
        facts = sorted(facts)
        for idx, (vf, fid, okey, vt) in enumerate(facts):
            closer = next((g for g in facts[idx + 1 :] if g[0] > vf and g[2] != okey), None)
            new_vt = closer[0] if closer else None
            if new_vt != vt:
                updates.append({"id": fid, "valid_to": new_vt})
            if closer:
                supersedes.append((closer[1], fid))
    return updates, supersedes


@dataclass
class Batch:
    nodes: dict = field(default_factory=lambda: defaultdict(dict))      # label -> id -> props
    edges: dict = field(default_factory=lambda: defaultdict(dict))      # (s, t, d) -> (src, dst) -> props
    owns: dict = field(default_factory=lambda: defaultdict(list))       # src label -> rows
    # (label, person, target) -> [weight, evidence docs, last_seen, answered, asked, documented]
    knows: dict = field(default_factory=lambda: defaultdict(lambda: [0.0, 0, "", 0, 0, 0]))
    vectors: dict = field(default_factory=lambda: defaultdict(dict))    # label -> id -> text to embed

    def node(self, label: str, nid: str, **props) -> None:
        self.nodes[label].setdefault(nid, {}).update(clean_props(props))

    def edge(self, s: str, t: str, d: str, src: str | None, dst: str | None, **props) -> None:
        if not src or not dst:
            return
        if (s, t, d) not in Q.MERGE_EDGES:
            raise KeyError(f"unregistered edge spec {(s, t, d)}")
        self.edges[(s, t, d)].setdefault((src, dst), {}).update(clean_props(props))

    def know(self, person: str, label: str, target: str, w: float, ts: str, role: str = "") -> None:
        if person and target:
            k = self.knows[(label, person, target)]
            k[0] += w
            k[1] += 1
            k[2] = max(k[2], ts)
            if role in KNOW_COUNTERS:
                k[3 + KNOW_COUNTERS.index(role)] += 1


class TopicIndex:
    """In-memory matrix of topic-name embeddings, used to canonicalise new topic phrases."""

    def __init__(self) -> None:
        self.dims = get_settings().embed_dims
        self.ids: list[str] = []
        self.names: list[str] = []
        self.by_name: dict[str, str] = {}
        self._mat = np.zeros((256, self.dims), dtype=np.float32)

    def _add(self, tid: str, name: str, vec) -> None:
        n = len(self.ids)
        if n == self._mat.shape[0]:
            self._mat = np.vstack([self._mat, np.zeros_like(self._mat)])
        v = np.asarray(vec, dtype=np.float32)
        self._mat[n] = v / (np.linalg.norm(v) or 1.0)
        self.ids.append(tid)
        self.names.append(name)
        self.by_name[name] = tid

    async def load(self, g) -> None:
        res = await g.query(Q.ALL_TOPICS)
        pairs = [(r[0], r[1]) for r in res.result_set if r[1]]
        if pairs:
            vecs = await embed_cached([n for _, n in pairs])
            for (tid, name), v in zip(pairs, vecs):
                self._add(tid, name, v)

    def _new_id(self, name: str) -> str:
        base = "topic_" + (re.sub(r"[^a-z0-9]+", "_", name).strip("_")[:48] or sha(name, 8))
        tid, i = base, 2
        taken = set(self.ids)
        while tid in taken:
            tid, i = f"{base}_{i}", i + 1
        return tid

    async def canonicalize(self, phrases: Counter) -> dict[str, tuple[str, str, bool]]:
        """phrase -> (topic id, canonical name, created_now). Frequent phrases become canonical first."""
        out: dict[str, tuple[str, str, bool]] = {}
        todo = []
        for p, _ in phrases.most_common():
            if p in self.by_name:
                tid = self.by_name[p]
                out[p] = (tid, self.names[self.ids.index(tid)], False)
            else:
                todo.append(p)
        if not todo:
            return out
        vecs = await embed_cached(todo)
        for p, vec in zip(todo, vecs):
            v = np.asarray(vec, dtype=np.float32)
            v = v / (np.linalg.norm(v) or 1.0)
            n = len(self.ids)
            if n:
                sims = self._mat[:n] @ v
                j = int(np.argmax(sims))
                if sims[j] >= TOPIC_SIM:
                    self.by_name[p] = self.ids[j]
                    out[p] = (self.ids[j], self.names[j], False)
                    continue
            tid = self._new_id(p)
            self._add(tid, p, v)
            out[p] = (tid, p, True)
        return out


class Ingestor:
    def __init__(self, tenant: str, *, graph: str | None = None, offline: bool = False, model: str | None = None):
        self.tenant = tenant
        self.world = load_world(tenant)
        self.cutoff = cutoff(tenant)
        self.entity_tokens = load_entity_tokens(tenant)
        self.decisions = load_decision_log(tenant)
        self.resolver = Resolver(self.world, self.entity_tokens)
        self.extractor = Extractor(tenant, self.world, model=model)
        self.offline = offline
        self.graph_name = graph or brain_graph(tenant)
        self.g = get_db().select_graph(self.graph_name)
        self.topics = TopicIndex()
        self._topics_loaded = False
        self.team_members: dict[str, list[str]] = defaultdict(list)
        for p in self.world["people"]:
            self.team_members[team_id(p["team_id"])].append(p["id"])
        corpus = load_redacted_docs(tenant)
        self.ticket_doc = {d["ticket"]["key"]: d["doc_id"] for d in corpus if d.get("ticket")}
        self.msg_meta: dict[str, tuple[str | None, bool]] = {d["doc_id"]: (d.get("author_id"), is_question(d))
                                                            for d in corpus}
        self.decision_roles: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for dec_id, meta in self.decisions.items():
            decided = set(meta.get("decided_in", []))
            for doc in decided:
                self.decision_roles[doc].append((dec_id, "DECIDED_IN"))
            for doc in meta.get("justified_by", []):
                self.decision_roles[doc].append((dec_id, "JUSTIFIED_BY"))
            for doc in meta.get("documents", []):
                if doc not in decided:
                    self.decision_roles[doc].append((dec_id, "CITES"))
        self.stats: Counter = Counter()

    # ------------------------------------------------------------------------------------------------ writes
    async def _run(self, cypher: str, rows: list[dict], batch: int = DEFAULT_BATCH) -> None:
        for i in range(0, len(rows), batch):
            await self.g.query(cypher, {"rows": rows[i : i + batch]})

    async def write(self, b: Batch) -> None:
        for label, items in b.nodes.items():
            rows = [{"id": nid, "props": p} for nid, p in items.items()]
            await self._run(Q.UPSERT_NODES[label], rows, WRITE_BATCH.get(label, DEFAULT_BATCH))
            self.stats[f"node:{label}"] += len(rows)
        for src_label, rows in b.owns.items():
            await self._run(Q.MERGE_OWNS[src_label], rows)
            self.stats["rel:OWNS"] += len(rows)
        for spec, items in b.edges.items():
            rows = [{"src": s, "dst": d, "props": p} for (s, d), p in items.items()]
            await self._run(Q.MERGE_EDGES[spec], rows)
            self.stats[f"rel:{spec[1]}"] += len(rows)
        by_label: dict[str, list[dict]] = defaultdict(list)
        for (label, person, target), (w, n, last, ans, ask, doc) in b.knows.items():
            by_label[label].append({"src": person, "dst": target, "w": round(w, 5), "n": n, "last_seen": last,
                                    "answered": ans, "asked": ask, "documented": doc})
        for label, rows in by_label.items():
            await self._run(Q.KNOWS_ABOUT[label], rows)
            self.stats["rel:KNOWS_ABOUT(updates)"] += len(rows)
        for label, items in b.vectors.items():
            ids, texts = list(items.keys()), list(items.values())
            vecs = await embed_cached(texts, "document")
            rows = [{"id": i, "v": v} for i, v in zip(ids, vecs)]
            await self._run(Q.SET_EMBEDDINGS[label], rows, VEC_BATCH)
            self.stats[f"vec:{label}"] += len(rows)

    # ------------------------------------------------------------------------------------- structured records
    async def seed_world(self, as_of: str | None = None) -> None:
        """Write org, systems, ownership, clients, accounts, ledger, deals and channels as known at `as_of`.

        Records that start on/after `as_of` (ownership transfers, departures, later transactions) are left for
        the live feed, and validity intervals ending after `as_of` are written as still open.
        """
        as_of = as_of or self.cutoff
        w, b, tok = self.world, Batch(), self.entity_tokens

        def before(d: str | None) -> str | None:
            return d if d and d[:10] < as_of else None

        for t in w["teams"]:
            b.node("Team", t["id"], name=t["name"], lead_id=t.get("lead_id"))
        for p in w["people"]:
            if p.get("start_date") and p["start_date"] >= as_of:
                continue
            end = before(p.get("end_date"))
            # salary, home_address and medical_note are HR-confidential and deliberately not ingested
            b.node("Person", p["id"], name=p["name"], email=p["email"], title=p["title"], team_id=team_id(p["team_id"]),
                   location=p.get("location"), start_date=p.get("start_date"), end_date=end, role=p.get("role"),
                   persona=p.get("persona"), active=end is None)
            b.edge("Person", "MEMBER_OF", "Team", p["id"], team_id(p["team_id"]))
            b.edge("Person", "REPORTS_TO", "Person", p["id"], p.get("manager_id"))
        for s in w["systems"]:
            b.node("System", s["id"], name=s["name"], description=s.get("description"), tier=s.get("tier"))
            for dep in s.get("depends_on") or []:
                b.edge("System", "DEPENDS_ON", "System", s["id"], dep)
        for o in w["ownership"]:
            if o["valid_from"] >= as_of:
                continue
            vt = before(o.get("valid_to"))
            props = clean_props({"valid_from": o["valid_from"], "valid_to": vt, "current": vt is None})
            for label, src in (("Team", o.get("team_id")), ("Person", o.get("person_id"))):
                if src:
                    b.owns[label].append({"src": src, "dst": o["system_id"], "valid_from": o["valid_from"],
                                          "props": props})
        for c in w["clients"]:
            if c["onboarded"] >= as_of:
                continue
            toks = tok.get(("client", c["id"]), {})
            b.node("Client", c["id"], name=toks.get("CLIENT_NAME"), kind=c.get("kind"), segment=c.get("segment"),
                   risk_rating=c.get("risk_rating"), onboarded=c.get("onboarded"),
                   crypto_interest=c.get("crypto_interest"))
            b.edge("Person", "MANAGES", "Client", c.get("rm_id"), c["id"])
            self._identifiers(b, "Client", c["id"], toks)
        for a in w["accounts"]:
            if a["opened"] >= as_of:
                continue
            toks = tok.get(("account", a["id"]), {})
            b.node("Account", a["id"], kind=a.get("kind"), status=a.get("status"), opened=a.get("opened"),
                   number=toks.get("ACCOUNT_NUMBER"))
            b.edge("Client", "HOLDS", "Account", a["client_id"], a["id"])
            self._identifiers(b, "Account", a["id"], toks)
        for cp in w["counterparties"]:
            toks = tok.get(("counterparty", cp["id"]), {})
            b.node("Counterparty", cp["id"], name=cp["name"], country=cp.get("country"), kind=cp.get("kind"))
            self._identifiers(b, "Counterparty", cp["id"], toks)
        for d in w["deals"]:
            b.node("Deal", d["id"], codename=d["codename"], kind=d.get("kind"), target=d.get("target"),
                   principal=d.get("principal"), value=d.get("value"), status=d.get("status"),
                   sensitivity=d.get("sensitivity"))
            for m in d.get("team_ids") or []:
                for pid in (self.team_members.get(team_id(m), []) if not m.startswith("e_") else [m]):
                    b.edge("Person", "ON_DEAL_TEAM", "Deal", pid, d["id"])
        for ch in w["channels"]:
            b.node("Channel", ch["id"], name=ch["name"], kind=ch.get("kind"), topic=ch.get("topic"),
                   sensitivity=ch.get("sensitivity"))
            for pid in ch.get("member_ids") or []:
                b.edge("Person", "CAN_ACCESS", "Channel", pid, ch["id"])
        for t in w["transactions"]:
            if t.get("live_feed") or t["ts"][:10] >= as_of:
                continue
            self._transaction(b, t)
        await self.write(b)

    def _identifiers(self, b: Batch, label: str, eid: str, toks: dict[str, str]) -> None:
        for typ, token in toks.items():
            b.node("PiiToken", token, type=typ)
            b.edge(label, "HAS_IDENTIFIER", "PiiToken", eid, token, type=typ)

    def _transaction(self, b: Batch, t: dict) -> None:
        flags = list(t.get("flags") or [])
        score = next((float(f.split(":", 1)[1]) for f in flags if f.startswith("fraudscore:")), None)
        alert = next((f.split(":", 1)[1] for f in flags if f.startswith("alert:")), None)
        b.node("Transaction", t["id"], ts=t["ts"], date=t["ts"][:10], kind=t["kind"], amount=float(t["amount"]),
               currency=t.get("currency"), memo=t.get("memo"), flags=flags, fraud_score=score, alert=alert,
               live_feed=bool(t.get("live_feed")))
        b.edge("Transaction", "FROM_ACCOUNT", "Account", t["id"], t.get("from_account"))
        b.edge("Transaction", "TO_ACCOUNT", "Account", t["id"], t.get("to_account"))
        b.edge("Transaction", "COUNTERPARTY", "Counterparty", t["id"], t.get("counterparty_id"))

    # ----------------------------------------------------------------------------------------------- documents
    async def ingest_docs(self, docs: list[dict]) -> None:
        if not docs:
            return
        if not self._topics_loaded:
            await self.topics.load(self.g)
            self._topics_loaded = True
        ex = await self.extractor.extract_many(docs, offline=self.offline)
        phrases = Counter(p for d in docs for p in map(normalize_topic, ex[d["doc_id"]].topics) if p)
        canon = await self.topics.canonicalize(phrases)
        b = Batch()
        for tid, name, created in canon.values():
            if created:
                b.node("Topic", tid, name=name)
                b.vectors["Topic"][tid] = name
        fact_keys: list[tuple[str, str]] = []
        for d in docs:
            self._document(b, d, ex[d["doc_id"]], canon, fact_keys)
        await self.write(b)
        await self._supersede(fact_keys)
        self.stats["docs"] += len(docs)

    def _document(self, b: Batch, d: dict, x: Extraction, canon: dict, fact_keys: list) -> None:
        did, ts = d["doc_id"], d["ts"]
        acl = d.get("acl") or {}
        tk = d.get("ticket") or {}
        redactions = list(d.get("redactions") or []) + list(d.get("title_redactions") or [])
        b.node("Document", did, kind=d["kind"], ts=ts, date=ts[:10], title=d.get("title") or "", text=d.get("text") or "",
               sensitivity=d.get("sensitivity"), live_feed=bool(d.get("live_feed")), public=bool(acl.get("public")),
               channel_id=d.get("channel_id"), thread_id=d.get("thread_id"), author_id=d.get("author_id"),
               external_author=d.get("external_author"), doc_type=d.get("doc_type"), ticket_key=tk.get("key"),
               ticket_status=tk.get("status"), ticket_priority=tk.get("priority"), ticket_project=tk.get("project"),
               client_id=d.get("client_id"), redactions=d.get("redactions") or None,
               title_redactions=d.get("title_redactions") or None)
        b.vectors["Document"][did] = ((d.get("title") or "") + "\n\n" + (d.get("text") or "")).strip()[:EMBED_CHARS]

        # provenance and delivery
        author = d.get("author_id")
        b.edge("Person", "AUTHORED", "Document", author, did)
        for r in d.get("recipients") or []:
            b.edge("Document", "SENT_TO", "Person", did, r)
        for r in d.get("cc") or []:
            b.edge("Document", "SENT_TO", "Person", did, r, cc=True)
        for a in d.get("attendees") or []:
            b.edge("Person", "ATTENDED", "Document", a, did)
        b.edge("Document", "ASSIGNED_TO", "Person", did, tk.get("assignee_id"))
        b.edge("Document", "POSTED_IN", "Channel", did, d.get("channel_id"))
        if acl.get("channel") and acl["channel"] != d.get("channel_id"):
            b.edge("Document", "POSTED_IN", "Channel", did, acl["channel"])
        if d.get("thread_id"):
            b.node("Thread", d["thread_id"], kind=d["kind"])
            b.edge("Document", "IN_THREAD", "Thread", did, d["thread_id"])
        b.edge("Document", "REPLY_TO", "Document", did, d.get("reply_to"))
        b.edge("Document", "ABOUT", "Client", did, d.get("client_id"))

        # access control (channel docs inherit access from channel membership via POSTED_IN)
        for pid in acl.get("people") or []:
            b.edge("Person", "CAN_ACCESS", "Document", pid, did)
        for t in acl.get("teams") or []:
            b.edge("Team", "CAN_ACCESS", "Document", team_id(t), did)

        # mentions: source metadata (@-mentions, linked records), vault tokens, then LLM extraction
        targets: dict[str, set[str]] = defaultdict(set)
        mentioned: set[str] = set()
        for key, ids in (d.get("mentions") or {}).items():
            if key == "tickets":
                for k in ids:
                    tdoc = self.ticket_doc.get(k)
                    if tdoc and tdoc != did:
                        b.edge("Document", "MENTIONS", "Document", did, tdoc)
                continue
            label = MENTION_LABEL.get(key)
            for i in ids if label else []:
                b.edge("Document", "MENTIONS", label, did, i)
                if label in KNOWS_TARGETS:
                    targets[label].add(i)
                elif label == "Person":
                    mentioned.add(i)
        for r in redactions:
            token = r["token"]
            b.node("PiiToken", token, type=r["type"])
            b.edge("Document", "MENTIONS", "PiiToken", did, token)
            owner = self.resolver.token_owner.get(token)
            if owner:
                b.edge("Document", "MENTIONS", owner[0], did, owner[1])
                if owner[0] in KNOWS_TARGETS:
                    targets[owner[0]].add(owner[1])
        for s in x.systems:
            hit = self.resolver.resolve(s, "system")
            if hit and hit[0] == "System":
                b.edge("Document", "MENTIONS", "System", did, hit[1], extracted=True)
                targets["System"].add(hit[1])
        for p in x.people:
            hit = self.resolver.resolve(p, "person")
            if hit and hit[0] == "Person":
                b.edge("Document", "MENTIONS", "Person", did, hit[1], extracted=True)
                mentioned.add(hit[1])
        doc_topics = []
        for t in x.topics:
            c = canon.get(normalize_topic(t))
            if c:
                b.edge("Document", "ABOUT", "Topic", did, c[0])
                targets["Topic"].add(c[0])
                doc_topics.append(c[0])

        # decision trail (governance decision log)
        for dec_id, role in self.decision_roles.get(did, []):
            self._decision(b, dec_id, did, role, doc_topics)

        # reified facts (bitemporal) and extracted decisions
        for i, f in enumerate(x.facts):
            self._fact(b, d, i, f, fact_keys)
        for i, dec in enumerate(x.decisions):
            eff = dec.effective if DATE_RE.match(dec.effective or "") else ts[:10]
            self._fact(b, d, 100 + i, XFact(subject="decision", subject_type="other", predicate="decision",
                                            object=dec.status, statement=dec.summary, valid_from=eff), fact_keys)

        # derived expertise: who knows about which systems / clients / deals / topics
        self.msg_meta.setdefault(did, (author, is_question(d)))
        wdecay = decay(ts)
        for pid, role in participation(d, mentioned, self.msg_meta).items():
            for label, ids in targets.items():
                for t in ids:
                    b.know(pid, label, t, KNOW_W[role] * wdecay, ts, role)

        # redaction audit trail
        counts = d.get("redaction_counts") or {}
        if counts:
            b.node("RedactionEvent", f"re_{did}", doc_id=did, ts=ts, counts=counts, total=sum(counts.values()))
            b.edge("Document", "HAD_REDACTION", "RedactionEvent", did, f"re_{did}")

    def _decision(self, b: Batch, dec_id: str, did: str, role: str, doc_topics: list[str]) -> None:
        meta = self.decisions.get(dec_id) or {}
        if role == "DECIDED_IN":
            b.node("Decision", dec_id, summary=meta.get("summary"), effective=meta.get("effective"),
                   status="active", decided_in=did)
            # link evidence that arrived before the decision itself (edges to absent docs are skipped)
            for doc in meta.get("justified_by", []):
                b.edge("Decision", "JUSTIFIED_BY", "Document", dec_id, doc)
            for doc in meta.get("documents", []):
                if doc != did:
                    b.edge("Decision", "CITES", "Document", dec_id, doc)
            for t in doc_topics:
                b.edge("Decision", "ABOUT", "Topic", dec_id, t)
            if meta.get("supersedes"):
                b.node("Decision", meta["supersedes"], status="superseded", superseded_by=dec_id,
                       valid_to=meta.get("effective"))
                b.edge("Decision", "SUPERSEDES", "Decision", dec_id, meta["supersedes"])
        b.edge("Decision", role, "Document", dec_id, did)

    def _fact(self, b: Batch, d: dict, i: int, f: XFact, fact_keys: list) -> None:
        stmt = (f.statement or "").strip()
        if not stmt:
            return
        did = d["doc_id"]
        vf = f.valid_from if DATE_RE.match(f.valid_from or "") else d["ts"][:10]
        subj = self.resolver.resolve(f.subject, f.subject_type) if f.subject_type != "other" or f.subject else None
        obj = self.resolver.resolve(f.object) if f.object else None
        subject_id = subj[1] if subj else "txt:" + (norm(f.subject)[:60] or "none")
        fid = "f_" + sha(f"{did}|{i}|{stmt}")
        b.node("Fact", fid, statement=stmt, subject=f.subject, subject_type=f.subject_type, subject_id=subject_id,
               subject_label=subj[0] if subj else None, predicate=f.predicate, object=f.object,
               object_id=obj[1] if obj else None, object_key=obj[1] if obj else norm(f.object)[:80],
               valid_from=vf, source_doc=did, ts=d["ts"], sensitivity=d.get("sensitivity"))
        b.edge("Fact", "CITES", "Document", fid, did)
        for ref, role in ((subj, "subject"), (obj, "object")):
            if ref and ("Fact", "ABOUT", ref[0]) in Q.MERGE_EDGES:
                b.edge("Fact", "ABOUT", ref[0], fid, ref[1], role=role)
        b.vectors["Fact"][fid] = stmt
        if subj and f.predicate in FUNCTIONAL:
            fact_keys.append((subject_id, f.predicate))

    async def _supersede(self, keys: list[tuple[str, str]]) -> None:
        """Recompute validity for functional facts: a fact is closed (valid_to) by the first later fact on the
        same (subject, predicate) with a different object, which SUPERSEDES it. Nothing is deleted."""
        uniq = sorted(set(keys))
        if not uniq:
            return
        groups: dict[tuple[str, str], list[tuple]] = defaultdict(list)
        for i in range(0, len(uniq), 500):
            res = await self.g.query(Q.FACTS_FOR_KEYS, {"keys": [{"s": s, "p": p} for s, p in uniq[i : i + 500]]})
            for fid, s, p, okey, vf, vt in res.result_set:
                groups[(s, p)].append((vf or "", fid, okey or "", vt))
        updates, supersedes = supersession_plan(groups)
        b = Batch()
        for new_id, old_id in supersedes:
            b.edge("Fact", "SUPERSEDES", "Fact", new_id, old_id)
        await self._run(Q.SET_FACT_VALID_TO, updates)
        await self.write(b)
        self.stats["facts_closed"] += sum(1 for u in updates if u["valid_to"])
