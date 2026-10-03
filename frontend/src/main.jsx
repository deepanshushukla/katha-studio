import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { api } from "./api";
import { Diya } from "./components/ui";
import Home from "./Home";
import Studio from "./Studio";
import Settings from "./Settings";

function useHash() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const f = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return hash;
}

function App() {
  const hash = useHash();
  const [health, setHealth] = useState(null);
  useEffect(() => { api.get("/api/health").then(setHealth).catch(() => {}); }, []);
  const m = hash.match(/^#\/p\/(\d+)/);
  const missing = health?.ffmpeg && (!health.ffmpeg.found || Object.values(health.ffmpeg.filters || {}).some((v) => !v));

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-line/70 bg-night/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 md:px-6">
          <a href="#/" className="flex items-center gap-2.5">
            <Diya />
            <span className="font-display text-2xl tracking-wide text-marigold">Katha Studio</span>
          </a>
          <nav className="flex gap-1 text-sm">
            <a href="#/" className={`rounded-full px-3 py-1.5 ${!m && hash !== "#/settings" ? "bg-panel" : "text-mist hover:text-parchment"}`}>Stories</a>
            <a href="#/settings" className={`rounded-full px-3 py-1.5 ${hash === "#/settings" ? "bg-panel" : "text-mist hover:text-parchment"}`}>Settings</a>
          </nav>
        </div>
      </header>
      {missing && (
        <div className="mx-auto mt-4 max-w-7xl px-4 md:px-6">
          <div role="alert" className="rounded-xl border border-sindoor/50 bg-sindoor/10 px-4 py-3 text-sm">
            FFmpeg {health.ffmpeg.found ? "is missing some video filters" : "was not found"} — videos can’t be rendered.
            Run <code className="rounded bg-night px-1">./start.sh</code> again; it installs the full FFmpeg build.
          </div>
        </div>
      )}
      <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
        {m ? <Studio key={m[1]} id={Number(m[1])} /> : hash === "#/settings" ? <Settings health={health} /> : <Home />}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
