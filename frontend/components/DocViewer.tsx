"use client";

// Modal that opens a redacted source document (citations and document nodes link here).

import { useEffect, useState } from "react";
import { API, errorText, type DocDetail } from "@/lib/api";
import { useApp } from "@/lib/state";
import RedactedText from "@/components/RedactedText";
import { Badge, ErrorNote, Modal, Spinner, fmtTs } from "@/components/ui";

const SKIP = new Set(["id", "label", "name", "text", "title", "redaction", "kind", "ts", "embedding"]);

export default function DocViewer() {
  const { docId, openDoc, who, openEntity } = useApp();
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!docId) return;
    setDoc(null);
    setErr(null);
    API.doc(who, docId)
      .then(setDoc)
      .catch((e) => setErr(errorText(e)));
  }, [docId, who]);

  const meta = doc
    ? Object.entries(doc).filter(([k, v]) => !SKIP.has(k) && v !== null && v !== undefined && typeof v !== "object" && String(v).length < 200)
    : [];

  return (
    <Modal
      open={!!docId}
      onClose={() => openDoc(null)}
      wide
      title={
        <span className="flex items-center gap-2">
          <span className="font-mono text-xs text-blue-300">{docId}</span>
          {doc?.kind && <Badge>{doc.kind}</Badge>}
          <span className="truncate">{doc?.title || ""}</span>
        </span>
      }
    >
      {err && <ErrorNote error={err} />}
      {!doc && !err && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner /> loading…
        </div>
      )}
      {doc && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            {doc.ts && <span>{fmtTs(doc.ts)}</span>}
            {meta.map(([k, v]) => (
              <span key={k} className="rounded bg-slate-800 px-1.5 py-0.5">
                <span className="text-slate-500">{k}:</span> {String(v)}
              </span>
            ))}
          </div>
          {doc.redaction && doc.redaction.total > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-rose-500/20 bg-rose-500/5 px-2 py-1.5 text-xs text-rose-200">
              <span className="font-medium">Redacted before ingestion:</span>
              {Object.entries(doc.redaction.counts).map(([t, n]) => (
                <Badge key={t} tone="red">
                  {t} ×{n}
                </Badge>
              ))}
            </div>
          ) : (
            <div className="text-xs text-slate-500">No personal data was found in this document.</div>
          )}
          <div className="rounded-md border border-slate-800 bg-slate-950 p-3">
            <RedactedText text={String(doc.text ?? "")} />
          </div>
          <div className="flex justify-end">
            <button
              className="text-xs text-blue-300 underline"
              onClick={() => {
                openDoc(null);
                openEntity(doc.id);
              }}
            >
              Show in Graph Explorer
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
