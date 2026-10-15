"use client";

// Renders redacted text with every vault token (⟨PHONE_0042⟩, ⟨CLIENT_NAME_0007⟩, ...) highlighted.

const TOKEN_RE = /(⟨[A-Z][A-Z0-9_]*⟩)/g;

export default function RedactedText({ text, className }: { text: string; className?: string }) {
  const parts = text.split(TOKEN_RE);
  return (
    <div className={className ?? "whitespace-pre-wrap text-sm leading-relaxed text-slate-200"}>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <span key={i} className="pii-token" title="Pseudonymized: the original value is only in the encrypted vault">
            {p}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </div>
  );
}
