"use client";

import { useState } from "react";

interface ConfidenceIndicatorProps {
  convergenceScore: number;
}

function getLevel(score: number): {
  label: string;
  tooltip: string;
  cls: string;
} {
  if (score > 0.85) {
    return {
      label: "High Confidence",
      tooltip:
        "Agents converged strongly \u2014 low disagreement on final outcome",
      cls: "text-emerald-400/70",
    };
  }
  if (score >= 0.7) {
    return {
      label: "Moderate Confidence",
      tooltip:
        "Some disagreement remains \u2014 treat verdict as directional, not definitive",
      cls: "text-muted",
    };
  }
  return {
    label: "Low Confidence",
    tooltip:
      "Agents remained split \u2014 HIGH UNCERTAINTY, outcome genuinely unclear",
    cls: "text-yellow-400/70",
  };
}

export default function ConfidenceIndicator({
  convergenceScore,
}: ConfidenceIndicatorProps) {
  const [show, setShow] = useState(false);
  const { label, tooltip, cls } = getLevel(convergenceScore);

  return (
    <span
      className="relative inline-flex items-center gap-1.5"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <span className="text-muted/40">&middot;</span>
      <span className={`font-mono text-xs cursor-help ${cls}`}>{label}</span>
      {show && (
        <span className="absolute z-20 bottom-full left-1/2 -translate-x-1/2 mb-2 w-60 px-3 py-2 rounded bg-surface border border-gold/30 text-xs font-mono text-foreground/80 leading-relaxed shadow-lg pointer-events-none">
          {tooltip}
        </span>
      )}
    </span>
  );
}

export function UncertaintyWarning({
  convergenceScore,
}: {
  convergenceScore: number;
}) {
  if (convergenceScore >= 0.7) return null;

  return (
    <div className="bg-yellow-900/10 border border-yellow-700/30 rounded px-4 py-3">
      <p className="text-xs font-mono text-yellow-400/80">
        High uncertainty — agents did not converge. Treat this verdict with
        caution.
      </p>
    </div>
  );
}
