interface VerdictBadgeProps {
  verdict: string;
}

const COLORS: Record<string, string> = {
  "LIKELY BEAT": "bg-emerald-900/60 text-emerald-300 border-emerald-700",
  "LEAN BEAT": "bg-emerald-900/30 text-emerald-400 border-emerald-800",
  "TOSS-UP": "bg-yellow-900/30 text-yellow-300 border-yellow-800",
  "LEAN MISS": "bg-red-900/30 text-red-400 border-red-800",
  "LIKELY MISS": "bg-red-900/60 text-red-300 border-red-700",
};

export default function VerdictBadge({ verdict }: VerdictBadgeProps) {
  const cls = COLORS[verdict] || "bg-surface text-muted border-surface-border";
  return (
    <span
      className={`inline-block px-4 py-2 rounded border font-mono text-sm tracking-wider ${cls}`}
    >
      {verdict}
    </span>
  );
}
