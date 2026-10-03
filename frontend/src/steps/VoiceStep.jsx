import { useEffect, useRef, useState } from "react";
import { api, useJob } from "../api";
import { Label, Progress, Section } from "../components/ui";

const PROVIDERS = [
  { key: "edge", name: "Microsoft neural voices", sub: "Free, needs internet, most natural" },
  { key: "offline", name: "Test tone", sub: "Beeps only, for trying the app" },
];

// Same base voice, different rate/pitch — quick character variety without extra providers.
const VOICE_PRESETS = {
  "hi-IN-MadhurNeural": [
    { label: "Deep", rate: -10, pitch: -6 },
    { label: "Warm", rate: -5, pitch: -2 },
    { label: "Energetic", rate: 10, pitch: 2 },
  ],
};

export default function VoiceStep({ p, setP, reload, go }) {
  const [catalog, setCatalog] = useState(null);
  const [provider, setProvider] = useState(p.tts_provider || "edge");
  const [voice, setVoice] = useState(p.voice);
  const [rate, setRate] = useState(p.rate);
  const [pitch, setPitch] = useState(p.pitch);
  const [sample, setSample] = useState("");
  const [playing, setPlaying] = useState("");
  const [err, setErr] = useState("");
  const audio = useRef(null);
  const [job, start] = useJob(() => reload());
  const running = job?.status === "running";

  useEffect(() => {
    api.get(`/api/voices?lang=${p.language}`).then(setCatalog);
    api.get(`/api/projects/${p.id}/jobs`).then((jobs) => {
      const j = jobs.find((x) => x.kind === "narration");
      if (j) start(Promise.resolve(j));
    });
    return () => audio.current?.pause();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const firstLine = (p.scenes[0]?.narration || "").split(/(?<=[.!?।])\s/)[0];
  const list = catalog?.[provider] || [];
  const settingsChanged = provider !== p.tts_provider || voice !== p.voice || rate !== p.rate || pitch !== p.pitch;

  const choose = async (patch) => {
    const next = { tts_provider: provider, voice, rate, pitch, ...patch };
    setProvider(next.tts_provider); setVoice(next.voice); setRate(next.rate); setPitch(next.pitch);
    setP(await api.patch(`/api/projects/${p.id}`, next));
  };

  const play = async (v) => {
    setErr("");
    audio.current?.pause();
    setPlaying(v);
    try {
      const { url } = await api.post("/api/voices/preview", { provider, voice: v, rate, pitch, lang: p.language, text: sample || undefined });
      audio.current = new Audio(url);
      audio.current.onended = () => setPlaying("");
      await audio.current.play();
    } catch (e) {
      setErr(e.message);
      setPlaying("");
    }
  };

  const record = async (force = false, sceneIds) => {
    if (settingsChanged) await choose({});
    start(api.post(`/api/projects/${p.id}/narration`, { force, scene_ids: sceneIds }));
  };

  const recorded = p.scenes.filter((s) => s.audio_current).length;
  const total = p.scenes.reduce((t, s) => t + (s.audio_current ? s.audio_duration : 0), 0);

  return (
    <div className="space-y-6">
      <Section title="Choose the narrator" hint="Play a few samples and pick the voice that suits this story. Speed and pitch apply to the whole video.">
        <div className="mb-5 grid gap-2 sm:grid-cols-3">
          {PROVIDERS.map((pr) => {
            return (
              <button key={pr.key}
                onClick={() => { const v = catalog?.[pr.key]?.[0]?.id || ""; choose({ tts_provider: pr.key, voice: v }); }}
                aria-pressed={provider === pr.key}
                className={`rounded-xl border px-3 py-2.5 text-left ${provider === pr.key ? "border-marigold bg-marigold/10" : "border-line hover:border-mist"}`}>
                <div className="text-sm font-semibold">{pr.name}</div>
                <div className="text-xs text-mist">{pr.sub}</div>
              </button>
            );
          })}
        </div>

        {!catalog ? <p className="text-sm text-mist">Loading voices…</p> : (
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {list.map((v) => (
              <li key={v.id} className={`flex items-center justify-between gap-3 rounded-xl border p-3 ${voice === v.id ? "border-marigold bg-marigold/10" : "border-line"}`}>
                <button className="min-w-0 flex-1 text-left" onClick={() => choose({ voice: v.id })} aria-pressed={voice === v.id}>
                  <div className="font-semibold">{v.name} <span className="text-xs font-normal text-mist">{v.gender}</span></div>
                  <div className="truncate text-xs text-mist">{v.note || v.id}</div>
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => play(v.id)} disabled={!!playing && playing !== v.id}>
                  {playing === v.id ? "Playing…" : "Play sample"}
                </button>
              </li>
            ))}
          </ul>
        )}
        {err && <p role="alert" className="mt-3 text-sm text-sindoor">{err}</p>}

        <div className="mt-5 grid gap-5 md:grid-cols-2">
          <div>
            <Label>Speed: {rate > 0 ? "+" : ""}{rate}%</Label>
            <input type="range" min={-30} max={30} step={5} value={rate} onChange={(e) => setRate(Number(e.target.value))} onMouseUp={() => choose({})} onTouchEnd={() => choose({})} onKeyUp={() => choose({})} aria-label="Speed" />
          </div>
          <div>
            <Label>Pitch: {pitch > 0 ? "+" : ""}{pitch} Hz</Label>
            <input type="range" min={-10} max={10} step={1} value={pitch} onChange={(e) => setPitch(Number(e.target.value))} onMouseUp={() => choose({})} onTouchEnd={() => choose({})} onKeyUp={() => choose({})} aria-label="Pitch" />
          </div>
        </div>
        {VOICE_PRESETS[voice] && (
          <div className="mt-4">
            <Label>Quick presets</Label>
            <div className="flex flex-wrap gap-2">
              {VOICE_PRESETS[voice].map((pr) => (
                <button key={pr.label} className="btn btn-ghost btn-sm"
                  onClick={() => choose({ rate: pr.rate, pitch: pr.pitch })}>
                  {pr.label}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="mt-4">
          <Label htmlFor="sample">Sample line</Label>
          <div className="flex gap-2">
            <input id="sample" className="field" value={sample} onChange={(e) => setSample(e.target.value)} placeholder="Leave empty for a standard greeting" lang={p.language} />
            {firstLine && <button className="btn btn-ghost btn-sm" onClick={() => setSample(firstLine)}>Use scene 1</button>}
          </div>
        </div>
      </Section>

      <Section
        title="Narration"
        hint={`${recorded} of ${p.scenes.length} scenes recorded${recorded ? `, ${total.toFixed(1)} s of speech` : ""}. Changing the voice, speed, pitch or a scene’s text marks it for re-recording.`}
        actions={
          <button className="btn btn-primary btn-sm" disabled={running || !voice} onClick={() => record(false)}>
            {recorded === p.scenes.length && !settingsChanged ? "Everything is recorded" : recorded ? "Record the remaining scenes" : "Record all scenes"}
          </button>
        }
      >
        <Progress job={job} />
        <ol className="mt-4 space-y-3">
          {p.scenes.map((s, i) => <SceneAudio key={s.id} s={s} i={i} p={p} setP={setP} busy={running} rerecord={() => record(true, [s.id])} edgeVoices={catalog?.edge || []} />)}
        </ol>
      </Section>

      <button className="btn btn-primary" disabled={recorded < p.scenes.length || running} onClick={() => go(5)}>Continue to video</button>
    </div>
  );
}

function SceneAudio({ s, i, p, setP, busy, rerecord, edgeVoices }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(s.narration);
  const saveText = async () => {
    const scenes = p.scenes.map((x) => ({ id: x.id, narration: x.id === s.id ? text : x.narration, caption: x.caption, image_prompt: x.image_prompt }));
    setP(await api.put(`/api/projects/${p.id}/scenes`, scenes));
    setEditing(false);
  };
  return (
    <li className="grid gap-3 rounded-xl border border-line bg-night/40 p-3 md:grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-w-0">
        <div className="text-sm font-semibold text-marigold">
          Scene {i + 1}
          <span className="ml-2 text-xs font-normal text-mist">
            {s.audio_current ? `${s.audio_duration.toFixed(1)} s${s.audio_self ? " · your voice" : ""}` : s.audio_url ? "needs re-recording" : "not recorded"}
          </span>
        </div>
        {editing ? (
          <div className="mt-2 space-y-2">
            <textarea className="field min-h-20 text-sm" lang={p.language} value={text} onChange={(e) => setText(e.target.value)} />
            <div className="flex gap-2">
              <button className="btn btn-primary btn-sm" onClick={saveText}>Save text</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>Cancel</button>
            </div>
          </div>
        ) : (
          <p className="mt-1 text-sm leading-relaxed" lang={p.language}>{s.narration}</p>
        )}
      </div>
      <div className="space-y-2">
        {s.audio_url && <audio controls src={s.audio_url} preload="none" className={s.audio_current ? "" : "opacity-50"} />}
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={rerecord}>Re-record (AI voice)</button>
          {!editing && <button className="btn btn-ghost btn-sm" onClick={() => { setText(s.narration); setEditing(true); }}>Edit text</button>}
        </div>
        <MicRecorder sceneId={s.id} onDone={setP} />
        {s.audio_self && <VoiceChangePanel sceneId={s.id} edgeVoices={edgeVoices} onDone={setP} />}
      </div>
    </li>
  );
}

function VoiceChangePanel({ sceneId, edgeVoices, onDone }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("adjust"); // voice | upload | adjust
  const [targetVoice, setTargetVoice] = useState(edgeVoices?.[0]?.id || "");
  const [rate, setRate] = useState(0);
  const [pitch, setPitch] = useState(0);
  const [err, setErr] = useState("");
  const fileRef = useRef();
  const [job, start] = useJob((j) => {
    if (j.status === "done" && j.result) onDone(j.result);
    else if (j.status === "error") setErr(j.error);
  });
  const busy = job?.status === "running";

  const apply = () => {
    setErr("");
    if (mode === "voice" && !targetVoice) { setErr("Pick a target voice"); return; }
    const fields = { rate, pitch };
    if (mode === "voice") Object.assign(fields, { provider: "edge", voice: targetVoice });
    start(api.postForm(`/api/scenes/${sceneId}/change-voice`, fields));
  };
  const applyWithFile = (f) => {
    setErr("");
    start(api.postForm(`/api/scenes/${sceneId}/change-voice`, { ref_file: f, rate, pitch }));
  };

  if (!open) {
    return <button className="btn btn-ghost btn-sm" onClick={() => setOpen(true)}>Change voice / pace / pitch…</button>;
  }
  return (
    <div className="space-y-3 rounded-lg border border-dashed border-line p-2">
      <div className="flex flex-wrap gap-2 text-xs">
        <button className={`btn btn-ghost btn-sm ${mode === "adjust" ? "border-marigold" : ""}`} onClick={() => setMode("adjust")}>Pace / pitch only</button>
        <button className={`btn btn-ghost btn-sm ${mode === "voice" ? "border-marigold" : ""}`} onClick={() => setMode("voice")}>Existing voice</button>
        <button className={`btn btn-ghost btn-sm ${mode === "upload" ? "border-marigold" : ""}`} onClick={() => setMode("upload")}>Upload reference clip</button>
      </div>
      {mode !== "adjust" && <p className="text-xs text-mist">Keeps your performance, re-voices the timbre to match a target — pace/pitch below still apply on top.</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label>Pace: {rate > 0 ? "+" : ""}{rate}%</Label>
          <input type="range" min={-30} max={30} step={5} value={rate} onChange={(e) => setRate(Number(e.target.value))} aria-label="Pace" />
        </div>
        <div>
          <Label>Pitch: {pitch > 0 ? "+" : ""}{pitch}</Label>
          <input type="range" min={-10} max={10} step={1} value={pitch} onChange={(e) => setPitch(Number(e.target.value))} aria-label="Pitch" />
        </div>
      </div>
      {mode === "voice" && (
        <div className="flex flex-wrap items-center gap-2">
          <select className="field !w-auto text-sm" value={targetVoice} onChange={(e) => setTargetVoice(e.target.value)}>
            {edgeVoices.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.gender})</option>)}
          </select>
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={apply}>{busy ? "Working…" : "Apply"}</button>
        </div>
      )}
      {mode === "upload" && (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => fileRef.current.click()}>{busy ? "Working…" : "Choose audio file"}</button>
          <input ref={fileRef} type="file" accept="audio/*" hidden onChange={(e) => e.target.files[0] && applyWithFile(e.target.files[0])} />
        </div>
      )}
      {mode === "adjust" && (
        <button className="btn btn-primary btn-sm" disabled={busy || (!rate && !pitch)} onClick={apply}>{busy ? "Working…" : "Apply"}</button>
      )}
      {busy && <Progress job={job} label="Working…" />}
      <button className="text-xs text-mist hover:text-parchment" onClick={() => setOpen(false)}>Close</button>
      {err && <p role="alert" className="text-xs text-sindoor">{err}</p>}
    </div>
  );
}

function MicRecorder({ sceneId, onDone }) {
  const [state, setState] = useState("idle"); // idle | recording
  const [err, setErr] = useState("");
  const [seconds, setSeconds] = useState(0);
  const recRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);
  const [job, start] = useJob((j) => {
    if (j.status === "done" && j.result) onDone(j.result);
    else if (j.status === "error") setErr(j.error);
  });

  const stopTimer = () => { clearInterval(timerRef.current); timerRef.current = null; };

  const beginRecording = async () => {
    setErr("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        stopTimer();
        setState("idle");
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        const file = new File([blob], "recording.webm", { type: blob.type });
        start(api.upload(`/api/scenes/${sceneId}/record-audio`, file));
      };
      recRef.current = rec;
      rec.start();
      setState("recording");
      setSeconds(0);
      timerRef.current = setInterval(() => setSeconds((x) => x + 1), 1000);
    } catch (e) {
      setErr(e.name === "NotAllowedError" ? "Microphone permission denied" : e.message);
    }
  };
  const stopRecording = () => recRef.current?.stop();

  const busy = job?.status === "running";

  return (
    <div className="space-y-2 rounded-lg border border-dashed border-line p-2">
      {state === "recording" ? (
        <button className="btn btn-primary btn-sm w-full justify-center" onClick={stopRecording}>
          ● Recording… {seconds}s — tap to stop
        </button>
      ) : busy ? (
        <Progress job={job} label="Saving your recording…" />
      ) : (
        <button className="btn btn-ghost btn-sm w-full justify-center" onClick={beginRecording}>🎙 Record with your mic</button>
      )}
      {err && <p role="alert" className="mt-1 text-xs text-sindoor">{err}</p>}
    </div>
  );
}
