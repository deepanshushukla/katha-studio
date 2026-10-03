import { useEffect, useRef, useState } from "react";

async function req(method, url, body, isForm) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) {
    const msg = data?.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `Request failed (${r.status})`;
    throw new Error(msg);
  }
  return data;
}

export const api = {
  get: (u) => req("GET", u),
  post: (u, b = {}) => req("POST", u, b),
  put: (u, b) => req("PUT", u, b),
  patch: (u, b) => req("PATCH", u, b),
  del: (u) => req("DELETE", u),
  upload: (u, file, extra = {}) => api.postForm(u, { file, ...extra }),
  /** multipart/form-data POST, no file required (e.g. a form with only text/select fields). */
  postForm: (u, fields = {}) => {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => v !== undefined && v !== null && fd.append(k, v));
    return req("POST", u, fd, true);
  },
};

/** Track a background job: returns [job, start(jobPromise)] and calls onDone when finished. */
export function useJob(onDone) {
  const [job, setJob] = useState(null);
  const timer = useRef(null);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => () => clearTimeout(timer.current), []);

  const poll = (j) => {
    setJob(j);
    if (j.status === "running") {
      timer.current = setTimeout(async () => {
        try { poll(await api.get(`/api/jobs/${j.id}`)); }
        catch (e) { setJob({ ...j, status: "error", error: e.message }); }
      }, 700);
    } else {
      doneRef.current?.(j);
    }
  };

  const start = async (promise) => {
    try { poll(await promise); }
    catch (e) { setJob({ status: "error", error: e.message }); doneRef.current?.({ status: "error", error: e.message }); }
  };
  return [job, start, setJob];
}

export function copy(text) {
  navigator.clipboard?.writeText(text);
}
