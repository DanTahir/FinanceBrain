"use client";

// Memory Inspector: episodic (Session -> Turn), semantic (MemoryFact, bitemporal) and procedural (Playbook -> Step)
// memory. Episodic + semantic memory live in a private FalkorDB graph per tenant x persona x visitor; playbooks are
// shared procedure in the tenant brain graph.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  API,
  ApiError,
  DOC_ID_RE,
  ENTITY_ID_RE,
  errorText,
  type Guided,
  type MemoryFact,
  type MemoryOverview,
  type MemorySession,
  type TraceItem,
} from "@/lib/api";
import { useApp } from "@/lib/state";
import { Badge, Button, Card, CodeBlock, Disclosure, Empty, ErrorNote, LabelBadge, Modal, Spinner, Stat, cx, fmtTs } from "@/components/ui";
import Markdown from "@/components/Markdown";

// ------------------------------------------------------------------------------------------------ helpers
type Obj = Record<string, unknown>;

const ENT_ONE = new RegExp(`^(?:${ENTITY_ID_RE.source})$`);
const DOC_ONE = new RegExp(`^(?:${DOC_ID_RE.source})$`);

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function str(v: unknown): string | undefined {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return undefined;
}
function num(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() && Number.isFinite(Number(v))) return Number(v);
  return undefined;
}
function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

const COUNT_LABELS: Record<string, string> = {
  Session: "Sessions",
  Turn: "Turns",
  MemoryFact: "Memories (facts)",
  EntityRef: "Entity refs",
  User: "User node",
};
const COUNT_ORDER = ["Session", "Turn", "MemoryFact", "EntityRef", "User"];
function humanise(k: string): string {
  if (COUNT_LABELS[k]) return COUNT_LABELS[k];
  return k
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^./, (c) => c.toUpperCase());
}

const KIND_ORDER = ["preference", "fact", "goal", "open_thread"];
const KIND_LABEL: Record<string, string> = {
  preference: "Preferences",
  fact: "Facts",
  goal: "Goals",
  open_thread: "Open threads",
};

function explain(e: unknown): string {
  if (e instanceof ApiError && e.status === 403) return `${e.message} (this persona lacks the scope needed for this view).`;
  return errorText(e);
}

function isRetired(f: MemoryFact): boolean {
  return Boolean(f.valid_to) || Boolean(f.superseded_by);
}

// ------------------------------------------------------------------------------------------------ reference chips
function RefChip({ value }: { value: unknown }) {
  const { openEntity, openDoc } = useApp();
  let id: string | undefined;
  let name: string | undefined;
  let label: string | undefined;
  if (typeof value === "string") id = value;
  else if (isObj(value)) {
    id = str(value.id);
    name = str(value.name);
    label = str(value.label);
  }
  if (!id) return <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-400">{show(value)}</span>;
  const text = name && name !== id ? `${name} · ${id}` : id;
  if (DOC_ONE.test(id)) {
    const docId = id;
    return (
      <button className="cite-chip text-[11px]" onClick={() => openDoc(docId)} title={`Open document ${docId}`}>
        {text}
      </button>
    );
  }
  if (ENT_ONE.test(id) || label) {
    const entId = id;
    return (
      <button
        className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800/70 px-1.5 py-0.5 text-[11px] text-sky-300 hover:border-sky-500/60"
        onClick={() => openEntity(entId)}
        title={`Show ${entId} in the Graph Explorer`}
      >
        {label && <span className="text-slate-500">{label}</span>}
        {text}
      </button>
    );
  }
  return <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-400">{text}</span>;
}

function RefList({ value }: { value: unknown }) {
  const items = Array.isArray(value) ? value.filter((x) => x !== null && x !== undefined) : value ? [value] : [];
  if (!items.length) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">about</span>
      {items.map((r, i) => (
        <RefChip key={i} value={r} />
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ session modal
function SessionModal({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { tenant, persona } = useApp();
  const [data, setData] = useState<{ session: Obj; turns: Obj[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const req = useRef(0);

  const load = useCallback(() => {
    if (!id) return;
    const my = ++req.current;
    setLoading(true);
    setError(null);
    setData(null);
    API.memorySession({ tenant, persona }, id)
      .then((r) => {
        if (my !== req.current) return;
        setData({ session: isObj(r.session) ? r.session : {}, turns: arr(r.turns).filter(isObj) });
      })
      .catch((e) => my === req.current && setError(explain(e)))
      .finally(() => my === req.current && setLoading(false));
  }, [id, tenant, persona]);

  useEffect(() => {
    load();
  }, [load]);

  const s = data?.session ?? {};
  return (
    <Modal
      open={Boolean(id)}
      onClose={onClose}
      wide
      title={
        <span className="flex items-center gap-2">
          <span>Episodic memory: {str(s.title) || id}</span>
          <code className="text-[11px] text-slate-500">{id}</code>
        </span>
      }
    >
      <p className="mb-3 text-xs text-slate-400">
        Every turn is a <code>Turn</code> node linked <code>NEXT</code> in order under its <code>Session</code>, PII-scrubbed and
        embedded for vector recall. Company entities are referenced by id only.
      </p>
      <ErrorNote error={error} onRetry={load} />
      {loading && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner /> Loading turns…
        </div>
      )}
      {data && str(s.summary) && (
        <div className="mb-3 rounded-md border border-slate-800 bg-slate-950/50 p-2 text-sm text-slate-300">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">Librarian summary</div>
          {str(s.summary)}
        </div>
      )}
      {data && !data.turns.length && <Empty>No turns stored for this session.</Empty>}
      <div className="space-y-2">
        {data?.turns.map((t, i) => {
          const role = str(t.role) ?? "turn";
          const text = str(t.text) ?? "";
          const tools = arr(t.tools).map(str).filter((x): x is string => Boolean(x));
          const cost = num(t.cost_usd);
          return (
            <div
              key={str(t.id) ?? i}
              className={cx(
                "rounded-md border p-2",
                role === "user" ? "border-blue-500/20 bg-blue-500/5" : "border-slate-800 bg-slate-950/40",
              )}
            >
              <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                <Badge tone={role === "user" ? "blue" : role === "assistant" ? "violet" : "slate"}>
                  {num(t.n) !== undefined ? `#${num(t.n)} ` : ""}
                  {role}
                </Badge>
                {str(t.agent) && <Badge tone="pink">{str(t.agent)}</Badge>}
                <span>{fmtTs(str(t.ts))}</span>
                {tools.map((tl, j) => (
                  <code key={j} className="rounded bg-slate-800 px-1 text-[10px] text-emerald-300">
                    {tl}
                  </code>
                ))}
                {cost !== undefined && <span className="ml-auto tabular-nums">${cost.toFixed(4)}</span>}
              </div>
              {role === "assistant" ? (
                <Markdown text={text} />
              ) : (
                <div className="whitespace-pre-wrap text-sm text-slate-200">{text}</div>
              )}
              {arr(t.refs).length > 0 && (
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  <span className="text-[10px] uppercase tracking-wide text-slate-500">refs</span>
                  {arr(t.refs).slice(0, 30).map((r, j) => (
                    <RefChip key={j} value={r} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ playbooks
type Step = { n: number; tool?: string; text: string };
function readSteps(v: unknown): Step[] {
  const out: Step[] = [];
  arr(v).forEach((s, i) => {
    if (typeof s === "string") out.push({ n: i + 1, text: s });
    else if (isObj(s)) {
      out.push({
        n: num(s.n) ?? num(s.order) ?? i + 1,
        tool: str(s.tool),
        text: str(s.instruction) ?? str(s.text) ?? str(s.name) ?? show(s),
      });
    }
  });
  return out.sort((a, b) => a.n - b.n);
}

const PB_KNOWN = new Set(["id", "name", "title", "trigger", "when", "description", "uses", "success", "created", "updated", "learned_by", "source", "steps", "distance", "match"]);

function PlaybookItem({ p }: { p: Obj }) {
  const steps = readSteps(p.steps);
  const name = str(p.name) ?? str(p.title) ?? str(p.id) ?? "playbook";
  const trigger = str(p.trigger) ?? str(p.when);
  const uses = num(p.uses);
  const success = num(p.success);
  const source = str(p.source);
  const extra = Object.entries(p).filter(([k, v]) => !PB_KNOWN.has(k) && str(v) !== undefined);
  return (
    <div className="rounded-md border border-slate-800 bg-slate-950/40 p-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-sm font-medium text-emerald-200">{name}</span>
        {uses !== undefined && <Badge tone="green" title="Times agents reused or reinforced this playbook">{uses} uses</Badge>}
        {success !== undefined && <Badge tone="blue">{success} successes</Badge>}
      </div>
      {trigger && (
        <div className="mt-1 text-xs text-slate-400">
          <span className="text-slate-500">Use when:</span> {trigger}
        </div>
      )}
      {str(p.description) && <div className="mt-1 text-xs text-slate-400">{str(p.description)}</div>}
      {steps.length > 0 && (
        <ol className="mt-2 space-y-1">
          {steps.map((s) => (
            <li key={s.n} className="flex gap-2 text-xs text-slate-300">
              <span className="w-4 shrink-0 text-right tabular-nums text-slate-500">{s.n}.</span>
              <span className="min-w-0">
                {s.tool && <code className="mr-1 rounded bg-slate-800 px-1 text-[10px] text-emerald-300">{s.tool}</code>}
                {s.text}
              </span>
            </li>
          ))}
        </ol>
      )}
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
        {str(p.learned_by) && <span>learned by {str(p.learned_by)}</span>}
        {source && <span>from {source}</span>}
        {str(p.created) && <span>created {fmtTs(str(p.created))}</span>}
        {str(p.updated) && <span>updated {fmtTs(str(p.updated))}</span>}
        {extra.map(([k, v]) => (
          <span key={k}>
            {k}: {str(v)}
          </span>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ view
export default function MemoryView() {
  const { tenant, persona, tenantInfo, personaInfo, view, version, bump, ask } = useApp();
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [playbooks, setPlaybooks] = useState<Obj[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pbError, setPbError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [openSession, setOpenSession] = useState<string | null>(null);
  const [guided, setGuided] = useState<{ tenant: string; questions: Guided[] } | null>(null);
  const req = useRef(0);
  const loadedKey = useRef<string | null>(null);

  const load = useCallback(async () => {
    const my = ++req.current;
    const w = { tenant, persona };
    setLoading(true);
    setError(null);
    setPbError(null);
    const [m, p] = await Promise.allSettled([API.memory(w), API.playbooks(w)]);
    if (my !== req.current) return;
    if (m.status === "fulfilled") setOverview(m.value);
    else {
      setOverview(null);
      setError(explain(m.reason));
    }
    if (p.status === "fulfilled") setPlaybooks(arr(p.value?.playbooks).filter(isObj));
    else {
      setPlaybooks(null);
      setPbError(explain(p.reason));
    }
    setLoading(false);
  }, [tenant, persona]);

  // Lazy refresh: load when shown and whenever tenant, persona or the shared version changed since the last load.
  const key = `${tenant}|${persona}|${version}`;
  useEffect(() => {
    if (view !== "memory" || !tenantInfo) return;
    if (loadedKey.current === key) return;
    loadedKey.current = key;
    void load();
  }, [view, key, tenantInfo, load]);

  // Guided T2 questions for the empty state.
  const empty = overview !== null && !overview.exists;
  useEffect(() => {
    if (view !== "memory" || !empty || guided?.tenant === tenant) return;
    let live = true;
    API.guided(tenant)
      .then((g) => live && setGuided({ tenant, questions: arr(g.questions) as Guided[] }))
      .catch(() => live && setGuided({ tenant, questions: [] }));
    return () => {
      live = false;
    };
  }, [view, empty, tenant, guided?.tenant]);

  // Forget actions: bump() increments the shared version, which triggers the reload above.
  const run = async (id: string, fn: () => Promise<unknown>) => {
    setBusy(id);
    setActionError(null);
    try {
      await fn();
      bump();
    } catch (e) {
      setActionError(explain(e));
    } finally {
      setBusy(null);
    }
  };
  const w = { tenant, persona };
  const forgetAll = () => {
    if (!window.confirm(`Delete your whole memory graph for ${personaInfo?.name ?? persona}? This cannot be undone.`)) return;
    void run("all", () => API.forgetAll(w));
  };
  const forgetSession = (s: MemorySession) => {
    if (!window.confirm(`Forget session "${s.title || s.id}" and all its turns?`)) return;
    void run(s.id, () => API.forgetSession(w, s.id));
  };
  const forgetFact = (f: MemoryFact) => {
    if (!window.confirm("Forget this memory?")) return;
    void run(f.id, () => API.forgetFact(w, f.id));
  };

  const counts = overview?.counts ?? {};
  const countKeys = Object.keys(counts).sort((a, b) => {
    const ia = COUNT_ORDER.indexOf(a);
    const ib = COUNT_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  const facts = arr(overview?.facts) as MemoryFact[];
  const sessions = arr(overview?.sessions) as MemorySession[];
  const trace = arr(overview?.trace).filter(isObj) as TraceItem[];
  const activeFacts = facts.filter((f) => !isRetired(f));
  const prefs = activeFacts.filter((f) => f.kind === "preference").length;
  const kinds = Array.from(new Set(facts.map((f) => f.kind || "fact"))).sort((a, b) => {
    const ia = KIND_ORDER.indexOf(a);
    const ib = KIND_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  const t2 = (guided?.tenant === tenant ? guided.questions : []).filter((g) => g.track === "T2");
  const personaName = (key: string) => tenantInfo?.personas.find((p) => p.key === key)?.name ?? key;

  return (
    <div className="space-y-4">
      <Card
        title="Memory: what the agents remember about you"
        actions={
          <>
            <Button size="sm" onClick={() => void load()} disabled={loading}>
              {loading ? <Spinner /> : "↻"} Refresh
            </Button>
            <Button size="sm" variant="danger" onClick={forgetAll} disabled={busy !== null || !overview?.exists}>
              {busy === "all" && <Spinner />} Forget everything
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-300">
          Your memory lives in its own FalkorDB graph,{" "}
          <code className="rounded bg-slate-950 px-1 text-emerald-300">{overview?.graph ?? "…"}</code>: one graph per tenant ×
          persona × browser visitor. Other visitors and other personas cannot see it.
        </p>
        <p className="mt-1 text-xs text-slate-500">
          Viewing as {personaInfo ? `${personaInfo.name} (${personaInfo.title})` : persona} · all data is synthetic demo data.
          Memories are PII-scrubbed and point at company entities by id only.
        </p>
        <div className="mt-3 space-y-2">
          <ErrorNote error={error} onRetry={() => void load()} />
          <ErrorNote error={actionError} />
        </div>
        {overview?.exists && (
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {countKeys.map((k) => (
              <Stat key={k} label={humanise(k)} value={counts[k] ?? 0} />
            ))}
            <Stat label="Active memories" value={activeFacts.length} sub={`${facts.length - activeFacts.length} superseded`} tone="green" />
            <Stat label="Preferences" value={prefs} tone="amber" />
          </div>
        )}
        {!overview && loading && (
          <div className="mt-3 flex items-center gap-2 text-sm text-slate-400">
            <Spinner /> Reading your memory graph…
          </div>
        )}
      </Card>

      {empty && (
        <Empty>
          <div className="space-y-3">
            <div className="text-slate-300">No memory yet for {personaInfo?.name ?? persona}.</div>
            <div>
              The graph is created lazily on your first conversation. Try a Track 2 question, then come back to see the
              sessions, facts and playbooks it produced.
            </div>
            {t2.length > 0 && (
              <div className="flex flex-wrap justify-center gap-2">
                {t2.map((g) => (
                  <Button key={g.id} size="sm" variant="primary" onClick={() => ask(g)} title={g.hint || g.question}>
                    {g.title}
                    {g.persona !== persona && <span className="opacity-70">· as {personaName(g.persona)}</span>}
                  </Button>
                ))}
              </div>
            )}
            {t2.length > 0 && t2.some((g) => g.persona !== persona) && (
              <div className="text-[11px]">Asking switches to the question&apos;s persona, which has its own memory graph.</div>
            )}
          </div>
        </Empty>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* -------------------------------------------------------------------------------- episodic */}
        <Card title="Sessions (episodic)" actions={<Badge tone="blue">{sessions.length}</Badge>}>
          <p className="mb-2 text-xs text-slate-500">Each conversation is a Session with its Turns; click one to replay it from the graph.</p>
          {overview?.exists && !sessions.length && <Empty>No sessions stored.</Empty>}
          <div className="space-y-2">
            {sessions.map((s) => (
              <div key={s.id} className="rounded-md border border-slate-800 bg-slate-950/40 p-2 hover:border-slate-700">
                <div className="flex items-start gap-2">
                  <button className="min-w-0 flex-1 text-left" onClick={() => setOpenSession(s.id)} title="Show turns">
                    <div className="truncate text-sm font-medium text-slate-100">{s.title || s.id}</div>
                    {s.summary && <div className="mt-0.5 line-clamp-3 text-xs text-slate-400">{s.summary}</div>}
                  </button>
                  <Button size="sm" variant="ghost" onClick={() => forgetSession(s)} disabled={busy !== null} title="Delete this session and its turns">
                    {busy === s.id ? <Spinner /> : "Forget"}
                  </Button>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                  <span>{fmtTs(s.started)}</span>
                  {s.updated && s.updated !== s.started && <span>→ {fmtTs(s.updated)}</span>}
                  <Badge>{s.turns ?? 0} turns</Badge>
                  {s.agent && <Badge tone="pink">{s.agent}</Badge>}
                  {(s.consolidated_n ?? 0) > 0 && (
                    <Badge tone="green" title="The Librarian distilled this session into long-term memories">
                      consolidated
                    </Badge>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* -------------------------------------------------------------------------------- semantic */}
        <Card title="Facts & preferences (semantic)" actions={<Badge tone="violet">{activeFacts.length} active</Badge>}>
          <p className="mb-2 text-xs text-slate-500">
            Facts are never overwritten: a newer fact sets <code>valid_to</code> on the old one (bitemporal).
          </p>
          {overview?.exists && !facts.length && <Empty>No memories distilled yet.</Empty>}
          <div className="space-y-3">
            {kinds.map((kind) => (
              <div key={kind}>
                <div className="mb-1 flex items-center gap-2">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{KIND_LABEL[kind] ?? humanise(kind)}</span>
                  <LabelBadge label="Fact" />
                </div>
                <div className="space-y-1.5">
                  {facts
                    .filter((f) => (f.kind || "fact") === kind)
                    .sort((a, b) => Number(isRetired(a)) - Number(isRetired(b)) || String(b.created ?? "").localeCompare(String(a.created ?? "")))
                    .map((f) => {
                      const retired = isRetired(f);
                      return (
                        <div key={f.id} className={cx("rounded-md border border-slate-800 p-2", retired ? "bg-slate-950/20 opacity-60" : "bg-slate-950/40")}>
                          <div className="flex items-start gap-2">
                            <div className={cx("min-w-0 flex-1 text-sm", retired ? "text-slate-500 line-through" : "text-slate-200")}>{f.text}</div>
                            {retired ? (
                              <Badge tone="slate" title={f.superseded_by ? `Superseded by ${f.superseded_by}` : `Valid until ${f.valid_to ?? ""}`}>
                                superseded
                              </Badge>
                            ) : (
                              <Button size="sm" variant="ghost" onClick={() => forgetFact(f)} disabled={busy !== null}>
                                {busy === f.id ? <Spinner /> : "Forget"}
                              </Button>
                            )}
                          </div>
                          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[11px] text-slate-500">
                            <span>{fmtTs(f.created)}</span>
                            {f.valid_to && <span>→ {fmtTs(f.valid_to)}</span>}
                            {f.by && <span>by {f.by}</span>}
                            {f.session && (
                              <button className="font-mono hover:text-slate-300" onClick={() => setOpenSession(String(f.session))} title="Open source session">
                                {f.session}
                              </button>
                            )}
                            {f.superseded_by && <span>replaced by {f.superseded_by}</span>}
                          </div>
                          <RefList value={f.about} />
                        </div>
                      );
                    })}
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* -------------------------------------------------------------------------------- procedural */}
        <Card title="Playbooks (procedural)" actions={playbooks ? <Badge tone="green">{playbooks.length}</Badge> : undefined}>
          <p className="mb-1 text-xs text-slate-500">
            The Librarian distils successful investigations into reusable playbooks; agents look them up with{" "}
            <code>find_playbook</code>.
          </p>
          <p className="mb-2 text-[11px] text-slate-500">
            Unlike your private memory, playbooks are tenant-wide: they live in the tenant brain graph as Playbook → Step chains
            and hold procedure only, no case data.
          </p>
          <ErrorNote error={pbError} onRetry={() => void load()} />
          {playbooks && !playbooks.length && <Empty>No playbooks learned yet for this tenant.</Empty>}
          <div className="space-y-2">
            {playbooks?.map((p, i) => (
              <PlaybookItem key={str(p.id) ?? i} p={p} />
            ))}
          </div>
        </Card>
      </div>

      {overview && (
        <Disclosure summary={`Cypher used to read your memory (${trace.length} queries)`}>
          {!trace.length && <div className="text-xs text-slate-500">No queries recorded.</div>}
          <div className="space-y-2">
            {trace.map((t, i) => {
              const name = str(t.name) ?? str(t.query) ?? `query ${i + 1}`;
              const note = str(t.note);
              const err = str(t.error);
              return (
                <div key={i} className="space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400">
                    <code className="text-slate-200">{name}</code>
                    {t.rows !== undefined && <Badge>{t.rows} rows</Badge>}
                    {t.ms !== undefined && <Badge>{t.ms} ms</Badge>}
                    {str(t.graph) && <span className="font-mono text-slate-500">{str(t.graph)}</span>}
                    {note && <Badge tone="amber">{note}</Badge>}
                    {err && <Badge tone="red">{err}</Badge>}
                  </div>
                  {str(t.cypher) && <CodeBlock code={String(t.cypher).trim()} />}
                  {t.params !== undefined && isObj(t.params) && Object.keys(t.params).length > 0 && (
                    <div className="font-mono text-[10px] text-slate-500">params: {show(t.params)}</div>
                  )}
                </div>
              );
            })}
          </div>
        </Disclosure>
      )}

      <SessionModal id={openSession} onClose={() => setOpenSession(null)} />
    </div>
  );
}