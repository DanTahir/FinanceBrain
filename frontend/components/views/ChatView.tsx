"use client";

// Chat with the company brain: guided questions on the right, a streamed, fully explained conversation on the left
// (routing, tool calls with their Cypher, handoffs, case/action side effects, cited answer and evidence subgraph).

import { useEffect, useMemo, useRef, useState } from "react";
import { API, ApiError, errorText, type ChatEvent, type Guided, type TraceItem } from "@/lib/api";
import { useApp } from "@/lib/state";
import { Badge, Button, Card, CodeBlock, Disclosure, Empty, ErrorNote, Spinner, cx, fmtTs } from "@/components/ui";
import GraphCanvas from "@/components/GraphCanvas";
import Markdown from "@/components/Markdown";

// ------------------------------------------------------------------------------------------------ types + helpers
type Turn = { id: string; question: string; agentChoice: string; events: ChatEvent[]; error?: string; status?: number; done: boolean };

type StartEv = Extract<ChatEvent, { type: "start" }>;
type CallEv = Extract<ChatEvent, { type: "tool_call" }>;
type ResultEv = Extract<ChatEvent, { type: "tool_result" }>;
type OpsEv = Extract<ChatEvent, { type: "ops" }>;
type FinalEv = Extract<ChatEvent, { type: "final" }>;
type HandoffEv = Extract<ChatEvent, { type: "handoff" }>;

const AGENTS: { id: string; label: string }[] = [
  { id: "auto", label: "Auto (Concierge routes)" },
  { id: "brain", label: "Brain (Q&A)" },
  { id: "investigator", label: "Investigator" },
  { id: "compliance", label: "Compliance" },
];

const TRACKS: { track: Guided["track"]; title: string }[] = [
  { track: "T3", title: "Company brain (Track 3)" },
  { track: "T1", title: "Agentic reasoning & actions (Track 1)" },
  { track: "T2", title: "Memory & coordination (Track 2)" },
];

function cap(s?: string | null): string {
  if (!s) return "";
  return s.charAt(0).toUpperCase() + s.slice(1);
}
function toJson(v: unknown, space?: number): string {
  try {
    return JSON.stringify(v, null, space) ?? String(v);
  } catch {
    return String(v);
  }
}
function trunc(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
function humanKind(k: string): string {
  return cap(k.replace(/_/g, " "));
}
function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}
function finalText(t: Turn): string | null {
  for (let i = t.events.length - 1; i >= 0; i--) {
    const ev = t.events[i];
    if (ev.type === "final") return ev.text;
  }
  return null;
}
function textsOf(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  const out: string[] = [];
  for (const x of v) {
    if (typeof x === "string") out.push(x);
    else if (x && typeof x === "object") {
      const o = x as Record<string, unknown>;
      const s = [o.text, o.summary, o.title, o.name].find((y) => typeof y === "string" && y);
      if (typeof s === "string") out.push(s);
    }
  }
  return out;
}

type Seg = { start: StartEv | null; events: ChatEvent[] };
type Block = { k: "ev"; ev: ChatEvent } | { k: "seg"; seg: Seg };

function buildBlocks(events: ChatEvent[]): { blocks: Block[]; step: number } {
  const blocks: Block[] = [];
  let cur: Seg | null = null;
  let step = 0;
  for (const ev of events) {
    switch (ev.type) {
      case "start":
        cur = { start: ev, events: [] };
        blocks.push({ k: "seg", seg: cur });
        break;
      case "step":
        step = ev.n;
        break;
      case "tool_call":
      case "tool_result":
      case "ops":
      case "final":
        if (!cur) {
          cur = { start: null, events: [] };
          blocks.push({ k: "seg", seg: cur });
        }
        cur.events.push(ev);
        break;
      case "handoff":
        blocks.push({ k: "ev", ev });
        cur = null;
        break;
      case "done":
        break;
      case "session":
        if (ev.cached) blocks.push({ k: "ev", ev });
        break;
      default:
        blocks.push({ k: "ev", ev });
    }
  }
  return { blocks, step };
}

// ------------------------------------------------------------------------------------------------ view
export default function ChatView() {
  const { tenant, persona, tenantInfo, personaInfo, view, bump, ask, pending, clearPending, setPersona } = useApp();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [input, setInput] = useState("");
  const [agent, setAgent] = useState("auto");
  const [memory, setMemory] = useState(true);
  const [guided, setGuided] = useState<Guided[]>([]);
  const [guidedErr, setGuidedErr] = useState<string | null>(null);
  const [guidedLoading, setGuidedLoading] = useState(false);
  const guidedFor = useRef<string | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);
  const turnsRef = useRef<Turn[]>([]);
  const sidRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    turnsRef.current = turns;
  }, [turns]);

  // guided questions: fetched when the view is shown, refetched on tenant change
  const loadGuided = (t: string) => {
    guidedFor.current = t;
    setGuidedLoading(true);
    setGuidedErr(null);
    API.guided(t)
      .then((r) => {
        if (guidedFor.current === t) setGuided(Array.isArray(r.questions) ? r.questions : []);
      })
      .catch((e) => {
        if (guidedFor.current === t) setGuidedErr(errorText(e));
      })
      .finally(() => {
        if (guidedFor.current === t) setGuidedLoading(false);
      });
  };
  useEffect(() => {
    if (view === "chat" && guidedFor.current !== tenant) loadGuided(tenant);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, tenant]);

  const resetConversation = () => {
    ctrlRef.current?.abort();
    ctrlRef.current = null;
    setStreaming(false);
    setTurns([]);
    turnsRef.current = [];
    setSessionId(null);
    sidRef.current = null;
  };

  // a new identity means a new conversation (different ACLs, different memory graph)
  useEffect(() => {
    resetConversation();
  }, [tenant, persona]);

  async function send(q: string, agentChoice: string, fresh: boolean) {
    const text = q.trim().slice(0, 4000);
    if (!text || ctrlRef.current) return;
    const id = `turn_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const prev = fresh ? [] : turnsRef.current;
    const history = prev
      .flatMap((t) => {
        const a = finalText(t);
        return a ? [{ role: "user" as const, content: t.question }, { role: "assistant" as const, content: a }] : [];
      })
      .slice(-8);
    const sid = fresh ? null : sidRef.current;
    if (fresh) {
      setSessionId(null);
      sidRef.current = null;
    }
    const turn: Turn = { id, question: text, agentChoice, events: [], done: false };
    setTurns((p) => (fresh ? [turn] : [...p, turn]));
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setStreaming(true);
    const patch = (f: (t: Turn) => Turn) => setTurns((p) => p.map((t) => (t.id === id ? f(t) : t)));
    try {
      await API.chat(
        { tenant, persona, message: text, agent: agentChoice, memory, session_id: sid, history },
        (ev) => {
          if (ev.type === "session" && ev.session_id && ctrlRef.current === ctrl) {
            setSessionId(ev.session_id);
            sidRef.current = ev.session_id;
          }
          patch((t) => ({ ...t, events: [...t.events, ev] }));
        },
        ctrl.signal,
      );
      patch((t) => ({ ...t, done: true }));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : undefined;
      const msg =
        status === 403
          ? `This persona lacks a scope needed for that request (${errorText(e)}). Switch persona to one that is allowed.`
          : errorText(e);
      patch((t) => ({ ...t, done: true, error: msg, status }));
    } finally {
      if (ctrlRef.current === ctrl) {
        ctrlRef.current = null;
        setStreaming(false);
      }
      bump();
    }
  }

  // guided question hand-off from ask(): wait until persona/tenant match and nothing is streaming
  useEffect(() => {
    if (pending && pending.tenant === tenant && pending.persona === persona && !ctrlRef.current) {
      const g = pending;
      clearPending();
      void send(g.question, g.agent || "auto", true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, tenant, persona, streaming]);

  useEffect(() => {
    if (streaming && view === "chat") bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns, streaming, view]);

  const submit = () => {
    if (!input.trim() || streaming) return;
    const q = input;
    setInput("");
    void send(q, agent, false);
  };

  const grouped = useMemo(
    () => TRACKS.map((t) => ({ ...t, items: guided.filter((g) => g.track === t.track) })).filter((t) => t.items.length),
    [guided],
  );

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
      {/* ------------------------------------------------ conversation */}
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Chat with the company brain</h2>
            <p className="text-xs text-slate-400">Every answer is grounded in graph queries you can inspect below, and cited to source documents.</p>
          </div>
          <div className="flex items-center gap-2">
            {sessionId && (
              <Badge tone="violet" title="Episodic memory session in your memory graph">
                session {sessionId.slice(0, 18)}
              </Badge>
            )}
            <Button size="sm" onClick={resetConversation} disabled={!turns.length && !streaming}>
              New conversation
            </Button>
          </div>
        </div>

        {turns.length === 0 ? (
          <Card title="Welcome to FinanceBrain">
            <div className="space-y-2 text-sm text-slate-300">
              <p>
                Answers come from a FalkorDB knowledge graph built from synthetic email, Slack, tickets, documents, meetings, CRM and the
                ledger. Personal data is pseudonymized before ingestion, so neither the graph nor the model ever sees raw PII.
              </p>
              <p>Every answer is cited: click a source to open the redacted document, or an entity id to explore it in the graph.</p>
              <p>
                You are{" "}
                <span className="font-semibold text-slate-100">
                  {personaInfo ? `${personaInfo.name}, ${personaInfo.title}` : persona}
                </span>{" "}
                and only see what that person may see. Switch persona at the top to test the information barrier.
              </p>
              <p className="text-slate-400">Pick a question on the right to start, or type your own below.</p>
            </div>
          </Card>
        ) : (
          <div className="flex flex-col gap-4">
            {turns.map((t) => (
              <TurnView key={t.id} turn={t} onPersona={setPersona} personas={tenantInfo?.personas ?? []} current={persona} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />

        <Card>
          <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-slate-300">
            <label className="flex items-center gap-1.5">
              Agent
              <select
                className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200"
                value={agent}
                onChange={(e) => setAgent(e.target.value)}
              >
                {AGENTS.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5" title="Recall preferences, facts and past sessions from your private memory graph">
              <input type="checkbox" checked={memory} onChange={(e) => setMemory(e.target.checked)} />
              Use my memory
            </label>
            <span className="text-slate-500">Enter to send · Shift+Enter for a new line</span>
          </div>
          <div className="flex items-end gap-2">
            <textarea
              className="min-h-[64px] flex-1 resize-y rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
              placeholder="Ask about clients, transactions, policies, decisions…"
              maxLength={4000}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <div className="flex flex-col gap-2">
              {streaming ? (
                <Button variant="danger" onClick={() => ctrlRef.current?.abort()}>
                  Stop
                </Button>
              ) : (
                <Button variant="primary" onClick={submit} disabled={!input.trim()}>
                  Send
                </Button>
              )}
            </div>
          </div>
        </Card>
      </div>

      {/* ------------------------------------------------ guided questions */}
      <aside className="flex min-w-0 flex-col gap-3">
        <Card
          title="Try these"
          actions={guidedLoading ? <Spinner /> : undefined}
        >
          <p className="mb-3 text-xs text-slate-400">Curated questions per hackathon track; each asks as the persona allowed to see the answer.</p>
          <ErrorNote error={guidedErr} onRetry={() => loadGuided(tenant)} />
          {!guidedLoading && !guidedErr && grouped.length === 0 && <Empty>No guided questions for this tenant.</Empty>}
          <div className="flex flex-col gap-4">
            {grouped.map((grp) => (
              <div key={grp.track}>
                <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{grp.title}</h4>
                <div className="flex flex-col gap-2">
                  {grp.items.map((g) => {
                    const p = tenantInfo?.personas.find((x) => x.key === g.persona);
                    return (
                      <button
                        key={g.id}
                        className="rounded-md border border-slate-800 bg-slate-950/40 p-2.5 text-left transition-colors hover:border-blue-500/50 hover:bg-slate-800/50 disabled:opacity-50"
                        onClick={() => ask(g)}
                        disabled={streaming}
                        title={streaming ? "Wait for the current answer to finish" : "Ask this question"}
                      >
                        <div className="text-sm font-semibold text-slate-100">{g.title}</div>
                        <div className="mt-1 text-xs text-slate-300">{g.question}</div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400">
                          <span>
                            asks as {p?.name ?? g.persona}
                            {p?.label ? ` · ${p.label}` : ""}
                          </span>
                          {g.cacheable ? <Badge tone="green">instant (cached)</Badge> : <Badge>runs live</Badge>}
                        </div>
                        {g.hint && <div className="mt-1 text-[11px] text-slate-400">{g.hint}</div>}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </aside>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ one turn
function TurnView({
  turn,
  personas,
  current,
  onPersona,
}: {
  turn: Turn;
  personas: { key: string; name: string; label: string }[];
  current: string;
  onPersona: (p: string) => void;
}) {
  const { blocks, step } = useMemo(() => buildBlocks(turn.events), [turn.events]);
  const segs = blocks.filter((b): b is { k: "seg"; seg: Seg } => b.k === "seg");
  const last = segs[segs.length - 1];
  const hasFinal = !!last && last.seg.events.some((e) => e.type === "final");

  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-100">
          {turn.question}
          {turn.agentChoice !== "auto" && (
            <div className="mt-1 text-right">
              <Badge tone="blue">{turn.agentChoice}</Badge>
            </div>
          )}
        </div>
      </div>
      <div className="flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        {blocks.map((b, i) => (b.k === "seg" ? <SegmentView key={i} seg={b.seg} /> : <EventNote key={i} ev={b.ev} />))}
        {!turn.done && !hasFinal && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Spinner /> thinking…{step ? ` step ${step}` : ""}
          </div>
        )}
        {!turn.done && hasFinal && (
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <Spinner className="h-3 w-3" /> finishing (memory write / consolidation)…
          </div>
        )}
        {turn.error && (
          <div className="flex flex-col gap-2">
            <ErrorNote error={turn.error} />
            {turn.status === 403 && personas.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {personas
                  .filter((p) => p.key !== current)
                  .map((p) => (
                    <Button key={p.key} size="sm" onClick={() => onPersona(p.key)}>
                      Switch to {p.name} · {p.label}
                    </Button>
                  ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ standalone events
function EventNote({ ev }: { ev: ChatEvent }) {
  const { setView } = useApp();
  switch (ev.type) {
    case "session":
      return (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-200">
          {ev.note || "Cached answer — identical guided question for this persona and live-feed position; no model calls."}
        </div>
      );
    case "pii_masked":
      return (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-200">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold">PII masked:</span>
            {Object.entries(ev.counts ?? {}).map(([k, n]) => (
              <Badge key={k} tone="red">
                {k} ×{n}
              </Badge>
            ))}
          </div>
          {ev.note && <div className="mt-1">{ev.note}</div>}
        </div>
      );
    case "memory_context":
      return <MemoryContextNote ev={ev as Record<string, unknown>} />;
    case "route":
      return (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge tone="violet">Concierge → {ev.agent}</Badge>
          {str(ev.reason) && <span className="text-slate-400">{ev.reason}</span>}
        </div>
      );
    case "handoff":
      return <HandoffRow ev={ev} onOpen={() => setView("investigations")} />;
    case "memory": {
      if (ev.error) return <div className="text-[11px] text-slate-500">Memory: {ev.error}</div>;
      const nums = Object.entries(ev).filter(([k, v]) => k !== "type" && typeof v === "number");
      return (
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
          <span>Librarian consolidated this session into memory</span>
          {nums.map(([k, v]) => (
            <Badge key={k}>
              {k.replace(/_/g, " ")}: {String(v)}
            </Badge>
          ))}
        </div>
      );
    }
    case "error":
      return <ErrorNote error={ev.message} />;
    default:
      return null;
  }
}

function MemoryContextNote({ ev }: { ev: Record<string, unknown> }) {
  const chips: [string, string][] = [];
  const lists: [string, string[]][] = [];
  for (const [k, v] of Object.entries(ev)) {
    if (k === "type") continue;
    if (typeof v === "number" || typeof v === "boolean") chips.push([k, String(v)]);
    else if (typeof v === "string" && v.length <= 60) chips.push([k, v]);
    else if (Array.isArray(v)) {
      const t = textsOf(v);
      if (t.length) lists.push([k, t]);
      else chips.push([k, String(v.length)]);
    }
  }
  return (
    <div className="rounded-md border border-violet-500/30 bg-violet-500/10 px-3 py-1.5 text-xs text-violet-200">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold">Recalled from your memory graph</span>
        {chips.map(([k, v]) => (
          <Badge key={k} tone="violet">
            {k.replace(/_/g, " ")}: {trunc(v, 40)}
          </Badge>
        ))}
      </div>
      {lists.map(([k, items]) => (
        <div key={k} className="mt-1">
          <div className="text-[11px] uppercase tracking-wide text-violet-300/70">{k.replace(/_/g, " ")}</div>
          <ul className="ml-4 list-disc">
            {items.slice(0, 6).map((s, i) => (
              <li key={i}>{trunc(s, 200)}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function HandoffRow({ ev, onOpen }: { ev: HandoffEv; onOpen: () => void }) {
  return (
    <div className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">
          Handoff: {cap(ev.from)} → {cap(ev.to)}
        </span>
        {ev.task_id && <Badge tone="cyan">{ev.task_id}</Badge>}
        {ev.case_id && <Badge tone="red">{ev.case_id}</Badge>}
        <Button size="sm" variant="ghost" onClick={onOpen}>
          Open Investigations
        </Button>
      </div>
      {ev.note && <div className="mt-1 text-cyan-200/90">{ev.note}</div>}
      <div className="mt-1 text-[11px] text-cyan-300/60">
        Written to the graph as (:Agent)-[:HANDED_OFF_TO]-&gt;(:Agent) and a Task on the blackboard.
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ agent segment
function SegmentView({ seg }: { seg: Seg }) {
  const { setView } = useApp();
  const calls = seg.events.filter((e): e is CallEv => e.type === "tool_call");
  const results = new Map<string, ResultEv>();
  for (const e of seg.events) if (e.type === "tool_result") results.set(e.id, e);
  // results with no matching call (defensive) still get a row
  const orphanResults = [...results.values()].filter((r) => !calls.some((c) => c.id === r.id));
  const ops = seg.events.filter((e): e is OpsEv => e.type === "ops");
  const finals = seg.events.filter((e): e is FinalEv => e.type === "final");

  return (
    <div className="flex flex-col gap-2">
      {seg.start && (
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-100">{cap(seg.start.agent)}</span>
          {seg.start.model && <Badge>{seg.start.model}</Badge>}
        </div>
      )}
      {calls.length + orphanResults.length > 0 && (
        <div className="flex flex-col gap-1">
          <div className="text-[11px] uppercase tracking-wide text-slate-500">
            Tool &amp; Cypher trace ({calls.length + orphanResults.length} calls)
          </div>
          {calls.map((c) => (
            <ToolRow key={c.id} name={c.name} args={c.args} result={results.get(c.id)} />
          ))}
          {orphanResults.map((r) => (
            <ToolRow key={r.id} name={r.name} args={r.args} result={r} />
          ))}
        </div>
      )}
      {ops.map((o, i) => (
        <OpsRow key={i} ev={o} onOpen={() => setView("investigations")} />
      ))}
      {finals.map((f, i) => (
        <FinalView key={i} ev={f} />
      ))}
    </div>
  );
}

function ToolRow({ name, args, result }: { name: string; args: unknown; result?: ResultEv }) {
  const trace: TraceItem[] = Array.isArray(result?.trace) ? result.trace : [];
  return (
    <Disclosure
      summary={
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] text-blue-300">{name}</span>
          <span className="min-w-0 truncate font-mono text-[11px] text-slate-500">{trunc(toJson(args), 120)}</span>
          {!result ? (
            <Spinner className="h-3 w-3" />
          ) : (
            <>
              <Badge tone={result.ok ? "green" : "red"}>{result.ok ? "ok" : "error"}</Badge>
              <span className="text-[11px] tabular-nums text-slate-500">{result.ms} ms</span>
              {result.summary && <span className="text-[11px] text-slate-400">{trunc(result.summary, 160)}</span>}
            </>
          )}
        </div>
      }
    >
      <div className="flex flex-col gap-2">
        <div>
          <div className="mb-1 text-[11px] text-slate-500">Arguments</div>
          <CodeBlock code={toJson(args, 2)} />
        </div>
        {result?.summary && <div className="text-xs text-slate-300">{result.summary}</div>}
        {trace.length === 0 && result && <div className="text-[11px] text-slate-500">No graph queries recorded for this call.</div>}
        {trace.map((t, i) => {
          const hasParams =
            t.params !== undefined && t.params !== null && !(typeof t.params === "object" && Object.keys(t.params as object).length === 0);
          return (
            <div key={i} className="rounded border border-slate-800 p-2">
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                <span className="font-mono text-slate-200">{t.name ?? `query ${i + 1}`}</span>
                {typeof t.rows === "number" && <Badge>{t.rows} rows</Badge>}
                {typeof t.ms === "number" && <span className="tabular-nums">{t.ms} ms</span>}
                {str(t.graph) && <span className="font-mono text-slate-500">{t.graph}</span>}
              </div>
              {t.cypher && <CodeBlock code={t.cypher} />}
              {hasParams && <CodeBlock className="mt-1 text-slate-300" code={toJson(t.params, 2)} />}
            </div>
          );
        })}
      </div>
    </Disclosure>
  );
}

function OpsRow({ ev, onOpen }: { ev: OpsEv; onOpen: () => void }) {
  const extra = ["title", "status", "note", "to_agent"].map((k) => [k, str(ev[k])] as const).filter(([, v]) => v);
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-100">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{humanKind(String(ev.kind ?? "operation"))}</span>
        {ev.case_id && <Badge tone="red">{ev.case_id}</Badge>}
        {ev.action_id && <Badge tone="cyan">{ev.action_id}</Badge>}
        {ev.task_id && <Badge tone="green">{ev.task_id}</Badge>}
        <Button size="sm" variant="ghost" onClick={onOpen}>
          Open Investigations
        </Button>
      </div>
      {extra.length > 0 && (
        <div className="mt-1 text-amber-200/80">
          {extra.map(([k, v]) => (
            <span key={k} className="mr-3">
              {k.replace(/_/g, " ")}: {trunc(v as string, 160)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function FinalView({ ev }: { ev: FinalEv }) {
  const { openDoc, openEntity } = useApp();
  const [showGraph, setShowGraph] = useState(false);
  const citations = Array.isArray(ev.citations) ? ev.citations : [];
  const nums = useMemo(() => {
    const m: Record<string, number> = {};
    for (const c of citations) m[c.id] = c.n;
    return m;
  }, [citations]);
  const cited = useMemo(() => citations.map((c) => c.id), [citations]);
  const nodes = Array.isArray(ev.evidence?.nodes) ? ev.evidence.nodes : [];
  const edges = Array.isArray(ev.evidence?.edges) ? ev.evidence.edges : [];
  const unverified = Array.isArray(ev.unverified_citations) ? ev.unverified_citations : [];
  const cost = typeof ev.cost_usd === "number" ? ev.cost_usd : typeof ev.usage?.cost_usd === "number" ? ev.usage.cost_usd : undefined;
  const pt = ev.usage?.prompt_tokens;
  const ct = ev.usage?.completion_tokens;
  const footer = [
    cap(ev.agent),
    typeof ev.steps === "number" ? `${ev.steps} steps` : null,
    typeof ev.ms === "number" ? `${(ev.ms / 1000).toFixed(1)} s` : null,
    cost !== undefined ? `$${cost.toFixed(4)}` : null,
    typeof pt === "number" || typeof ct === "number" ? `${pt ?? 0} in / ${ct ?? 0} out tokens` : null,
  ].filter(Boolean);

  return (
    <div className="flex flex-col gap-2 border-t border-slate-800 pt-2">
      <div className="text-sm text-slate-200">
        <Markdown text={ev.text ?? ""} citationNumbers={nums} />
      </div>
      {citations.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">Sources</div>
          <ul className="flex flex-col gap-0.5">
            {citations.map((c) => {
              const who = c.title || [c.author, c.channel].filter(Boolean).join(" / ");
              return (
                <li key={`${c.n}-${c.id}`}>
                  <button
                    className="w-full truncate text-left text-xs text-slate-300 hover:text-blue-300"
                    onClick={() => openDoc(c.id)}
                    title={c.snippet ?? `Open ${c.id}`}
                  >
                    <span className="text-slate-500">[{c.n}]</span> <span className="font-mono">{c.id}</span>
                    {c.kind ? ` · ${c.kind}` : ""}
                    {who ? ` · ${who}` : ""}
                    {c.ts ? ` · ${fmtTs(c.ts)}` : ""}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {unverified.length > 0 && (
        <div className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200">
          Cited but not retrieved by a tool: {unverified.join(", ")}
        </div>
      )}
      {nodes.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div>
            <Button size="sm" onClick={() => setShowGraph((s) => !s)}>
              {showGraph ? "Hide" : "Show"} evidence subgraph ({nodes.length} nodes, {edges.length} edges)
            </Button>
          </div>
          {showGraph && (
            <>
              <GraphCanvas
                nodes={nodes}
                edges={edges}
                highlight={cited}
                className="h-[320px] w-full rounded-lg border border-slate-800 bg-[#060912]"
                onSelect={(n) => {
                  if (n && n.label === "Document") openDoc(n.id);
                }}
                onExpand={(n) => openEntity(n.id)}
              />
              <p className="text-[11px] text-slate-500">
                Nodes and edges the agent&apos;s tools touched; cited documents highlighted. Double-click to explore.
              </p>
            </>
          )}
        </div>
      )}
      {footer.length > 0 && <div className={cx("text-[11px] text-slate-500")}>{footer.join(" · ")}</div>}
    </div>
  );
}