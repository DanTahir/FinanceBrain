"use client";

// Markdown for agent answers. Document ids (email-00301) become citation chips that open the DocViewer; entity ids
// (c_0060, a_00238, t_r025, ...) become links that focus the Graph Explorer. Vault tokens are highlighted.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DOC_ID_RE, ENTITY_ID_RE } from "@/lib/api";
import { useApp } from "@/lib/state";

function linkify(text: string): string {
  // Skip fenced code blocks; linkify ids elsewhere.
  return text
    .split(/(```[\s\S]*?```)/g)
    .map((chunk, i) =>
      i % 2 === 1
        ? chunk
        : chunk
            .replace(DOC_ID_RE, (id) => `[${id}](#doc:${id})`)
            .replace(ENTITY_ID_RE, (id) => `[${id}](#ent:${id})`)
            .replace(/(⟨[A-Z][A-Z0-9_]*⟩)/g, "`$1`"),
    )
    .join("");
}

export default function Markdown({ text, citationNumbers }: { text: string; citationNumbers?: Record<string, number> }) {
  const { openDoc, openEntity } = useApp();
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(u) => u}
        components={{
          a({ href, children }) {
            if (href?.startsWith("#doc:")) {
              const id = href.slice(5);
              const n = citationNumbers?.[id];
              return (
                <a className="cite-chip" href={href} onClick={(e) => { e.preventDefault(); openDoc(id); }} title={`Open source document ${id}`}>
                  {n ? `[${n}] ` : ""}
                  {children}
                </a>
              );
            }
            if (href?.startsWith("#ent:")) {
              const id = href.slice(5);
              return (
                <a className="ent-chip" href={href} onClick={(e) => { e.preventDefault(); openEntity(id); }} title={`Show ${id} in the Graph Explorer`}>
                  {children}
                </a>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          code({ children, className }) {
            const s = String(children);
            if (!className && /^⟨[A-Z][A-Z0-9_]*⟩$/.test(s)) return <span className="pii-token">{s}</span>;
            return <code className={className}>{children}</code>;
          },
        }}
      >
        {linkify(text)}
      </ReactMarkdown>
    </div>
  );
}
