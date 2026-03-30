interface ProbabilityBarsProps {
  pBeat: number;
  pMiss: number;
  pInline: number;
  mean: number;
  bandLow: number;
  bandHigh: number;
}

function Bar({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-muted w-16 text-right font-mono">
        {label}
      </span>
      <div className="flex-1 h-6 bg-surface rounded overflow-hidden">
        <div
          className={`h-full rounded transition-all duration-700 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-sm font-mono text-foreground w-14 text-right">
        {pct}%
      </span>
    </div>
  );
}

export default function ProbabilityBars({
  pBeat,
  pMiss,
  pInline,
  mean,
  bandLow,
  bandHigh,
}: ProbabilityBarsProps) {
  return (
    <div className="space-y-3">
      <Bar label="Beat" value={pBeat} color="bg-emerald-600" />
      <Bar label="Inline" value={pInline} color="bg-yellow-600/70" />
      <Bar label="Miss" value={pMiss} color="bg-red-600" />

      <div className="mt-4 pt-4 border-t border-surface-border flex justify-between text-xs font-mono text-muted">
        <span>
          Mean: <span className="text-foreground">{mean.toFixed(3)}</span>
        </span>
        <span>
          Band:{" "}
          <span className="text-foreground">
            {bandLow.toFixed(3)} — {bandHigh.toFixed(3)}
          </span>{" "}
          (±1σ)
        </span>
      </div>
    </div>
  );
}
