import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Label, Section, Toggle } from "./components/ui";

export const CODE_THEMES = ["dark", "light", "dracula", "monokai"];

function Choice({ value, onChange, options }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {options.map((o) => (
        <button key={o.value} type="button" onClick={() => onChange(o.value)} aria-pressed={value === o.value}
          className={`rounded-xl border px-3 py-2.5 text-left ${value === o.value ? "border-marigold bg-marigold/10" : "border-line hover:border-mist"}`}>
          <div className="text-sm font-semibold">{o.label}</div>
          <div className="text-xs leading-snug text-mist">{o.sub}</div>
        </button>
      ))}
    </div>
  );
}

function Tester({ kind, provider, save }) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    setRes(null);
    try { await save(); setRes(await api.post("/api/settings/test", { kind, provider })); }
    catch (e) { setRes({ ok: false, message: e.message }); }
    setBusy(false);
  };
  return (
    <div className="mt-4 space-y-2">
      <button className="btn btn-ghost btn-sm" onClick={run} disabled={busy}>{busy ? "Testing…" : "Test this"}</button>
      {res && (
        <div role="status" className={`rounded-lg border px-3 py-2 text-sm ${res.ok ? "border-peacock/50 text-peacock" : "border-sindoor/50 text-sindoor"}`}>
          {res.message}
          {res.url && kind === "image" && <img src={res.url} alt="" className="mt-2 h-40 rounded-lg" />}
          {res.url && kind === "tts" && <audio controls autoPlay src={res.url} className="mt-2" />}
        </div>
      )}
    </div>
  );
}

function Secret({ id, label, value, onChange, placeholder }) {
  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <input id={id} className="field font-mono text-sm" type="password" autoComplete="off" value={value} placeholder={placeholder}
        onFocus={(e) => value.startsWith("•") && onChange("")} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export default function Settings({ health }) {
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState("");
  const [models, setModels] = useState([]);
  const [trademarkUrl, setTrademarkUrl] = useState("");
  const [tracks, setTracks] = useState([]);
  const trademarkRef = useRef();
  const musicRef = useRef();

  useEffect(() => {
    api.get("/api/settings").then(setS);
    api.get("/api/catalog").then((c) => setTrademarkUrl(c.trademark_url));
    api.get("/api/music").then((m) => setTracks(m.tracks));
  }, []);
  if (!s) return <p className="text-mist">Loading settings…</p>;

  const uploadTrademark = async (file) => {
    const r = await api.upload("/api/settings/trademark-image", file);
    setTrademarkUrl(r.trademark_url);
  };

  const uploadMusic = async (file) => {
    const m = await api.upload("/api/music/upload", file);
    setTracks(m.tracks);
    set("default_code_quiz_music", m.name);
  };

  const set = (k, v) => setS((x) => ({ ...x, [k]: v }));
  const save = async () => { setS(await api.put("/api/settings", s)); setSaved("Saved"); setTimeout(() => setSaved(""), 2000); };
  const loadModels = async () => {
    await save();
    try { setModels((await api.get("/api/gemini/models")).models); } catch (e) { alert(e.message); }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="font-display text-3xl text-parchment">Settings</h1>
        <p className="mt-1 text-mist">Everything here is free. Keys are stored only on this computer, in <code>data/settings.json</code>.</p>
      </div>

      <Section title="Scene writer (AI)" hint="Turns your story into scenes, narration and image prompts.">
        <Choice value={s.llm_provider} onChange={(v) => set("llm_provider", v)} options={[
          { value: "gemini", label: "Google Gemini", sub: "Free API key, best Hindi" },
          { value: "pollinations", label: "Pollinations", sub: "Uses your Pollinations key" },
          { value: "ollama", label: "Ollama", sub: "Runs on your Mac, offline" },
          { value: "offline", label: "No AI", sub: "Splits your sentences into scenes" },
        ]} />
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {s.llm_provider === "gemini" && (<>
            <Secret id="gk" label="Gemini API key (aistudio.google.com → Get API key)" value={s.gemini_api_key} onChange={(v) => set("gemini_api_key", v)} placeholder="AIza…" />
            <div>
              <Label htmlFor="gm">Model</Label>
              <div className="flex gap-2">
                {models.length ? (
                  <select id="gm" className="field" value={s.gemini_model} onChange={(e) => set("gemini_model", e.target.value)}>
                    {[s.gemini_model, ...models.filter((m) => m !== s.gemini_model)].map((m) => <option key={m}>{m}</option>)}
                  </select>
                ) : <input id="gm" className="field" value={s.gemini_model} onChange={(e) => set("gemini_model", e.target.value)} />}
                <button className="btn btn-ghost btn-sm" onClick={loadModels}>List</button>
              </div>
            </div>
          </>)}
          {s.llm_provider === "ollama" && (<>
            <div><Label htmlFor="ou">Ollama address</Label><input id="ou" className="field" value={s.ollama_url} onChange={(e) => set("ollama_url", e.target.value)} /></div>
            <div><Label htmlFor="om">Model (run “ollama pull” first)</Label><input id="om" className="field" value={s.ollama_model} onChange={(e) => set("ollama_model", e.target.value)} /></div>
          </>)}
          {s.llm_provider === "pollinations" && (
            <div><Label htmlFor="ptm">Text model</Label><input id="ptm" className="field" value={s.pollinations_text_model} onChange={(e) => set("pollinations_text_model", e.target.value)} /></div>
          )}
        </div>
        <Tester kind="llm" provider={s.llm_provider} save={save} />
      </Section>

      <Section title="Images" hint="FLUX-family models make the pictures. If one service is busy, the next configured one is tried automatically.">
        <Choice value={s.image_provider} onChange={(v) => set("image_provider", v)} options={[
          { value: "pollinations", label: "Pollinations", sub: "Free online FLUX, 9:16" },
          { value: "cloudflare", label: "Cloudflare AI", sub: "Free daily quota, square images" },
          { value: "mflux", label: "On this Mac", sub: health?.mflux ? "FLUX via mflux, slow, unlimited" : "Not installed (--local-images)" },
          { value: "offline", label: "Placeholders", sub: "For testing without internet" },
        ]} />
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Secret id="pk" label="Pollinations key (enter.pollinations.ai, optional)" value={s.pollinations_api_key} onChange={(v) => set("pollinations_api_key", v)} placeholder="sk_…" />
          <div><Label htmlFor="pim">Pollinations image model</Label><input id="pim" className="field" value={s.pollinations_image_model} onChange={(e) => set("pollinations_image_model", e.target.value)} /></div>
          <div><Label htmlFor="cfa">Cloudflare account ID</Label><input id="cfa" className="field font-mono text-sm" value={s.cloudflare_account_id} onChange={(e) => set("cloudflare_account_id", e.target.value)} /></div>
          <Secret id="cft" label="Cloudflare API token (Workers AI)" value={s.cloudflare_api_token} onChange={(v) => set("cloudflare_api_token", v)} />
          <div><Label htmlFor="ms">Steps on this Mac (more = better, slower)</Label><input id="ms" type="number" min={2} max={12} className="field" value={s.mflux_steps} onChange={(e) => set("mflux_steps", Number(e.target.value))} /></div>
        </div>
        <Tester kind="image" provider={s.image_provider} save={save} />
      </Section>

      <Section title="Voice" hint="The default for new stories. You can switch per story on the Voice step.">
        <Choice value={s.tts_provider} onChange={(v) => set("tts_provider", v)} options={[
          { value: "edge", label: "Microsoft neural", sub: "Free, online, Hindi + Indian English" },
          { value: "offline", label: "Test tone", sub: "Beeps, for testing" },
        ]} />
        <Tester kind="tts" provider={s.tts_provider} save={save} />
      </Section>

      <Section title="Code quiz reels" hint="Your trademark background image and default music, reused automatically for every code-quiz video.">
        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <Label>Trademark background image</Label>
            {trademarkUrl ? (
              <img src={trademarkUrl} alt="" className="mb-2 h-40 w-auto rounded-lg border border-line object-cover" />
            ) : (
              <p className="mb-2 text-sm text-mist">None set yet — code-quiz videos need one.</p>
            )}
            <button className="btn btn-ghost btn-sm" onClick={() => trademarkRef.current.click()}>
              {trademarkUrl ? "Replace image" : "Upload image"}
            </button>
            <input ref={trademarkRef} type="file" accept="image/*" hidden
              onChange={(e) => e.target.files[0] && uploadTrademark(e.target.files[0])} />
          </div>
          <div>
            <Label htmlFor="dcqm">Default music track</Label>
            <div className="flex gap-2">
              <select id="dcqm" className="field" value={s.default_code_quiz_music}
                onChange={(e) => set("default_code_quiz_music", e.target.value)}>
                <option value="">No music</option>
                {tracks.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
              </select>
              <button className="btn btn-ghost btn-sm" onClick={() => musicRef.current.click()}>Add a track</button>
              <input ref={musicRef} type="file" accept="audio/*" hidden onChange={(e) => e.target.files[0] && uploadMusic(e.target.files[0])} />
            </div>
            <p className="mt-1 text-xs text-mist">Tracks you add here are shared across the app (also usable from any story project's Video step).</p>
          </div>
          <div>
            <Label htmlFor="cqtheme">Code block theme</Label>
            <select id="cqtheme" className="field" value={s.code_theme} onChange={(e) => set("code_theme", e.target.value)}>
              {CODE_THEMES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <Label htmlFor="cqsize">Code block font size: {s.code_font_size}</Label>
            <input id="cqsize" type="range" min={18} max={64} step={2} value={s.code_font_size}
              onChange={(e) => set("code_font_size", Number(e.target.value))} aria-label="Code block font size" />
          </div>
        </div>
      </Section>

      <Section title="Reliability">
        <Toggle label="If a service fails, try the other configured services automatically" checked={s.auto_fallback} onChange={(v) => set("auto_fallback", v)} />
        {health?.ffmpeg?.found && <p className="mt-2 text-xs text-mist">FFmpeg: {health.ffmpeg.path}</p>}
      </Section>

      <div className="sticky bottom-4 flex items-center gap-3">
        <button className="btn btn-primary shadow-lg shadow-black/40" onClick={save}>Save settings</button>
        {saved && <span role="status" className="text-sm text-peacock">{saved}</span>}
      </div>
    </div>
  );
}
