interface DateBannerProps {
  ticker: string;
  reportingDate: string | null;
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-AU", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function daysUntil(iso: string): number {
  const target = new Date(iso + "T00:00:00");
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.ceil((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

export default function DateBanner({ ticker, reportingDate }: DateBannerProps) {
  if (!reportingDate) {
    return (
      <div className="bg-surface border border-surface-border rounded px-4 py-3">
        <span className="text-xs font-mono text-muted/50">
          No reporting date specified
        </span>
      </div>
    );
  }

  const days = daysUntil(reportingDate);
  const formatted = formatDate(reportingDate);
  const isPast = days < 0;

  return (
    <div className="bg-surface border border-gold/20 rounded px-4 py-3">
      <div className="flex items-center gap-2 font-mono text-sm">
        <span className="text-gold">{ticker}</span>
        <span className="text-muted">&middot;</span>
        {isPast ? (
          <span className="text-muted">Reported {formatted}</span>
        ) : (
          <>
            <span className="text-foreground">Reporting {formatted}</span>
            <span className="text-muted">&middot;</span>
            <span className="text-gold">
              {days === 0 ? "Today" : days === 1 ? "Tomorrow" : `${days} days`}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
