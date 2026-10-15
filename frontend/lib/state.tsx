"use client";

// App-wide state: who you are (tenant + persona), which view is showing, a refresh counter that views watch
// (bumped after live-feed replays, resets and approvals), plus cross-view hand-offs (open a document, focus an
// entity in the Graph Explorer, ask a guided question in Chat).

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { API, type Budget, type Guided, type Persona, type Tenant } from "@/lib/api";

export type View = "chat" | "graph" | "redaction" | "investigations" | "agents" | "memory";
export const VIEWS: { id: View; label: string; hint: string }[] = [
  { id: "chat", label: "Chat", hint: "Ask the company brain; answers are cited and explained" },
  { id: "graph", label: "Graph Explorer", hint: "Browse the knowledge graph with your ACLs applied" },
  { id: "redaction", label: "Ingestion & Redaction", hint: "Live feed, PII redaction metrics and the canary scan" },
  { id: "investigations", label: "Investigations", hint: "Cases, evidence and actions awaiting approval" },
  { id: "agents", label: "Agents", hint: "Handoffs and the shared blackboard" },
  { id: "memory", label: "Memory", hint: "What the agents remember about you" },
];

export type Who = { tenant: string; persona: string };

type AppState = {
  tenants: Tenant[];
  tenant: string;
  persona: string;
  who: Who;
  tenantInfo: Tenant | undefined;
  personaInfo: Persona | undefined;
  setTenant: (t: string) => void;
  setPersona: (p: string) => void;
  view: View;
  setView: (v: View) => void;
  /** Increments whenever shared graph state changes (live feed, reset, approval, finished agent run). */
  version: number;
  bump: () => void;
  budget: Budget | null;
  refreshBudget: () => void;
  /** Document viewer modal (rendered by Shell). */
  docId: string | null;
  openDoc: (id: string | null) => void;
  /** Graph Explorer focus request; GraphView consumes it and calls clearFocus(). */
  focus: string | null;
  openEntity: (id: string) => void;
  clearFocus: () => void;
  /** Guided question hand-off: switches persona + view, ChatView consumes it and calls clearPending(). */
  pending: Guided | null;
  ask: (g: Guided) => void;
  clearPending: () => void;
  ready: boolean;
  error: string | null;
};

const Ctx = createContext<AppState | null>(null);

function readHash(): View | null {
  if (typeof window === "undefined") return null;
  const h = window.location.hash.replace(/^#/, "");
  return (VIEWS.find((v) => v.id === h)?.id as View) ?? null;
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenant, setTenantRaw] = useState("halcyon");
  const [persona, setPersonaRaw] = useState("aml_analyst");
  const [view, setViewRaw] = useState<View>("chat");
  const [version, setVersion] = useState(0);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [docId, setDocId] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [pending, setPending] = useState<Guided | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const saved = (k: string) => (typeof window !== "undefined" ? window.localStorage.getItem(k) : null);
    API.tenants()
      .then((ts) => {
        setTenants(ts);
        const t = ts.find((x) => x.id === saved("fb.tenant")) ?? ts[0];
        if (t) {
          setTenantRaw(t.id);
          const p = t.personas.find((x) => x.key === saved("fb.persona")) ?? t.personas.find((x) => x.key === "aml_analyst") ?? t.personas[0];
          if (p) setPersonaRaw(p.key);
        }
        setReady(true);
      })
      .catch((e) => setError(String(e?.message ?? e)));
    const v = readHash();
    if (v) setViewRaw(v);
    const onHash = () => {
      const h = readHash();
      if (h) setViewRaw(h);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refreshBudget = useCallback(() => {
    API.budget().then(setBudget).catch(() => undefined);
  }, []);
  useEffect(() => {
    refreshBudget();
    const t = setInterval(refreshBudget, 60_000);
    return () => clearInterval(t);
  }, [refreshBudget]);

  const tenantInfo = tenants.find((t) => t.id === tenant);
  const personaInfo = tenantInfo?.personas.find((p) => p.key === persona);

  const setTenant = useCallback(
    (t: string) => {
      setTenantRaw(t);
      window.localStorage.setItem("fb.tenant", t);
      const info = tenants.find((x) => x.id === t);
      if (info && !info.personas.some((p) => p.key === persona)) {
        const p = info.personas[0].key;
        setPersonaRaw(p);
        window.localStorage.setItem("fb.persona", p);
      }
    },
    [tenants, persona],
  );
  const setPersona = useCallback((p: string) => {
    setPersonaRaw(p);
    window.localStorage.setItem("fb.persona", p);
  }, []);
  const setView = useCallback((v: View) => {
    setViewRaw(v);
    if (window.location.hash !== `#${v}`) window.history.replaceState(null, "", `#${v}`);
  }, []);
  const bump = useCallback(() => {
    setVersion((n) => n + 1);
    refreshBudget();
  }, [refreshBudget]);
  const openEntity = useCallback(
    (id: string) => {
      setFocus(id);
      setView("graph");
    },
    [setView],
  );
  const ask = useCallback(
    (g: Guided) => {
      if (g.tenant !== tenant) setTenant(g.tenant);
      setPersona(g.persona);
      setPending(g);
      setView("chat");
    },
    [tenant, setTenant, setPersona, setView],
  );

  const value = useMemo<AppState>(
    () => ({
      tenants,
      tenant,
      persona,
      who: { tenant, persona },
      tenantInfo,
      personaInfo,
      setTenant,
      setPersona,
      view,
      setView,
      version,
      bump,
      budget,
      refreshBudget,
      docId,
      openDoc: setDocId,
      focus,
      openEntity,
      clearFocus: () => setFocus(null),
      pending,
      ask,
      clearPending: () => setPending(null),
      ready,
      error,
    }),
    [tenants, tenant, persona, tenantInfo, personaInfo, setTenant, setPersona, view, setView, version, bump, budget,
     refreshBudget, docId, focus, openEntity, pending, ask, ready, error],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}
