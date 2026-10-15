// Typed client for the FinanceBrain API (backend/api/app.py). Same origin: Caddy in production, a Next rewrite in dev.

// ------------------------------------------------------------------------------------------------ shared types
export type Persona = {
  key: string;
  person_id: string;
  name: string;
  title: string;
  team: string;
  label: string;
};
export type Tenant = { id: string; name: string; personas: Persona[] };

export type WhoAmI = { visitor: string; tenant: string; persona: string; scopes: string[]; memory_graph: string };

/** Evidence subgraph node, as built by backend/tools/base.py Evidence.to_dict(). Extra props vary by label. */
export type EvNode = { id: string; label: string; name: string; [k: string]: unknown };
export type EvEdge = { source: string; target: string; type: string; [k: string]: unknown };
export type EvidenceGraph = { nodes: EvNode[]; edges: EvEdge[] };

/** One Cypher query a tool ran (tool_result.trace items; also memory overview `trace`). */
export type TraceItem = { name?: string; cypher?: string; params?: unknown; rows?: number; ms?: number; graph?: string; [k: string]: unknown };

export type Citation = {
  n: number;
  id: string;
  kind?: string;
  title?: string;
  ts?: string;
  author?: string;
  channel?: string;
  snippet?: string;
  [k: string]: unknown;
};

export type ToolCallSummary = { id: string; name: string; args: unknown; ok: boolean; ms: number; summary: string };

export type OpsEvent = { kind: string; agent?: string; case_id?: string; action_id?: string; task_id?: string; [k: string]: unknown };

export type Usage = { prompt_tokens?: number; completion_tokens?: number; cached_tokens?: number; cost_usd?: number; [k: string]: unknown };

/** Server-sent chat events (backend/agents/{session,orchestrator,runtime}.py and api/guardrails.py replay). */
export type ChatEvent =
  | { type: "session"; session_id: string | null; resumed?: boolean; memory_graph?: string | null; cached?: boolean; note?: string }
  | { type: "pii_masked"; counts: Record<string, number>; note: string }
  | { type: "memory_context"; [k: string]: unknown }
  | { type: "route"; agent: string; reason?: string; [k: string]: unknown }
  | { type: "start"; agent: string; model?: string; tenant?: string; persona?: string }
  | { type: "step"; n: number }
  | { type: "tool_call"; id: string; name: string; args: unknown }
  | { type: "tool_result"; id: string; name: string; args: unknown; ok: boolean; ms: number; summary: string; trace: TraceItem[] }
  | ({ type: "ops"; agent: string } & OpsEvent)
  | { type: "handoff"; from: string; to: string; task_id?: string; case_id?: string; note?: string }
  | {
      type: "final";
      agent: string;
      text: string;
      citations: Citation[];
      unverified_citations?: string[];
      evidence?: EvidenceGraph;
      tool_calls?: ToolCallSummary[];
      ops?: OpsEvent[];
      steps?: number;
      usage?: Usage;
      cost_usd?: number;
      ms?: number;
    }
  | { type: "memory"; error?: string; [k: string]: unknown }
  | { type: "error"; message: string }
  | { type: "done"; session_id?: string | null; agent?: string; cached?: boolean };

export type ChatRequest = {
  tenant: string;
  persona: string;
  message: string;
  agent?: string; // "auto" | "brain" | "investigator" | "compliance" | ...
  session_id?: string | null;
  memory?: boolean;
  history?: { role: "user" | "assistant"; content: string }[];
};

export type Guided = {
  id: string;
  tenant: string;
  track: "T1" | "T2" | "T3";
  title: string;
  question: string;
  persona: string;
  agent: string;
  cacheable: boolean;
  hint: string;
};

export type Budget = { day: string; spent_usd: number; limit_usd: number; remaining_usd: number; over: boolean };

export type LiveDay = { day: string; docs: number; txns: number; ownership: number; joiners: number; leavers: number; done: boolean };
export type LiveStatus = {
  tenant: string;
  graph: string;
  cutoff: string;
  cursor: string | null;
  next_day: string | null;
  remaining_days: number;
  remaining_docs: number;
  remaining_txns: number;
  days: LiveDay[];
};
/** POST /api/live/advance -> { replayed: [...per-day reports, incl. Sentinel alerts], status }. */
export type LiveAdvanceResult = { replayed: Record<string, unknown>[]; status: LiveStatus };
export type ResetResult = { tenant: string; graph: string; restored_from: string; ms: number };

export type CaseSummary = {
  id: string;
  title: string;
  kind: string;
  severity: string;
  status: string;
  created: string;
  updated: string;
  evidence: number;
  actions: number;
  pending_approval: number;
  assigned_to: string[];
};
/** GET /api/cases/{id} -> { case: CaseDetail } (backend/tools/ops.py _case_view; fields beyond these vary). */
export type CaseDetail = CaseSummary & {
  summary?: string;
  opened_by?: string;
  sar_ref?: string;
  evidence_items?: unknown[];
  actions_list?: unknown[];
  [k: string]: unknown;
};

export type MemorySession = {
  id: string;
  title: string | null;
  summary: string | null;
  started: string;
  updated: string;
  turns: number;
  agent: string | null;
  consolidated_n: number | null;
};
export type MemoryFact = {
  id: string;
  kind: string;
  text: string;
  created: string;
  valid_to: string | null;
  session: string | null;
  by: string | null;
  superseded_by: string | null;
  about: unknown;
};
export type MemoryOverview = {
  graph: string;
  exists: boolean;
  counts: Record<string, number>;
  sessions: MemorySession[];
  facts: MemoryFact[];
  trace: TraceItem[];
  [k: string]: unknown;
};

export type SearchResult = { id: string; label: string; name: string; [k: string]: unknown };
export type Neighbor = { rel: string; direction: "out" | "in" | string; label: string; id: string; name: string; weight?: number; ts?: string };
export type Neighborhood = {
  entity: SearchResult;
  neighbors: Neighbor[];
  counts: Record<string, number>;
  graph: EvidenceGraph;
};

export type DocDetail = {
  id: string;
  label: "Document";
  name?: string;
  kind?: string;
  title?: string;
  text?: string;
  ts?: string;
  author?: string;
  channel?: string;
  redaction: { counts: Record<string, number>; total: number } | null;
  [k: string]: unknown;
};

export type RedactionEvalType = { gt: number; recall_any: number; recall_typed: number; detections: number; precision: number };
export type RedactionEval = {
  tenant: string;
  summary: { gt_spans: number; detections: number; recall_any: number; recall_typed: number; precision: number; docs: number; seconds: number };
  by_type: Record<string, RedactionEvalType>;
};
export type CanaryReport = {
  strings_scanned: number;
  hits: number;
  by_type: Record<string, number>;
  examples: unknown[];
  clean: boolean;
  graphs: number;
  ts: string;
  seconds: number;
};
export type RedactionSummary = {
  tenant: string;
  documents_redacted: number;
  by_type: Record<string, number>;
  tokens: { type: string; tokens: number; shared: number }[];
  recent: { id: string; kind: string; ts: string; title: string | null; counts: Record<string, number>; total: number }[];
  eval: RedactionEval | null;
  canary: CanaryReport | null;
  outbound_guard: { blocked_total: number; by_type: Record<string, number> };
  policy: { category: string; handling: string }[];
};
export type RedactionPreview = {
  redacted: string;
  spans: { type: string; start: number; end: number; score: number; recognizer: string }[];
  counts: Record<string, number>;
  note: string;
};

// ------------------------------------------------------------------------------------------------ transport
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

function qs(params?: Params): string {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

async function detail(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (typeof j?.detail === "string") return j.detail;
    if (Array.isArray(j?.detail)) return j.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ");
    return JSON.stringify(j);
  } catch {
    return r.statusText || `HTTP ${r.status}`;
  }
}

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) throw new ApiError(r.status, await detail(r));
  return (await r.json()) as T;
}

export function get<T>(path: string, params?: Params): Promise<T> {
  return fetch(path + qs(params), { credentials: "same-origin", cache: "no-store" }).then((r) => handle<T>(r));
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  }).then((r) => handle<T>(r));
}

export function del<T>(path: string, params?: Params): Promise<T> {
  return fetch(path + qs(params), { method: "DELETE", credentials: "same-origin" }).then((r) => handle<T>(r));
}

/** POST an SSE endpoint and call onEvent for every JSON event until the stream ends. Throws ApiError on HTTP errors
 *  (429 rate limit / budget, 400, 403 ...). Abort with the signal to stop early. */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!r.ok || !r.body) throw new ApiError(r.status, await detail(r));
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const flush = (block: string) => {
    let event = "";
    const data: string[] = [];
    for (const raw of block.split(/\r?\n/)) {
      if (!raw || raw.startsWith(":")) continue; // comment / ping
      const i = raw.indexOf(":");
      const field = i < 0 ? raw : raw.slice(0, i);
      const value = i < 0 ? "" : raw.slice(i + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (!data.length) return;
    try {
      const ev = JSON.parse(data.join("\n"));
      if (ev && typeof ev === "object") {
        if (!ev.type && event) ev.type = event;
        if (ev.type && ev.type !== "ping") onEvent(ev as ChatEvent);
      }
    } catch {
      /* ignore non-JSON keep-alives */
    }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let m: RegExpExecArray | null;
    const sep = /\r?\n\r?\n/g;
    let last = 0;
    while ((m = sep.exec(buf))) {
      flush(buf.slice(last, m.index));
      last = m.index + m[0].length;
    }
    buf = buf.slice(last);
  }
  if (buf.trim()) flush(buf);
}

// ------------------------------------------------------------------------------------------------ endpoints
type Who = { tenant: string; persona: string };

export const API = {
  health: () => get<{ ok: boolean; graphs: number }>("/api/healthz"),
  tenants: () => get<Tenant[]>("/api/tenants"),
  whoami: (w: Who) => get<WhoAmI>("/api/whoami", w),
  guided: (tenant: string) => get<{ tenant: string; questions: Guided[] }>("/api/guided", { tenant }),
  budget: () => get<Budget>("/api/budget"),

  chat: (req: ChatRequest, onEvent: (ev: ChatEvent) => void, signal?: AbortSignal) =>
    streamSSE("/api/chat", req, onEvent, signal),

  // memory (per visitor + persona graph)
  memory: (w: Who) => get<MemoryOverview>("/api/memory", w),
  memorySession: (w: Who, id: string) =>
    get<{ session: Record<string, unknown>; turns: Record<string, unknown>[] }>(`/api/memory/sessions/${encodeURIComponent(id)}`, w),
  forgetSession: (w: Who, id: string) => del<{ forgotten: string }>(`/api/memory/sessions/${encodeURIComponent(id)}`, w),
  forgetFact: (w: Who, id: string) => del<Record<string, unknown>>(`/api/memory/facts/${encodeURIComponent(id)}`, w),
  forgetAll: (w: Who) => del<Record<string, unknown>>("/api/memory", w),
  playbooks: (w: Who) => get<{ playbooks: Record<string, unknown>[] }>("/api/playbooks", w),

  // live feed + reset
  live: (tenant: string) => get<LiveStatus>("/api/live", { tenant }),
  advance: (tenant: string, days = 1) => post<LiveAdvanceResult>("/api/live/advance", { tenant, days }),
  reset: (tenant: string) => post<ResetResult>("/api/demo/reset", { tenant }),

  // investigations + coordination
  cases: (w: Who) => get<{ cases: CaseSummary[] }>("/api/cases", w),
  caseDetail: (w: Who, id: string) => get<{ case: CaseDetail }>(`/api/cases/${encodeURIComponent(id)}`, w),
  tasks: (w: Who) => get<{ tasks: Record<string, unknown>[] }>("/api/tasks", w),
  blackboard: (w: Who) => get<{ tasks: Record<string, unknown>[]; handoffs: Record<string, unknown>[] }>("/api/blackboard", w),
  runTask: (w: Who, id: string, onEvent: (ev: ChatEvent) => void, signal?: AbortSignal) =>
    streamSSE(`/api/tasks/${encodeURIComponent(id)}/run`, w, onEvent, signal),
  decide: (w: Who, actionId: string, approve: boolean, note = "") =>
    post<Record<string, unknown>>(`/api/actions/${encodeURIComponent(actionId)}/decision`, { ...w, approve, note }),

  // graph explorer + documents
  search: (w: Who, q: string, limit = 20) => get<{ query: string; results: SearchResult[] }>("/api/graph/search", { ...w, q, limit }),
  neighborhood: (w: Who, entity: string, limit = 30) =>
    get<Neighborhood>("/api/graph/neighborhood", { ...w, entity, limit }),
  doc: (w: Who, id: string) => get<DocDetail>(`/api/docs/${encodeURIComponent(id)}`, w),

  // redaction
  redaction: (w: Who) => get<RedactionSummary>("/api/redaction", w),
  redactPreview: (tenant: string, text: string) => post<RedactionPreview>("/api/redaction/preview", { tenant, text }),
  canary: (tenant: string) => post<CanaryReport>("/api/redaction/canary", { tenant }),
};

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.status === 429 ? `Slow down: ${e.message}` : e.message;
  if (e instanceof Error) return e.name === "AbortError" ? "Stopped." : e.message;
  return String(e);
}

/** Document ids as written by agents and stored in the graph: email-00301, slack-01399, ticket-0042, ... */
export const DOC_ID_RE = /\b(?:email|slack|ticket|doc|meeting|crm)-\d{3,6}\b/g;
/** Entity ids worth linking to the Graph Explorer: people, clients, accounts, transactions, counterparties, cases. */
export const ENTITY_ID_RE = /\b(?:e_\d{3}|c_\d{4}|a_\d{5}|t_r?\d{3,5}|cp_\d{3}|case_[a-z0-9]{6,}|act_[a-z0-9]{6,}|task_[a-z0-9]{6,})\b/g;
