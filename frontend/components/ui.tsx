"use client";

// Small shared UI primitives (dark theme). Views compose these instead of re-inventing styles.

import { useState } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Node colours per graph label; shared by GraphCanvas, legends and badges. */
export const LABEL_COLORS: Record<string, string> = {
  Person: "#60a5fa",
  Team: "#a78bfa",
  System: "#34d399",
  Project: "#2dd4bf",
  Policy: "#fbbf24",
  Document: "#94a3b8",
  Channel: "#f472b6",
  Thread: "#cbd5e1",
  Topic: "#e2e8f0",
  Client: "#fb923c",
  Account: "#f97316",
  Transaction: "#facc15",
  Counterparty: "#fda4af",
  Deal: "#ef4444",
  Decision: "#c084fc",
  Fact: "#818cf8",
  PiiToken: "#f43f5e",
  Case: "#f87171",
  Action: "#22d3ee",
  Task: "#a3e635",
  Agent: "#e879f9",
  Playbook: "#4ade80",
  Step: "#86efac",
};
export function labelColor(label?: string | null): string {
  return (label && LABEL_COLORS[label]) || "#64748b";
}

export function Card({ title, actions, children, className }: { title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg border border-slate-800 bg-slate-900/60", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
          <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-3">{children}</div>
    </section>
  );
}

type Tone = "slate" | "blue" | "green" | "amber" | "red" | "violet" | "pink" | "cyan";
const TONES: Record<Tone, string> = {
  slate: "bg-slate-800 text-slate-300 border-slate-700",
  blue: "bg-blue-500/15 text-blue-300 border-blue-500/30",
  green: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  amber: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  red: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  violet: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  pink: "bg-pink-500/15 text-pink-300 border-pink-500/30",
  cyan: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
};
export function Badge({ tone = "slate", children, title, className }: { tone?: Tone; children: React.ReactNode; title?: string; className?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none whitespace-nowrap", TONES[tone], className)}>
      {children}
    </span>
  );
}

/** Badge coloured like the graph node of that label. */
export function LabelBadge({ label }: { label: string }) {
  const c = labelColor(label);
  return (
    <span className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] leading-none" style={{ color: c, borderColor: `${c}55`, background: `${c}1a` }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: c }} />
      {label}
    </span>
  );
}

export function severityTone(s?: string | null): Tone {
  return s === "critical" ? "red" : s === "high" ? "red" : s === "medium" ? "amber" : s === "low" ? "blue" : "slate";
}
export function statusTone(s?: string | null): Tone {
  if (!s) return "slate";
  if (["approved", "done", "completed", "closed", "executed"].includes(s)) return "green";
  if (["rejected", "failed", "blocked"].includes(s)) return "red";
  if (["proposed", "pending", "pending_approval", "open", "escalated"].includes(s)) return "amber";
  if (["claimed", "in_progress", "investigating"].includes(s)) return "blue";
  return "slate";
}

type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "success";
const BTN: Record<BtnVariant, string> = {
  primary: "bg-blue-600 hover:bg-blue-500 text-white border-blue-500",
  secondary: "bg-slate-800 hover:bg-slate-700 text-slate-100 border-slate-700",
  ghost: "bg-transparent hover:bg-slate-800 text-slate-300 border-transparent",
  danger: "bg-rose-600/90 hover:bg-rose-500 text-white border-rose-500",
  success: "bg-emerald-600/90 hover:bg-emerald-500 text-white border-emerald-500",
};
export function Button({ variant = "secondary", size = "md", className, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: "sm" | "md" }) {
  return (
    <button
      {...rest}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm",
        BTN[variant],
        className,
      )}
    />
  );
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cx("inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-500 border-t-transparent", className)} />;
}

export function ErrorNote({ error, onRetry }: { error: string | null | undefined; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="flex items-start justify-between gap-3 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
      <span>{error}</span>
      {onRetry && (
        <button className="text-xs underline" onClick={onRetry}>
          retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-md border border-dashed border-slate-800 px-3 py-6 text-center text-sm text-slate-500">{children}</div>;
}

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "green" | "red" | "amber" }) {
  const color = tone === "green" ? "text-emerald-300" : tone === "red" ? "text-rose-300" : tone === "amber" ? "text-amber-300" : "text-slate-50";
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cx("text-xl font-semibold tabular-nums", color)}>{value}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

/** Collapsible block, used for tool traces and Cypher. */
export function Disclosure({ summary, children, defaultOpen = false, className }: { summary: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={cx("rounded-md border border-slate-800", className)}>
      <button className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs text-slate-300 hover:bg-slate-800/60" onClick={() => setOpen((o) => !o)}>
        <span className={cx("inline-block transition-transform", open && "rotate-90")}>▸</span>
        <span className="min-w-0 flex-1">{summary}</span>
      </button>
      {open && <div className="border-t border-slate-800 p-2">{children}</div>}
    </div>
  );
}

export function CodeBlock({ code, className }: { code: string; className?: string }) {
  return <pre className={cx("overflow-x-auto rounded bg-slate-950 p-2 font-mono text-[11px] leading-relaxed text-emerald-200", className)}>{code}</pre>;
}

export function fmtTs(ts?: string | null): string {
  if (!ts) return "";
  const s = String(ts);
  return s.length >= 16 ? `${s.slice(0, 10)} ${s.slice(11, 16)}` : s;
}

export function pct(x?: number | null, digits = 1): string {
  return x === undefined || x === null ? "–" : `${(x * 100).toFixed(digits)}%`;
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 pt-16" onClick={onClose}>
      <div className={cx("w-full rounded-lg border border-slate-700 bg-slate-900 shadow-2xl", wide ? "max-w-4xl" : "max-w-2xl")} onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-2.5">
          <div className="min-w-0 text-sm font-semibold text-slate-100">{title}</div>
          <button className="text-slate-400 hover:text-slate-100" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="max-h-[75vh] overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}
