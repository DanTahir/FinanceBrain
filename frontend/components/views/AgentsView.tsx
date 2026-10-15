"use client";

// Agents view (Track 2): the agent roster, the shared task blackboard and the HANDED_OFF_TO timeline. Agents never
// talk to each other directly; they coordinate only through Task / Agent / Case state in the tenant graph.

import { useCallback, useEffect, useRef, useState } from "react";
import { API, ApiError, errorText, type ChatEvent } from "@/lib/api";
import { useApp } from "@/lib/state";
import { Badge, Button, Card, Empty, ErrorNote, Spinner, cx, fmtTs, labelColor, statusTone } from "@/components/ui";
import Markdown from "@/components/Markdown";

// ------------------------------------------------------------------------------------------------ roster (static)
// Model slugs mirror backend/config.py defaults (model_default / model_deep / model_fast).
const MODEL_DEFAULT = "anthropic/claude-sonnet-5.5";
const MODEL_DEEP = "anthropic/claude-opus-5.5";
const MODEL_FAST = "anthropic/claude-haiku-5.5";

type RosterAgent = { key: string; name: string; role: string; model: string };
const ROSTER: RosterAgent[] = [
  { key: "concierge", name: "Concierge", role: "Routes each request, loads your memory, manages handoffs", model: MODEL_DEFAULT },
  { key: "brain", name: "Brain", role: "Company Q&A with citations and an evidence subgraph", model: MODEL_DEFAULT },
  { key: "investigator", name: "Investigator", role: "Fraud, AML and information-barrier investigations; graph algorithms; proposes actions", model: MODEL_DEEP },
  { key: "compliance", name: "Compliance", role: "Deal-wall checks, SAR drafting, takes over cases", model: MODEL_DEFAULT },
  { key: "sentinel", name: "Sentinel", role: "Watches newly ingested live-feed data and opens cases", model: MODEL_FAST },
  { key: "librarian", name: "Librarian", role: "Consolidates finished sessions into memory facts and playbooks", model: MODEL_FAST },
];
const AGENT_NAMES: Record<string, string> = {
  ...Object.fromEntries(ROSTER.map((a) => [a.key, a.name])),
  comms: "Comms",
  external: "External MCP client",
  human: "Human reviewer",
};
/** Agents the backend can run from the UI (orchestrator.py SPECS). */
const RUNNABLE = new Set(["brain", "investigator", "compliance"]);

function modelLabel(slug: string): string {
  const s = (slug.split("/").pop() ?? slug).replace(/^claude-/, "");
  const [fam, ...rest] = s.split("-");
  if (!fam) return slug;
  return `${fam[0].toUpperCase()}${fam.slice(1)}${rest.length ? ` ${rest.join(".")}` : ""}`;
}
function modelTone(slug: string): "violet" | "blue" | "green" {
  return slug.includes("opus") ? "violet" : slug.includes("haiku") ? "green" : "blue";
}
function stripAgent(s?: string | null): string {
  return String(s ?? "").replace(/^agent_/, "");
}
function agentName(s?: string | null): string {
  const k = stripAgent(s);
  return AGENT_NAMES[k] ?? (k || "unknown");
}

// ------------------------------------------------------------------------------------------------ row helpers
type Row = Record<string, unknown>;
function str(r: Row, k: string): string | undefined {
  const v = r[k];
  if (typeof v === "string") return v || undefined;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return undefined;
}
const KNOWN_TASK_KEYS = new Set(["id", "title", "status", "case_id", "from_agent", "to_agent", "note", "created", "claimed", "done", "result"]);

function bucket(status: string): number {
  if (["open", "pending", "proposed"].includes(status)) return 0;
  if (["claimed", "in_progress"].includes(status)) return 1;
  return 2;
}
const GROUPS = ["Open: waiting for an agent", "Claimed: in progress", "Done & other"];

// ------------------------------------------------------------------------------------------------ live run state
type RunRow =
  | { kind: "tool"; id: string; name: string; done: boolean; ok?: boolean; ms?: number; summary?: string }
  | { kind: "note"; text: string; tone: "amber" | "slate" };
type RunState = { rows: RunRow[]; finals: { agent: string; text: string }[]; error: string | null; running: boolean };
const emptyRun = (): RunState => ({ rows: [], finals: [], error: null, running: false });

function opsText(ev: Row): string {
  const kind = str(ev, "kind") ?? "event";
  const parts = [`${agentName(str(ev, "agent"))}: ${kind.replace(/_/g, " ")}`];
  for (const k of ["title", "action", "case_id", "action_id", "task_id"]) {
    const v = str(ev, k);
    if (v) parts.push(v);
  }
  return parts.join(" · ");
}

// ------------------------------------------------------------------------------------------------ view
type BoardErr = { status: number; msg: string };

export default function AgentsView() {
  const { tenant, persona, who, tenantInfo, personaInfo, view, setView, version, bump, setPersona, ask } = useApp();
  const active = view === "agents";

  const [tasks, setTasks] = useState<Row[]>([]);
  const [handoffs, setHandoffs] = useState<Row[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<BoardErr | null>(null);
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [runningId, setRunningId] = useState<string | null>(null);
  const [guideBusy, setGuideBusy] = useState(false);
  const [guideErr, setGuideErr] = useState<string | null>(null);

  const seq = useRef(0);
  const stale = useRef(true);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (silent = false) => {
      const n = ++seq.current;
      if (!silent) setLoading(true);
      try {
        const r = await API.blackboard({ tenant, persona });
        if (n !== seq.current) return;
        setTasks(Array.isArray(r.tasks) ? r.tasks : []);
        setHandoffs(Array.isArray(r.handoffs) ? r.handoffs : []);
        setErr(null);
        setLoaded(true);
      } catch (e) {
        if (n !== seq.current) return;
        const status = e instanceof ApiError ? e.status : 0;
        setErr({ status, msg: errorText(e) });
        if (status === 403) {
          setTasks([]);
          setHandoffs([]);
          setLoaded(false);
        }
      } finally {
        if (n === seq.current) setLoading(false);
      }
    },
    [tenant, persona],
  );

  useEffect(() => {
    stale.current = true;
  }, [tenant, persona, version]);
  useEffect(() => {
    if (active && stale.current) {
      stale.current = false;
      void load();
    }
  }, [active, tenant, persona, version, load]);
  useEffect(() => {
    setRuns({});
  }, [tenant]);
  useEffect(() => {
    if (!active || runningId) return;
    const t = setInterval(() => void load(true), 15_000);
    return () => clearInterval(t);
  }, [active, runningId, load]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const patch = useCallback((id: string, fn: (r: RunState) => RunState) => {
    setRuns((p) => ({ ...p, [id]: fn(p[id] ?? emptyRun()) }));
  }, []);

  const handleEvent = useCallback(
    (taskId: string, ev: ChatEvent) => {
      switch (ev.type) {
        case "start":
          patch(taskId, (r) => ({ ...r, rows: [...r.rows, { kind: "note", tone: "slate", text: `${agentName(ev.agent)} agent started${ev.model ? ` on ${ev.model}` : ""}` }] }));
          break;
        case "tool_call":
          patch(taskId, (r) => ({ ...r, rows: [...r.rows, { kind: "tool", id: ev.id, name: ev.name, done: false }] }));
          break;
        case "tool_result":
          patch(taskId, (r) => {
            let found = false;
            const rows = r.rows.map((row) => {
              if (row.kind === "tool" && row.id === ev.id && !row.done) {
                found = true;
                return { ...row, done: true, ok: ev.ok, ms: ev.ms, summary: ev.summary };
              }
              return row;
            });
            if (!found) rows.push({ kind: "tool", id: ev.id, name: ev.name, done: true, ok: ev.ok, ms: ev.ms, summary: ev.summary });
            return { ...r, rows };
          });
          break;
        case "ops":
          patch(taskId, (r) => ({ ...r, rows: [...r.rows, { kind: "note", tone: "amber", text: opsText(ev as unknown as Row) }] }));
          break;
        case "handoff":
          patch(taskId, (r) => ({
            ...r,
            rows: [...r.rows, { kind: "note", tone: "amber", text: `Handoff ${agentName(ev.from)} → ${agentName(ev.to)}${ev.case_id ? ` · ${ev.case_id}` : ""}${ev.task_id ? ` · ${ev.task_id}` : ""}` }],
          }));
          break;
        case "final":
          patch(taskId, (r) => ({ ...r, finals: [...r.finals, { agent: ev.agent, text: ev.text ?? "" }] }));
          break;
        case "error":
          patch(taskId, (r) => ({ ...r, error: ev.message }));
          break;
        default:
          break;
      }
    },
    [patch],
  );

  const runTask = useCallback(
    async (taskId: string) => {
      if (runningId) return;
      const ac = new AbortController();
      abortRef.current = ac;
      setRunningId(taskId);
      setRuns((p) => ({ ...p, [taskId]: { ...emptyRun(), running: true } }));
      try {
        await API.runTask(who, taskId, (ev) => handleEvent(taskId, ev), ac.signal);
      } catch (e) {
        let msg = errorText(e);
        if (e instanceof ApiError && e.status === 403)
          msg = `Running blackboard tasks needs the 'act' scope, which ${personaInfo?.name ?? persona} does not have. ${e.message}`;
        else if (e instanceof ApiError && e.status === 409) msg = `This task was already claimed or completed by an agent (${e.message}). Refreshing the blackboard.`;
        patch(taskId, (r) => ({ ...r, error: msg }));
      } finally {
        patch(taskId, (r) => ({ ...r, running: false, rows: r.rows.map((row) => (row.kind === "tool" && !row.done ? { ...row, done: true } : row)) }));
        abortRef.current = null;
        setRunningId(null);
        void load(true);
        bump();
      }
    },
    [runningId, who, handleEvent, patch, personaInfo, persona, load, bump],
  );

  const askWire = useCallback(async () => {
    setGuideErr(null);
    setGuideBusy(true);
    try {
      const r = await API.guided(tenant);
      const qs = Array.isArray(r.questions) ? r.questions : [];
      const g = qs.find((q) => q.id === "h_wire") ?? qs.find((q) => q.track === "T1" && !q.cacheable);
      if (g) ask(g);
      else setGuideErr("No investigation question is defined for this tenant; ask the Investigator in Chat instead.");
    } catch (e) {
      setGuideErr(errorText(e));
    } finally {
      setGuideBusy(false);
    }
  }, [tenant, ask]);

  const groups: Row[][] = [[], [], []];
  for (const t of tasks) groups[bucket(str(t, "status") ?? "")].push(t);
  for (const g of groups) g.sort((a, b) => (str(b, "created") ?? "").localeCompare(str(a, "created") ?? ""));
  const timeline = [...handoffs].sort((a, b) => (str(b, "ts") ?? "").localeCompare(str(a, "ts") ?? ""));

  const switchable = (tenantInfo?.personas ?? []).filter((p) => ["aml_analyst", "compliance_officer"].includes(p.key) && p.key !== persona);
  const forbidden = err?.status === 403;
  const empty = loaded && !forbidden && tasks.length === 0 && handoffs.length === 0;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">Agents &amp; coordination</h2>
        <p className="text-sm text-slate-400">
          Multi-agent work through shared graph state: handoffs and tasks are nodes and edges in the tenant graph, visible to every agent, every session and you.
        </p>
      </div>

      <Card title="Agents">
        <p className="mb-3 text-xs text-slate-400">
          Every agent discovers and calls its tools through the FinanceBrain MCP server (<code className="text-slate-300">/mcp</code>); they coordinate only through the graph.
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {ROSTER.map((a) => (
            <div key={a.key} className="rounded-md border border-slate-800 bg-slate-950/40 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: labelColor("Agent") }} />
                  <span className="font-semibold text-slate-100">{a.name}</span>
                </div>
                <Badge tone={modelTone(a.model)} title={a.model}>
                  {modelLabel(a.model)}
                </Badge>
              </div>
              <p className="mt-1.5 text-xs text-slate-400">{a.role}</p>
              <div className="mt-1 font-mono text-[10px] text-slate-600">agent_{a.key}</div>
            </div>
          ))}
        </div>
      </Card>

      {forbidden ? (
        <Card title="Blackboard needs the act scope">
          <p className="text-sm text-slate-300">
            {personaInfo ? `${personaInfo.name} (${personaInfo.title})` : persona} cannot see the agent blackboard: tasks and handoffs belong to investigations, which require the{" "}
            <code className="text-slate-200">act</code> scope. The same ACL check applies to agents, the MCP server and this UI.
          </p>
          <p className="mt-1 text-xs text-slate-500">{err?.msg}</p>
          {switchable.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {switchable.map((p) => (
                <Button key={p.key} variant="primary" size="sm" onClick={() => setPersona(p.key)}>
                  Switch to {p.name} · {p.title}
                </Button>
              ))}
            </div>
          )}
        </Card>
      ) : (
        <>
          {err && <ErrorNote error={err.msg} onRetry={() => void load()} />}
          {!loaded && loading && (
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Spinner /> Loading the blackboard…
            </div>
          )}
          {empty ? (
            <Card title="Blackboard">
              <Empty>
                <div className="space-y-3">
                  <p>No tasks or handoffs yet.</p>
                  <p className="text-slate-400">
                    Run the &lsquo;Investigate the flagged $48k wire&rsquo; guided question: the Investigator opens a case and hands off to Compliance.
                  </p>
                  <Button variant="primary" size="sm" disabled={guideBusy} onClick={() => void askWire()}>
                    {guideBusy && <Spinner />} Ask the guided investigation question
                  </Button>
                  {guideErr && <ErrorNote error={guideErr} />}
                </div>
              </Empty>
            </Card>
          ) : loaded ? (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card
                title={`Blackboard tasks (${tasks.length})`}
                actions={
                  <Button size="sm" variant="ghost" disabled={loading} onClick={() => void load()}>
                    {loading ? <Spinner /> : "↻"} Refresh
                  </Button>
                }
              >
                <p className="mb-3 text-xs text-slate-400">
                  Task nodes posted by one agent and claimed by another. Open tasks can be run here: the assigned agent claims the task from the graph and streams its work.
                </p>
                {tasks.length === 0 ? (
                  <Empty>No tasks on the blackboard.</Empty>
                ) : (
                  <div className="space-y-4">
                    {groups.map((g, gi) =>
                      g.length === 0 ? null : (
                        <div key={gi}>
                          <div className="mb-1.5 text-[11px] uppercase tracking-wide text-slate-500">
                            {GROUPS[gi]} · {g.length}
                          </div>
                          <div className="space-y-2">
                            {g.map((t) => {
                              const id = str(t, "id") ?? "";
                              return (
                                <TaskCard
                                  key={id}
                                  task={t}
                                  run={runs[id]}
                                  busy={runningId !== null}
                                  onRun={() => void runTask(id)}
                                  onStop={() => abortRef.current?.abort()}
                                  onCase={() => setView("investigations")}
                                />
                              );
                            })}
                          </div>
                        </div>
                      ),
                    )}
                  </div>
                )}
              </Card>

              <Card title={`Handoff timeline (${timeline.length})`}>
                <p className="mb-3 text-xs text-slate-400">
                  Stored as (:Agent)-[:HANDED_OFF_TO {"{note, ts}"}]-&gt;(:Agent) plus Task nodes the receiving agent claims — shared state any agent (or a later session) can pick up.
                </p>
                {timeline.length === 0 ? (
                  <Empty>No handoffs yet.</Empty>
                ) : (
                  <ol className="relative ml-2 space-y-4 border-l border-slate-800 pl-4">
                    {timeline.map((h, i) => {
                      const note = str(h, "note");
                      const caseId = str(h, "case_id");
                      const taskId = str(h, "task_id");
                      return (
                        <li key={`${str(h, "ts") ?? ""}-${taskId ?? i}`} className="relative">
                          <span className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border border-slate-900" style={{ background: labelColor("Agent") }} />
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium text-slate-100">
                              {agentName(str(h, "from"))} <span className="text-amber-300">→</span> {agentName(str(h, "to"))}
                            </span>
                            <span className="text-xs text-slate-500">{fmtTs(str(h, "ts"))}</span>
                          </div>
                          {note && (
                            <div className="mt-1 text-xs text-slate-300">
                              <Markdown text={note} />
                            </div>
                          )}
                          <div className="mt-1 flex flex-wrap items-center gap-1.5">
                            {taskId && <Badge>{taskId}</Badge>}
                            {caseId && (
                              <Button size="sm" variant="ghost" className="!px-1.5 !py-0.5 text-blue-300" onClick={() => setView("investigations")} title="Open in Investigations">
                                {caseId} ↗
                              </Button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </Card>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ task card
function TaskCard({ task, run, busy, onRun, onStop, onCase }: { task: Row; run?: RunState; busy: boolean; onRun: () => void; onStop: () => void; onCase: () => void }) {
  const id = str(task, "id") ?? "";
  const status = str(task, "status") ?? "unknown";
  const to = stripAgent(str(task, "to_agent"));
  const from = str(task, "from_agent");
  const caseId = str(task, "case_id");
  const note = str(task, "note");
  const result = str(task, "result");
  const open = bucket(status) === 0;
  const canRun = open && !!to && RUNNABLE.has(to);
  const extras = Object.entries(task).filter(
    ([k, v]) => !KNOWN_TASK_KEYS.has(k) && (typeof v === "string" || typeof v === "number" || typeof v === "boolean") && String(v) !== "",
  );

  return (
    <div className={cx("rounded-md border bg-slate-950/40 p-2.5", run?.running ? "border-blue-500/50" : "border-slate-800")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-slate-100">{str(task, "title") ?? id}</div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-600">{id}</div>
        </div>
        <Badge tone={statusTone(status)}>{status}</Badge>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
        {from && <span>from {agentName(from)}</span>}
        {to && (
          <span>
            → <span className="text-slate-200">{agentName(to)}</span>
          </span>
        )}
        {caseId && (
          <Button size="sm" variant="ghost" className="!px-1.5 !py-0.5 text-blue-300" onClick={onCase} title="Open in Investigations">
            {caseId} ↗
          </Button>
        )}
        {str(task, "created") && <span className="text-slate-500">created {fmtTs(str(task, "created"))}</span>}
        {str(task, "claimed") && <span className="text-slate-500">claimed {fmtTs(str(task, "claimed"))}</span>}
        {str(task, "done") && <span className="text-slate-500">done {fmtTs(str(task, "done"))}</span>}
      </div>
      {note && (
        <div className="mt-1.5 rounded bg-slate-900/70 px-2 py-1 text-xs text-slate-300">
          <Markdown text={note} />
        </div>
      )}
      {result && (
        <div className="mt-1.5 rounded border border-emerald-500/20 bg-emerald-500/5 px-2 py-1 text-xs text-slate-300">
          <Markdown text={result} />
        </div>
      )}
      {extras.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-500">
          {extras.map(([k, v]) => (
            <span key={k}>
              {k}: <span className="text-slate-400">{String(v)}</span>
            </span>
          ))}
        </div>
      )}
      {open && (
        <div className="mt-2 flex items-center gap-2">
          {canRun ? (
            run?.running ? (
              <Button size="sm" variant="danger" onClick={onStop}>
                Stop
              </Button>
            ) : (
              <Button size="sm" variant="primary" disabled={busy} onClick={onRun} title={busy ? "Another agent run is in progress" : `Run the ${agentName(to)} agent on this task`}>
                Run {agentName(to)}
              </Button>
            )
          ) : (
            to && <span className="text-[11px] text-slate-500">Assigned to {agentName(to)}, which picks it up on its own (not runnable from the UI).</span>
          )}
        </div>
      )}
      {run && <RunPanel run={run} />}
    </div>
  );
}

function RunPanel({ run }: { run: RunState }) {
  if (!run.running && !run.rows.length && !run.finals.length && !run.error) return null;
  return (
    <div className="mt-2 space-y-1 border-t border-slate-800 pt-2">
      {run.rows.map((row, i) =>
        row.kind === "tool" ? (
          <div key={`${row.id}-${i}`} className="flex items-start gap-2 text-xs">
            <span className="mt-0.5 w-3.5 shrink-0">{row.done ? <span className={row.ok === false ? "text-rose-400" : "text-emerald-400"}>{row.ok === false ? "✕" : "✓"}</span> : <Spinner className="h-3 w-3" />}</span>
            <code className="shrink-0 text-cyan-300">{row.name}</code>
            <span className="min-w-0 flex-1 truncate text-slate-400" title={row.summary}>
              {row.summary ?? (row.done ? "" : "running…")}
            </span>
            {row.ms !== undefined && <span className="shrink-0 tabular-nums text-slate-500">{Math.round(row.ms)} ms</span>}
          </div>
        ) : (
          <div key={i} className={cx("rounded px-2 py-0.5 text-xs", row.tone === "amber" ? "bg-amber-500/10 text-amber-200" : "text-slate-500")}>
            {row.text}
          </div>
        ),
      )}
      {run.running && (
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <Spinner className="h-3 w-3" /> agent working…
        </div>
      )}
      {run.finals.map((f, i) => (
        <div key={i} className="rounded border border-slate-800 bg-slate-900/70 p-2 text-sm text-slate-200">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">{agentName(f.agent)} · final answer</div>
          <Markdown text={f.text} />
        </div>
      ))}
      {run.error && <ErrorNote error={run.error} />}
    </div>
  );
}