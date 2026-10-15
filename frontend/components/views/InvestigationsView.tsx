"use client";

// Investigations: the case board agents write into the graph (Case, Action, Task nodes) and the human-in-the-loop
// approval step. Agents can only propose actions; a persona with the `approve` scope decides, and the decision plus
// its effect (frozen account, SAR reference, restricted channel) is written back to the tenant graph.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, errorText, type CaseSummary, type EvEdge, type EvNode, type Guided } from "@/lib/api";
import { useApp } from "@/lib/state";
import {
  Badge,
  Button,
  Card,
  Disclosure,
  Empty,
  ErrorNote,
  LabelBadge,
  Spinner,
  cx,
  fmtTs,
  severityTone,
  statusTone,
} from "@/components/ui";
import GraphCanvas from "@/components/GraphCanvas";
import Markdown from "@/components/Markdown";

// ------------------------------------------------------------------------------------------------ defensive readers
type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
function str(v: unknown): string | undefined {
  if (typeof v === "string") return v || undefined;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return undefined;
}
function arr(v: unknown): Obj[] {
  return Array.isArray(v) ? v.filter(isObj) : [];
}
function strs(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => str(x)).filter((x): x is string => !!x) : [];
}
function agentName(v: unknown): string {
  const s = str(v) ?? "";
  return s.replace(/^agent_/, "") || "unknown";
}
function humanise(kind?: string): string {
  if (!kind) return "Action";
  const special: Record<string, string> = {
    freeze_account: "Freeze account",
    file_sar: "File SAR",
    notify_owner: "Notify owner",
    restrict_channel: "Restrict channel",
  };
  if (special[kind]) return special[kind];
  const s = kind.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
function httpStatus(e: unknown): number | undefined {
  const s = (e as { status?: unknown } | null)?.status;
  return typeof s === "number" ? s : undefined;
}

const PENDING = ["proposed", "pending", "pending_approval"];

type EvidenceItem = { label: string; id: string; name?: string; note?: string; addedBy?: string; ts?: string };
type Decision = { by?: string; approved: boolean; ts?: string; note?: string };
type ActionItem = {
  id: string;
  kind?: string;
  status?: string;
  targetLabel?: string;
  targetId?: string;
  rationale?: string;
  details?: string;
  proposedBy?: string;
  created?: string;
  effect?: string;
  decision?: Decision;
};

function parseEvidence(v: unknown): EvidenceItem[] {
  return arr(v).map((e) => ({
    label: str(e.label) ?? "Entity",
    id: str(e.id) ?? "?",
    name: str(e.name),
    note: str(e.note),
    addedBy: str(e.added_by),
    ts: str(e.ts),
  }));
}

function parseActions(v: unknown): ActionItem[] {
  return arr(v)
    .filter((a) => !!str(a.id))
    .map((a) => {
      const target = str(a.target) ?? "";
      const i = target.indexOf(":");
      const d = isObj(a.decision) ? a.decision : null;
      return {
        id: str(a.id) as string,
        kind: str(a.kind),
        status: str(a.status),
        targetLabel: i > 0 ? target.slice(0, i) : undefined,
        targetId: i > 0 ? target.slice(i + 1) || undefined : target || undefined,
        rationale: str(a.rationale),
        details: str(a.details),
        proposedBy: str(a.proposed_by),
        created: str(a.created),
        effect: str(a.effect),
        decision: d ? { by: str(d.by), approved: d.approved === true, ts: str(d.ts), note: str(d.note) } : undefined,
      };
    });
}

const KNOWN_CASE_KEYS = new Set([
  "id", "title", "kind", "severity", "status", "summary", "created", "updated", "opened_by", "assigned_to",
  "sar_reference", "sar_ref", "evidence", "actions", "tasks", "handoffs",
]);

// ------------------------------------------------------------------------------------------------ view
export default function InvestigationsView() {
  const { tenant, persona, who, tenantInfo, personaInfo, view, version, bump, setPersona, ask, openDoc, openEntity } = useApp();
  const active = view === "investigations";

  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const [scopes, setScopes] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Obj | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailNonce, setDetailNonce] = useState(0);
  const [guided, setGuided] = useState<{ tenant: string; qs: Guided[] } | null>(null);
  const [stale, setStale] = useState(true);
  const listSeq = useRef(0);
  const detailSeq = useRef(0);

  const loadList = useCallback(
    (quiet = false) => {
      const seq = ++listSeq.current;
      if (!quiet) setListLoading(true);
      API.cases({ tenant, persona })
        .then((r) => {
          if (seq !== listSeq.current) return;
          const list = Array.isArray(r?.cases) ? [...r.cases] : [];
          list.sort((a, b) => String(b.updated ?? "").localeCompare(String(a.updated ?? "")));
          setCases(list);
          setForbidden(false);
          setListErr(null);
        })
        .catch((e) => {
          if (seq !== listSeq.current) return;
          if (httpStatus(e) === 403) {
            setForbidden(true);
            setCases(null);
            setListErr(null);
          } else setListErr(errorText(e));
        })
        .finally(() => {
          if (seq === listSeq.current) setListLoading(false);
        });
    },
    [tenant, persona],
  );

  // identity change: forget the old board
  useEffect(() => {
    setSelected(null);
    setDetail(null);
    setDetailErr(null);
    setCases(null);
    setForbidden(false);
    setScopes(null);
  }, [tenant, persona]);

  // mark stale whenever identity or shared graph state changes
  useEffect(() => {
    setStale(true);
  }, [tenant, persona, version]);

  // lazily refetch when shown
  useEffect(() => {
    if (!active || !stale) return;
    setStale(false);
    loadList();
    setDetailNonce((n) => n + 1);
    API.whoami({ tenant, persona })
      .then((w) => setScopes(Array.isArray(w?.scopes) ? w.scopes : null))
      .catch(() => setScopes(null));
  }, [active, stale, loadList, tenant, persona]);

  // poll the list while visible
  useEffect(() => {
    if (!active || forbidden) return;
    const t = setInterval(() => loadList(true), 20_000);
    return () => clearInterval(t);
  }, [active, forbidden, loadList]);

  // auto-select the most recently updated case
  useEffect(() => {
    if (!cases) return;
    if (!cases.length) {
      if (selected) setSelected(null);
      return;
    }
    if (!selected || !cases.some((c) => c.id === selected)) setSelected(cases[0].id);
  }, [cases, selected]);

  // quick-start questions for an empty board
  useEffect(() => {
    if (!active || !cases || cases.length || guided?.tenant === tenant) return;
    API.guided(tenant)
      .then((r) => setGuided({ tenant, qs: (r?.questions ?? []).filter((g) => g.track === "T1" && g.cacheable === false) }))
      .catch(() => setGuided({ tenant, qs: [] }));
  }, [active, cases, guided, tenant]);

  const sel = cases?.find((c) => c.id === selected);
  const selKey = sel ? `${sel.updated}|${sel.actions}|${sel.pending_approval}|${sel.evidence}|${sel.status}` : "";

  const loadDetail = useCallback(
    (id: string) => {
      const seq = ++detailSeq.current;
      setDetailLoading(true);
      API.caseDetail({ tenant, persona }, id)
        .then((r) => {
          if (seq !== detailSeq.current) return;
          setDetail(r as unknown as Obj);
          setDetailErr(null);
        })
        .catch((e) => {
          if (seq !== detailSeq.current) return;
          setDetailErr(errorText(e));
        })
        .finally(() => {
          if (seq === detailSeq.current) setDetailLoading(false);
        });
    },
    [tenant, persona],
  );

  useEffect(() => {
    if (!selected) {
      detailSeq.current++;
      setDetail(null);
      setDetailLoading(false);
      return;
    }
    loadDetail(selected);
  }, [selected, selKey, detailNonce, loadDetail]);

  const canApprove = scopes ? scopes.includes("approve") : persona === "compliance_officer";
  const hasCompliance = !!tenantInfo?.personas.some((p) => p.key === "compliance_officer");

  const decide = useCallback(
    async (actionId: string, approve: boolean, note: string) => {
      const res = await API.decide(who, actionId, approve, note);
      if (isObj(res) && isObj(res.case)) setDetail((prev) => ({ ...(prev ?? {}), case: res.case }));
      bump();
    },
    [who, bump],
  );

  // ------------------------------------------------------------------ forbidden
  if (forbidden) {
    const options = [
      { key: "aml_analyst", label: "AML analyst" },
      { key: "compliance_officer", label: "Compliance officer" },
    ]
      .map((o) => ({ ...o, info: tenantInfo?.personas.find((p) => p.key === o.key) }))
      .filter((o) => !!o.info);
    return (
      <div className="space-y-4">
        <Header />
        <Card title="Investigation rights required">
          <p className="text-sm text-slate-300">
            Cases, proposed actions and tasks are hidden from {personaInfo?.name ?? persona} ({personaInfo?.label ?? persona}): only
            personas with investigation rights see them.
          </p>
          <p className="mt-1 text-xs text-slate-500">
            The API answered 403: this persona&apos;s signed identity lacks the <code>act</code> scope, so the graph never returns case data to it.
          </p>
          {options.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {options.map((o) => (
                <Button key={o.key} variant="primary" size="sm" onClick={() => setPersona(o.key)}>
                  Switch to {o.label} ({o.info?.name})
                </Button>
              ))}
            </div>
          )}
        </Card>
      </div>
    );
  }

  // ------------------------------------------------------------------ board
  const caseObj = detail && isObj(detail.case) ? detail.case : null;

  return (
    <div className="space-y-4">
      <Header />
      <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        {/* left: case list */}
        <Card
          title={
            <span className="flex items-center gap-2">
              Cases {cases && <Badge>{cases.length}</Badge>}
            </span>
          }
          actions={
            <Button size="sm" variant="ghost" onClick={() => loadList()} disabled={listLoading}>
              {listLoading ? <Spinner /> : "↻"} Refresh
            </Button>
          }
        >
          <div className="space-y-2">
            <ErrorNote error={listErr} onRetry={() => loadList()} />
            {!cases && !listErr && (
              <div className="flex items-center gap-2 py-4 text-sm text-slate-400">
                <Spinner /> Loading cases…
              </div>
            )}
            {cases && cases.length === 0 && (
              <div className="space-y-2">
                <Empty>No cases yet.</Empty>
                <p className="text-xs text-slate-500">Start an investigation; the agent opens a case and proposes actions here.</p>
                {(guided?.tenant === tenant ? guided.qs : []).map((g) => (
                  <Button key={g.id} size="sm" className="w-full justify-start text-left" onClick={() => ask(g)} title={g.question}>
                    ▶ {g.title}
                  </Button>
                ))}
              </div>
            )}
            {cases?.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelected(c.id)}
                className={cx(
                  "w-full rounded-md border px-3 py-2 text-left transition-colors",
                  c.id === selected ? "border-blue-500/60 bg-blue-500/10" : "border-slate-800 bg-slate-950/40 hover:bg-slate-800/50",
                )}
              >
                <div className="text-sm font-medium text-slate-100">{c.title}</div>
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  <Badge tone={severityTone(c.severity)}>{c.severity}</Badge>
                  <Badge tone={statusTone(c.status)}>{c.status}</Badge>
                  <Badge>{c.kind}</Badge>
                  {c.pending_approval > 0 && <Badge tone="amber">{c.pending_approval} awaiting approval</Badge>}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-400">
                  <span>{c.evidence ?? 0} evidence</span>
                  <span>{c.actions ?? 0} actions</span>
                  {Array.isArray(c.assigned_to) && c.assigned_to.length > 0 && (
                    <span>→ {c.assigned_to.map((a) => agentName(a)).join(", ")}</span>
                  )}
                  <span className="ml-auto">{fmtTs(c.updated)}</span>
                </div>
              </button>
            ))}
          </div>
        </Card>

        {/* right: detail */}
        <div className="min-w-0 space-y-4">
          {!selected && cases && cases.length > 0 && <Empty>Select a case.</Empty>}
          {selected && detailErr && <ErrorNote error={detailErr} onRetry={() => loadDetail(selected)} />}
          {selected && !caseObj && !detailErr && (
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Spinner /> Loading case…
            </div>
          )}
          {caseObj && (
            <CaseDetailPanel
              c={caseObj}
              graph={detail?.evidence_graph}
              loading={detailLoading}
              canApprove={canApprove}
              onSwitchCompliance={hasCompliance ? () => setPersona("compliance_officer") : null}
              onDecide={decide}
              openDoc={openDoc}
              openEntity={openEntity}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function Header() {
  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-100">Investigations</h2>
      <p className="text-sm text-slate-400">
        Cases opened by agents from graph evidence (Track 1: actions from the graph). Every case, piece of evidence and proposed action
        is a node in the tenant graph; humans decide, the graph records it. All data is synthetic.
      </p>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ detail
function CaseDetailPanel({
  c,
  graph,
  loading,
  canApprove,
  onSwitchCompliance,
  onDecide,
  openDoc,
  openEntity,
}: {
  c: Obj;
  graph: unknown;
  loading: boolean;
  canApprove: boolean;
  onSwitchCompliance: (() => void) | null;
  onDecide: (id: string, approve: boolean, note: string) => Promise<void>;
  openDoc: (id: string) => void;
  openEntity: (id: string) => void;
}) {
  const evidence = useMemo(() => parseEvidence(c.evidence), [c.evidence]);
  const actions = useMemo(() => parseActions(c.actions), [c.actions]);
  const tasks = arr(c.tasks);
  const handoffs = arr(c.handoffs);
  const assigned = strs(c.assigned_to);
  const sar = str(c.sar_reference) ?? str(c.sar_ref);
  const summary = str(c.summary);
  const pending = actions.filter((a) => PENDING.includes(a.status ?? ""));
  const extras = Object.entries(c).filter(([k, v]) => !KNOWN_CASE_KEYS.has(k) && str(v) !== undefined);

  const { nodes, edges } = useMemo(() => {
    const g = isObj(graph) ? graph : {};
    const ns = arr(g.nodes).filter((n) => typeof n.id === "string") as unknown as EvNode[];
    const es = arr(g.edges).filter((e) => typeof e.source === "string" && typeof e.target === "string") as unknown as EvEdge[];
    return { nodes: ns, edges: es };
  }, [graph]);

  const openRef = (label: string | undefined, id: string) => (label === "Document" ? openDoc(id) : openEntity(id));

  return (
    <>
      <Card
        title={
          <span className="flex items-center gap-2">
            {str(c.title) ?? "Case"} {loading && <Spinner />}
          </span>
        }
      >
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-xs text-slate-400">{str(c.id)}</span>
            <Badge tone={severityTone(str(c.severity))}>{str(c.severity) ?? "?"}</Badge>
            <Badge tone={statusTone(str(c.status))}>{str(c.status) ?? "?"}</Badge>
            {str(c.kind) && <Badge>{str(c.kind)}</Badge>}
            {sar && (
              <Badge tone="violet" title="Filed SAR reference (simulated e-filing)">
                SAR {sar}
              </Badge>
            )}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
            {str(c.opened_by) && <span>Opened by {agentName(c.opened_by)}</span>}
            {str(c.created) && <span>Created {fmtTs(str(c.created))}</span>}
            {str(c.updated) && <span>Updated {fmtTs(str(c.updated))}</span>}
            {assigned.length > 0 && <span>Assigned to {assigned.map(agentName).join(", ")}</span>}
          </div>
          {summary ? (
            <div className="rounded-md border border-slate-800 bg-slate-950/40 p-2 text-sm">
              <Markdown text={summary} />
            </div>
          ) : (
            <p className="text-xs text-slate-500">No summary yet.</p>
          )}
          {extras.length > 0 && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
              {extras.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-slate-500">{k}</dt>
                  <dd className="text-slate-300">{str(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </Card>

      <Card
        title={
          <span className="flex items-center gap-2">
            Proposed actions {pending.length > 0 && <Badge tone="amber">{pending.length} awaiting approval</Badge>}
          </span>
        }
      >
        <p className="mb-3 text-xs text-slate-400">
          Agents can only propose. Freezes, SAR filings and channel restrictions take effect only after a human with the approve scope
          decides; every decision is written back to the graph.
        </p>
        {actions.length === 0 ? (
          <Empty>No actions proposed for this case.</Empty>
        ) : (
          <div className="space-y-2">
            {actions.map((a) => (
              <ActionRow
                key={a.id}
                a={a}
                canApprove={canApprove}
                onSwitchCompliance={onSwitchCompliance}
                onDecide={onDecide}
                openRef={openRef}
              />
            ))}
          </div>
        )}
      </Card>

      <Card title={<span className="flex items-center gap-2">Evidence <Badge>{evidence.length}</Badge></span>}>
        <p className="mb-2 text-xs text-slate-400">
          EVIDENCE edges from the case to graph entities; documents you are not cleared for stay hidden even here.
        </p>
        {evidence.length === 0 ? (
          <Empty>No evidence attached.</Empty>
        ) : (
          <ul className="space-y-1.5">
            {evidence.map((e, i) => {
              const restricted = e.id.startsWith("⟨");
              return (
                <li key={`${e.id}-${i}`} className="flex flex-wrap items-start gap-2 rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1.5 text-sm">
                  <LabelBadge label={e.label} />
                  {restricted ? (
                    <span className="font-mono text-xs text-slate-500">{e.id}</span>
                  ) : (
                    <button
                      className="font-mono text-xs text-blue-300 hover:underline"
                      onClick={() => openRef(e.label, e.id)}
                      title={e.label === "Document" ? "Open the redacted document" : "Show in the Graph Explorer"}
                    >
                      {e.id}
                    </button>
                  )}
                  {e.name && <span className="text-slate-200">{e.name}</span>}
                  {e.note && <span className="basis-full text-xs text-slate-400">{e.note}</span>}
                  {(e.addedBy || e.ts) && (
                    <span className="basis-full text-[11px] text-slate-500">
                      {e.addedBy && `added by ${agentName(e.addedBy)}`} {e.ts && fmtTs(e.ts)}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {nodes.length > 0 && (
          <Disclosure className="mt-3" summary={`Evidence subgraph (${nodes.length} nodes, ${edges.length} edges) — double-click a node to explore it`}>
            <GraphCanvas
              nodes={nodes}
              edges={edges}
              focus={str(c.id) ?? null}
              onExpand={(n) => openRef(n.label, n.id)}
              className="h-[320px] w-full rounded-lg border border-slate-800 bg-[#060912]"
            />
          </Disclosure>
        )}
      </Card>

      {(tasks.length > 0 || handoffs.length > 0) && (
        <Card title="Tasks & handoffs">
          <p className="mb-2 text-xs text-slate-400">
            Agents coordinate through Task nodes on a shared blackboard and HANDED_OFF_TO edges, not hidden prompts.
          </p>
          {tasks.length > 0 && (
            <ul className="mb-3 space-y-1.5">
              {tasks.map((t, i) => (
                <li key={str(t.id) ?? i} className="rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1.5 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-slate-400">{str(t.id)}</span>
                    <Badge tone={statusTone(str(t.status))}>{str(t.status) ?? "?"}</Badge>
                    {str(t.to_agent) && <Badge tone="violet">→ {agentName(t.to_agent)}</Badge>}
                    {str(t.title) && <span className="text-slate-200">{str(t.title)}</span>}
                  </div>
                  {str(t.note) && <div className="mt-1 text-xs text-slate-400">{str(t.note)}</div>}
                  {str(t.result) && (
                    <div className="mt-1 text-xs text-emerald-200/90">
                      <span className="text-slate-500">Result: </span>
                      {str(t.result)}
                    </div>
                  )}
                  <div className="mt-0.5 text-[11px] text-slate-500">
                    {str(t.from_agent) && `from ${agentName(t.from_agent)} · `}
                    {fmtTs(str(t.created))}
                    {str(t.done) && ` · done ${fmtTs(str(t.done))}`}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {handoffs.length > 0 && (
            <ol className="space-y-1.5 border-l border-slate-800 pl-3">
              {handoffs.map((h, i) => (
                <li key={i} className="text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="violet">{agentName(h.from_agent)}</Badge>
                    <span className="text-slate-500">→</span>
                    <Badge tone="violet">{agentName(h.to_agent)}</Badge>
                    <span className="text-[11px] text-slate-500">{fmtTs(str(h.ts))}</span>
                    {str(h.task_id) && <span className="font-mono text-[11px] text-slate-500">{str(h.task_id)}</span>}
                  </div>
                  {str(h.note) && <div className="mt-0.5 text-xs text-slate-400">{str(h.note)}</div>}
                </li>
              ))}
            </ol>
          )}
        </Card>
      )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ action row
function ActionRow({
  a,
  canApprove,
  onSwitchCompliance,
  onDecide,
  openRef,
}: {
  a: ActionItem;
  canApprove: boolean;
  onSwitchCompliance: (() => void) | null;
  onDecide: (id: string, approve: boolean, note: string) => Promise<void>;
  openRef: (label: string | undefined, id: string) => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const pending = PENDING.includes(a.status ?? "");

  const go = async (approve: boolean) => {
    setBusy(approve ? "approve" : "reject");
    setErr(null);
    try {
      await onDecide(a.id, approve, note);
      setNote("");
    } catch (e) {
      setErr(httpStatus(e) === 403 ? `${errorText(e)} (only the Compliance Officer can decide).` : errorText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={cx("rounded-md border px-3 py-2", pending ? "border-amber-500/40 bg-amber-500/5" : "border-slate-800 bg-slate-950/40")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-100">{humanise(a.kind)}</span>
        {a.targetId && (
          <span className="flex items-center gap-1 text-xs">
            {a.targetLabel && <LabelBadge label={a.targetLabel} />}
            <button className="font-mono text-blue-300 hover:underline" onClick={() => openRef(a.targetLabel, a.targetId as string)}>
              {a.targetId}
            </button>
          </span>
        )}
        <Badge tone={statusTone(a.status)}>{a.status ?? "?"}</Badge>
        <span className="ml-auto font-mono text-[11px] text-slate-500">{a.id}</span>
      </div>
      {a.rationale && (
        <div className="mt-1 text-sm text-slate-300">
          <Markdown text={a.rationale} />
        </div>
      )}
      <div className="mt-1 text-[11px] text-slate-500">
        Proposed by {agentName(a.proposedBy)} {a.created && `· ${fmtTs(a.created)}`}
      </div>
      {a.details && (
        <Disclosure className="mt-2" summary="Details (e.g. drafted SAR narrative)">
          <div className="text-sm">
            <Markdown text={a.details} />
          </div>
        </Disclosure>
      )}

      {pending &&
        (canApprove ? (
          <div className="mt-2 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={1000}
                placeholder="Decision note (optional, stored on the graph)"
                className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-200 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
              />
              <Button size="sm" variant="success" disabled={!!busy} onClick={() => go(true)}>
                {busy === "approve" && <Spinner />} Approve
              </Button>
              <Button size="sm" variant="danger" disabled={!!busy} onClick={() => go(false)}>
                {busy === "reject" && <Spinner />} Reject
              </Button>
            </div>
            <ErrorNote error={err} />
          </div>
        ) : (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400">Awaiting approval by the Compliance Officer</span>
            {onSwitchCompliance && (
              <Button size="sm" onClick={onSwitchCompliance}>
                Switch to Compliance Officer
              </Button>
            )}
          </div>
        ))}

      {a.decision && (
        <div className={cx("mt-2 rounded border px-2 py-1 text-xs", a.decision.approved ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-rose-500/30 bg-rose-500/10 text-rose-200")}>
          {a.decision.approved ? "Approved" : "Rejected"} by {a.decision.by ?? "a reviewer"}
          {a.decision.ts && ` · ${fmtTs(a.decision.ts)}`}
          {a.decision.note && <div className="mt-0.5 text-slate-300">“{a.decision.note}”</div>}
        </div>
      )}
      {a.effect && (
        <div className="mt-1 text-xs text-cyan-200">
          <span className="text-slate-500">Effect in graph: </span>
          {a.effect}
        </div>
      )}
    </div>
  );
}