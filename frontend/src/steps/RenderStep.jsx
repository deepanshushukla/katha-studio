import { useEffect, useRef, useState } from "react";
import { api, copy, useJob } from "../api";
import { Label, Progress, Section, Segmented, Toggle } from "../components/ui";

const SWATCH = { gold: "#FFD530", saffron: "#FF8C1E", cyan: "#4DE1FF", white: "#FFFFFF" };

export default function RenderStep({ p, setP, reload }) {
  const [o, setO] = useState(p.render);
  const [cat, setCat] = useState(null);
  const [tracks, setTracks] = useState([]);
  const [backgrounds, setBackgrounds] = useState([]);
  const [renders, setRenders] = useState([]);
  const [meta, setMeta] = useState(p.metadata || {});
  const [copied, setCopied] = useState("");
  const musicRef = useRef();
  const player = useRef(null);
  const loadRenders = () => api.get(`/api/projects/${p.id}/renders`).then(setRenders);
  const [job, start] = useJob(async () => { await reload(); loadRenders(); });
  const [mjob, mstart] = useJob(async (j) => { if (j.result) setMeta(j.result); });
  const running = job?.status === "running";

  useEffect(() => {
    api.get("/api/catalog").then(setCat);
    api.get("/api/music").then((m) => setTracks(m.tracks));
    api.get("/api/backgrounds").then(setBackgrounds);
    loadRenders();
    api.get(`/api/projects/${p.id}/jobs`).then((jobs) => {
      const j = jobs.find((x) => x.kind === "render");
      if (j) start(Promise.resolve(j));
    });
    if (p.content_type !== "code_quiz" && !p.metadata?.title) mstart(api.post(`/api/projects/${p.id}/metadata`));
    return () => player.current?.pause();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k, v) => setO((x) => ({ ...x, [k]: v }));
  const speech = p.scenes.reduce((t, s) => t + s.audio_duration, 0);

  const uploadMusic = async (f) => {
    const m = await api.upload("/api/music/upload", f);
    setTracks(m.tracks);
    set("music", m.name);
  };
  const previewMusic = () => {
    if (player.current && !player.current.paused) { player.current.pause(); return; }
    const t = tracks.find((x) => x.name === o.music);
    if (!t) return;
    player.current = new Audio(t.url);
    player.current.volume = Math.min(1, o.music_volume * 3);
    player.current.play();
  };
  const saveMeta = (m) => { setMeta(m); api.patch(`/api/projects/${p.id}`, { metadata: m }); };
  const doCopy = (k, text) => { copy(text); setCopied(k); setTimeout(() => setCopied(""), 1500); };
  const tags = (meta.hashtags || []).join(" ");

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-6">
        <Section title="Captions & title">
          <div className="grid gap-5 md:grid-cols-2">
            <div>
              <Label>Caption style</Label>
              <Segmented name="Caption style" value={o.captions} onChange={(v) => set("captions", v)}
                options={[{ value: "karaoke", label: "Word highlight" }, { value: "simple", label: "Plain" }, { value: "off", label: "Off" }]} />
            </div>
            <div>
              <Label>Position</Label>
              <Segmented name="Caption position" value={o.caption_position} onChange={(v) => set("caption_position", v)}
                options={[{ value: "middle", label: "Middle" }, { value: "lower", label: "Lower" }, { value: "bottom", label: "Bottom" }]} />
            </div>
            <div>
              <Label>Highlight colour</Label>
              <div className="flex gap-2" role="radiogroup" aria-label="Highlight colour">
                {Object.entries(SWATCH).map(([k, c]) => (
                  <button key={k} role="radio" aria-checked={o.highlight === k} aria-label={k} onClick={() => set("highlight", k)}
                    className={`h-8 w-8 rounded-full ring-offset-2 ring-offset-dusk ${o.highlight === k ? "ring-2 ring-parchment" : ""}`} style={{ background: c }} />
                ))}
              </div>
            </div>
            <div>
              <Toggle label="Show title at the start" checked={o.show_title} onChange={(v) => set("show_title", v)} />
              {o.show_title && <input className="field mt-2" lang={p.language} value={o.title_text || ""} placeholder={p.title} onChange={(e) => set("title_text", e.target.value)} aria-label="Title text" />}
            </div>
          </div>
        </Section>

        <Section title="Visuals" hint="Use your scene images, or play one looping ambient background behind the whole video instead.">
          <Segmented name="Visual mode" value={o.visual_mode || "scenes"} onChange={(v) => set("visual_mode", v)}
            options={[{ value: "scenes", label: "Your scene images" }, { value: "background", label: "Background video" }]} />
          {o.visual_mode === "background" && (
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {backgrounds.map((b) => (
                <button key={b.key} type="button" onClick={() => set("background", b.key)} aria-pressed={o.background === b.key}
                  className={`overflow-hidden rounded-xl border text-left ${o.background === b.key ? "border-marigold ring-2 ring-marigold" : "border-line hover:border-mist"}`}>
                  <img src={b.thumb} alt="" className="aspect-[9/16] w-full object-cover" />
                  <div className="p-2 text-xs font-medium">{b.label}</div>
                </button>
              ))}
              {!backgrounds.length && <p className="text-sm text-mist">Loading backgrounds…</p>}
            </div>
          )}
        </Section>

        <Section title="Motion & look" hint={o.visual_mode === "background" ? "Camera movement and transitions only apply to scene images." : undefined}>
          <div className="grid gap-5 md:grid-cols-2">
            <div>
              <Label>Camera movement</Label>
              <Segmented name="Camera movement" value={o.motion} onChange={(v) => set("motion", v)}
                options={[{ value: "gentle", label: "Gentle" }, { value: "normal", label: "Normal" }, { value: "strong", label: "Strong" }]} />
            </div>
            <div>
              <Label htmlFor="tr">Transitions</Label>
              <select id="tr" className="field" value={o.transition} onChange={(e) => set("transition", e.target.value)}>
                {(cat?.transitions || ["auto"]).map((t) => <option key={t} value={t}>{t === "auto" ? "Mixed (varied per scene)" : t === "cut" ? "Hard cut" : t}</option>)}
              </select>
            </div>
            <div>
              <Toggle label="Warm golden grade" checked={o.warm} onChange={(v) => set("warm", v)} />
              <Toggle label="Vignette" checked={o.vignette} onChange={(v) => set("vignette", v)} />
              <Toggle label="Film grain" checked={o.grain} onChange={(v) => set("grain", v)} />
            </div>
            <div>
              <Label>Pause between scenes: {Number(o.scene_pause).toFixed(2)} s</Label>
              <input type="range" min={0} max={0.8} step={0.04} value={o.scene_pause} onChange={(e) => set("scene_pause", Number(e.target.value))} aria-label="Pause between scenes" />
            </div>
          </div>
        </Section>

        <Section title="Background music" hint="Use tracks you’re allowed to publish, e.g. from the YouTube Audio Library. Music dips automatically while the narrator speaks.">
          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto]">
            <select className="field" value={o.music} onChange={(e) => set("music", e.target.value)} aria-label="Music track">
              <option value="">No music</option>
              {tracks.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
            </select>
            <div className="flex gap-2">
              <button className="btn btn-ghost btn-sm" disabled={!o.music} onClick={previewMusic}>Listen</button>
              <button className="btn btn-ghost btn-sm" onClick={() => musicRef.current.click()}>Add a track</button>
              <input ref={musicRef} type="file" accept="audio/*" hidden onChange={(e) => e.target.files[0] && uploadMusic(e.target.files[0])} />
            </div>
          </div>
          {o.music && (
            <div className="mt-4">
              <Label>Music volume: {Math.round(o.music_volume * 100)}%</Label>
              <input type="range" min={0.04} max={0.4} step={0.02} value={o.music_volume} onChange={(e) => set("music_volume", Number(e.target.value))} aria-label="Music volume" />
            </div>
          )}
        </Section>

        {p.content_type === "code_quiz" ? (
        <Section title="Title" hint="Set on the Quiz step's project title — shown on screen at the start of the video.">
          <p className="text-sm text-mist">{p.title}</p>
        </Section>
        ) : (
        <Section title="Title, description and hashtags" hint="Suggested for the upload; edit anything."
          actions={<button className="btn btn-ghost btn-sm" disabled={mjob?.status === "running"} onClick={() => mstart(api.post(`/api/projects/${p.id}/metadata`))}>Suggest again</button>}>
          {mjob?.status === "running" && <Progress job={mjob} />}
          <div className="space-y-3">
            {[["title", "Title", 1], ["description", "Description", 4]].map(([k, l, rows]) => (
              <div key={k}>
                <div className="flex items-center justify-between"><Label>{l}</Label>
                  <button className="text-xs text-mist hover:text-parchment" onClick={() => doCopy(k, meta[k] || "")}>{copied === k ? "Copied" : "Copy"}</button></div>
                <textarea className="field text-sm" rows={rows} lang={p.language} value={meta[k] || ""} onChange={(e) => saveMeta({ ...meta, [k]: e.target.value })} />
              </div>
            ))}
            <div>
              <div className="flex items-center justify-between"><Label>Hashtags</Label>
                <button className="text-xs text-mist hover:text-parchment" onClick={() => doCopy("tags", tags)}>{copied === "tags" ? "Copied" : "Copy"}</button></div>
              <textarea className="field text-sm" rows={2} value={tags} onChange={(e) => saveMeta({ ...meta, hashtags: e.target.value.split(/\s+/).filter(Boolean) })} />
            </div>
          </div>
        </Section>
        )}
      </div>

      <div className="xl:sticky xl:top-20 xl:self-start">
        <div className="rounded-[2.2rem] border-[6px] border-panel bg-black p-1.5 shadow-2xl shadow-black/40">
          <div className="aspect-[9/16] overflow-hidden rounded-[1.7rem] bg-night">
            {p.last_render_url && !running ? (
              <video key={p.last_render_url} src={p.last_render_url} controls playsInline className="h-full w-full object-contain" />
            ) : (
              <div className="grid h-full place-items-center p-8 text-center text-sm text-mist">
                {running ? "Rendering… this takes a minute or two." : `Your video will play here. About ${Math.round(speech + p.scenes.length * 0.6)} seconds long.`}
              </div>
            )}
          </div>
        </div>
        <div className="mt-4 space-y-3">
          <button className="btn btn-primary w-full justify-center" disabled={running}
            onClick={() => start(api.post(`/api/projects/${p.id}/render`, o))}>
            {p.last_render_url ? "Render again with these settings" : "Render video"}
          </button>
          <Progress job={job} />
          {p.last_render_url && !running && (
            <a className="btn btn-ghost w-full justify-center" href={p.last_render_url.split("?")[0]} download>Download MP4</a>
          )}
          {renders.length > 1 && (
            <details className="text-sm text-mist">
              <summary className="cursor-pointer">Earlier renders ({renders.length - 1})</summary>
              <ul className="mt-2 space-y-1">
                {renders.slice(1).map((r) => (
                  <li key={r.name}><a className="underline hover:text-parchment" href={r.url.split("?")[0]} download>{r.name}</a> ({r.size_mb} MB)</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
