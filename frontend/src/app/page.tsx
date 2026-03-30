"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import TickerInput from "@/components/TickerInput";
import VerdictBadge from "@/components/VerdictBadge";
import { startSimulation, listSimulations, SimulationListItem } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [ticker, setTicker] = useState("");
  const [reportingDate, setReportingDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [simulations, setSimulations] = useState<SimulationListItem[]>([]);

  useEffect(() => {
    listSimulations().then(setSimulations).catch(() => {});
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticker) return;

    setLoading(true);
    setError("");

    try {
      const res = await startSimulation(ticker, reportingDate);
      router.push(`/simulation/${res.job_id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start simulation");
      setLoading(false);
    }
  };

  return (
    <div className="space-y-12">
      {/* Hero */}
      <div className="text-center space-y-3">
        <h1 className="font-heading text-5xl font-bold text-gold">
          Predict Earnings Surprises
        </h1>
        <p className="text-muted text-sm max-w-lg mx-auto">
          50 AI analyst agents debate ASX earnings outcomes through 5 rounds of
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
            Reporting Date (optional)
          </label>
          <input
            type="date"
            value={reportingDate}
            onChange={(e) => setReportingDate(e.target.value)}
            className="w-full bg-surface border border-surface-border rounded px-4 py-3 text-sm font-mono text-foreground focus:outline-none focus:border-gold/50 transition [color-scheme:dark]"
          />
        </div>

        {error && (
          <p className="text-red-400 text-xs font-mono">{error}</p>
        )}

        <button
          type="submit"
          disabled={!ticker || loading}
          className="w-full bg-gold text-background font-mono text-sm tracking-wider py-3 rounded hover:bg-gold-light disabled:opacity-30 disabled:cursor-not-allowed transition"
        >
          {loading ? "Starting..." : "Run Simulation"}
        </button>
      </form>

      {/* Recent simulations */}
      {simulations.length > 0 && (
        <div className="max-w-2xl mx-auto">
          <h2 className="font-heading text-xl text-gold/80 mb-4">
            Recent Simulations
          </h2>
          <div className="space-y-2">
            {simulations.map((sim) => (
              <div
                key={sim.simulation_id}
                className="flex items-center justify-between bg-surface border border-surface-border rounded px-4 py-3 hover:border-gold/30 transition"
              >
                <div className="flex items-center gap-4">
                  <span className="font-mono text-gold text-sm">
                    {sim.ticker}
                  </span>
                  <span className="text-xs text-muted font-mono">
                    {sim.simulation_id}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  {sim.verdict ? (
                    <VerdictBadge verdict={sim.verdict} />
                  ) : (
                    <span className="text-xs font-mono text-muted uppercase">
                      {sim.status}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
