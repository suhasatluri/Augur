"use client";

import { useEffect, useState } from "react";

interface ProgressTrackerProps {
  status: "queued" | "running" | "complete" | "failed";
  startedAt: number;
  ticker: string;
}

const TOTAL_ESTIMATE = 150; // seconds

const STAGES = [
  { label: "Harvesting seed data", duration: 30 },
  { label: "Forging 50 analyst agents", duration: 45 },
  { label: "Running debate rounds 1–3", duration: 130 },
  { label: "Synthesising prediction", duration: 5 },
];

const SIMPLE_MESSAGES = [
  { until: 25, text: (t: string) => `Analysing ${t}...` },
  { until: 70, text: () => "Running simulation..." },
  { until: 130, text: () => "Crunching the numbers..." },
  { until: Infinity, text: () => "Almost done..." },
];

function getSimpleMessage(elapsed: number, ticker: string): string {
  for (const m of SIMPLE_MESSAGES) {
    if (elapsed < m.until) return m.text(ticker);
  }
  return "Almost done...";
}

export default function ProgressTracker({
  status,
  startedAt,
  ticker,
}: ProgressTrackerProps) {
  const [elapsed, setElapsed] = useState(0);
  const [mode, setMode] = useState<"simple" | "technical">("simple");

  // Load preference from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem("augur_progress_mode");
      if (saved === "simple" || saved === "technical") setMode(saved);
    } catch {}
  }, []);

  const toggleMode = () => {
    const next = mode === "simple" ? "technical" : "simple";
    setMode(next);
    try {
      localStorage.setItem("augur_progress_mode", next);
    } catch {}
  };

  useEffect(() => {
    if (status !== "running" && status !== "queued") return;
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [status, startedAt]);

  // Stage calculation (for technical mode)
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

  const progress = Math.min((elapsed / TOTAL_ESTIMATE) * 100, 95);
  const remaining = Math.max(TOTAL_ESTIMATE - elapsed, 0);
  const remMin = Math.floor(remaining / 60);
  const remSec = remaining % 60;

  if (status === "complete" || status === "failed") return null;

  return (
    <div className="bg-surface border border-surface-border rounded p-6">
      {/* Toggle */}
      <div className="flex justify-end mb-4">
        <button
          onClick={toggleMode}
          className="text-xs font-mono text-muted hover:text-gold transition"
        >
          {mode === "simple" ? "Technical view" : "Simple view"}
        </button>
      </div>

      {mode === "simple" ? (
        /* ---- SIMPLE MODE ---- */
        <div>
          {/* Progress bar */}
          <div className="h-2 bg-surface-light rounded overflow-hidden mb-4">
            <div
              className="h-full bg-gold transition-all duration-1000 rounded"
              style={{ width: `${progress}%` }}
            />
          </div>

          <div className="flex items-center justify-between">
            <span className="text-sm font-mono text-gold">
              {getSimpleMessage(elapsed, ticker)}
            </span>
            <span className="text-sm font-mono text-muted">
              {Math.round(progress)}%
            </span>
          </div>

          <div className="text-xs text-muted font-mono text-right mt-2">
            {remaining > 0
              ? `~${remMin}:${String(remSec).padStart(2, "0")} remaining`
              : "Finishing up..."}
          </div>
        </div>
      ) : (
        /* ---- TECHNICAL MODE ---- */
        <div>
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
                      <span className="text-gold animate-pulse-gold">
                        &#9679;
                      </span>
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

          <div className="mt-4 flex justify-between text-xs text-muted font-mono">
            <span>
              {Math.floor(elapsed / 60)}:
              {String(elapsed % 60).padStart(2, "0")} elapsed
            </span>
            <span>
              ~{remMin}:{String(remSec).padStart(2, "0")} remaining
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
