import { useState } from "react";
import { buildRoutes } from "../services/api";
import type { LatLon, RoutesResponse } from "../types";

export function useRoutes() {
  const [data, setData] = useState<RoutesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);

  async function run(prompt: string, start: LatLon) {
    setLoading(true); setError(null); setSelected(0);
    try { setData(await buildRoutes(prompt, start)); }
    catch (e) { setData(null); setError(e instanceof Error ? e.message : "Something went wrong."); }
    finally { setLoading(false); }
  }
  return { data, loading, error, selected, setSelected, run };
}
