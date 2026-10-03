import { useEffect, useRef, useState } from "react";
import { api, useJob } from "../api";
import { Progress, Section } from "../components/ui";

export default function ImagesStep({ p, setP, reload, go }) {
  const [variants, setVariants] = useState(2);
  const [view, setView] = useState(null);
  const [job, start] = useJob(() => reload());
  const running = job?.status === "running";
  const autoStarted = useRef(false);

  // refresh while images arrive so they appear one by one
  useEffect(() => {
    if (!running) return;
    const t = setInterval(reload, 2500);
    return () => clearInterval(t);
  }, [running, reload]);

  useEffect(() => {
    api.get(`/api/projects/${p.id}/jobs`).then((jobs) => {
      const j = jobs.find((x) => x.kind === "images");
      if (j) start(Promise.resolve(j));
      else if (!autoStarted.current && p.scenes.every((s) => s.images.length === 0)) {
        autoStarted.current = true;
        start(api.post(`/api/projects/${p.id}/images`, { variants: 2, only_missing: true }));
      }
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const missing = p.scenes.filter((s) => s.images.length === 0).length;
  const approved = p.scenes.filter((s) => s.approved_image_id).length;
  const approveFirst = async () => {
    for (const s of p.scenes) if (!s.approved_image_id && s.images[0]) await api.post(`/api/scenes/${s.id}/approve`, { image_id: s.images[0].id });
    reload();
  };

  return (
    <div className="space-y-6">
      <Section
        title="Images"
        hint="Tap the picture you like for each scene. Not right? Ask for more options, tweak the prompt, upload your own image as-is, or restyle a photo into this scene's art style."
        actions={
          <>
            <select className="field !w-auto !py-1.5 text-sm" value={variants} onChange={(e) => setVariants(Number(e.target.value))} aria-label="Options per scene">
              {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n} per scene</option>)}
            </select>
            <button className="btn btn-primary btn-sm" disabled={running || missing === 0}
              onClick={() => start(api.post(`/api/projects/${p.id}/images`, { variants, only_missing: true }))}>
              {missing === 0 ? "All scenes have images" : `Create images for ${missing} scene${missing > 1 ? "s" : ""}`}
            </button>
          </>
        }
      >
        <Progress job={job} />
        <p className="mt-2 text-sm text-mist">{approved} of {p.scenes.length} scenes approved</p>
      </Section>

      <ol className="space-y-4">
        {p.scenes.map((s, i) => (
          <SceneImages key={s.id} s={s} i={i} p={p} setP={setP} reload={reload} variants={variants} busy={running} onView={setView} />
        ))}
      </ol>

      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" disabled={approved < p.scenes.length} onClick={() => go(4)}>Continue to voice</button>
        {approved < p.scenes.length && p.scenes.some((s) => !s.approved_image_id && s.images.length) && (
          <button className="btn btn-ghost" onClick={approveFirst}>Approve the first option where none is picked</button>
        )}
      </div>

      {view && (
        <div role="dialog" aria-modal="true" aria-label="Image preview" className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-4" onClick={() => setView(null)}>
          <img src={view} alt="" className="max-h-full max-w-full rounded-xl" />
        </div>
      )}
    </div>
  );
}

function SceneImages({ s, i, p, setP, reload, variants, busy, onView }) {
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState(s.image_prompt);
  const [err, setErr] = useState("");
  const [job, start] = useJob(() => reload());
  const fileRef = useRef();
  const restyleRef = useRef();
  const running = job?.status === "running";

  const approve = async (id) => setP(await api.post(`/api/scenes/${s.id}/approve`, { image_id: id }));
  const more = async () => {
    if (editing && prompt !== s.image_prompt) {
      const scenes = p.scenes.map((x) => ({ id: x.id, narration: x.narration, caption: x.caption, image_prompt: x.id === s.id ? prompt : x.image_prompt }));
      await api.put(`/api/projects/${p.id}/scenes`, scenes);
    }
    setEditing(false);
    start(api.post(`/api/scenes/${s.id}/images`, { variants, new_seed: true }));
  };
  const upload = async (f) => {
    setErr("");
    try { setP(await api.upload(`/api/scenes/${s.id}/upload`, f)); } catch (e) { setErr(e.message); }
  };
  const restyle = (f) => {
    setErr("");
    start(api.upload(`/api/scenes/${s.id}/upload-reference`, f, { strength: 0.55 }));
  };
  const remove = async (id) => setP(await api.del(`/api/images/${id}`));

  return (
    <li className={`rounded-2xl border p-4 md:p-5 ${s.approved_image_id ? "border-line bg-dusk/60" : "border-marigold/40 bg-dusk/80"}`}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-prose">
          <div className="text-sm font-semibold text-marigold">Scene {i + 1}{s.approved_image_id ? "" : " — choose an image"}</div>
          <p className="mt-1 text-sm leading-relaxed" lang={p.language}>{s.narration}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-ghost btn-sm" disabled={running || busy} onClick={more}>{editing ? "Save prompt & create" : "More options"}</button>
          <button className="btn btn-ghost btn-sm" onClick={() => { setEditing(!editing); setPrompt(s.image_prompt); }}>{editing ? "Cancel" : "Edit prompt"}</button>
          <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current.click()}>Upload</button>
          <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => e.target.files[0] && upload(e.target.files[0])} />
          <button className="btn btn-ghost btn-sm" disabled={running || busy} onClick={() => restyleRef.current.click()}>Restyle a photo</button>
          <input ref={restyleRef} type="file" accept="image/*" hidden onChange={(e) => e.target.files[0] && restyle(e.target.files[0])} />
        </div>
      </div>
      {editing && <textarea className="field mb-3 min-h-20 text-sm" value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Image prompt" />}
      {running && <div className="mb-3"><Progress job={job} /></div>}
      {job?.status === "error" && <div className="mb-3"><Progress job={job} /></div>}
      {err && <p role="alert" className="mb-2 text-sm text-sindoor">{err}</p>}
      <div className="flex gap-3 overflow-x-auto pb-1">
        {s.images.length === 0 && !running && !busy && <p className="text-sm text-mist">No images yet.</p>}
        {s.images.length === 0 && (busy || running) && <div className="aspect-[9/16] w-36 shrink-0 animate-pulse rounded-xl bg-panel" />}
        {s.images.map((im) => {
          const on = s.approved_image_id === im.id;
          return (
            <figure key={im.id} className="relative w-36 shrink-0 md:w-40">
              <button
                onClick={() => approve(im.id)}
                aria-pressed={on}
                aria-label={on ? "Approved image" : "Approve this image"}
                className={`block aspect-[9/16] w-full overflow-hidden rounded-xl ring-offset-2 ring-offset-dusk transition ${on ? "ring-3 ring-marigold" : "opacity-85 hover:opacity-100"}`}
              >
                <img src={im.url} alt="" loading="lazy" className="h-full w-full object-cover" />
              </button>
              {on && <span className="absolute left-2 top-2 rounded-full bg-marigold px-2 py-0.5 text-xs font-semibold text-[#241a05]">Approved</span>}
              <figcaption className="mt-1 flex justify-between text-xs text-mist">
                <button className="hover:text-parchment" onClick={() => onView(im.url)}>View</button>
                <span>{im.uploaded ? "yours" : im.provider}</span>
                <button className="hover:text-sindoor" onClick={() => remove(im.id)} aria-label="Delete image">Delete</button>
              </figcaption>
            </figure>
          );
        })}
      </div>
    </li>
  );
}
