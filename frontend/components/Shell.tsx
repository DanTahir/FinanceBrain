"use client";

// App chrome: brand, tenant + persona switchers, budget meter, Reset demo, view tabs. All views stay mounted (hidden
// when inactive) so a conversation or an explored graph survives switching tabs.

import { useState } from "react";
import { API, errorText } from "@/lib/api";
import { VIEWS, useApp } from "@/lib/state";
import DocViewer from "@/components/DocViewer";
import { Badge, Button, Spinner, cx } from "@/components/ui";
import ChatView from "@/components/views/ChatView";
import GraphView from "@/components/views/GraphView";
import RedactionView from "@/components/views/RedactionView";
import InvestigationsView from "@/components/views/InvestigationsView";
import AgentsView from "@/components/views/AgentsView";
import MemoryView from "@/components/views/MemoryView";

const SCOPE_HINT: Record<string, string> = {
  read: "read company knowledge (ACL-filtered)",
  act: "open cases, run investigations",
  approve: "approve or reject proposed actions",
  reveal: "reveal vault values (audit-logged)",
};
const PERSONA_SCOPES: Record<string, string[]> = {
  compliance_officer: ["read", "act", "approve", "reveal"],
  aml_analyst: ["read", "act"],
};

export default function Shell() {
  const app = useApp();
  const { tenants, tenant, persona, setTenant, setPersona, tenantInfo, personaInfo, view, setView, budget, bump, error, ready } = app;
  const [resetting, setResetting] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const scopes = PERSONA_SCOPES[persona] ?? ["read"];

  async function reset() {
    if (!confirm(`Reset ${tenantInfo?.name ?? tenant} to its seeded snapshot? Cases, approvals and live-feed replays are discarded (your private memory is kept).`)) return;
    setResetting(true);
    setNote(null);
    try {
      const r = await API.reset(tenant);
      setNote(`Restored ${r.graph} from ${r.restored_from} in ${r.ms} ms`);
      bump();
    } catch (e) {
      setNote(errorText(e));
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-slate-800 bg-[#070b14]/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
          <div className="flex items-center gap-2">
            <div className="grid h-7 w-7 place-items-center rounded-md bg-gradient-to-br from-blue-500 to-violet-600 text-sm font-bold text-white">FB</div>
            <div className="leading-tight">
              <div className="text-sm font-semibold text-slate-50">FinanceBrain</div>
              <div className="text-[10px] text-slate-500">company brain on FalkorDB · synthetic data</div>
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <label className="text-slate-500">Tenant</label>
            <select className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100" value={tenant} onChange={(e) => setTenant(e.target.value)}>
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <label className="ml-2 text-slate-500">You are</label>
            <select className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100" value={persona} onChange={(e) => setPersona(e.target.value)}>
              {(tenantInfo?.personas ?? []).map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name} — {p.label}
                </option>
              ))}
            </select>
            <span className="hidden items-center gap-1 lg:flex">
              {scopes.map((s) => (
                <Badge key={s} tone={s === "approve" || s === "reveal" ? "violet" : s === "act" ? "blue" : "slate"} title={SCOPE_HINT[s]}>
                  {s}
                </Badge>
              ))}
            </span>
          </div>

          <div className="ml-auto flex items-center gap-3 text-xs">
            {budget && (
              <div className="flex items-center gap-2" title={`Model spend today (UTC ${budget.day}); over budget only cached guided answers are served`}>
                <span className="text-slate-500">Demo budget</span>
                <div className="h-1.5 w-24 overflow-hidden rounded bg-slate-800">
                  <div className={cx("h-full", budget.over ? "bg-rose-500" : "bg-emerald-500")} style={{ width: `${Math.min(100, (budget.spent_usd / Math.max(budget.limit_usd, 0.01)) * 100)}%` }} />
                </div>
                <span className={cx("tabular-nums", budget.over ? "text-rose-300" : "text-slate-400")}>
                  ${budget.spent_usd.toFixed(2)} / ${budget.limit_usd.toFixed(0)}
                </span>
              </div>
            )}
            <Button size="sm" variant="secondary" onClick={reset} disabled={resetting || !ready} title="Restore the tenant graph from its pristine snapshot (GRAPH.COPY)">
              {resetting ? <Spinner /> : "↺"} Reset demo
            </Button>
          </div>
        </div>
        <nav className="mx-auto flex max-w-[1500px] gap-1 overflow-x-auto px-4">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              title={v.hint}
              onClick={() => setView(v.id)}
              className={cx("whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors", view === v.id ? "border-blue-500 text-slate-50" : "border-transparent text-slate-400 hover:text-slate-200")}
            >
              {v.label}
            </button>
          ))}
        </nav>
      </header>

      {(error || note) && (
        <div className="mx-auto w-full max-w-[1500px] px-4 pt-2">
          <div className={cx("rounded-md border px-3 py-1.5 text-xs", error ? "border-rose-500/30 bg-rose-500/10 text-rose-200" : "border-slate-700 bg-slate-900 text-slate-300")}>
            {error ? `API unavailable: ${error}` : note}
            {note && (
              <button className="ml-3 underline" onClick={() => setNote(null)}>
                dismiss
              </button>
            )}
          </div>
        </div>
      )}

      <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-4">
        {!ready && !error && (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Spinner /> connecting…
          </div>
        )}
        {ready && personaInfo && (
          <>
            <div className={view === "chat" ? "" : "hidden"}><ChatView /></div>
            <div className={view === "graph" ? "" : "hidden"}><GraphView /></div>
            <div className={view === "redaction" ? "" : "hidden"}><RedactionView /></div>
            <div className={view === "investigations" ? "" : "hidden"}><InvestigationsView /></div>
            <div className={view === "agents" ? "" : "hidden"}><AgentsView /></div>
            <div className={view === "memory" ? "" : "hidden"}><MemoryView /></div>
          </>
        )}
      </main>

      <footer className="border-t border-slate-900 px-4 py-3 text-center text-[11px] text-slate-600">
        All people, companies and transactions are synthetic. Personal data is pseudonymized before it reaches the graph or any model.
        {" · "}
        <a className="underline" href="https://github.com/DanTahir/FinanceBrain" target="_blank" rel="noreferrer">
          source
        </a>
      </footer>
      <DocViewer />
    </div>
  );
}
