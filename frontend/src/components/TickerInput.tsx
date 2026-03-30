"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { ASX200_TICKERS } from "@/lib/asx200";
import { getActivity } from "@/lib/api";

const STATIC_DEFAULTS = ["BHP", "CBA", "RIO", "WES", "ANZ", "WBC"];
const CHIP_COUNT = 6;
const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 minutes

interface TickerInputProps {
  value: string;
  onChange: (v: string) => void;
}

export default function TickerInput({ value, onChange }: TickerInputProps) {
  const [open, setOpen] = useState(false);
  const [filtered, setFiltered] = useState<string[]>([]);
  const [chips, setChips] = useState<string[]>(STATIC_DEFAULTS);
  const [isLive, setIsLive] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const fetchChips = useCallback(async () => {
    try {
      // Try today first
      let items = await getActivity("today");

      // Fall back to week if fewer than 3 today
      if (items.length < 3) {
        items = await getActivity("week");
      }

      const liveTickers = items.map((i) => i.ticker).slice(0, CHIP_COUNT);

      if (liveTickers.length >= 3) {
        // Pad with static defaults if under 6
        const seen = new Set(liveTickers);
        for (const t of STATIC_DEFAULTS) {
          if (liveTickers.length >= CHIP_COUNT) break;
          if (!seen.has(t)) {
            liveTickers.push(t);
            seen.add(t);
          }
        }
        setChips(liveTickers);
        setIsLive(true);
      } else {
        setChips(STATIC_DEFAULTS);
        setIsLive(false);
      }
    } catch {
      setChips(STATIC_DEFAULTS);
      setIsLive(false);
    }
  }, []);

  useEffect(() => {
    fetchChips();
    const timer = setInterval(fetchChips, REFRESH_INTERVAL);
    return () => clearInterval(timer);
  }, [fetchChips]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleChange = (v: string) => {
    const upper = v.toUpperCase();
    onChange(upper);
    if (upper.length > 0) {
      setFiltered(
        ASX200_TICKERS.filter((t) => t.startsWith(upper)).slice(0, 8)
      );
      setOpen(true);
    } else {
      setOpen(false);
    }
  };

  const select = (t: string) => {
    onChange(t);
    setOpen(false);
  };

  return (
    <div ref={ref} className="relative">
      <label className="block text-xs text-muted mb-1 tracking-widest uppercase">
        ASX Ticker
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        placeholder="BHP"
        className="w-full bg-surface border border-surface-border rounded px-4 py-3 text-lg font-mono text-gold placeholder:text-muted/40 focus:outline-none focus:border-gold/50 transition"
        maxLength={4}
      />
      {open && filtered.length > 0 && value.length > 0 && (
        <ul className="absolute z-10 w-full mt-1 bg-surface border border-surface-border rounded shadow-lg max-h-48 overflow-y-auto">
          {filtered.map((t) => (
            <li
              key={t}
              onClick={() => select(t)}
              className="px-4 py-2 cursor-pointer hover:bg-surface-light hover:text-gold transition text-sm font-mono"
            >
              {t}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-xs text-muted tracking-widest uppercase">
            Quick select
          </span>
          {isLive && (
            <span className="flex items-center gap-1 text-xs text-gold/60 font-mono">
              <span className="animate-pulse-gold">&#9673;</span>
              Live
            </span>
          )}
        </div>
        <div className="flex gap-2 flex-wrap">
          {chips.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => select(t)}
              className={`px-3 py-1 rounded text-xs font-mono border transition ${
                value === t
                  ? "bg-gold/20 border-gold text-gold"
                  : "bg-surface border-surface-border text-muted hover:border-gold/40 hover:text-gold"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
