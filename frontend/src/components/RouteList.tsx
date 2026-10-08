import type { RouteResult } from "../types";
import { ROUTE_COLOR } from "./MapView";
import MetricBar from "./MetricBar";

export default function RouteList({ routes, selected, onSelect }: { routes: RouteResult[]; selected: number; onSelect: (i: number) => void }) {
  const cur = routes[selected];
  return (
    <section aria-label="Route options" className="space-y-4">
      <div className="grid grid-cols-3 gap-2">
        {routes.map((r, i) => (
          <button key={r.id} onClick={() => onSelect(i)} aria-pressed={i === selected}
            className={`rounded-md border-2 px-2 py-2 text-left text-sm ${i === selected ? "bg-white" : "bg-transparent opacity-80"}`}
            style={{ borderColor: i === selected ? ROUTE_COLOR[r.profile] : "#D5E0DC" }}>
            <div className="font-medium">{r.label}</div>
            <div className="text-xs text-ink/70">{r.distance_km} km · {r.duration_minutes} min</div>
          </button>
        ))}
      </div>
      {cur && (
        <article className="rounded-md bg-white p-4 shadow-sm">
          <h2 className="text-2xl font-semibold tabular-nums">
            {cur.distance_km} km <span className="text-base font-normal text-ink/70">{cur.duration_minutes} min{cur.elevation_gain_m !== null ? `, +${Math.round(cur.elevation_gain_m)} m climb` : ""}</span>
          </h2>
          <div className="mt-3 space-y-1.5">
            <MetricBar label="Shade" value={cur.metrics.shade} estimate />
            <MetricBar label="Low traffic" value={cur.metrics.traffic} estimate />
            <MetricBar label="Air quality" value={cur.metrics.air_quality} />
            <MetricBar label="Surface" value={cur.metrics.surface} estimate />
            <MetricBar label="Match score" value={cur.score} />
          </div>
          <h3 className="mt-4 font-medium">Why this route?</h3>
          <p className="mt-1 text-sm leading-relaxed">{cur.explanation}</p>
          {cur.details.aqi && cur.details.aqi.status !== "unavailable" && (
            <p className="mt-2 text-xs text-ink/70">
              AQI avg {cur.details.aqi.avg}, max {cur.details.aqi.max}, {Math.round((cur.details.aqi.pct_poor ?? 0) * 100)}% of sampled points above 100.
              Data is {cur.details.aqi.status === "stale" ? "stale" : "fresh"} ({cur.details.aqi.age_minutes} min old). Source: {cur.details.aqi.source}.
            </p>
          )}
          {cur.warnings.map((w) => <p key={w} className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-900">{w}</p>)}
        </article>
      )}
    </section>
  );
}
