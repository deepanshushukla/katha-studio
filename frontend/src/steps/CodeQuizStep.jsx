import { useEffect, useRef, useState } from "react";
import { api, useJob } from "../api";
import { Label, Progress, Section, Toggle } from "../components/ui";
import { CODE_THEMES } from "../Settings";

export default function CodeQuizStep({ p, reload, go }) {
  const question = p.scenes.find((s) => s.beat_type === "question");
  const answer = p.scenes.find((s) => s.beat_type === "answer");
  const [q, setQ] = useState(question?.narration || "");
  const [code, setCode] = useState(question?.code_text || "");
  const [a, setA] = useState(answer?.narration || "");
  const [showTitleCard, setShowTitleCard] = useState(question?.show_title_card ?? true);
  const [music, setMusic] = useState(p.render?.music || "");
  const [tracks, setTracks] = useState([]);
  const [questionImage, setQuestionImage] = useState(null);
  const [answerImage, setAnswerImage] = useState(null);
  const [backgroundImage, setBackgroundImage] = useState(null);
  const [narrationEnabled, setNarrationEnabled] = useState(p.narration_enabled ?? true);
  const [silentBeatSeconds, setSilentBeatSeconds] = useState(p.silent_beat_seconds || 5);
  const [codeTheme, setCodeTheme] = useState(p.code_theme || "");
  const [codeFontSize, setCodeFontSize] = useState(p.code_font_size || "");
  const qImgRef = useRef(); const aImgRef = useRef(); const bgImgRef = useRef();
  const [job, start] = useJob(async (j) => { if (j.status === "done") { await reload(); go(4); } });
  const running = job?.status === "running";

  useEffect(() => { api.get("/api/music").then((m) => setTracks(m.tracks)); }, []);

  const save = () => start(api.postForm(`/api/projects/${p.id}/code-quiz`, {
    question: q, code, answer: a, show_title_card: showTitleCard, music,
    narration_enabled: narrationEnabled, silent_beat_seconds: silentBeatSeconds,
    ...(questionImage ? { question_image: questionImage } : {}),
    ...(answerImage ? { answer_image: answerImage } : {}),
    ...(backgroundImage ? { background_image: backgroundImage } : {}),
    ...(codeTheme ? { code_theme: codeTheme } : {}),
    ...(codeFontSize ? { code_font_size: codeFontSize } : {}),
  }));

  return (
    <Section title="Code quiz" hint="This is used exactly as written — no AI rewrites it. Line breaks are kept, so put each multiple-choice option on its own line.">
      <div className="space-y-5">
        <div>
          <Label htmlFor="cq-question">Question</Label>
          <textarea id="cq-question" className="field min-h-32" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="mt-2 flex items-center gap-2">
            {question?.content_image_url && <img src={question.content_image_url} alt="" className="h-12 w-12 rounded border border-line object-cover" />}
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => qImgRef.current.click()}>
              {questionImage ? questionImage.name : "Image instead of text (e.g. math)"}
            </button>
            <input ref={qImgRef} type="file" accept="image/*" hidden onChange={(e) => setQuestionImage(e.target.files[0] || null)} />
          </div>
        </div>
        <div>
          <Label htmlFor="cq-code">Code snippet (optional)</Label>
          <textarea id="cq-code" className="field min-h-32 font-mono text-sm" value={code} onChange={(e) => setCode(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="cq-answer">Answer + explanation</Label>
          <textarea id="cq-answer" className="field min-h-32" value={a} onChange={(e) => setA(e.target.value)} />
          <div className="mt-2 flex items-center gap-2">
            {answer?.content_image_url && <img src={answer.content_image_url} alt="" className="h-12 w-12 rounded border border-line object-cover" />}
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => aImgRef.current.click()}>
              {answerImage ? answerImage.name : "Image instead of text (e.g. math)"}
            </button>
            <input ref={aImgRef} type="file" accept="image/*" hidden onChange={(e) => setAnswerImage(e.target.files[0] || null)} />
          </div>
        </div>
        <Toggle label="Show question/answer text on screen" checked={showTitleCard} onChange={setShowTitleCard} />
        <Toggle label="Narration (spoken voice)" checked={narrationEnabled} onChange={setNarrationEnabled} />
        {!narrationEnabled && (
          <div>
            <Label htmlFor="cq-silent-seconds">Seconds per question/answer beat (no voice)</Label>
            <input id="cq-silent-seconds" type="number" min={1} max={30} className="field w-32"
              value={silentBeatSeconds} onChange={(e) => setSilentBeatSeconds(Number(e.target.value))} />
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="cq-music">Music for this video</Label>
            <select id="cq-music" className="field" value={music} onChange={(e) => setMusic(e.target.value)}>
              <option value="">Use the default from Settings</option>
              {tracks.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
            </select>
          </div>
          <div>
            <Label>Background image for this video</Label>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => bgImgRef.current.click()}>
              {backgroundImage ? backgroundImage.name : "Use a different image just for this video"}
            </button>
            <input ref={bgImgRef} type="file" accept="image/*" hidden onChange={(e) => setBackgroundImage(e.target.files[0] || null)} />
          </div>
          <div>
            <Label htmlFor="cq-theme">Code block theme for this video</Label>
            <select id="cq-theme" className="field" value={codeTheme} onChange={(e) => setCodeTheme(e.target.value)}>
              <option value="">Use the default from Settings</option>
              {CODE_THEMES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <Label htmlFor="cq-fontsize">Code font size for this video</Label>
            <input id="cq-fontsize" type="number" min={18} max={64} className="field" value={codeFontSize}
              placeholder="Use the default from Settings" onChange={(e) => setCodeFontSize(e.target.value)} />
          </div>
        </div>
        <button className="btn btn-primary" disabled={running || !q.trim() || !a.trim()} onClick={save}>
          {running ? "Saving…" : question ? "Save changes" : "Create the quiz"}
        </button>
        <Progress job={job} />
      </div>
    </Section>
  );
}
