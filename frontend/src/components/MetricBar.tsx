export default function MetricBar({ label, value, estimate }: { label: string; value: number | null; estimate?: boolean }) {
  const pct = value === null ? null : Math.round(value * 100);
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-28 shrink-0">{label}{estimate ? <span className="text-ink/50"> (est.)</span> : null}</span>
      <div className="h-2 flex-1 rounded-full bg-mist" role="img" aria-label={pct === null ? `${label}: not available` : `${label}: ${pct}%`}>
        {pct !== null && <div className="h-2 rounded-full bg-moss" style={{ width: `${pct}%` }} />}
      </div>
      <span className="w-10 text-right tabular-nums">{pct === null ? "n/a" : `${pct}%`}</span>
    </div>
  );
}
