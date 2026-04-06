"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { getCalendar, CalendarEntry } from "@/lib/api";

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-AU", { day: "numeric", month: "short" });
}

function daysUntil(iso: string): number {
  const target = new Date(iso + "T00:00:00");
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.ceil((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

function ConfidenceDot({ confidence, source }: { confidence: string; source: string | null }) {
  const color =
    confidence === "high"
      ? "bg-emerald-500/60"
      : confidence === "medium"
      ? "bg-amber-400/50"
      : "bg-red-400/40";
  return (
    <span
      title={`${confidence} confidence${source ? ` (${source})` : ""}`}
      className={`w-1.5 h-1.5 rounded-full shrink-0 ${color}`}
    />
  );
}

interface Props {
  onTickerClick?: (ticker: string) => void;
}

export default function EarningsCalendar({ onTickerClick }: Props) {
  const [entries, setEntries] = useState<{ date: string; entry: CalendarEntry }[]>([]);
  const [totalCompanies, setTotalCompanies] = useState(0);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(() => {
    getCalendar({ weeks: 26 })
      .then((data) => {
        // Flatten calendar map into sorted list, take first 5
        const flat: { date: string; entry: CalendarEntry }[] = [];
        Object.entries(data.calendar)
          .sort(([a], [b]) => a.localeCompare(b))
          .forEach(([date, entries]) => {
            entries.forEach((e) => flat.push({ date, entry: e }));
          });
        setEntries(flat.slice(0, 5));
        setTotalCompanies(data.total_companies);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="text-xs font-mono text-muted/50 text-center py-4">
        Loading calendar...
      </div>
    );
  }

  if (entries.length === 0) {
    return null;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-gold/60">&#9670;</span>
          <h2 className="font-heading text-xl text-gold/80">Upcoming Earnings</h2>
          {totalCompanies > 0 && (
            <span className="text-[9px] font-mono text-muted ml-1">{totalCompanies} upcoming</span>
          )}
        </div>
        <Link
          href="/calendar"
          className="font-mono text-[9px] tracking-widest uppercase text-muted hover:text-gold transition"
        >
          View all →
        </Link>
      </div>

      <div className="space-y-1">
        {entries.map(({ date, entry }) => {
          const days = daysUntil(date);
          return (
            <button
              key={`${entry.ticker}-${date}`}
              type="button"
              onClick={() => onTickerClick?.(entry.ticker)}
              className="w-full flex items-center gap-3 bg-surface border border-surface-border rounded px-4 py-2.5 hover:border-gold/30 transition text-left"
            >
              <span className="font-mono text-gold text-sm w-10">{entry.ticker}</span>
              <span className="text-xs text-muted font-mono flex-1 truncate">
                {entry.company || ""}
              </span>
              <span className="text-xs text-muted font-mono w-16 text-right">
                {entry.report_type || ""}
              </span>
              <span className="text-xs text-foreground font-mono w-14 text-right">
                {formatDate(date)}
              </span>
              <span className="text-xs font-mono w-16 text-right">
                {days === 0 ? (
                  <span className="text-gold">Today</span>
                ) : days === 1 ? (
                  <span className="text-gold">Tomorrow</span>
                ) : days <= 7 ? (
                  <span className="text-gold">{days}d</span>
                ) : (
                  <span className="text-muted">{days}d</span>
                )}
              </span>
              <ConfidenceDot confidence={entry.confidence} source={entry.source} />
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-4 mt-2 px-1">
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/60" />
          <span className="text-[9px] font-mono text-muted/50">confirmed</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400/50" />
          <span className="text-[9px] font-mono text-muted/50">single source</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-red-400/40" />
          <span className="text-[9px] font-mono text-muted/50">estimated</span>
        </div>
        <span className="text-[9px] font-mono text-muted/30 ml-auto">
          Verify with official ASX announcements
        </span>
      </div>
    </div>
  );
}
