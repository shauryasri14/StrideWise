import { useEffect, useRef } from "react";
import L from "leaflet";
import type { LatLon, RouteResult } from "../types";

// Tile requests reveal the area you are viewing to the tile host. Point VITE_TILE_URL at a self-hosted server to avoid that.
const TILE_URL = import.meta.env.VITE_TILE_URL ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const ROUTE_COLOR: Record<string, string> = { best_match: "#0B7A75", lowest_pollution: "#6A5BC4", quietest: "#B8741A" };

interface Props { start: LatLon | null; routes: RouteResult[]; selected: number; onPick: (p: LatLon) => void }

export default function MapView({ start, routes, selected, onPick }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup>(L.layerGroup());
  const pick = useRef(onPick);
  pick.current = onPick;

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current).setView([20, 0], 2);
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: "© OpenStreetMap contributors" }).addTo(m);
    layer.current.addTo(m);
    m.on("click", (e) => pick.current({ lat: e.latlng.lat, lon: e.latlng.lng }));
    map.current = m;
    return () => { m.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const m = map.current; if (!m) return;
    layer.current.clearLayers();
    if (start) L.circleMarker([start.lat, start.lon], { radius: 8, color: "#12292B", weight: 3, fillColor: "#fff", fillOpacity: 1 }).bindTooltip("Start").addTo(layer.current);
    routes.forEach((r, i) => {
      if (i === selected) return;
      L.polyline(r.geometry.coordinates.map(([lo, la]) => [la, lo] as [number, number]), { color: ROUTE_COLOR[r.profile], weight: 4, opacity: 0.35 }).addTo(layer.current);
    });
    const sel = routes[selected];
    if (sel) {
      const pts = sel.geometry.coordinates.map(([lo, la]) => [la, lo] as [number, number]);
      const line = L.polyline(pts, { color: ROUTE_COLOR[sel.profile], weight: 6, opacity: 0.95 }).addTo(layer.current);
      L.circleMarker(pts[pts.length - 1], { radius: 7, color: ROUTE_COLOR[sel.profile], fillColor: ROUTE_COLOR[sel.profile], fillOpacity: 1 }).bindTooltip("Finish").addTo(layer.current);
      m.fitBounds(line.getBounds(), { padding: [40, 40] });
    } else if (start) m.setView([start.lat, start.lon], Math.max(m.getZoom(), 14));
  }, [start, routes, selected]);

  return <div ref={el} className="h-full w-full" role="application" aria-label="Map. Click to set your start point." />;
}
