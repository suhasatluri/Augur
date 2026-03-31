"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import { getSimulationStatus, SimulationStatus } from "@/lib/api";
import ConfidenceIndicator, { UncertaintyWarning } from "@/components/ConfidenceIndicator";
import DateBanner from "@/components/DateBanner";
import ProgressTracker from "@/components/ProgressTracker";
import VerdictBadge from "@/components/VerdictBadge";
import ProbabilityBars from "@/components/ProbabilityBars";
import SwingFactors from "@/components/SwingFactors";
import SentimentCascade from "@/components/SentimentCascade";
import VerdictScale from "@/components/VerdictScale";

const POLL_INTERVAL = 5000;

export default function SimulationPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [data, setData] = useState<SimulationStatus | null>(null);
  const [error, setError] = useState("");
  const startedAt = useRef(Date.now());

  useEffect(() => {
    let active = true;

    const poll = async () => {
      try {
        const status = await getSimulationStatus(jobId);
        if (active) setData(status);

        if (status.status === "queued" || status.status === "running") {
          setTimeout(poll, POLL_INTERVAL);
        }
      } catch (err: unknown) {
        if (active)
          setError(
            err instanceof Error ? err.message : "Failed to fetch status"
          );
      }
    };

    poll();
    return () => {
      active = false;
    };
  }, [jobId]);

  if (error) {
    return (
      <div className="text-center space-y-4 mt-20">
        <p className="text-red-400 font-mono text-sm">{error}</p>
        <a href="/" className="text-gold text-xs hover:underline">
          Back to home
        </a>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="text-center mt-20">
        <div className="text-gold animate-pulse-gold font-mono text-sm">
          Loading...
        </div>
      </div>
    );
  }

  const isRunning = data.status === "queued" || data.status === "running";
  const isDone = data.status === "complete";
  const isFailed = data.status === "failed";
  const result = data.result;

  return (
    <div className="space-y-8 max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-3xl font-bold text-gold">
            {data.ticker}
          </h1>
          <p className="text-xs text-muted font-mono mt-1">
            {data.simulation_id} &middot; {jobId}
          </p>
        </div>
        {isDone && result && (
          <div className="flex items-center gap-1">
            <VerdictBadge verdict={result.verdict} />
            <ConfidenceIndicator convergenceScore={result.convergence_score} />
          </div>
        )}
        {isRunning && (
          <span className="text-xs font-mono text-gold animate-pulse-gold uppercase">
            {data.status}
          </span>
        )}
        {isFailed && (
          <span className="text-xs font-mono text-red-400 uppercase">
            Failed
          </span>
        )}
      </div>

      {/* Date context banner */}
      <DateBanner ticker={data.ticker} reportingDate={data.reporting_date} />

      {/* Uncertainty warning */}
      {isDone && result && (
        <UncertaintyWarning convergenceScore={result.convergence_score} />
      )}

      {/* Progress tracker (while running) */}
      {isRunning && (
        <ProgressTracker status={data.status} startedAt={startedAt.current} ticker={data.ticker} />
      )}

      {/* Failed state */}
      {isFailed && (
        <div className="bg-red-900/20 border border-red-800 rounded p-4">
          <p className="text-red-400 text-sm font-mono">
            Simulation failed: {data.error || "Unknown error"}
          </p>
        </div>
      )}

      {/* Results (when complete) */}
      {isDone && result && (
        <>
          {/* Probability distribution */}
          <section>
            <h2 className="font-heading text-lg text-gold/80 mb-3">
              Probability Distribution
            </h2>
            <div className="bg-surface border border-surface-border rounded-lg p-5">
              <ProbabilityBars
                pBeat={result.distribution.p_beat}
                pMiss={result.distribution.p_miss}
                pInline={result.distribution.p_inline}
                mean={result.distribution.mean_probability}
                bandLow={result.distribution.confidence_band_low}
                bandHigh={result.distribution.confidence_band_high}
              />
              <VerdictScale verdict={result.verdict} />
            </div>
          </section>

          {/* Convergence */}
          <section className="bg-surface border border-surface-border rounded-lg p-5">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-muted font-mono tracking-widest uppercase">
                Convergence
              </span>
              <span className="font-mono text-sm text-foreground">
                {result.convergence_score.toFixed(3)}
              </span>
            </div>
            <div className="h-2 bg-surface-light rounded overflow-hidden">
              <div
                className="h-full bg-gold rounded transition-all duration-700"
                style={{
                  width: `${Math.round(result.convergence_score * 100)}%`,
                }}
              />
            </div>
            {result.high_uncertainty && (
              <p className="text-xs text-yellow-400 font-mono mt-2">
                HIGH UNCERTAINTY — agents did not converge. Prediction
                unreliable.
              </p>
            )}
          </section>

          {/* Swing factors */}
          <section>
            <h2 className="font-heading text-lg text-gold/80 mb-3">
              Top Swing Factors
            </h2>
            <SwingFactors factors={result.swing_factors} />
          </section>

          {/* Sentiment cascade */}
          <section>
            <h2 className="font-heading text-lg text-gold/80 mb-3">
              Sentiment Cascade Risk
            </h2>
            <SentimentCascade
              direction={result.sentiment_cascade.direction}
              severity={result.sentiment_cascade.severity}
              retailConviction={result.sentiment_cascade.retail_conviction}
              reasoning={result.sentiment_cascade.reasoning}
            />
          </section>

          {/* Summary */}
          <section className="bg-surface border border-surface-border rounded-lg p-5">
            <h2 className="font-heading text-lg text-gold/80 mb-3">Summary</h2>
            <p className="text-sm text-foreground/80 leading-relaxed">
              {result.human_summary}
            </p>
          </section>

          {/* Disclaimer */}
          <div className="border border-gold/20 rounded p-4 text-center">
            <p className="text-xs text-gold/60 font-mono">
              {result.disclaimer}
            </p>
          </div>
        </>
      )}

      {/* Back link */}
      <div className="text-center">
        <a
          href="/"
          className="text-xs text-muted hover:text-gold transition font-mono"
        >
          &larr; New simulation
        </a>
      </div>
    </div>
  );
}
