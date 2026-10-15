"use client";

// Graph Explorer: an ACL-aware view over the tenant's FalkorDB knowledge graph. Every search hit and every
// neighbourhood is vetted server-side for the active persona (document ACLs + the deal information barrier).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import GraphCanvas from "@/components/GraphCanvas";
import { API, ApiError, errorText, type EvEdge, type EvNode, type EvidenceGraph, type Neighbor, type SearchResult } from "@/lib/api";
import { useApp } from "@/lib/state";
import { Button, Card, Empty, ErrorNote, LabelBadge, Spinner, cx, fmtTs } from "@/components/ui";

type G = { nodes: EvNode[]; edges: EvEdge[] };
const EMPTY: G = { nodes: [], edges: [] };
const HIDDEN_PROPS = new Set(["id", "label", "name", "display", "color", "shape"]);

const START_HERE: Record<string, string[]> = {
  halcyon: ["ReconLegacy", "PricingSvc", "KYC pipeline", "c_0060", "t_r016", "Project Falcon"],
  brightwater: ["LoanDesk", "CoreBanking", "BSAWatch", "MemberCRM"],
};

const DATA_MODEL: { label: string; text: string }[] = [
  { label: "Person", text: "employees, linked to teams, systems they own or know about, and who they talk to" },
  { label: "Team", text: "org units; members and formal system ownership" },
  { label: "System", text: "internal services and their DEPENDS_ON chains" },
  { label: "Document", text: "emails, Slack, tickets, meetings, CRM notes (ACL-checked per persona)" },
  { label: "Client", text: "customers with risk rating and KYC status, holding accounts" },
  { label: "Account", text: "accounts; money flows between them (FLOWS_TO)" },
  { label: "Transaction", text: "payments FROM_ACCOUNT / TO_ACCOUNT, with fraud scores" },
  { label: "PiiToken", text: "pseudonymous identifiers (phone, address, device ...)" },
  { label: "Deal", text: "M&A deals behind the information barrier" },
  { label: "Decision", text: "decisions with their justifying documents and supersessions" },
  { label: "Fact", text: "bitemporal facts extracted from documents, citing their sources" },
];

function edgeKey(e: EvEdge): string {
  return `${e.source}|${e.type}|${e.target}`;
}

function mergeGraph(base: G, add: EvidenceGraph | null | undefined): G {
  const nm = new Map<string, EvNode>(base.nodes.map((n) => [n.id, n]));
  const em = new Map<string, EvEdge>(base.edges.map((e) => [edgeKey(e), e]));
  const nodes = Array.isArray(add?.nodes) ? add.nodes : [];
  const edges = Array.isArray(add?.edges) ? add.edges : [];
  for (const n of nodes) {
    if (!n || typeof n.id !== "string") continue;
    const prev = nm.get(n.id);
    nm.set(n.id, { ...(prev ?? {}), ...n, name: String(n.name ?? prev?.name ?? n.id), label: String(n.label ?? prev?.label ?? "Node") } as EvNode);
  }
  for (const e of edges) {
    if (!e || typeof e.source !== "string" || typeof e.target !== "string") continue;
    const k = edgeKey(e);
    em.set(k, { ...(em.get(k) ?? {}), ...e } as EvEdge);
  }
  return { nodes: [...nm.values()], edges: [...em.values()] };
}

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  let s: string;
  if (typeof v === "object") {
    try {
      s = JSON.stringify(v);
    } catch {
      s = String(v);
    }
  } else s = String(v);
  return s.length > 200 ? `${s.slice(0, 199)}…` : s;
}

export default function GraphView() {
  const { tenant, persona, personaInfo, tenantInfo, setPersona, focus, clearFocus, openDoc } = useApp();
  const personaName = personaInfo?.name ?? persona;

  const [graph, setGraph] = useState<G>(EMPTY);
  const graphRef = useRef<G>(EMPTY);
  const [history, setHistory] = useState<G[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lastLoaded, setLastLoaded] = useState<string | null>(null);
  const [neighbors, setNeighbors] = useState<Record<string, Neighbor[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notVisible, setNotVisible] = useState<string | null>(null);
  const [lastAttempt, setLastAttempt] = useState<{ entity: string; fresh: boolean } | null>(null);
  const genRef = useRef(0);

  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const commit = useCallback((next: G, pushHistory: boolean) => {
    const prev = graphRef.current;
    if (pushHistory && prev.nodes.length) setHistory((h) => [...h.slice(-24), prev]);
    graphRef.current = next;
    setGraph(next);
  }, []);

  const handleErr = useCallback(
    (e: unknown, entity: string) => {
      if (e instanceof ApiError && e.status === 404) {
        setNotVisible(`${entity} is not visible to ${personaName} (not found, outside your ACL, or behind the information barrier).`);
      } else if (e instanceof ApiError && e.status === 403) {
        setForbidden(true);
        setError(`${personaName} lacks the scope needed to browse the graph: ${e.message}`);
      } else setError(errorText(e));
    },
    [personaName],
  );

  const load = useCallback(
    async (entity: string, opts: { fresh?: boolean } = {}) => {
      const gen = genRef.current;
      const fresh = !!opts.fresh;
      setLastAttempt({ entity, fresh });
      setLoading(true);
      setError(null);
      setForbidden(false);
      setNotVisible(null);
      try {
        const res = await API.neighborhood({ tenant, persona }, entity, 40);
        if (gen !== genRef.current) return;
        const ent = res?.entity;
        const base = fresh ? EMPTY : graphRef.current;
        let next = mergeGraph(base, res?.graph);
        if (ent && typeof ent.id === "string" && !next.nodes.some((n) => n.id === ent.id)) {
          next = mergeGraph(next, { nodes: [{ ...ent, id: ent.id, label: String(ent.label ?? "Node"), name: String(ent.name ?? ent.id) }], edges: [] });
        }
        commit(next, true);
        const id = ent?.id ?? entity;
        setNeighbors((m) => ({ ...m, [id]: Array.isArray(res?.neighbors) ? res.neighbors : [] }));
        setSelectedId(id);
        setLastLoaded(id);
      } catch (e) {
        if (gen !== genRef.current) return;
        handleErr(e, entity);
      } finally {
        if (gen === genRef.current) setLoading(false);
      }
    },
    [tenant, persona, commit, handleErr],
  );

  // reset when identity changes
  useEffect(() => {
    genRef.current += 1;
    graphRef.current = EMPTY;
    setGraph(EMPTY);
    setHistory([]);
    setSelectedId(null);
    setLastLoaded(null);
    setNeighbors({});
    setLoading(false);
    setError(null);
    setForbidden(false);
    setNotVisible(null);
    setResults([]);
    setQ("");
    setSearchError(null);
  }, [tenant, persona]);

  // cross-view focus requests (openEntity from other views)
  useEffect(() => {
    if (!focus) return;
    void load(focus, { fresh: true });
    clearFocus();
  }, [focus, load, clearFocus]);

  // debounced search
  useEffect(() => {
    const text = q.trim();
    if (text.length < 2) {
      setResults([]);
      setSearching(false);
      setSearchError(null);
      return;
    }
    let cancelled = false;
    const t = setTimeout(() => {
      setSearching(true);
      setSearchError(null);
      API.search({ tenant, persona }, text)
        .then((r) => {
          if (!cancelled) setResults(Array.isArray(r?.results) ? r.results : []);
        })
        .catch((e) => {
          if (cancelled) return;
          setResults([]);
          setSearchError(e instanceof ApiError && e.status === 403 ? `${personaName} lacks the scope to search the graph.` : errorText(e));
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [q, tenant, persona, personaName]);

  const startHere = useCallback(
    async (text: string) => {
      const gen = genRef.current;
      setQ(text);
      setNotVisible(null);
      setError(null);
      setLoading(true);
      try {
        const r = await API.search({ tenant, persona }, text);
        if (gen !== genRef.current) return;
        const first = Array.isArray(r?.results) ? r.results[0] : undefined;
        if (first?.id) await load(first.id);
        else {
          setNotVisible(`${text} is not visible to ${personaName} (not found, outside your ACL, or behind the information barrier).`);
          setLoading(false);
        }
      } catch (e) {
        if (gen !== genRef.current) return;
        handleErr(e, text);
        setLoading(false);
      }
    },
    [tenant, persona, personaName, load, handleErr],
  );

  const undo = () => {
    setHistory((h) => {
      if (!h.length) return h;
      const prev = h[h.length - 1];
      graphRef.current = prev;
      setGraph(prev);
      setSelectedId((s) => (s && prev.nodes.some((n) => n.id === s) ? s : null));
      return h.slice(0, -1);
    });
  };
  const clear = () => {
    commit(EMPTY, true);
    setSelectedId(null);
    setLastLoaded(null);
  };
  const remove = (id: string) => {
    const g = graphRef.current;
    commit({ nodes: g.nodes.filter((n) => n.id !== id), edges: g.edges.filter((e) => e.source !== id && e.target !== id) }, true);
    setSelectedId(null);
  };

  const legend = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of graph.nodes) c[n.label] = (c[n.label] ?? 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [graph.nodes]);

  const selected = selectedId ? graph.nodes.find((n) => n.id === selectedId) ?? null : null;
  const suggestions = START_HERE[tenant] ?? [];

  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">Graph Explorer</h2>
        <p className="text-sm text-slate-400">
          Browse the synthetic company knowledge graph exactly as {personaName} is allowed to see it: every node and edge is
          ACL-checked on the server.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)_320px]">
        {/* left: search */}
        <div className="space-y-3">
          <Card title="Search entities">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Name or id (e.g. c_0060)"
              className="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200 outline-none placeholder:text-slate-600 focus:border-blue-500"
            />
            <div className="mt-2 space-y-1">
              {searching && (
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <Spinner /> searching…
                </div>
              )}
              <ErrorNote error={searchError} />
              {!searching && q.trim().length >= 2 && !results.length && !searchError && (
                <div className="text-xs text-slate-500">No visible entities match.</div>
              )}
              <ul className="max-h-72 space-y-1 overflow-y-auto">
                {results.map((r) => (
                  <li key={r.id}>
                    <button
                      className="flex w-full flex-col items-start gap-0.5 rounded px-2 py-1 text-left hover:bg-slate-800/70"
                      onClick={() => void load(r.id, { fresh: false })}
                    >
                      <span className="flex items-center gap-1.5">
                        <LabelBadge label={String(r.label ?? "Node")} />
                        <span className="text-sm text-slate-200">{String(r.name ?? r.id)}</span>
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">{r.id}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </Card>
          {suggestions.length > 0 && (
            <Card title="Start here">
              <div className="flex flex-wrap gap-1.5">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    onClick={() => void startHere(s)}
                    className="rounded-full border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-xs text-slate-200 hover:border-blue-500 hover:text-blue-200"
                  >
                    {s}
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[11px] leading-snug text-slate-500">
                Results are filtered by your persona&apos;s ACLs and the deal information barrier (try Project Falcon as the
                Engineer vs the Compliance Officer).
              </p>
            </Card>
          )}
          <Card title="On the canvas">
            {legend.length ? (
              <div className="flex flex-wrap gap-1.5">
                {legend.map(([label, n]) => (
                  <span key={label} className="inline-flex items-center gap-1">
                    <LabelBadge label={label} />
                    <span className="text-[11px] tabular-nums text-slate-400">{n}</span>
                  </span>
                ))}
              </div>
            ) : (
              <div className="text-xs text-slate-500">Nothing loaded yet.</div>
            )}
          </Card>
        </div>

        {/* center: canvas */}
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs tabular-nums text-slate-400">
              {graph.nodes.length} nodes · {graph.edges.length} edges
            </span>
            {loading && <Spinner />}
            <div className="ml-auto flex items-center gap-2">
              <Button size="sm" variant="ghost" disabled={!history.length} onClick={undo}>
                Undo last expand
              </Button>
              <Button size="sm" disabled={!graph.nodes.length} onClick={clear}>
                Clear
              </Button>
            </div>
          </div>
          {notVisible && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">{notVisible}</div>
          )}
          <ErrorNote error={error} onRetry={lastAttempt && !forbidden ? () => void load(lastAttempt.entity, { fresh: lastAttempt.fresh }) : undefined} />
          {forbidden && tenantInfo && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
              Switch persona:
              {tenantInfo.personas
                .filter((p) => p.key !== persona)
                .map((p) => (
                  <Button key={p.key} size="sm" onClick={() => setPersona(p.key)}>
                    {p.label || p.name}
                  </Button>
                ))}
            </div>
          )}
          <div className="relative">
            <GraphCanvas
              nodes={graph.nodes}
              edges={graph.edges}
              selected={selectedId}
              focus={lastLoaded}
              onSelect={(n) => setSelectedId(n ? n.id : null)}
              onExpand={(n) => void load(n.id)}
              className="h-[640px] w-full rounded-lg border border-slate-800 bg-[#060912]"
            />
            {!graph.nodes.length && !loading && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-slate-500">
                Search for an entity or pick a &quot;Start here&quot; chip to load its neighbourhood.
              </div>
            )}
          </div>
          <p className="text-[11px] text-slate-500">Click a node for details · double-click to expand its neighbourhood</p>
        </div>

        {/* right: selection */}
        <div className="space-y-3">
          {selected ? (
            <SelectedPanel
              node={selected}
              neighbors={neighbors[selected.id]}
              onOpenDoc={() => openDoc(selected.id)}
              onExpand={() => void load(selected.id)}
              onFocus={() => void load(selected.id, { fresh: true })}
              onRemove={() => remove(selected.id)}
              onNeighbor={(id) => {
                if (neighbors[id] && graph.nodes.some((n) => n.id === id)) setSelectedId(id);
                else void load(id);
              }}
            />
          ) : (
            <Card title="Data model">
              <p className="mb-2 text-xs text-slate-400">Select a node to inspect it. The graph (all synthetic) connects:</p>
              <ul className="space-y-1.5">
                {DATA_MODEL.map((d) => (
                  <li key={d.label} className="flex items-start gap-2 text-xs text-slate-300">
                    <span className="shrink-0">
                      <LabelBadge label={d.label} />
                    </span>
                    <span>{d.text}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs leading-snug text-slate-400">
                PII never appears in the graph in clear: it is stored only as pseudonymous <span className="text-rose-300">PiiToken</span> nodes.
                Clients, accounts or counterparties that share a token (same phone, address, device) become linked
                structure, which is how fraud rings show up as graph patterns.
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function SelectedPanel({
  node,
  neighbors,
  onOpenDoc,
  onExpand,
  onFocus,
  onRemove,
  onNeighbor,
}: {
  node: EvNode;
  neighbors: Neighbor[] | undefined;
  onOpenDoc: () => void;
  onExpand: () => void;
  onFocus: () => void;
  onRemove: () => void;
  onNeighbor: (id: string) => void;
}) {
  const props = Object.entries(node).filter(([k]) => !HIDDEN_PROPS.has(k));
  const groups = useMemo(() => {
    const m = new Map<string, Neighbor[]>();
    for (const n of neighbors ?? []) {
      if (!n || typeof n.id !== "string") continue;
      const k = String(n.rel ?? "RELATED");
      const arr = m.get(k) ?? [];
      arr.push(n);
      m.set(k, arr);
    }
    return [...m.entries()];
  }, [neighbors]);

  return (
    <>
      <Card title="Selected">
        <div className="space-y-1">
          <LabelBadge label={String(node.label ?? "Node")} />
          <div className="break-words text-sm font-semibold text-slate-100">{String(node.name ?? node.id)}</div>
          <div className="break-all font-mono text-[11px] text-slate-500">{node.id}</div>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {node.label === "Document" && (
            <Button size="sm" variant="primary" onClick={onOpenDoc}>
              Open document
            </Button>
          )}
          <Button size="sm" onClick={onExpand}>
            Expand
          </Button>
          <Button size="sm" onClick={onFocus}>
            Focus
          </Button>
          <Button size="sm" variant="ghost" onClick={onRemove}>
            Remove
          </Button>
        </div>
        {props.length > 0 && (
          <table className="mt-3 w-full text-xs">
            <tbody>
              {props.map(([k, v]) => (
                <tr key={k} className="border-t border-slate-800 align-top">
                  <td className="py-1 pr-2 font-mono text-slate-500">{k}</td>
                  <td className="break-all py-1 text-slate-300">{fmtVal(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Neighbours">
        {!neighbors ? (
          <Empty>Expand this node to list its visible neighbours.</Empty>
        ) : !groups.length ? (
          <Empty>No neighbours visible to this persona.</Empty>
        ) : (
          <div className="max-h-[420px] space-y-3 overflow-y-auto">
            {groups.map(([rel, items]) => (
              <div key={rel}>
                <div className="mb-1 font-mono text-[11px] uppercase tracking-wide text-slate-500">
                  {rel} <span className="text-slate-600">({items.length})</span>
                </div>
                <ul className="space-y-0.5">
                  {items.map((n, i) => (
                    <li key={`${n.id}-${n.direction}-${i}`}>
                      <button
                        className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs hover:bg-slate-800/70"
                        onClick={() => onNeighbor(n.id)}
                        title={n.id}
                      >
                        <span className={cx("w-3 shrink-0 text-center", n.direction === "out" ? "text-emerald-400" : "text-blue-400")}>
                          {n.direction === "out" ? "→" : "←"}
                        </span>
                        <LabelBadge label={String(n.label ?? "Node")} />
                        <span className="min-w-0 flex-1 truncate text-slate-200">{String(n.name ?? n.id)}</span>
                        {typeof n.weight === "number" && <span className="tabular-nums text-slate-500">w{n.weight}</span>}
                        {n.ts && <span className="shrink-0 text-[10px] text-slate-500">{fmtTs(String(n.ts))}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}