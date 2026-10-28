/* Общие помощники для страниц TTS Lab */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const store = {
  get(k, d) { try { const v = localStorage.getItem("ttslab." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ttslab." + k, JSON.stringify(v)); } catch {} },
};

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) {
    o.method = o.method || "POST";
    o.headers = { ...(o.headers || {}), "Content-Type": "application/json" };
    o.body = JSON.stringify(o.json);
    delete o.json;
  }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const j = ct.includes("json") ? await r.json() : { detail: await r.text() }; msg = j.detail?.error?.message || (typeof j.detail === "string" ? j.detail : "") || j.error || msg; if (j.detail && typeof j.detail === "string" && j.error) msg = j.error + "\n" + j.detail; } catch {}
    throw new Error(msg);
  }
  return ct.includes("json") ? r.json() : r;
}

let toastTimer;
function toast(text) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); document.body.append(t); }
  t.textContent = text; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 2600);
}

function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => toast("Скопировано"), () => toast(text));
}

const STATUS_RU = { ready: "готова", loading: "загружается", starting: "запускается", error: "ошибка", stopped: "остановлена" };

/* поле настройки по описанию из registry.py */
function fieldHtml(f, value, refs) {
  const id = "f_" + f.key;
  const help = f.help ? `<small class="hint">${esc(f.help)}</small>` : "";
  if (f.type === "bool") {
    return `<div><label class="check"><input type="checkbox" id="${id}" data-key="${f.key}" ${value ? "checked" : ""}> ${esc(f.label)}</label>${help}</div>`;
  }
  let input;
  if (f.type === "select") {
    input = `<select id="${id}" data-key="${f.key}">${f.options.map((o) => `<option value="${esc(o.value)}" ${String(o.value) === String(value) ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
  } else if (f.type === "ref") {
    const opts = [{ name: "none", label: "нет" }, ...(refs || []).map((r) => ({ name: r.name, label: r.name }))];
    if (value && !opts.some((o) => o.name === value)) opts.push({ name: value, label: value + " (нет файла)" });
    input = `<select id="${id}" data-key="${f.key}">${opts.map((o) => `<option value="${esc(o.name)}" ${o.name === value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
  } else if (f.type === "number") {
    input = `<input type="number" id="${id}" data-key="${f.key}" value="${esc(value)}" ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} ${f.step != null ? `step="${f.step}"` : 'step="any"'}>`;
  } else if (f.type === "textarea") {
    input = `<textarea id="${id}" data-key="${f.key}" rows="2">${esc(value)}</textarea>`;
  } else {
    input = `<input type="text" id="${id}" data-key="${f.key}" value="${esc(value)}">`;
  }
  return `<label class="field" for="${id}"><span><b>${esc(f.label)}</b></span>${input}${help}</label>`;
}

function readFields(root) {
  const out = {};
  $$("[data-key]", root).forEach((el) => {
    const k = el.dataset.key;
    if (el.type === "checkbox") out[k] = el.checked;
    else if (el.type === "number") out[k] = el.value === "" ? null : Number(el.value);
    else out[k] = el.value;
  });
  return out;
}

/* слова, которых не было в исходном тексте — подсвечиваем: так видно, во что модель превратила 17:00 и С. А. */
function highlightChanges(original, read) {
  if (!read) return "";
  const norm = (w) => w.toLowerCase().replace(/[^\p{L}\p{N}+]/gu, "").replace(/\+/g, "").replace(/ё/g, "е");
  const src = new Set(String(original).split(/\s+/).map(norm).filter(Boolean));
  return String(read).split(/(\s+)/).map((tok) => {
    if (/^\s+$/.test(tok)) return tok;
    const n = norm(tok);
    return n && !src.has(n) ? `<mark>${esc(tok)}</mark>` : esc(tok);
  }).join("").replace(/<\/mark>(\s+)<mark>/g, "$1");
}

/* волна по аудио */
async function drawWave(canvas, blob, audio) {
  const ctx2d = canvas.getContext("2d");
  const css = getComputedStyle(document.documentElement);
  const colWave = css.getPropertyValue("--wave").trim() || "#2f7f6f";
  const colPlayed = css.getPropertyValue("--wave-played").trim() || "#23628f";
  let peaks = [];
  try {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const buf = await ac.decodeAudioData(await blob.arrayBuffer());
    const data = buf.getChannelData(0);
    const n = 240, step = Math.max(1, Math.floor(data.length / n));
    for (let i = 0; i < n; i++) {
      let m = 0;
      for (let j = i * step; j < Math.min(data.length, (i + 1) * step); j += 4) m = Math.max(m, Math.abs(data[j]));
      peaks.push(m);
    }
    ac.close?.();
  } catch { peaks = new Array(240).fill(0.2); }
  const max = Math.max(0.01, ...peaks);
  function paint() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx2d.clearRect(0, 0, w, h);
    const pos = audio && audio.duration ? audio.currentTime / audio.duration : 0;
    const bw = w / peaks.length;
    peaks.forEach((p, i) => {
      const bh = Math.max(2, (p / max) * (h - 6));
      ctx2d.fillStyle = i / peaks.length < pos ? colPlayed : colWave;
      ctx2d.fillRect(i * bw + 0.5, (h - bh) / 2, Math.max(1, bw - 1.5), bh);
    });
  }
  paint();
  if (audio) {
    audio.addEventListener("timeupdate", paint);
    audio.addEventListener("ended", paint);
    canvas.addEventListener("click", (e) => {
      if (!audio.duration) return;
      const r = canvas.getBoundingClientRect();
      audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
      paint();
    });
  }
  new ResizeObserver(paint).observe(canvas);
}

function fmtSec(x) { return x == null ? "—" : (Math.round(x * 100) / 100).toFixed(2) + " с"; }
