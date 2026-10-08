import type { LatLon, RoutesResponse } from "../types";

export async function buildRoutes(prompt: string, start: LatLon): Promise<RoutesResponse> {
  const res = await fetch("/api/routes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, start }),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error?.message ?? `Request failed (HTTP ${res.status})`);
  return body as RoutesResponse;
}

export async function health(): Promise<{ ollama: boolean; valhalla: boolean; parser_mode: string }> {
  return (await fetch("/api/health")).json();
}
