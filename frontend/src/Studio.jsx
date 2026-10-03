import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import StoryStep from "./steps/StoryStep";
import ScriptStep from "./steps/ScriptStep";
import ImagesStep from "./steps/ImagesStep";
import VoiceStep from "./steps/VoiceStep";
import RenderStep from "./steps/RenderStep";

const STEPS = [
  { n: 1, name: "Story", sub: "Text, language, style" },
  { n: 2, name: "Scenes", sub: "Review the script" },
  { n: 3, name: "Images", sub: "Approve each picture" },
  { n: 4, name: "Voice", sub: "Pick the narrator" },
  { n: 5, name: "Video", sub: "Render & download" },
];

export function readiness(p) {
  const sc = p?.scenes || [];
  return {
    1: true,
    2: sc.length > 0,
    3: sc.length > 0,
    4: sc.length > 0 && sc.every((s) => s.approved_image_id),
    5: sc.length > 0 && sc.every((s) => s.approved_image_id && s.audio_current),
  };
}

export default function Studio({ id }) {
  const [p, setP] = useState(null);
  const [step, setStep] = useState(null);
  const [err, setErr] = useState("");
  const [title, setTitle] = useState("");

  const reload = useCallback(async () => {
    try {
      const d = await api.get(`/api/projects/${id}`);
      setP(d);
      setTitle(d.title);
      return d;
    } catch (e) {
      setErr(e.message);
    }
  }, [id]);

  useEffect(() => {
    reload().then((d) => {
      if (!d) return;
      const r = readiness(d);
      let s = Math.min(d.step, 5);
      while (s > 1 && !r[s]) s--;
      setStep(s);
    });
  }, [reload]);

  if (err) return <p role="alert" className="text-sindoor">{err}</p>;
  if (!p || !step) return <p className="text-mist">Opening your story…</p>;

  const ready = readiness(p);
  const go = async (n) => {
    setStep(n);
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (n > p.step) setP(await api.patch(`/api/projects/${id}`, { step: n }));
  };
  const saveTitle = async () => {
    if (title.trim() && title !== p.title) setP(await api.patch(`/api/projects/${id}`, { title: title.trim() }));
  };
  const props = { p, setP, reload, go };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside className="min-w-0 lg:sticky lg:top-20 lg:self-start">
        <input
          aria-label="Story title"
          className="mb-4 w-full rounded-lg border border-transparent bg-transparent px-2 py-1 font-display text-xl leading-snug text-parchment hover:border-line focus:border-marigold focus:outline-none"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={saveTitle}
        />
        <ol className="flex gap-1 overflow-x-auto lg:flex-col lg:gap-0">
          {STEPS.map((s, i) => {
            const active = step === s.n;
            const done = s.n < step || (s.n <= p.step && ready[s.n + 1]);
            const enabled = ready[s.n];
            return (
              <li key={s.n} className="relative shrink-0 lg:pb-4">
                {i < STEPS.length - 1 && <span aria-hidden className="absolute left-[15px] top-8 hidden h-[calc(100%-24px)] w-px bg-line lg:block" />}
                <button
                  disabled={!enabled}
                  onClick={() => go(s.n)}
                  aria-current={active ? "step" : undefined}
                  className={`flex items-center gap-3 rounded-xl px-2 py-1.5 text-left disabled:opacity-40 ${active ? "bg-panel" : "hover:bg-panel/50"}`}
                >
                  <span className={`grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full border text-sm font-semibold ${active ? "border-marigold bg-marigold text-[#241a05]" : done ? "border-peacock text-peacock" : "border-line text-mist"}`}>
                    {done && !active ? "✓" : s.n}
                  </span>
                  <span>
                    <span className="block text-sm font-semibold">{s.name}</span>
                    <span className="hidden text-xs text-mist lg:block">{s.sub}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </aside>
      <div className="min-w-0">
        {step === 1 && <StoryStep {...props} />}
        {step === 2 && <ScriptStep {...props} />}
        {step === 3 && <ImagesStep {...props} />}
        {step === 4 && <VoiceStep {...props} />}
        {step === 5 && <RenderStep {...props} />}
      </div>
    </div>
  );
}
