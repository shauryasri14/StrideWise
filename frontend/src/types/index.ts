export interface LatLon { lat: number; lon: number }

export interface Metrics {
  distance: number | null; shade: number | null; traffic: number | null;
  air_quality: number | null; surface: number | null; elevation: number | null;
}
export interface AqiSummary {
  status: "ok" | "stale" | "unavailable"; avg?: number; max?: number; pct_poor?: number; age_minutes?: number; source: string;
}
export interface RouteResult {
  id: string; label: string; profile: "best_match" | "lowest_pollution" | "quietest"; shape: string;
  distance_km: number; duration_minutes: number; elevation_gain_m: number | null; score: number;
  metrics: Metrics; weights: Record<string, number>; explanation: string; warnings: string[];
  details: { aqi: AqiSummary | null; parks_passed: string[]; shade_confidence: string | null };
  geometry: { type: "LineString"; coordinates: [number, number][] };
}
export interface RoutesResponse {
  clarification: string | null; routes: RouteResult[]; warnings: string[]; parser: "llm" | "rules";
  constraints: Record<string, unknown>;
}
