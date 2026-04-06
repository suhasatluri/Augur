"use client";

import { useState, useEffect, useCallback } from "react";
import TimeRangePicker from "@/components/TimeRangePicker";

const API =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://augur-production-46e9.up.railway.app";

interface Stats {
  totals: Record<string, number>;
  token_breakdown: Record<string, number>;
  daily: Record<string, unknown>[];
  top_tickers: Record<string, unknown>[];
  recent: Record<string, unknown>[];
  feedback: Record<string, number>;
  cached_at?: string;
  range?: { from: string; to: string };
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-surface-border bg-surface p-4">
      <div className="font-mono text-[9px] tracking-widest uppercase text-muted mb-1">{label}</div>
      <div className="font-heading text-2xl text-gold">{value}</div>
    </div>
  );
}

export default function AdminPage() {
  const [secret, setSecret] = useState("");
  const [authed, setAuthed] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState("");
  const [rangeFrom, setRangeFrom] = useState<Date>(() => new Date(Date.now() - 30 * 86400000));
  const [rangeTo, setRangeTo] = useState<Date>(() => new Date());

  useEffect(() => {
    const saved = sessionStorage.getItem("augur_admin_secret");
    if (saved) {
      setSecret(saved);
      setAuthed(true);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    if (!secret) return;
    try {
      const fromIso = rangeFrom.toISOString();
      const toIso = rangeTo.toISOString();
      const url = `${API}/admin/stats?from_ts=${encodeURIComponent(fromIso)}&to_ts=${encodeURIComponent(toIso)}`;
      const res = await fetch(url, { headers: { "X-Admin-Secret": secret } });
      if (res.status === 401) {
        setError("Invalid admin secret");
        setAuthed(false);
        sessionStorage.removeItem("augur_admin_secret");
        return;
      }
      const data = await res.json();
      setStats(data);
      setError("");
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (e) {
      setError(`Fetch failed: ${e}`);
    }
  }, [secret, rangeFrom, rangeTo]);

  useEffect(() => {
    if (authed && secret) {
      fetchStats();
      const timer = setInterval(fetchStats, 120_000);
      return () => clearInterval(timer);
    }
  }, [authed, secret, fetchStats]);

  function handleLogin() {
    if (!secret.trim()) return;
    sessionStorage.setItem("augur_admin_secret", secret);
    setAuthed(true);
  }

  function handleLogout() {
    sessionStorage.removeItem("augur_admin_secret");
    setSecret("");
    setAuthed(false);
    setStats(null);
  }

  if (!authed) {
    return (
      <div className="max-w-sm mx-auto mt-32 space-y-4">
        <h1 className="font-heading text-2xl text-gold text-center">Admin</h1>
        <input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleLogin()}
          placeholder="Admin secret"
          className="w-full bg-surface border border-surface-border text-foreground font-mono text-sm p-3 outline-none focus:border-gold/50"
        />
        <button onClick={handleLogin} className="w-full bg-gold text-background font-mono text-sm tracking-wider py-3 hover:bg-gold-light transition">
          Authenticate
        </button>
        {error && <p className="text-red-400 text-xs font-mono text-center">{error}</p>}
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="text-center mt-20">
        <div className="text-gold animate-pulse-gold font-mono text-sm">Loading dashboard...</div>
      </div>
    );
  }

  const t = stats.totals;
  const tb = stats.token_breakdown;
  const fb = stats.feedback;
  const sonnetCost = ((tb.sonnet_in || 0) / 1e6) * 3.0 + ((tb.sonnet_out || 0) / 1e6) * 15.0;
  const haikuCost = ((tb.haiku_in || 0) / 1e6) * 0.25 + ((tb.haiku_out || 0) / 1e6) * 1.25;

  return (
    <div className="space-y-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="font-heading text-3xl text-gold">Admin Dashboard</h1>
          <div className="font-mono text-[9px] text-muted mt-0.5">
            Last updated: {lastUpdated}
            {stats.cached_at && <span className="ml-2">· cached</span>}
            {" · "}
            <button onClick={fetchStats} className="hover:text-gold transition">refresh</button>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <TimeRangePicker
            from={rangeFrom}
            to={rangeTo}
            onChange={(f, t) => { setRangeFrom(f); setRangeTo(t); }}
          />
          <button
            onClick={handleLogout}
            className="font-mono text-[9px] tracking-widest uppercase text-muted hover:text-red-400 border border-transparent hover:border-red-400/30 px-2.5 py-1.5 transition"
          >
            Log out
          </button>
        </div>
      </div>

      {/* Section 1: Headline metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Total Simulations" value={`${t.total_simulations}`} />
        <StatCard label="Total Cost" value={`$${Number(t.total_cost_usd).toFixed(2)}`} />
        <StatCard label="Avg Cost / Sim" value={`$${Number(t.avg_cost_usd).toFixed(2)}`} />
        <StatCard label="Avg Duration" value={`${Math.round(Number(t.avg_duration_s))}s`} />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Unique Tickers" value={`${t.unique_tickers}`} />
        <StatCard label="Completed" value={`${t.completed}`} />
        <StatCard label="Failed" value={`${t.failed}`} />
        <StatCard label="Avg Seed Quality" value={`${Number(t.avg_seed_quality).toFixed(2)}`} />
      </div>

      {/* Section 2: Token breakdown */}
      <div>
        <h2 className="font-heading text-lg text-gold/80 mb-3">Token Breakdown</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="border border-surface-border bg-surface p-4">
            <div className="font-mono text-[9px] tracking-widest uppercase text-gold mb-2">Sonnet</div>
            <div className="font-mono text-xs text-muted space-y-1">
              <div>Input: {((tb.sonnet_in || 0) / 1e6).toFixed(2)}M tokens</div>
              <div>Output: {((tb.sonnet_out || 0) / 1e6).toFixed(2)}M tokens</div>
              <div className="text-gold">Cost: ${sonnetCost.toFixed(2)}</div>
            </div>
          </div>
          <div className="border border-surface-border bg-surface p-4">
            <div className="font-mono text-[9px] tracking-widest uppercase text-gold mb-2">Haiku</div>
            <div className="font-mono text-xs text-muted space-y-1">
              <div>Input: {((tb.haiku_in || 0) / 1e6).toFixed(2)}M tokens</div>
              <div>Output: {((tb.haiku_out || 0) / 1e6).toFixed(2)}M tokens</div>
              <div className="text-gold">Cost: ${haikuCost.toFixed(2)}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Section 3: Daily activity */}
      <div>
        <h2 className="font-heading text-lg text-gold/80 mb-3">Daily Activity</h2>
        <div className="border border-surface-border bg-surface overflow-hidden">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="border-b border-surface-border text-muted text-left">
                <th className="p-2">Date</th><th className="p-2">Sims</th><th className="p-2">Cost</th>
              </tr>
            </thead>
            <tbody>
              {stats.daily.slice(0, 14).map((d, i) => (
                <tr key={i} className="border-b border-surface-border/50">
                  <td className="p-2 text-foreground">{String(d.date)}</td>
                  <td className="p-2 text-gold">{String(d.simulations)}</td>
                  <td className="p-2 text-muted">${Number(d.cost_usd).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Section 4: Top tickers */}
      <div>
        <h2 className="font-heading text-lg text-gold/80 mb-3">Top Tickers</h2>
        <div className="border border-surface-border bg-surface overflow-x-auto">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="border-b border-surface-border text-muted text-left">
                <th className="p-2">Ticker</th><th className="p-2">Sims</th><th className="p-2">Total Cost</th>
                <th className="p-2">Avg Cost</th><th className="p-2">Avg Quality</th><th className="p-2">Last Run</th>
              </tr>
            </thead>
            <tbody>
              {stats.top_tickers.map((r, i) => (
                <tr key={i} className="border-b border-surface-border/50">
                  <td className="p-2 text-gold">{String(r.ticker)}</td>
                  <td className="p-2 text-foreground">{String(r.simulations)}</td>
                  <td className="p-2 text-muted">${Number(r.total_cost).toFixed(2)}</td>
                  <td className="p-2 text-muted">${Number(r.avg_cost).toFixed(2)}</td>
                  <td className="p-2 text-muted">{Number(r.avg_quality).toFixed(2)}</td>
                  <td className="p-2 text-muted">{String(r.last_run).slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Section 5: Recent simulations */}
      <div>
        <h2 className="font-heading text-lg text-gold/80 mb-3">Recent Simulations</h2>
        <div className="border border-surface-border bg-surface overflow-x-auto">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="border-b border-surface-border text-muted text-left">
                <th className="p-2">Ticker</th><th className="p-2">Status</th><th className="p-2">Cost</th>
                <th className="p-2">Sonnet</th><th className="p-2">Haiku</th><th className="p-2">Duration</th>
                <th className="p-2">Quality</th><th className="p-2">Conv.</th><th className="p-2">Time</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent.map((r, i) => (
                <tr key={i} className="border-b border-surface-border/50">
                  <td className="p-2 text-gold">{String(r.ticker)}</td>
                  <td className={`p-2 ${r.status === "complete" ? "text-emerald-400" : r.status === "failed" ? "text-red-400" : "text-muted"}`}>{String(r.status)}</td>
                  <td className="p-2 text-muted">${Number(r.estimated_cost_usd || 0).toFixed(2)}</td>
                  <td className="p-2 text-muted">{Number(r.sonnet_tokens || 0).toLocaleString()}</td>
                  <td className="p-2 text-muted">{Number(r.haiku_tokens || 0).toLocaleString()}</td>
                  <td className="p-2 text-muted">{r.duration_seconds ? `${r.duration_seconds}s` : "—"}</td>
                  <td className="p-2 text-muted">{r.seed_quality ? Number(r.seed_quality).toFixed(2) : "—"}</td>
                  <td className="p-2 text-muted">{r.convergence_score ? Number(r.convergence_score).toFixed(3) : "—"}</td>
                  <td className="p-2 text-muted">{String(r.created_at).slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Section 6: Feedback */}
      <div>
        <h2 className="font-heading text-lg text-gold/80 mb-3">Feedback</h2>
        <div className="grid grid-cols-4 gap-3">
          <StatCard label="Total" value={`${fb.total}`} />
          <StatCard label="Positive" value={`${fb.positive}`} />
          <StatCard label="Negative" value={`${fb.negative}`} />
          <StatCard label="Unsure" value={`${fb.unsure}`} />
        </div>
      </div>
    </div>
  );
}
