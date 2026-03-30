"use client";

import { useEffect, useState } from "react";

interface ProgressTrackerProps {
  status: "queued" | "running" | "complete" | "failed";
  startedAt: number;
}

const STAGES = [
  { label: "Harvesting seed data", duration: 30 },
  { label: "Forging 50 analyst agents", duration: 45 },
  { label: "Running debate rounds 1–5", duration: 130 },
  { label: "Synthesising prediction", duration: 5 },
];

export default function ProgressTracker({
  status,
  startedAt,
}: ProgressTrackerProps) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (status !== "running" && status !== "queued") return;
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [status, startedAt]);

  // Estimate which stage we're in based on elapsed time
  let cumulative = 0;
  let activeStage = 0;
  for (let i = 0; i < STAGES.length; i++) {
    cumulative += STAGES[i].duration;
    if (elapsed < cumulative) {
      activeStage = i;
      break;
    }
    if (i === STAGES.length - 1) activeStage = i;
  }

  const totalDuration = STAGES.reduce((s, st) => s + st.duration, 0);
  const progress = Math.min((elapsed / totalDuration) * 100, 95);

  if (status === "complete" || status === "failed") return null;

  return (
    <div className="bg-surface border border-surface-border rounded p-6">
      {/* Progress bar */}
      <div className="h-1 bg-surface-light rounded overflow-hidden mb-6">
        <div
          className="h-full bg-gold transition-all duration-1000 rounded"
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Stages */}
      <div className="space-y-3">
        {STAGES.map((stage, i) => {
          const isActive = i === activeStage;
          const isDone = i < activeStage;
          return (
            <div key={i} className="flex items-center gap-3">
              <span className="w-5 text-center">
                {isDone ? (
                  <span className="text-gold">&#10003;</span>
                ) : isActive ? (
                  <span className="text-gold animate-pulse-gold">&#9679;</span>
                ) : (
                  <span className="text-muted/30">&#9679;</span>
                )}
              </span>
              <span
                className={`text-sm font-mono ${
                  isActive
                    ? "text-gold"
                    : isDone
                    ? "text-foreground/60"
                    : "text-muted/30"
                }`}
              >
                {stage.label}
                {isActive && "..."}
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-4 text-xs text-muted font-mono text-right">
        {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}{" "}
        elapsed
      </div>
    </div>
  );
}
