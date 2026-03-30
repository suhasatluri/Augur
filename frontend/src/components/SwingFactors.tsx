import type { SwingFactor } from "@/lib/api";

interface SwingFactorsProps {
  factors: SwingFactor[];
}

export default function SwingFactors({ factors }: SwingFactorsProps) {
  if (!factors.length) return null;

  return (
    <div className="space-y-4">
      {factors.map((f, i) => {
        const barWidth = Math.round(f.disagreement_score * 100);
        return (
          <div
            key={i}
            className="bg-surface rounded border border-surface-border p-4"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="font-mono text-sm text-gold">
                {i + 1}. {f.theme}
              </span>
              <span className="text-xs text-muted font-mono">
                {f.mentions} mentions
              </span>
            </div>

            {/* Contention bar */}
            <div className="flex items-center gap-2 mb-3">
              <div className="flex-1 h-2 bg-surface-light rounded overflow-hidden">
                <div
                  className={`h-full rounded transition-all duration-500 ${
                    f.disagreement_score >= 0.7
                      ? "bg-red-500"
                      : f.disagreement_score >= 0.4
                      ? "bg-gold/70"
                      : "bg-emerald-500"
                  }`}
                  style={{ width: `${barWidth}%` }}
                />
              </div>
              <span
                className={`text-xs font-mono tracking-wider w-32 text-right ${
                  f.disagreement_score >= 0.7
                    ? "text-red-400"
                    : f.disagreement_score >= 0.4
                    ? "text-gold"
                    : "text-emerald-400"
                }`}
              >
                {f.disagreement_score >= 0.7
                  ? "HIGH CONTENTION"
                  : f.disagreement_score >= 0.4
                  ? "CONTESTED"
                  : "CONSENSUS"}
              </span>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-emerald-500 font-mono">BULL</span>
                <p className="text-muted mt-1 leading-relaxed">{f.bull_view}</p>
              </div>
              <div>
                <span className="text-red-500 font-mono">BEAR</span>
                <p className="text-muted mt-1 leading-relaxed">{f.bear_view}</p>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
