import { useEffect, useState } from "react";
import { api } from "./api";
import { Label, Section, Segmented } from "./components/ui";

export const LENGTHS = [
  { value: 30, label: "30 s" },
  { value: 60, label: "60 s" },
  { value: 90, label: "90 s" },
];

export function StyleFields({ styleKey, setStyleKey, custom, setCustom, styles }) {
  const cur = styles.find((s) => s.key === styleKey);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {styles.map((s) => (
          <button
            type="button"
            key={s.key}
            onClick={() => setStyleKey(s.key)}
            aria-pressed={styleKey === s.key}
            className={`rounded-xl border px-3 py-2.5 text-left text-sm leading-snug transition ${styleKey === s.key ? "border-marigold bg-marigold/10 text-parchment" : "border-line text-mist hover:border-mist"}`}
          >
            {s.label}
          </button>
        ))}
      </div>
      {cur?.prompt && <p className="text-xs leading-relaxed text-mist">Adds to every image: “{cur.prompt}”</p>}
      <input
        className="field"
        value={custom}
        onChange={(e) => setCustom(e.target.value)}
        placeholder={styleKey === "custom" ? "Describe the look, e.g. watercolour, soft pastel, temple mural…" : "Optional extra style words, e.g. night scene, moonlight"}
      />
    </div>
  );
}

export default function Home() {
  const [projects, setProjects] = useState(null);
  const [styles, setStyles] = useState([]);
  const [contentType, setContentType] = useState("story");
  const [story, setStory] = useState("");
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("hi");
  const [styleKey, setStyleKey] = useState("cinematic");
  const [custom, setCustom] = useState("");
  const [seconds, setSeconds] = useState(60);
  const [question, setQuestion] = useState("");
  const [code, setCode] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = () => api.get("/api/projects").then(setProjects).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
    api.get("/api/catalog").then((c) => setStyles(c.styles));
  }, []);

  const create = async () => {
    setBusy(true);
    setErr("");
    try {
      if (contentType === "code_quiz") {
        const { id } = await api.post("/api/projects", { title, language, content_type: "code_quiz" });
        await api.postForm(`/api/projects/${id}/code-quiz`, { question, code, answer });
        window.location.hash = `#/p/${id}`;
      } else {
        const { id } = await api.post("/api/projects", { title, story, language, style_key: styleKey, style_custom: custom, target_seconds: seconds });
        await api.post(`/api/projects/${id}/script`);
        window.location.hash = `#/p/${id}`;
      }
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };

  const remove = async (p) => {
    if (!confirm(`Delete “${p.title}” and all its images, audio and videos?`)) return;
    await api.del(`/api/projects/${p.id}`);
    load();
  };

  const words = story.trim() ? story.trim().split(/\s+/).length : 0;

  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      <div>
        <h1 className="font-display text-4xl leading-tight text-parchment md:text-5xl">
          Tell a story. Get a Short.
        </h1>
        <p className="mt-3 max-w-prose text-mist">
          Paste a katha from the Puranas, Ramayana or Mahabharata. You’ll review the scenes, approve every image,
          choose the narrator’s voice, and then render a vertical video for YouTube Shorts and Instagram Reels.
        </p>

        <div className="mt-6 space-y-5 rounded-2xl border border-line bg-dusk/70 p-5 md:p-6">
          <div>
            <Label>What are you making?</Label>
            <Segmented name="Content type" value={contentType} onChange={setContentType}
              options={[{ value: "story", label: "Mythology story" }, { value: "code_quiz", label: "Code quiz" }]} />
          </div>
          {contentType === "story" ? (
          <div>
            <Label htmlFor="story">Your story</Label>
            <textarea
              id="story"
              className="field min-h-56"
              value={story}
              onChange={(e) => setStory(e.target.value)}
              placeholder="एक बार माता पार्वती ने अपने उबटन से एक बालक बनाया… (Hindi or English — the narration language is chosen below)"
            />
            <p className="mt-1 text-xs text-mist">{words} words. The script will be condensed to fit the length you pick</p>
          </div>
          ) : (
          <div className="space-y-4">
            <div>
              <Label htmlFor="question">Question (one option per line, if any)</Label>
              <textarea id="question" className="field min-h-32" value={question} onChange={(e) => setQuestion(e.target.value)}
                placeholder={"What does this log?\nA) 5\nB) 10\nC) 15\nD) 20"} />
            </div>
            <div>
              <Label htmlFor="code">Code snippet (optional)</Label>
              <textarea id="code" className="field min-h-32 font-mono text-sm" value={code} onChange={(e) => setCode(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="answer">Answer + explanation</Label>
              <textarea id="answer" className="field min-h-32" value={answer} onChange={(e) => setAnswer(e.target.value)} />
            </div>
          </div>
          )}
          {contentType === "story" ? (
          <>
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
            <Label>Art style for this story</Label>
            <StyleFields {...{ styleKey, setStyleKey, custom, setCustom, styles }} />
          </div>
          </>
          ) : (
          <div>
            <Label>Narration language</Label>
            <Segmented name="Narration language" value={language} onChange={setLanguage}
              options={[{ value: "hi", label: "हिन्दी" }, { value: "en", label: "English (Indian)" }]} />
          </div>
          )}
          <div>
            <Label htmlFor="title">Title</Label>
            <input id="title" className="field" value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder={contentType === "story" ? "Leave empty and one will be suggested" : "e.g. JS Closures Quiz #1"} />
          </div>
          {err && <p role="alert" className="text-sm text-sindoor">{err}</p>}
          <button className="btn btn-primary"
            disabled={busy || (contentType === "story" ? words < 15 : !question.trim() || !answer.trim())}
            onClick={create}>
            {busy ? "Starting…" : contentType === "story" ? "Write the scene script" : "Create the quiz"}
          </button>
          {contentType === "story" && words > 0 && words < 15 && <span className="ml-3 text-sm text-mist">Add a little more story first.</span>}
        </div>
      </div>

      <div>
        <Section title="Your stories" hint={projects?.length ? "Pick up where you left off." : undefined}>
          {projects === null ? (
            <p className="text-sm text-mist">Loading…</p>
          ) : projects.length === 0 ? (
            <p className="text-sm text-mist">Nothing here yet. Your first story will appear here once you start it.</p>
          ) : (
            <ul className="space-y-2">
              {projects.map((p) => (
                <li key={p.id} className="group flex items-center gap-3 rounded-xl border border-transparent p-2 hover:border-line hover:bg-panel/50">
                  <a href={`#/p/${p.id}`} className="flex min-w-0 flex-1 items-center gap-3">
                    <div className="aspect-[9/16] w-11 shrink-0 overflow-hidden rounded-md bg-panel">
                      {p.thumb && <img src={p.thumb} alt="" className="h-full w-full object-cover" />}
                    </div>
                    <div className="min-w-0">
                      <div className="truncate font-medium">{p.title}</div>
                      <div className="text-xs text-mist">
                        {p.language === "hi" ? "Hindi" : "English"}, {p.scenes} scenes, {p.has_video ? "video rendered" : `step ${p.step} of 5`}
                      </div>
                    </div>
                  </a>
                  <button className="btn btn-sm btn-danger opacity-0 group-hover:opacity-100 focus:opacity-100" onClick={() => remove(p)}>Delete</button>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </div>
  );
}
