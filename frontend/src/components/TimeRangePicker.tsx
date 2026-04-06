"use client";

import { useState, useRef, useEffect } from "react";

interface TimeRangePickerProps {
  from: Date;
  to: Date;
  onChange: (from: Date, to: Date) => void;
}

const PRESETS = [
  { label: "Last 5 minutes", minutes: 5 },
  { label: "Last 15 minutes", minutes: 15 },
  { label: "Last 1 hour", minutes: 60 },
  { label: "Last 24 hours", minutes: 1440 },
  { label: "Last 7 days", minutes: 10080 },
  { label: "Last 30 days", minutes: 43200 },
  { label: "Last 90 days", minutes: 129600 },
  { label: "All time", minutes: 0 },
] as const;

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function fmtDate(d: Date) {
  return `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function getRangeLabel(from: Date, to: Date): string {
  const diffMin = (to.getTime() - from.getTime()) / 60000;
  const isRecent = Date.now() - to.getTime() < 120000;
  if (isRecent) {
    const preset = PRESETS.find((p) => p.minutes > 0 && Math.abs(p.minutes - diffMin) < 2);
    if (preset) return preset.label;
  }
  return `${fmtDate(from)}  →  ${fmtDate(to)}`;
}

function DateTimeInput({ value, onChange, label }: { value: Date; onChange: (d: Date) => void; label: string }) {
  const [d, setD] = useState(pad(value.getDate()));
  const [mo, setMo] = useState(pad(value.getMonth() + 1));
  const [y, setY] = useState(String(value.getFullYear()));
  const [h, setH] = useState(pad(value.getHours()));
  const [mi, setMi] = useState(pad(value.getMinutes()));
  const [s, setS] = useState(pad(value.getSeconds()));

  useEffect(() => {
    setD(pad(value.getDate()));
    setMo(pad(value.getMonth() + 1));
    setY(String(value.getFullYear()));
    setH(pad(value.getHours()));
    setMi(pad(value.getMinutes()));
    setS(pad(value.getSeconds()));
  }, [value]);

  function commit(dd = d, mm = mo, yy = y, hh = h, mmi = mi, ss = s) {
    const parsed = new Date(Number(yy), Number(mm) - 1, Number(dd), Number(hh), Number(mmi), Number(ss));
    if (!isNaN(parsed.getTime())) onChange(parsed);
  }

  const fc = "w-9 bg-transparent border border-gold/20 text-foreground font-mono text-[11px] text-center py-1 outline-none focus:border-gold/60 transition";
  const fcWide = "w-14 bg-transparent border border-gold/20 text-foreground font-mono text-[11px] text-center py-1 outline-none focus:border-gold/60 transition";
  const sep = (c: string) => <span className="font-mono text-[10px] text-muted select-none">{c}</span>;

  return (
    <div className="mb-3">
      <div className="font-mono text-[9px] tracking-[.12em] uppercase text-gold mb-1.5">{label}</div>
      <div className="flex items-center gap-1 mb-1">
        <input type="number" min={1} max={31} value={d} onChange={(e) => setD(e.target.value)} onBlur={() => commit()} className={fc} />
        {sep("-")}
        <input type="number" min={1} max={12} value={mo} onChange={(e) => setMo(e.target.value)} onBlur={() => commit()} className={fc} />
        {sep("-")}
        <input type="number" min={2020} max={2099} value={y} onChange={(e) => setY(e.target.value)} onBlur={() => commit()} className={fcWide} />
      </div>
      <div className="flex items-center gap-1">
        <input type="number" min={0} max={23} value={h} onChange={(e) => setH(e.target.value)} onBlur={() => commit()} className={fc} />
        {sep(":")}
        <input type="number" min={0} max={59} value={mi} onChange={(e) => setMi(e.target.value)} onBlur={() => commit()} className={fc} />
        {sep(":")}
        <input type="number" min={0} max={59} value={s} onChange={(e) => setS(e.target.value)} onBlur={() => commit()} className={fc} />
      </div>
    </div>
  );
}

export default function TimeRangePicker({ from, to, onChange }: TimeRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [customFrom, setCustomFrom] = useState(from);
  const [customTo, setCustomTo] = useState(to);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => {
    setCustomFrom(from);
    setCustomTo(to);
  }, [from, to]);

  function applyPreset(minutes: number) {
    const now = new Date();
    const f = minutes === 0 ? new Date("2024-01-01T00:00:00") : new Date(now.getTime() - minutes * 60000);
    onChange(f, now);
    setOpen(false);
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 border border-gold/25 bg-gold/[.03] px-3 py-1.5 font-mono text-[10px] text-foreground hover:border-gold/50 transition whitespace-nowrap"
      >
        <span className="text-gold">&#9201;</span>
        {getRangeLabel(from, to)}
        <span className="text-muted ml-1">&#9662;</span>
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-50 border border-gold/20 bg-background shadow-2xl flex divide-x divide-gold/10"
          style={{ minWidth: "480px" }}
        >
          <div className="p-4 w-44 shrink-0">
            <div className="font-mono text-[9px] tracking-[.12em] uppercase text-gold mb-3">Quick ranges</div>
            {PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => applyPreset(p.minutes)}
                className="block w-full text-left font-mono text-[10px] text-muted hover:text-foreground hover:bg-gold/5 px-2 py-1.5 transition"
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="p-4 flex-1">
            <div className="font-mono text-[9px] tracking-[.12em] uppercase text-gold mb-3">Custom range</div>
            <DateTimeInput label="FROM" value={customFrom} onChange={setCustomFrom} />
            <DateTimeInput label="TO" value={customTo} onChange={setCustomTo} />
            <button
              onClick={() => {
                onChange(customFrom, customTo);
                setOpen(false);
              }}
              className="w-full mt-2 py-1.5 bg-gold/80 hover:bg-gold text-background font-mono text-[10px] tracking-[.1em] uppercase transition"
            >
              Apply range
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
