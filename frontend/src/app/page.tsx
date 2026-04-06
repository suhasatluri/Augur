"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import TickerInput from "@/components/TickerInput";
import VerdictBadge from "@/components/VerdictBadge";
import { startSimulation, getActivity, ActivityItem } from "@/lib/api";

const ACTIVITY_POLL = 60000; // 60s

export default function Home() {
  const router = useRouter();
  const [ticker, setTicker] = useState("");
  const [reportingDate, setReportingDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activity, setActivity] = useState<ActivityItem[]>([]);

  const fetchActivity = useCallback(() => {
    getActivity().then(setActivity).catch(() => {});
  }, []);

  useEffect(() => {
    fetchActivity();
    const timer = setInterval(fetchActivity, ACTIVITY_POLL);
    return () => clearInterval(timer);
  }, [fetchActivity]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticker) return;

    setLoading(true);
    setError("");

    try {
      const res = await startSimulation(ticker, reportingDate);
      router.push(`/simulation/${res.job_id}`);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to start simulation"
      );
      setLoading(false);
    }
  };

  const maxCount = activity.length > 0 ? activity[0].count : 1;

  return (
    <div className="space-y-12">
      {/* Hero */}
      <div className="text-center space-y-3">
        <h1 className="font-heading text-5xl font-bold text-gold">
          Predict Earnings Surprises
        </h1>
        <p className="text-muted text-sm max-w-lg mx-auto">
          50 AI analyst agents debate ASX earnings outcomes through 3 rounds of
          structured negotiation. Swarm intelligence, not single-model
          prediction.
        </p>
      </div>

      {/* Form */}
      <form
        onSubmit={handleSubmit}
        className="bg-surface border border-surface-border rounded-lg p-6 max-w-md mx-auto space-y-4"
      >
        <TickerInput value={ticker} onChange={setTicker} />

        <div>
          <label className="block text-xs text-muted mb-1 tracking-widest uppercase">
            Reporting Date (recommended)
          </label>
          <input
            type="date"
            value={reportingDate}
            onChange={(e) => setReportingDate(e.target.value)}
            className="w-full bg-surface border border-surface-border rounded px-4 py-3 text-sm font-mono text-foreground focus:outline-none focus:border-gold/50 transition [color-scheme:dark]"
          />
          <p className="text-xs text-muted/60 mt-1">
            Enter the expected earnings date for more accurate macro context
          </p>
        </div>

        {error && <p className="text-red-400 text-xs font-mono">{error}</p>}

        <button
          type="submit"
          disabled={!ticker || loading}
          className="w-full bg-gold text-background font-mono text-sm tracking-wider py-3 rounded hover:bg-gold-light disabled:opacity-30 disabled:cursor-not-allowed transition"
        >
          {loading ? "Starting..." : "Run Simulation"}
        </button>
      </form>

      {/* Video Teaser */}
      <a
        href="/about"
        className="block max-w-md mx-auto bg-surface border border-gold/20 rounded-lg p-5 hover:border-gold/40 transition group"
      >
        <div className="flex items-center gap-3 mb-2">
          <span className="text-gold text-lg">&#9655;</span>
          <span className="font-mono text-xs tracking-widest uppercase text-gold/80 group-hover:text-gold transition">
            Watch how Augur works
          </span>
          <span className="text-gold/40 ml-auto group-hover:text-gold/70 transition">→</span>
        </div>
        <p className="text-muted text-xs font-mono leading-relaxed">
          50 analysts debate an ASX earnings outcome in real time. See it happen.
        </p>
      </a>

      {/* Community Activity */}
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center gap-2 mb-4">
          <span className="text-gold animate-pulse-gold">&#9673;</span>
          <h2 className="font-heading text-xl text-gold/80">
            Community Activity
          </h2>
          <span className="text-xs text-muted font-mono ml-auto">today</span>
        </div>

        {activity.length > 0 ? (
          <div className="space-y-2">
            {activity.map((item) => {
              const barWidth = Math.max(
                Math.round((item.count / maxCount) * 100),
                8
              );
              return (
                <button
                  key={item.ticker}
                  type="button"
                  onClick={() => setTicker(item.ticker)}
                  className="w-full flex items-center gap-4 bg-surface border border-surface-border rounded px-4 py-3 hover:border-gold/30 transition text-left"
                >
                  <span className="font-mono text-gold text-sm w-10">
                    {item.ticker}
                  </span>
                  <div className="flex-1 h-3 bg-surface-light rounded overflow-hidden">
                    <div
                      className="h-full bg-gold/40 rounded transition-all duration-500"
                      style={{ width: `${barWidth}%` }}
                    />
                  </div>
                  <span className="text-xs text-muted font-mono w-28 text-right">
                    {item.count} simulation{item.count !== 1 ? "s" : ""}
                  </span>
                  {item.last_verdict ? (
                    <VerdictBadge verdict={item.last_verdict} />
                  ) : (
                    <span className="text-xs font-mono text-muted w-24 text-right">
                      —
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ) : (
          <div className="bg-surface border border-surface-border rounded px-4 py-6 text-center">
            <p className="text-muted text-sm font-mono">
              No simulations yet today. Be the first to simulate a ticker.
            </p>
          </div>
        )}
      </div>

    </div>
  );
}
