"use client";

import { useState, useRef, useEffect } from "react";
import { ASX200_TICKERS } from "@/lib/asx200";

const QUICK_PICKS = ["BHP", "CBA", "RIO", "WES", "ANZ", "WBC"];

interface TickerInputProps {
  value: string;
  onChange: (v: string) => void;
}

export default function TickerInput({ value, onChange }: TickerInputProps) {
  const [open, setOpen] = useState(false);
  const [filtered, setFiltered] = useState<string[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
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
      <div className="flex gap-2 mt-2">
        {QUICK_PICKS.map((t) => (
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
  );
}
