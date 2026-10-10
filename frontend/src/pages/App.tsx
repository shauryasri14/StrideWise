import { useState } from "react";
import MapView from "../components/MapView";
import RouteList from "../components/RouteList";
import { useRoutes } from "../hooks/useRoutes";
import type { LatLon } from "../types";

const EXAMPLES = [
  "5 km, shaded, low traffic, finishing near a park",
  "3 km easy walk, avoid busy roads and highways",
  "8 km run with the least pollution possible",
  "5 km loop, mostly shaded, avoid steep roads",
];

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [start, setStart] = useState<LatLon | null>(null);
  const [geoError, setGeoError] = useState<string | null>(null);
  const { data, loading, error, selected, setSelected, run } = useRoutes();

  function useMyLocation() {
    setGeoError(null);
    if (!navigator.geolocation) return setGeoError("This browser has no location support. Click the map instead.");
    navigator.geolocation.getCurrentPosition(
      (p) => setStart({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => setGeoError("Location was blocked. Click the map to set your start point."),
    );
  }

  const canBuild = !!start && prompt.trim().length >= 3 && !loading;
  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="z-10 flex max-h-[55%] w-full flex-col gap-4 overflow-y-auto bg-fog p-5 md:max-h-full md:w-[420px] md:shrink-0">
        <header>
          <h1 className="text-xl font-semibold">Route Builder</h1>
          <p className="text-sm text-ink/70">Describe a walk or run. Your request is parsed by a model on your own machine.</p>
        </header>

        <label className="text-sm font-medium" htmlFor="prompt">What kind of route do you want?</label>
        <textarea id="prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
          placeholder='Include a distance, e.g. "5 km shaded run, low traffic, finishing near a park"'
          className="w-full resize-none rounded-md border border-mist bg-white p-3 text-sm" />
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((x) => <button key={x} onClick={() => setPrompt(x)} className="rounded-full border border-mist px-3 py-1 text-xs hover:bg-white">{x}</button>)}
        </div>

        <div className="rounded-md border border-mist p-3 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span>{start ? `Start: ${start.lat.toFixed(4)}, ${start.lon.toFixed(4)}` : "Start: click the map or use your location"}</span>
            <button onClick={useMyLocation} className="rounded bg-white px-3 py-1 text-xs shadow-sm">Use my location</button>
          </div>
          {geoError && <p className="mt-2 text-xs text-amber-900">{geoError}</p>}
          <p className="mt-2 text-xs text-ink/60">Your location is sent only to your own backend. It is not saved.</p>
        </div>

        <button disabled={!canBuild} onClick={() => start && run(prompt, start)}
          className="rounded-md bg-best px-4 py-3 font-medium text-white disabled:opacity-40">
          {loading ? "Building routes..." : "Build route"}
        </button>

        {error && <p role="alert" className="rounded bg-red-50 p-3 text-sm text-red-900">{error}</p>}
        {data?.clarification && <p role="status" className="rounded bg-white p-3 text-sm">{data.clarification}</p>}
        {data?.warnings.map((w) => <p key={w} className="rounded bg-amber-50 p-2 text-xs text-amber-900">{w}</p>)}
        {data && data.routes.length > 0 && <RouteList routes={data.routes} selected={selected} onSelect={setSelected} />}
        {data && <p className="text-xs text-ink/50">Request parsed by: {data.parser === "llm" ? "local model (Ollama)" : "built-in keyword parser"}</p>}
      </aside>
      <main className="min-h-[300px] flex-1">
        <MapView start={start} routes={data?.routes ?? []} selected={selected} onPick={setStart} />
      </main>
    </div>
  );
}
