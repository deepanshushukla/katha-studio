import { useEffect, useState } from "react";
import { api } from "../api";
import { Label, Section } from "../components/ui";

const WPS = { hi: 2.3, en: 2.5 };

export default function ScriptStep({ p, setP, go }) {
  const [scenes, setScenes] = useState(p.scenes.map(pick));
  const [chars, setChars] = useState(p.characters || []);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    setScenes(p.scenes.map(pick));
    setChars(p.characters || []);
  }, [p.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const words = scenes.reduce((n, s) => n + (s.narration.trim() ? s.narration.trim().split(/\s+/).length : 0), 0);
  const estSeconds = Math.round(words / WPS[p.language] + scenes.length * 0.7);

  const upd = (i, k, v) => setScenes((a) => a.map((s, j) => (j === i ? { ...s, [k]: v } : s)));
  const move = (i, d) => setScenes((a) => {
    const b = [...a];
    const [x] = b.splice(i, 1);
    b.splice(i + d, 0, x);
    return b;
  });
  const updChar = (i, k, v) => setChars((a) => a.map((c, j) => (j === i ? { ...c, [k]: v } : c)));

  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      await api.patch(`/api/projects/${p.id}`, { characters: chars.filter((c) => c.name.trim()) });
      const d = await api.put(`/api/projects/${p.id}/scenes`, scenes.filter((s) => s.narration.trim()));
      setP(d);
      setScenes(d.scenes.map(pick));
      setMsg("Saved");
      return d;
    } catch (e) {
      setMsg(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <Section
        title="Characters"
        hint="Each description is added to every image prompt that mentions the character, so they look the same across scenes. Be specific about clothes, colours and ornaments."
        actions={<button className="btn btn-ghost btn-sm" onClick={() => setChars([...chars, { name: "", description: "" }])}>Add character</button>}
      >
        {chars.length === 0 && <p className="text-sm text-mist">No recurring characters. Add one if a figure appears in several scenes.</p>}
        <div className="space-y-3">
          {chars.map((c, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[180px_minmax(0,1fr)_auto]">
              <input className="field" value={c.name} onChange={(e) => updChar(i, "name", e.target.value)} placeholder="Name, e.g. Ganesha" aria-label="Character name" />
              <textarea className="field min-h-[44px]" rows={2} value={c.description} onChange={(e) => updChar(i, "description", e.target.value)}
                placeholder="Young boy, elephant head with one broken tusk, golden crown, red dhoti, holding modak" aria-label="Character look" />
              <button className="btn btn-sm btn-danger self-start" onClick={() => setChars(chars.filter((_, j) => j !== i))}>Remove</button>
            </div>
          ))}
        </div>
      </Section>

      <Section
        title={`Scenes (${scenes.length})`}
        hint={`About ${estSeconds} seconds of narration. Narration is spoken; the image prompt stays in English because image models understand it best.`}
      >
        <ol className="space-y-4">
          {scenes.map((s, i) => (
            <li key={s.id ?? `new-${i}`} className="rounded-xl border border-line bg-night/40 p-4">
              <div className="mb-3 flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-marigold">Scene {i + 1}</span>
                <div className="flex gap-1">
                  <button className="btn btn-ghost btn-sm" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">Up</button>
                  <button className="btn btn-ghost btn-sm" disabled={i === scenes.length - 1} onClick={() => move(i, 1)} aria-label="Move down">Down</button>
                  <button className="btn btn-sm btn-danger" onClick={() => setScenes(scenes.filter((_, j) => j !== i))}>Delete</button>
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <Label>Narration</Label>
                  <textarea className="field min-h-28" lang={p.language} value={s.narration} onChange={(e) => upd(i, "narration", e.target.value)} />
                </div>
                <div>
                  <Label>Image prompt</Label>
                  <textarea className="field min-h-28" value={s.image_prompt} onChange={(e) => upd(i, "image_prompt", e.target.value)} />
                </div>
              </div>
            </li>
          ))}
        </ol>
        <button className="btn btn-ghost btn-sm mt-4" onClick={() => setScenes([...scenes, { narration: "", image_prompt: "", caption: "" }])}>Add scene</button>
      </Section>

      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" disabled={saving || !scenes.length} onClick={async () => { const d = await save(); if (d) go(3); }}>
          Save and create images
        </button>
        <button className="btn btn-ghost" disabled={saving} onClick={save}>Save</button>
        {msg && <span className="text-sm text-mist" role="status">{msg}</span>}
      </div>
    </div>
  );
}

function pick(s) {
  return { id: s.id, narration: s.narration, image_prompt: s.image_prompt, caption: s.caption };
}
