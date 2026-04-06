"use client";

import { useState, useEffect, useCallback } from "react";
import { getCalendar, CalendarEntry, CalendarData } from "@/lib/api";

const SECTOR_COLORS: Record<string, string> = {
  Financials: "#3B82F6",
  Materials: "#F59E0B",
  "Health Care": "#10B981",
  Industrials: "#9CA3AF",
  "Real Estate": "#8B5CF6",
  "Consumer Discretionary": "#EC4899",
  "Consumer Staples": "#84CC16",
  Energy: "#F97316",
  "Information Technology": "#06B6D4",
  "Communication Services": "#6366F1",
  Utilities: "#14B8A6",
};

function fmtDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString("en-AU", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function fmtWeekLabel(weekKey: string): string {
  const d = new Date(weekKey + "T00:00:00");
  const end = new Date(d);
  end.setDate(d.getDate() + 4);
  return (
    d.toLocaleDateString("en-AU", { day: "2-digit", month: "short" }) +
    " – " +
    end.toLocaleDateString("en-AU", { day: "2-digit", month: "short", year: "numeric" })
  );
}

function groupByWeek(calendar: Record<string, CalendarEntry[]>) {
  const weeks: Record<string, { date: string; entries: CalendarEntry[] }[]> = {};
  Object.entries(calendar)
    .sort(([a], [b]) => a.localeCompare(b))
    .forEach(([dateStr, entries]) => {
      const d = new Date(dateStr + "T00:00:00");
      const day = d.getDay();
      const diff = d.getDate() - day + (day === 0 ? -6 : 1);
      const monday = new Date(d);
      monday.setDate(diff);
      const weekKey = monday.toISOString().split("T")[0];
      if (!weeks[weekKey]) weeks[weekKey] = [];
      weeks[weekKey].push({ date: dateStr, entries });
    });
  return weeks;
}

function ConfidenceDot({ confidence }: { confidence: string }) {
  const color =
    confidence === "high"
      ? "bg-emerald-500/60"
      : confidence === "medium"
      ? "bg-amber-400/50"
      : "bg-red-400/40";
  return <span className={`w-1.5 h-1.5 rounded-full shrink-0 inline-block ${color}`} />;
}

export default function CalendarPage() {
  const [data, setData] = useState<CalendarData | null>(null);
  const [loading, setLoading] = useState(true);
  const [sector, setSector] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getCalendar({
        weeks: 26,
        sector,
        search: debouncedSearch || undefined,
      });
      setData(d);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [sector, debouncedSearch]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const today = new Date().toISOString().split("T")[0];
  const weeks = data ? groupByWeek(data.calendar) : {};
  const weekKeys = Object.keys(weeks).sort();

  return (
    <div className="space-y-8">
      {/* Page title */}
      <div>
        <div className="font-mono text-[9px] tracking-widest uppercase text-gold mb-2">
          ASX Earnings Calendar
        </div>
        <h1 className="font-heading text-3xl text-gold/90 mb-1">Upcoming Reporting Season</h1>
        <p className="font-mono text-[11px] text-muted">
          {data?.total_companies ?? "—"} companies
          {data?.last_updated && (
            <span>
              {" "}· Updated{" "}
              {new Date(data.last_updated).toLocaleDateString("en-AU", {
                day: "2-digit",
                month: "short",
                year: "numeric",
              })}
            </span>
          )}
        </p>
      </div>

      {/* Search + sector filters */}
      <div className="space-y-3">
        <input
          type="text"
          placeholder="Search ticker or company..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full bg-surface border border-surface-border text-foreground font-mono text-xs px-4 py-2.5 placeholder-muted/50 focus:outline-none focus:border-gold/50 transition rounded"
        />
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => setSector(null)}
            className={`font-mono text-[9px] tracking-widest uppercase px-3 py-1.5 border rounded transition ${
              sector === null
                ? "border-gold/60 text-gold bg-gold/10"
                : "border-surface-border text-muted hover:border-gold/30"
            }`}
          >
            All sectors
          </button>
          {(data?.sectors ?? []).map((s) => (
            <button
              key={s}
              onClick={() => setSector((prev) => (prev === s ? null : s))}
              className={`font-mono text-[9px] tracking-widest uppercase px-3 py-1.5 border rounded transition ${
                sector === s
                  ? "border-gold/60 text-gold bg-gold/10"
                  : "border-surface-border text-muted hover:border-gold/30"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Results count */}
      {!loading && data && (
        <div className="font-mono text-[9px] text-muted">
          Showing {data.total_companies} companies
          {sector ? ` in ${sector}` : ""}
          {search ? ` matching "${search}"` : ""}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="font-mono text-xs text-muted text-center py-20 animate-pulse-gold">
          Loading calendar...
        </div>
      )}

      {/* Empty state */}
      {!loading && (!data || data.total_companies === 0) && (
        <div className="font-mono text-xs text-muted text-center py-20 border border-surface-border rounded">
          No upcoming reports found{search ? ` for "${search}"` : ""}.
        </div>
      )}

      {/* Calendar weeks */}
      {!loading && data && weekKeys.length > 0 && (
        <div className="space-y-8">
          {weekKeys.map((weekKey) => (
            <div key={weekKey}>
              {/* Week header */}
              <div className="flex items-center gap-3 mb-3">
                <div className="font-mono text-[9px] tracking-widest uppercase text-gold/60">
                  {fmtWeekLabel(weekKey)}
                </div>
                <div className="flex-1 h-px bg-surface-border" />
                <div className="font-mono text-[9px] text-muted">
                  {weeks[weekKey].reduce((sum, { entries }) => sum + entries.length, 0)} companies
                </div>
              </div>

              {/* Rows */}
              <div className="border border-surface-border rounded overflow-hidden">
                {weeks[weekKey].map(({ date, entries }) =>
                  entries.map((entry, i) => {
                    const isToday = date === today;
                    const isPast = date < today;
                    return (
                      <div
                        key={`${date}-${entry.ticker}-${i}`}
                        className={`flex items-center gap-3 px-4 py-2.5 border-b border-surface-border/50 last:border-b-0 hover:bg-surface transition ${
                          isToday ? "border-l-2 border-l-gold/60" : ""
                        } ${isPast ? "opacity-40" : ""}`}
                      >
                        {/* Date */}
                        <span className="font-mono text-[10px] text-muted w-32 shrink-0">
                          {fmtDate(date)}
                        </span>

                        {/* Ticker */}
                        <span className="font-mono text-[11px] font-medium text-gold w-12 shrink-0">
                          {entry.ticker}
                        </span>

                        {/* Company */}
                        <span className="font-mono text-[10px] text-foreground/80 flex-1 truncate">
                          {entry.company}
                        </span>

                        {/* Report type */}
                        <span className="font-mono text-[9px] text-muted w-20 text-right shrink-0 hidden sm:block">
                          {entry.report_type ?? "—"}
                        </span>

                        {/* Sector */}
                        <span
                          className="font-mono text-[9px] w-20 text-right truncate shrink-0 hidden md:block"
                          style={{ color: SECTOR_COLORS[entry.sector ?? ""] ?? "#6a6560", opacity: 0.75 }}
                        >
                          {entry.sector ?? "—"}
                        </span>

                        {/* Confidence */}
                        <ConfidenceDot confidence={entry.confidence} />

                        {/* Run simulation */}
                        <a
                          href={`/?ticker=${entry.ticker}`}
                          className="font-mono text-[9px] tracking-widest uppercase border border-surface-border text-muted hover:border-gold/50 hover:text-gold px-3 py-1 transition rounded shrink-0"
                        >
                          Simulate
                        </a>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Confidence legend */}
      {!loading && data && data.total_companies > 0 && (
        <div className="flex items-center gap-6 font-mono text-[9px] text-muted">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500/60 inline-block" />
            High confidence (2 sources agree)
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-amber-400/50 inline-block" />
            Medium (single source)
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-red-400/40 inline-block" />
            Low (estimated)
          </span>
        </div>
      )}

      {/* Disclaimer */}
      {data?.disclaimer && (
        <div className="border border-surface-border bg-surface rounded p-4">
          <p className="font-mono text-[9px] text-muted leading-relaxed">
            {data.disclaimer} Augur does not hold an AFSL.
          </p>
        </div>
      )}
    </div>
  );
}
