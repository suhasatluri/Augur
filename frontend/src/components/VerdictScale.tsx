interface VerdictScaleProps {
  verdict: string;
}

const SCALE = [
  { label: "LIKELY MISS", color: "bg-red-500" },
  { label: "LEAN MISS", color: "bg-red-400/60" },
  { label: "TOSS-UP", color: "bg-yellow-500/60" },
  { label: "LEAN BEAT", color: "bg-emerald-400/60" },
  { label: "LIKELY BEAT", color: "bg-emerald-500" },
];

export default function VerdictScale({ verdict }: VerdictScaleProps) {
  return (
    <div className="mt-4">
      {/* Scale bar */}
      <div className="flex items-center gap-0.5 h-2 rounded overflow-hidden">
        {SCALE.map((s) => (
          <div
            key={s.label}
            className={`flex-1 h-full transition-all duration-500 ${
              s.label === verdict ? `${s.color} ring-1 ring-gold` : "bg-surface-light"
            }`}
          />
        ))}
      </div>

      {/* Labels */}
      <div className="flex justify-between mt-1.5">
        <span className="text-xs font-mono text-red-400/60">MISS</span>
        <span className="text-xs font-mono text-muted/40">TOSS-UP</span>
        <span className="text-xs font-mono text-emerald-400/60">BEAT</span>
      </div>

      {/* Footer */}
      <p className="text-center text-xs text-muted/40 font-mono mt-2">
        Based on 50 agent debate &middot; 3 rounds
      </p>
    </div>
  );
}
