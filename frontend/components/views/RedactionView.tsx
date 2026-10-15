"use client";

// Ingestion & Redaction: the live feed that keeps the brain current (Track 3), redaction metrics proving no raw PII
// reaches the graph, the canary scan, a no-persist redactor preview and the classification policy.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  API,
  ApiError,
  DOC_ID_RE,
  ENTITY_ID_RE,
  errorText,
  type LiveStatus,
  type RedactionPreview,
  type RedactionSummary,
} from "@/lib/api";
import { useApp } from "@/lib/state";
import { Badge, Button, Card, Empty, ErrorNote, Spinner, Stat, cx, fmtTs, pct, severityTone } from "@/components/ui";
import RedactedText from "@/components/RedactedText";

type Rec = Record<string, unknown>;

const SAMPLE =
  "Hi team, new client Dana Whitfield (dana.whitfield@example.com, +1 212-555-0198) wants to wire funds. Card on file 4111 1111 1111 1111, routing 021000021, IBAN GB82 WEST 1234 5698 7654 32. Deal codename stays visible: Project Falcon.";

// ------------------------------------------------------------------------------------------------ defensive readers
function rec(v: unknown): Rec | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : null;
}
function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null;
}
function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map((x) => fmtVal(x)).join(", ");
  try {
    const s = JSON.stringify(v);
    return s.length > 160 ? `${s.slice(0, 159)}…` : s;
  } catch {
    return String(v);
  }
}
function sumCounts(c: Record<string, number> | null | undefined): number {
  if (!c) return 0;
  return Object.values(c).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);
}
function numCounts(v: unknown): [string, number][] {
  const r = rec(v);
  if (!r) return [];
  return Object.entries(r).filter((e): e is [string, number] => typeof e[1] === "number" && e[1] !== 0);
}

// ------------------------------------------------------------------------------------------------ linkified text
const LINK_RE_SRC = `${DOC_ID_RE.source}|${ENTITY_ID_RE.source}`;
const DOC_ONE = new RegExp(`^(?:${DOC_ID_RE.source})$`);

function Linked({ text }: { text: string }) {
  const { openDoc, openEntity } = useApp();
  const re = new RegExp(LINK_RE_SRC, "g");
  const out: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    const id = m[0];
    if (m.index > last) out.push(<span key={k++}>{text.slice(last, m.index)}</span>);
    const isDoc = DOC_ONE.test(id);
    out.push(
      <button
        key={k++}
        className={cx("font-mono underline decoration-dotted", isDoc ? "text-sky-300 hover:text-sky-200" : "text-violet-300 hover:text-violet-200")}
        title={isDoc ? `Open document ${id}` : `Show ${id} in the Graph Explorer`}
        onClick={() => (isDoc ? openDoc(id) : openEntity(id))}
      >
        {id}
      </button>,
    );
    last = m.index + id.length;
  }
  if (last < text.length) out.push(<span key={k++}>{text.slice(last)}</span>);
  return <>{out}</>;
}

// ------------------------------------------------------------------------------------------------ live feed log
function GenericFields({ r, skip }: { r: Rec; skip: string[] }) {
  const entries = Object.entries(r).filter(([k, v]) => !skip.includes(k) && v !== null && v !== undefined && v !== "");
  if (!entries.length) return null;
  return (
    <span className="text-slate-400">
      {entries.map(([k, v]) => (
        <span key={k} className="mr-2">
          <span className="text-slate-500">{k}:</span> <Linked text={fmtVal(v)} />
        </span>
      ))}
    </span>
  );
}

function ReplayRow({ r }: { r: Rec }) {
  const { setView } = useApp();
  const day = str(r.day) ?? "?";
  const docs = num(r.docs);
  const txns = num(r.txns);
  const joiners = arr(r.joiners).map(fmtVal);
  const leavers = arr(r.leavers).map(fmtVal);
  const changes = arr(r.ownership_changes).map(rec).filter((x): x is Rec => !!x);
  const ingest = numCounts(r.ingest);
  const extraction = numCounts(r.extraction);
  const derivedRec = rec(r.derived);
  const derived = derivedRec ? numCounts(derivedRec) : num(r.derived) !== null ? [["derived", num(r.derived) as number] as [string, number]] : [];
  const sent = rec(r.sentinel);
  const cases = arr(sent?.cases).map(rec).filter((x): x is Rec => !!x);
  const alerts = arr(sent?.alerts).map(rec).filter((x): x is Rec => !!x);
  const ms = num(r.ms);

  return (
    <div className="space-y-1.5 rounded-md border border-slate-800 bg-slate-950/40 p-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono font-semibold text-slate-100">{day}</span>
        {docs !== null && <Badge tone="blue">{docs} docs</Badge>}
        {txns !== null && <Badge tone="violet">{txns} txns</Badge>}
        {joiners.length > 0 && <Badge tone="green">+{joiners.length} joiner{joiners.length > 1 ? "s" : ""}</Badge>}
        {leavers.length > 0 && <Badge tone="red">{leavers.length} leaver{leavers.length > 1 ? "s" : ""}</Badge>}
        {changes.length > 0 && <Badge tone="amber">{changes.length} owner change{changes.length > 1 ? "s" : ""}</Badge>}
        {ms !== null && <span className="ml-auto text-slate-500">{(ms / 1000).toFixed(1)} s</span>}
      </div>
      {(joiners.length > 0 || leavers.length > 0) && (
        <div className="text-slate-400">
          {joiners.length > 0 && (
            <span className="mr-3">
              joined: <Linked text={joiners.join(", ")} />
            </span>
          )}
          {leavers.length > 0 && (
            <span>
              left: <Linked text={leavers.join(", ")} />
            </span>
          )}
        </div>
      )}
      {changes.map((c, i) => (
        <div key={i} className="text-slate-400">
          <span className="text-amber-300">ownership</span> <Linked text={str(c.system) ?? "?"} />:{" "}
          <Linked text={[str(c.from_team), str(c.from_person)].filter(Boolean).join(" / ") || "—"} /> →{" "}
          <Linked text={[str(c.to_team), str(c.to_person)].filter(Boolean).join(" / ") || "—"} />
          {str(c.valid_from) && <span className="text-slate-500"> (from {str(c.valid_from)})</span>}
        </div>
      ))}
      {(ingest.length > 0 || extraction.length > 0 || derived.length > 0) && (
        <div className="flex flex-wrap gap-1">
          {ingest.map(([k, v]) => (
            <Badge key={`i-${k}`} title="ingestion pipeline counter">
              {k}: {v}
            </Badge>
          ))}
          {extraction.map(([k, v]) => (
            <Badge key={`e-${k}`} tone="cyan" title="entity extraction counter">
              extract {k}: {v}
            </Badge>
          ))}
          {derived.map(([k, v]) => (
            <Badge key={`d-${k}`} tone="violet" title="derived FLOWS_TO / COMMUNICATES edges">
              {k}: {v}
            </Badge>
          ))}
        </div>
      )}
      {(cases.length > 0 || alerts.length > 0) && (
        <div className="space-y-1 rounded border border-amber-500/30 bg-amber-500/10 p-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold text-amber-200">Sentinel: {cases.length} case{cases.length === 1 ? "" : "s"}, {alerts.length} alert{alerts.length === 1 ? "" : "s"}</span>
            <Button size="sm" variant="secondary" onClick={() => setView("investigations")}>
              Open Investigations
            </Button>
          </div>
          {cases.map((c, i) => (
            <div key={`c-${i}`} className="flex flex-wrap items-center gap-1.5 text-amber-100">
              <Badge tone="amber">case</Badge>
              {str(c.severity) && <Badge tone={severityTone(str(c.severity))}>{str(c.severity)}</Badge>}
              {str(c.kind) && <Badge>{str(c.kind)}</Badge>}
              {str(c.case_id) && <Linked text={str(c.case_id) as string} />}
              <span>{str(c.title) ?? ""}</span>
              {str(c.handoff) && <span className="text-slate-400">→ handoff <Linked text={fmtVal(c.handoff)} /></span>}
              {str(c.error) && <span className="text-rose-300">{str(c.error)}</span>}
            </div>
          ))}
          {alerts.map((a, i) => (
            <div key={`a-${i}`} className="flex flex-wrap items-center gap-1.5 text-amber-100">
              <Badge tone="amber">alert</Badge>
              {str(a.severity) && <Badge tone={severityTone(str(a.severity))}>{str(a.severity)}</Badge>}
              {str(a.kind) && <Badge>{str(a.kind)}</Badge>}
              <GenericFields r={a} skip={["kind", "severity"]} />
            </div>
          ))}
        </div>
      )}
      {sent && !cases.length && !alerts.length && <div className="text-slate-500">Sentinel scanned the batch: nothing suspicious.</div>}
    </div>
  );
}

function LiveCard({
  live,
  error,
  log,
  running,
  advErr,
  onAdvance,
}: {
  live: LiveStatus | null;
  error: string | null;
  log: Rec[] | null;
  running: number;
  advErr: string | null;
  onAdvance: (n: number) => void;
}) {
  const done = !live || live.remaining_days === 0;
  return (
    <Card
      title="Live feed · Track 3: keeping knowledge current"
      actions={
        <>
          <Button size="sm" variant="primary" disabled={!!running || done} onClick={() => onAdvance(1)}>
            {running === 1 && <Spinner />} Replay next day
          </Button>
          <Button size="sm" disabled={!!running || done} onClick={() => onAdvance(3)}>
            {running === 3 && <Spinner />} Replay 3 days
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-xs text-slate-400">
          Each replayed day runs the real pipeline: redact → extract → resolve → embed → write, then the Sentinel agent scans the new data and may open cases.
        </p>
        <ErrorNote error={error} />
        {live && (
          <>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
              <div><span className="text-slate-500">cutoff</span> <span className="font-mono text-slate-200">{live.cutoff}</span></div>
              <div><span className="text-slate-500">cursor</span> <span className="font-mono text-slate-200">{live.cursor ?? "not started"}</span></div>
              <div><span className="text-slate-500">next day</span> <span className="font-mono text-slate-200">{live.next_day ?? "—"}</span></div>
              <div><span className="text-slate-500">remaining days</span> <span className="text-slate-200">{live.remaining_days}</span></div>
              <div><span className="text-slate-500">remaining docs</span> <span className="text-slate-200">{live.remaining_docs}</span></div>
              <div><span className="text-slate-500">remaining txns</span> <span className="text-slate-200">{live.remaining_txns}</span></div>
            </div>
            {live.days.length ? (
              <div className="flex gap-1.5 overflow-x-auto pb-1">
                {live.days.map((d) => {
                  const next = d.day === live.next_day;
                  return (
                    <div
                      key={d.day}
                      title={`${d.day}: ${d.docs} docs, ${d.txns} txns, ${d.ownership} ownership events, ${d.joiners} joiners, ${d.leavers} leavers`}
                      className={cx(
                        "min-w-[72px] shrink-0 rounded-md border px-1.5 py-1 text-[11px]",
                        d.done ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-200" : next ? "border-blue-400 bg-slate-900 text-slate-200 ring-1 ring-blue-400" : "border-slate-800 bg-slate-900 text-slate-400",
                      )}
                    >
                      <div className="font-mono font-semibold">{d.day.slice(5, 10)}</div>
                      <div>{d.docs} docs · {d.txns} tx</div>
                      <div className="flex flex-wrap gap-0.5">
                        {d.ownership > 0 && <span className="text-amber-300">⇄ owner change</span>}
                        {d.joiners > 0 && <span className="text-emerald-300">+{d.joiners} join</span>}
                        {d.leavers > 0 && <span className="text-rose-300">−{d.leavers} leave</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <Empty>No post-cutoff events in this tenant's timeline.</Empty>
            )}
            {done && <div className="text-xs text-emerald-300">All live-feed days replayed. Reset the demo to start over.</div>}
          </>
        )}
        {!live && !error && <Spinner />}
        <ErrorNote error={advErr} />
        {running > 0 && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Spinner /> Replaying {running} day{running > 1 ? "s" : ""} through ingestion and the Sentinel…
          </div>
        )}
        {log && (
          <div className="space-y-1.5">
            <div className="text-[11px] uppercase tracking-wide text-slate-500">Replay log</div>
            {log.length ? log.map((r, i) => <ReplayRow key={`${str(r.day) ?? i}-${i}`} r={r} />) : <Empty>Nothing was pending.</Empty>}
          </div>
        )}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------ redactor preview
function Highlighted({ text, spans }: { text: string; spans: RedactionPreview["spans"] }) {
  const sorted = [...spans].sort((a, b) => a.start - b.start || b.end - a.end);
  const out: React.ReactNode[] = [];
  let pos = 0;
  sorted.forEach((s, i) => {
    if (s.start < pos || s.start >= text.length) return;
    if (s.start > pos) out.push(<span key={`t${i}`}>{text.slice(pos, s.start)}</span>);
    out.push(
      <span key={`s${i}`} className="rounded-sm bg-rose-500/20 text-rose-200 underline decoration-rose-400 decoration-2 underline-offset-2" title={`${s.type} · ${s.recognizer} · ${s.score}`}>
        {text.slice(s.start, Math.min(s.end, text.length))}
      </span>,
    );
    pos = Math.min(s.end, text.length);
  });
  if (pos < text.length) out.push(<span key="tail">{text.slice(pos)}</span>);
  return <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{out}</div>;
}

function CanaryAndTryCard({ summary, onCanaryDone }: { summary: RedactionSummary | null; onCanaryDone: () => void }) {
  const { tenant } = useApp();
  const [scanning, setScanning] = useState(false);
  const [canaryErr, setCanaryErr] = useState<string | null>(null);
  const [text, setText] = useState(SAMPLE);
  const [res, setRes] = useState<RedactionPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [resText, setResText] = useState("");

  useEffect(() => {
    setRes(null);
    setCanaryErr(null);
    setPreviewErr(null);
  }, [tenant]);

  const runCanary = async () => {
    setScanning(true);
    setCanaryErr(null);
    try {
      await API.canary(tenant);
      onCanaryDone();
    } catch (e) {
      setCanaryErr(errorText(e));
    } finally {
      setScanning(false);
    }
  };
  const redact = async () => {
    setPreviewing(true);
    setPreviewErr(null);
    try {
      const r = await API.redactPreview(tenant, text);
      setRes(r);
      setResText(text);
    } catch (e) {
      setPreviewErr(errorText(e));
    } finally {
      setPreviewing(false);
    }
  };

  const c = summary?.canary ?? null;
  const hits = c ? Object.entries(c.by_type ?? {}).filter(([, v]) => v > 0) : [];
  return (
    <div className="space-y-4">
      <Card
        title="Canary scan"
        actions={
          <Button size="sm" variant="primary" disabled={scanning} onClick={runCanary}>
            {scanning && <Spinner />} Run canary scan now
          </Button>
        }
      >
        <div className="space-y-2 text-xs">
          <p className="text-slate-400">
            Scans every string property in the tenant&apos;s brain graph and all of its memory graphs for raw SSNs, card numbers (Luhn), IBANs, routing numbers, phones, emails and client names. Must be 0.
          </p>
          <ErrorNote error={canaryErr} />
          {scanning && <div className="flex items-center gap-2 text-slate-400"><Spinner /> Scanning graphs, this can take several seconds…</div>}
          {c ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone={c.clean ? "green" : "red"}>{c.clean ? "clean" : `${c.hits} hits`}</Badge>
              <span className="text-slate-400">
                {c.strings_scanned.toLocaleString()} strings · {c.graphs} graphs · {fmtTs(c.ts)} · {c.seconds}s
              </span>
              {hits.map(([k, v]) => (
                <Badge key={k} tone="red">{k}: {v}</Badge>
              ))}
            </div>
          ) : (
            !scanning && <div className="text-slate-500">No scan recorded yet for this tenant.</div>
          )}
        </div>
      </Card>
      <Card title="Try the redactor">
        <div className="space-y-2">
          <p className="text-xs text-slate-400">Runs the same Presidio + finance recognizers used at ingestion. Preview only: nothing is tokenized, stored or sent to a model.</p>
          <textarea
            className="h-28 w-full rounded-md border border-slate-700 bg-slate-950 p-2 text-sm text-slate-200 focus:border-blue-500 focus:outline-none"
            value={text}
            maxLength={5000}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex items-center gap-2">
            <Button size="sm" variant="primary" disabled={previewing || !text.trim()} onClick={redact}>
              {previewing && <Spinner />} Redact
            </Button>
            <span className="text-[11px] text-slate-500">Sample text is synthetic.</span>
          </div>
          <ErrorNote error={previewErr} />
          {res && (
            <div className="space-y-2">
              <div className="text-[11px] uppercase tracking-wide text-slate-500">Detected spans (original)</div>
              <div className="rounded-md border border-slate-800 bg-slate-950/50 p-2">
                <Highlighted text={resText} spans={res.spans ?? []} />
              </div>
              <div className="text-[11px] uppercase tracking-wide text-slate-500">What the graph would store</div>
              <div className="rounded-md border border-slate-800 bg-slate-950/50 p-2">
                <RedactedText text={res.redacted} />
              </div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(res.counts ?? {}).map(([k, v]) => (
                  <Badge key={k} tone="red">{k}: {v}</Badge>
                ))}
                {!Object.keys(res.counts ?? {}).length && <span className="text-xs text-slate-500">No PII detected.</span>}
              </div>
              <div className="text-xs text-slate-500">{res.note}</div>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ tables
function ByTypeCard({ s }: { s: RedactionSummary }) {
  const byType = s.by_type ?? {};
  const tokens = s.tokens ?? [];
  const evalBy = s.eval?.by_type ?? {};
  const types = Array.from(new Set([...Object.keys(byType), ...tokens.map((t) => t.type), ...Object.keys(evalBy)]));
  types.sort((a, b) => (byType[b] ?? 0) - (byType[a] ?? 0) || a.localeCompare(b));
  return (
    <Card title="By PII type">
      <p className="mb-2 text-xs text-slate-400">Every value is replaced by a consistent token, so identical values still connect in the graph without ever being stored.</p>
      {types.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-slate-500">
              <tr className="border-b border-slate-800">
                <th className="py-1.5 pr-3 font-medium">Type</th>
                <th className="py-1.5 pr-3 text-right font-medium">Pseudonymized in graph</th>
                <th className="py-1.5 pr-3 text-right font-medium">Unique tokens</th>
                <th className="py-1.5 pr-3 text-right font-medium">Shared tokens</th>
                <th className="py-1.5 pr-3 text-right font-medium">Eval recall</th>
                <th className="py-1.5 text-right font-medium">Eval precision</th>
              </tr>
            </thead>
            <tbody>
              {types.map((t) => {
                const tok = tokens.find((x) => x.type === t);
                const ev = evalBy[t];
                const shared = tok?.shared ?? 0;
                return (
                  <tr key={t} className="border-b border-slate-800/60 text-slate-300">
                    <td className="py-1.5 pr-3 font-mono text-rose-200">{t}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{(byType[t] ?? 0).toLocaleString()}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{tok ? tok.tokens.toLocaleString() : "–"}</td>
                    <td
                      className={cx("py-1.5 pr-3 text-right tabular-nums", shared > 0 && "font-semibold text-amber-300")}
                      title={shared > 0 ? "same value used by more than one client/account — how the fraud ring shows up as graph structure" : undefined}
                    >
                      {tok ? shared.toLocaleString() : "–"}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{ev ? pct(ev.recall_typed) : "–"}</td>
                    <td className="py-1.5 text-right tabular-nums">{ev ? pct(ev.precision) : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No redactions recorded for this tenant.</Empty>
      )}
    </Card>
  );
}

function RecentCard({ s }: { s: RedactionSummary }) {
  const { openDoc } = useApp();
  const recent = s.recent ?? [];
  return (
    <Card title="Recently redacted documents (visible to you)">
      <p className="mb-2 text-xs text-slate-400">Filtered by your persona&apos;s ACLs; open one to see its pseudonymized text.</p>
      {recent.length ? (
        <ul className="divide-y divide-slate-800/70">
          {recent.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-2 py-1.5 text-xs">
              <button className="font-mono text-sky-300 underline decoration-dotted hover:text-sky-200" onClick={() => openDoc(d.id)}>
                {d.id}
              </button>
              {d.kind && <Badge>{d.kind}</Badge>}
              <span className="text-slate-500">{fmtTs(d.ts)}</span>
              <span className="min-w-0 flex-1 truncate text-slate-300">{d.title ?? ""}</span>
              <span className="flex flex-wrap gap-1">
                {Object.entries(d.counts ?? {}).map(([k, v]) => (
                  <Badge key={k} tone="red">{k}: {v}</Badge>
                ))}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No redacted documents visible to this persona.</Empty>
      )}
    </Card>
  );
}

function PolicyCard({ s }: { s: RedactionSummary }) {
  const rows = s.policy ?? [];
  return (
    <Card title="Classification policy">
      <p className="mb-2 text-xs text-slate-400">What is kept, what is pseudonymized, and what is access-controlled instead of redacted.</p>
      {rows.length ? (
        <table className="w-full text-left text-xs">
          <thead className="text-slate-500">
            <tr className="border-b border-slate-800">
              <th className="py-1.5 pr-3 font-medium">Category</th>
              <th className="py-1.5 font-medium">Handling</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-slate-800/60 align-top text-slate-300">
                <td className="py-1.5 pr-3">{r.category}</td>
                <td className="py-1.5"><RedactedText text={r.handling} className="text-xs text-slate-300" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No policy rows.</Empty>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------ view
export default function RedactionView() {
  const { tenant, persona, view, version, bump, tenantInfo, setPersona } = useApp();
  const [summary, setSummary] = useState<RedactionSummary | null>(null);
  const [live, setLive] = useState<LiveStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [errStatus, setErrStatus] = useState<number | null>(null);
  const [liveErr, setLiveErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [stale, setStale] = useState(true);
  const [log, setLog] = useState<Rec[] | null>(null);
  const [running, setRunning] = useState(0);
  const [advErr, setAdvErr] = useState<string | null>(null);
  const reqId = useRef(0);

  const load = useCallback(async () => {
    const id = ++reqId.current;
    setLoading(true);
    const who = { tenant, persona };
    const [s, l] = await Promise.allSettled([API.redaction(who), API.live(tenant)]);
    if (id !== reqId.current) return;
    if (s.status === "fulfilled") {
      setSummary(s.value);
      setErr(null);
      setErrStatus(null);
    } else {
      setErr(errorText(s.reason));
      setErrStatus(s.reason instanceof ApiError ? s.reason.status : null);
    }
    if (l.status === "fulfilled") {
      setLive(l.value);
      setLiveErr(null);
    } else setLiveErr(errorText(l.reason));
    setLoading(false);
  }, [tenant, persona]);

  useEffect(() => {
    setStale(true);
  }, [tenant, persona, version]);

  useEffect(() => {
    setSummary(null);
    setLive(null);
    setLog(null);
    setAdvErr(null);
  }, [tenant]);

  useEffect(() => {
    if (view === "redaction" && stale) {
      setStale(false);
      void load();
    }
  }, [view, stale, load]);

  const advance = async (n: number) => {
    setRunning(n);
    setAdvErr(null);
    try {
      const r = await API.advance(tenant, n);
      setLive(r.status);
      setLog(Array.isArray(r.replayed) ? r.replayed : []);
      bump();
    } catch (e) {
      setAdvErr(errorText(e));
    } finally {
      setRunning(0);
    }
  };

  const ev = summary?.eval?.summary ?? null;
  const canary = summary?.canary ?? null;
  const piiTotal = sumCounts(summary?.by_type);
  const scopedPersonas = (tenantInfo?.personas ?? []).filter((p) => p.key !== persona);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">Ingestion &amp; Redaction</h2>
        <p className="text-sm text-slate-400">
          Proof that the brain stays current and never holds raw PII: every document is pseudonymized before it touches the graph. All data is synthetic.
        </p>
      </div>

      {err && (
        <div className="space-y-2">
          <ErrorNote error={errStatus === 403 ? `This persona lacks the scope to view redaction metrics (${err}).` : err} onRetry={() => void load()} />
          {errStatus === 403 && scopedPersonas.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
              Switch persona:
              {scopedPersonas.map((p) => (
                <Button key={p.key} size="sm" onClick={() => setPersona(p.key)}>
                  {p.label || p.name}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}

      {loading && !summary && !err && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner /> Loading redaction metrics…
        </div>
      )}

      {summary && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Documents redacted" value={(summary.documents_redacted ?? 0).toLocaleString()} />
          <Stat label="PII values pseudonymized" value={piiTotal.toLocaleString()} />
          <Stat
            label="Redaction recall"
            value={ev ? pct(ev.recall_any) : "–"}
            sub={ev ? `${ev.gt_spans} injected spans` : "eval not run"}
            tone={ev && ev.recall_any === 1 ? "green" : undefined}
          />
          <Stat label="Precision" value={ev ? pct(ev.precision) : "–"} sub={ev ? `${ev.detections} detections` : undefined} />
          <Stat
            label="Canary scan"
            value={canary ? (canary.clean ? "0 hits" : `${canary.hits} hits`) : "not run"}
            tone={canary ? (canary.clean ? "green" : "red") : undefined}
            sub={canary ? `${canary.strings_scanned.toLocaleString()} strings · ${canary.graphs} graphs · ${fmtTs(canary.ts)}` : undefined}
          />
          <Stat label="Outbound guard blocks" value={(summary.outbound_guard?.blocked_total ?? 0).toLocaleString()} sub="prompts stopped before reaching a model" />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <LiveCard live={live} error={liveErr} log={log} running={running} advErr={advErr} onAdvance={(n) => void advance(n)} />
        <CanaryAndTryCard summary={summary} onCanaryDone={() => void load()} />
      </div>

      {summary && (
        <>
          <ByTypeCard s={summary} />
          <RecentCard s={summary} />
          <PolicyCard s={summary} />
        </>
      )}
    </div>
  );
}