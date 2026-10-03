import { useEffect, useState } from "react";
import { api, useJob } from "../api";
import { Label, Progress, Section, Segmented } from "../components/ui";
import { LENGTHS, StyleFields } from "../Home";

export default function StoryStep({ p, setP, reload, go }) {
  const [story, setStory] = useState(p.story);
  const [language, setLanguage] = useState(p.language);
  const [styleKey, setStyleKey] = useState(p.style_key);
  const [custom, setCustom] = useState(p.style_custom);
  const [seconds, setSeconds] = useState(p.target_seconds);
  const [styles, setStyles] = useState([]);
  const [job, start, setJob] = useJob(async (j) => {
    if (j.status === "done") {
      await reload();
      go(2);
    }
  });

  useEffect(() => {
    api.get("/api/catalog").then((c) => setStyles(c.styles));
    api.get(`/api/projects/${p.id}/jobs`).then((jobs) => {
      const j = jobs.find((x) => x.kind === "script");
      if (j) start(Promise.resolve(j));
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = story !== p.story || language !== p.language || styleKey !== p.style_key || custom !== p.style_custom || seconds !== p.target_seconds;
  const save = async () => {
    const d = await api.patch(`/api/projects/${p.id}`, { story, language, style_key: styleKey, style_custom: custom, target_seconds: seconds });
    setP(d);
    return d;
  };
  const write = async () => {
    if (p.scenes.length && !confirm("This replaces the current scenes, their images and narration. Continue?")) return;
    setJob(null);
    await save();
    start(api.post(`/api/projects/${p.id}/script`));
  };
  const running = job?.status === "running";

  return (
    <div className="space-y-6">
      <Section
        title="Story"
        hint="Change the story, language, length or art style here. A new art style only affects images you generate from now on."
      >
        <div className="space-y-5">
          <textarea className="field min-h-64" value={story} onChange={(e) => setStory(e.target.value)} aria-label="Story text" />
          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <Label>Narration language</Label>
              <Segmented name="Narration language" value={language} onChange={setLanguage}
                options={[{ value: "hi", label: "हिन्दी" }, { value: "en", label: "English (Indian)" }]} />
            </div>
            <div>
              <Label>Video length</Label>
              <Segmented name="Video length" value={seconds} onChange={setSeconds} options={LENGTHS} />
            </div>
          </div>
          <div>
            <Label>Art style</Label>
            <StyleFields {...{ styleKey, setStyleKey, custom, setCustom, styles }} />
          </div>
          {language !== p.language && p.scenes.length > 0 && (
            <p className="text-sm text-marigold">Changing the language needs a new scene script, since the narration is written in it.</p>
          )}
          <div className="flex flex-wrap gap-3">
            <button className="btn btn-primary" onClick={write} disabled={running || !story.trim()}>
              {p.scenes.length ? "Rewrite the scene script" : "Write the scene script"}
            </button>
            {dirty && <button className="btn btn-ghost" onClick={save} disabled={running}>Save changes</button>}
            {!dirty && p.scenes.length > 0 && <button className="btn btn-ghost" onClick={() => go(2)}>Continue to scenes</button>}
          </div>
          <Progress job={job} />
        </div>
      </Section>
    </div>
  );
}
