interface SentimentCascadeProps {
  direction: string;
  severity: string;
  retailConviction: number;
  reasoning: string;
}

const SEVERITY_COLORS: Record<string, string> = {
  severe: "text-red-400 border-red-800 bg-red-900/20",
  moderate: "text-yellow-400 border-yellow-800 bg-yellow-900/20",
  mild: "text-emerald-400 border-emerald-800 bg-emerald-900/20",
};

export default function SentimentCascade({
  direction,
  severity,
  retailConviction,
  reasoning,
}: SentimentCascadeProps) {
  const cls =
    SEVERITY_COLORS[severity] || "text-muted border-surface-border bg-surface";
  const dirLabel = direction.replace("_", " ").toUpperCase();

  return (
    <div className={`rounded border p-4 ${cls}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm tracking-wider">{dirLabel}</span>
          <span className="text-xs font-mono opacity-70 uppercase">
            {severity}
          </span>
        </div>
        <span className="text-xs font-mono">
          Retail conviction: {retailConviction.toFixed(2)}
        </span>
      </div>
      <p className="text-xs leading-relaxed opacity-80">{reasoning}</p>
    </div>
  );
}
