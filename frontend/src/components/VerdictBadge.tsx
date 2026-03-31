"use client";

import { useState } from "react";

interface VerdictBadgeProps {
  verdict: string;
}

const COLORS: Record<string, string> = {
  "LIKELY BEAT": "bg-emerald-900/60 text-emerald-300 border-emerald-700",
  "LEAN BEAT": "bg-emerald-900/30 text-emerald-400 border-emerald-800",
  "TOSS-UP": "bg-yellow-900/30 text-yellow-300 border-yellow-800",
  "LEAN MISS": "bg-red-900/30 text-red-400 border-red-800",
  "LIKELY MISS": "bg-red-900/60 text-red-300 border-red-700",
};

const TOOLTIPS: Record<string, string> = {
  "LIKELY BEAT":
    "Strong signal \u2014 majority of agents expect earnings to exceed consensus",
  "LEAN BEAT":
    "Mild signal \u2014 more agents lean bullish but conviction is moderate",
  "TOSS-UP":
    "Genuinely uncertain \u2014 agents are roughly split on the outcome",
  "LEAN MISS":
    "Mild signal \u2014 more agents lean bearish but conviction is moderate",
  "LIKELY MISS":
    "Strong signal \u2014 majority of agents expect earnings to fall short",
};

export default function VerdictBadge({ verdict }: VerdictBadgeProps) {
  const [show, setShow] = useState(false);
  const cls = COLORS[verdict] || "bg-surface text-muted border-surface-border";
  const tip = TOOLTIPS[verdict];

  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <span
        className={`inline-block px-4 py-2 rounded border font-mono text-sm tracking-wider cursor-help ${cls}`}
      >
        {verdict}
      </span>
      {show && tip && (
        <span className="absolute z-20 bottom-full left-1/2 -translate-x-1/2 mb-2 w-60 px-3 py-2 rounded bg-surface border border-gold/30 text-xs font-mono text-foreground/80 leading-relaxed shadow-lg pointer-events-none">
          {tip}
        </span>
      )}
    </span>
  );
}
