"use client";

// Cytoscape wrapper. Elements are diffed on every prop change, so expanding a node keeps the existing layout and only
// places the new nodes. Works inside hidden tabs: a ResizeObserver refits once the container gets a size.

import { useEffect, useRef } from "react";
import type { Core, ElementDefinition, LayoutOptions } from "cytoscape";
import type { EvEdge, EvNode } from "@/lib/api";
import { labelColor } from "@/components/ui";

export type GraphCanvasProps = {
  nodes: EvNode[];
  edges: EvEdge[];
  /** Node ids to emphasise (e.g. cited documents, the evidence path); everything else is dimmed if set. */
  highlight?: string[];
  selected?: string | null;
  onSelect?: (node: EvNode | null) => void;
  /** Double-click / double-tap on a node (Graph Explorer expands it). */
  onExpand?: (node: EvNode) => void;
  className?: string;
  /** "cose" (force-directed, default) or "concentric" (focus node in the middle). */
  layout?: "cose" | "concentric";
  /** Node to centre on / put in the middle for concentric layout. */
  focus?: string | null;
};

const SHAPES: Record<string, string> = {
  Document: "round-rectangle",
  PiiToken: "diamond",
  Transaction: "tag",
  Account: "round-hexagon",
  Decision: "star",
  Case: "octagon",
  Action: "vee",
  Deal: "pentagon",
  Channel: "round-triangle",
  System: "barrel",
};

function short(s: string, n = 26): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function toElements(nodes: EvNode[], edges: EvEdge[]): ElementDefinition[] {
  const ids = new Set(nodes.map((n) => n.id));
  const els: ElementDefinition[] = nodes.map((n) => ({
    group: "nodes",
    data: { ...n, id: n.id, display: short(String(n.name ?? n.id)), color: labelColor(n.label), shape: SHAPES[n.label] ?? "ellipse" },
  }));
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue;
    els.push({ group: "edges", data: { ...e, id: `${e.source}|${e.type}|${e.target}`, source: e.source, target: e.target, type: e.type } });
  }
  return els;
}

export default function GraphCanvas({ nodes, edges, highlight, selected, onSelect, onExpand, className, layout = "cose", focus }: GraphCanvasProps) {
  const box = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const cbs = useRef({ onSelect, onExpand });
  cbs.current = { onSelect, onExpand };

  // create once
  useEffect(() => {
    let disposed = false;
    let ro: ResizeObserver | null = null;
    (async () => {
      const cytoscape = (await import("cytoscape")).default;
      if (disposed || !box.current) return;
      const cy = cytoscape({
        container: box.current,
        elements: [],
        minZoom: 0.15,
        maxZoom: 3,
        wheelSensitivity: 0.25,
        style: [
          {
            selector: "node",
            style: {
              "background-color": "data(color)",
              shape: "data(shape)" as never,
              label: "data(display)",
              color: "#e2e8f0",
              "font-size": 9,
              "text-valign": "bottom",
              "text-margin-y": 3,
              "text-outline-color": "#070b14",
              "text-outline-width": 2,
              width: 18,
              height: 18,
              "border-width": 1,
              "border-color": "#0f172a",
            },
          },
          { selector: "node[label = 'Document']", style: { width: 14, height: 14 } },
          { selector: "node[label = 'PiiToken']", style: { width: 12, height: 12 } },
          {
            selector: "edge",
            style: {
              width: 1.2,
              "line-color": "#334155",
              "target-arrow-color": "#475569",
              "target-arrow-shape": "triangle",
              "arrow-scale": 0.7,
              "curve-style": "bezier",
              label: "data(type)",
              "font-size": 7,
              color: "#64748b",
              "text-rotation": "autorotate",
              "text-outline-color": "#070b14",
              "text-outline-width": 2,
            },
          },
          { selector: ".dim", style: { opacity: 0.22 } },
          { selector: "node.hl", style: { "border-width": 3, "border-color": "#fde68a", width: 24, height: 24 } },
          { selector: "edge.hl", style: { "line-color": "#fbbf24", "target-arrow-color": "#fbbf24", width: 2.2, color: "#fcd34d" } },
          { selector: "node:selected", style: { "border-width": 3, "border-color": "#ffffff" } },
        ],
      });
      cy.on("tap", "node", (ev) => cbs.current.onSelect?.(ev.target.data() as EvNode));
      cy.on("tap", (ev) => {
        if (ev.target === cy) cbs.current.onSelect?.(null);
      });
      cy.on("dbltap", "node", (ev) => cbs.current.onExpand?.(ev.target.data() as EvNode));
      cyRef.current = cy;
      let wasEmpty = true;
      ro = new ResizeObserver(() => {
        const el = box.current;
        if (!el) return;
        const empty = el.clientWidth === 0 || el.clientHeight === 0;
        cy.resize();
        if (wasEmpty && !empty && cy.elements().length) cy.fit(undefined, 30);
        wasEmpty = empty;
      });
      ro.observe(box.current);
      sync(cy);
    })();
    return () => {
      disposed = true;
      ro?.disconnect();
      cyRef.current?.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function sync(cy: Core) {
    const els = toElements(nodes, edges);
    const want = new Set(els.map((e) => e.data.id as string));
    cy.elements().forEach((el) => {
      if (!want.has(el.id())) el.remove();
    });
    const added: ElementDefinition[] = [];
    for (const el of els) {
      const cur = cy.getElementById(el.data.id as string);
      if (cur.nonempty()) cur.data(el.data);
      else added.push(el);
    }
    if (added.length) {
      const anchor = focus ? cy.getElementById(focus) : null;
      const pos = anchor && anchor.nonempty() ? anchor.position() : null;
      const col = cy.add(added.map((a) => (a.group === "nodes" && pos ? { ...a, position: { x: pos.x + Math.random() * 40 - 20, y: pos.y + Math.random() * 40 - 20 } } : a)));
      const fresh = cy.elements().length === col.length;
      const opts: LayoutOptions =
        layout === "concentric"
          ? ({ name: "concentric", animate: !fresh, animationDuration: 350, minNodeSpacing: 28, concentric: (n: { id: () => string; degree: (b: boolean) => number }) => (n.id() === focus ? 100 : n.degree(false)), levelWidth: () => 2 } as unknown as LayoutOptions)
          : ({ name: "cose", animate: !fresh, animationDuration: 400, randomize: fresh, nodeRepulsion: () => 9000, idealEdgeLength: () => 70, padding: 30, numIter: 1200 } as unknown as LayoutOptions);
      cy.layout(opts).run();
    }
    applyHighlight(cy);
  }

  function applyHighlight(cy: Core) {
    cy.elements().removeClass("dim hl");
    const hl = new Set(highlight ?? []);
    if (hl.size) {
      cy.nodes().forEach((n) => {
        if (hl.has(n.id())) n.addClass("hl");
      });
      cy.edges().forEach((e) => {
        if (hl.has(e.source().id()) && hl.has(e.target().id())) e.addClass("hl");
      });
      cy.elements().not(".hl").not(cy.nodes(".hl").neighborhood()).addClass("dim");
    }
    cy.nodes().unselect();
    if (selected) cy.getElementById(selected).select();
  }

  useEffect(() => {
    if (cyRef.current) sync(cyRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

  useEffect(() => {
    if (cyRef.current) applyHighlight(cyRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight, selected]);

  return <div ref={box} className={className ?? "h-[420px] w-full rounded-lg border border-slate-800 bg-[#060912]"} />;
}
