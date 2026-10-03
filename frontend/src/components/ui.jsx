export function Diya({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <path className="flame" d="M32 6c7 10 7 19 0 25-7-6-7-15 0-25z" fill="#F5B82E" />
      <path d="M32 16c3 5 3 9 0 12-3-3-3-7 0-12z" fill="#fff3c4" className="flame" />
      <path d="M6 36h52c-2 13-13 22-26 22S8 49 6 36z" fill="#C8702A" />
      <path d="M6 36h52" stroke="#8a4715" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Progress({ job, label }) {
  if (!job) return null;
  if (job.status === "error")
    return (
      <div role="alert" className="rounded-xl border border-sindoor/50 bg-sindoor/10 px-4 py-3 text-sm">
        <strong className="text-sindoor">That didn’t work.</strong> {job.error}
      </div>
    );
  const pct = Math.round((job.progress || 0) * 100);
  const running = job.status === "running";
  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex justify-between text-sm text-mist">
        <span>{job.message || label}</span>
        {running && <span>{pct}%</span>}
      </div>
      {running && (
        <div className="h-1.5 overflow-hidden rounded-full bg-line">
          <div className="h-full bg-marigold transition-all" style={{ width: `${Math.max(4, pct)}%` }} />
        </div>
      )}
      {job.warnings?.length > 0 && (
        <details className="text-xs text-mist">
          <summary className="cursor-pointer">{job.warnings.length} note(s) from the providers</summary>
          <ul className="mt-1 list-disc space-y-1 pl-5">{job.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

export function Section({ title, hint, children, actions }) {
  return (
    <section className="rounded-2xl border border-line bg-dusk/70 p-5 md:p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          {hint && <p className="mt-0.5 max-w-prose text-sm text-mist">{hint}</p>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function Label({ children, htmlFor }) {
  return <label htmlFor={htmlFor} className="mb-1.5 block text-sm font-medium text-mist">{children}</label>;
}

export function Segmented({ value, onChange, options, name }) {
  return (
    <div role="radiogroup" aria-label={name} className="inline-flex max-w-full overflow-x-auto rounded-full border border-line p-1">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={`whitespace-nowrap rounded-full px-3.5 py-1.5 text-sm font-medium ${value === o.value ? "bg-marigold text-[#241a05]" : "text-mist hover:text-parchment"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, label }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 py-1 text-sm">
      <span>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 rounded-full transition ${checked ? "bg-marigold" : "bg-line"}`}
      >
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-parchment transition ${checked ? "left-5.5" : "left-0.5"}`} />
      </button>
    </label>
  );
}
