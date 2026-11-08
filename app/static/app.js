"use strict";

// The form/quiz, the voice assistant and the fitting backend share one server-side session.
// This file owns navigation between screens, the quiz, the verdict and the summary, and exposes
// window.Fitter for voice.js, measure.js, swing.js and strike.js.

const IN_TO_CM = 2.54, M_PER_YD = 0.9144, KMH_PER_MPH = 1.609344;
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));

const S = { token: null, state: null, demos: [], demo: null, screen: "home", history: [], shownRecId: null, q: 1, path: null,
            returnTo: null, holdScreen: false };

// ------------------------------------------------------------------ server session
async function api(path, opts = {}, timeoutMs = 15000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const headers = { ...(opts.headers || {}) };
  if (S.token) headers["X-Session-Token"] = S.token;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  try {
    const res = await fetch(path, { ...opts, headers, signal: ctrl.signal });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401 && S.token && !opts._retried && path.startsWith("/api/session")) {
      // expired after hours of inactivity (or the server lost it): start a fresh session instead of failing
      await newSession();
      showNotice("Your earlier session had expired, so we started a new one. Answers given before that may need repeating.");
      return api(path, { ...opts, _retried: true }, timeoutMs);
    }
    if (!res.ok) {
      const err = new Error(body.detail || `Something went wrong (${res.status}).`);
      err.status = res.status;
      throw err;
    }
    return body;
  } catch (e) {
    if (e.name === "AbortError") { const err = new Error("The server took too long to answer. Try again."); err.status = 504; throw err; }
    throw e;
  } finally { clearTimeout(timer); }
}

function showNotice(text) {
  let n = $("#notice");
  if (!n) { n = document.createElement("p"); n.id = "notice"; n.className = "voice-alert"; n.setAttribute("role", "status"); $(".app").prepend(n); }
  n.textContent = text; n.hidden = false;
  setTimeout(() => (n.hidden = true), 9000);
}

async function newSession() {
  S.token = null;
  S.token = (await api("/api/session", { method: "POST" })).session_id;
  try { localStorage.setItem("fitter_session", S.token); } catch { /* private mode: this page only */ }
}

async function ensureSession() {
  try { S.token = localStorage.getItem("fitter_session") || sessionStorage.getItem("fitter_session"); } catch { S.token = null; }
  if (S.token) {
    try { applyState(await api("/api/session")); return; } catch { /* expired: start again */ }
  }
  await newSession();
  applyState(await api("/api/session"));
}

async function sync() {
  applyState(await api("/api/session"));
  return S.state;
}

function applyState(st) {
  const prev = S.state;
  S.state = st;
  if (!prev || JSON.stringify(prev.profile) !== JSON.stringify(st.profile)) fillForm(st.profile);
  renderVerdict();
  renderHeard();
  renderRail();
  renderJourney();
  window.Measure?.render(st);
  const rec = st.full_recommendation;
  if (rec && rec.id !== S.shownRecId) {
    const first = S.shownRecId === null && !prev;
    S.shownRecId = rec.id;
    // a new fit asked for in the conversation or the quiz: show it. Measuring and swing steps move on themselves.
    // typing in the chat: stay there (the text box would disappear) and offer a link to the fit instead
    if (!first && S.screen === "chat" && window.Voice?.mode?.() === "text") emit("fitter:fit-ready", rec.id);
    else if (!first && !S.holdScreen && ["chat", "step-1", "verdict"].includes(S.screen)) showScreen("verdict");
  }
  emit("fitter:state", st);
}

// ------------------------------------------------------------------ screens
function showScreen(id, { remember = true } = {}) {
  if (remember && S.screen && S.screen !== id) S.history.push(S.screen);
  S.screen = id;
  $$(".screen").forEach((s) => (s.hidden = s.id !== id));
  window.Measure?.onStep(id === "step-3" ? 3 : 0);
  window.scrollTo({ top: 0, behavior: "smooth" });
  const h = $(`#${id} h1, #${id} [tabindex="-1"]`);
  h?.focus({ preventScroll: true });
  if (id === "step-1") { showQ(S.q); $("#quiz-return").hidden = S.returnTo !== "chat"; }
  if (id === "chat" || id === "home") S.returnTo = null;
  renderJourney();
  emit("fitter:screen", id);
}

// ------------------------------------------------------------------ the journey: conversation → body → swing → fit
const JOURNEY = [["chat", "Conversation"], ["step-3", "Body"], ["swing-screen", "Swing"], ["verdict", "Your fit"]];
function journeyDone(st) {
  const p = st?.profile || {}, miss = st?.missing_fields || [];
  return {
    chat: !miss.filter((k) => !["height_cm", "wrist_to_floor_cm", "tempo"].includes(k)).length && !Object.keys(st?.staged_for_review || {}).length,
    "step-3": !!st?.measurement,                       // ticked only when measured, not when typed
    "swing-screen": !!st?.evidence?.swing_test,
    verdict: !!st?.full_recommendation,
  };
}
function renderJourney() {
  const done = journeyDone(S.state);
  const at = S.screen === "step-1" ? "chat" : S.screen === "review-screen" ? "verdict" : S.screen;
  $$(".journey").forEach((ol) => {
    ol.innerHTML = JOURNEY.map(([id, name], i) =>
      `<li class="${id === at ? "now" : done[id] ? "done" : ""}"><button type="button" data-journey="${id}" ${id === at ? 'aria-current="step"' : ""}>${i + 1} ${esc(name)}${done[id] && id !== at ? " ✓" : ""}</button></li>`).join("");
  });
}
document.addEventListener("click", (e) => {
  const j = e.target.closest("[data-journey]");
  if (!j) return;
  if (j.dataset.journey === "verdict" && !S.state?.full_recommendation) seeFit();
  else showScreen(j.dataset.journey);
});
function back() {
  const prev = S.history.pop() || "home";
  showScreen(prev, { remember: false });
}
// numeric steps kept for the measuring module and tests: 1 quiz, 2/4 verdict, 3 measure
function show(n) { showScreen({ 1: "step-1", 2: "verdict", 3: "step-3", 4: "verdict" }[n] || "home"); }

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-screen],[data-go],[data-back]");
  if (!t) return;
  if (t.dataset.back !== undefined) back();
  else if (t.dataset.screen) showScreen(t.dataset.screen);
  else show(Number(t.dataset.go));
});
$("#go-fitter").addEventListener("click", () => { S.path = "fitter"; showScreen("chat"); });
$("#go-quiz").addEventListener("click", () => { S.path = "quiz"; S.q = 1; showScreen("step-1"); });
$("#voice-hide").addEventListener("click", back);
$("#chat-next").addEventListener("click", () => show(3));
$("#confirm-game").addEventListener("click", () => show(3));
$("#edit-answers").addEventListener("click", () => { S.returnTo = "chat"; S.q = 1; showScreen("step-1"); });
$("#quiz-return").addEventListener("click", () => { saveNow(); showScreen("chat"); });
$("#open-tape-intro").addEventListener("click", () => { $("#tape").open = true; $("#tape").scrollIntoView({ behavior: "smooth" }); $("#manual-height").focus(); });
$("#swing-done").addEventListener("click", seeFit);
$("#swing-skip").addEventListener("click", seeFit);
$("#ask-anything").addEventListener("click", () => { showScreen("chat"); renderAskChips(); });
$("#review-order").addEventListener("click", () => { renderSummary(); showScreen("review-screen"); });
$("#save-fit").addEventListener("click", saveFit);

async function seeFit() {
  const r = await api("/api/session/recommendation", { method: "POST" }).catch((ex) => ({ error: ex.message }));
  if (r.error) { alert(r.error); return; }
  if (!r.tool_result.ok) {
    alert("A few answers are still missing: " + (r.tool_result.missing || []).join(", ").replace(/_/g, " ") + ".");
    S.q = firstMissingQ(); showScreen("step-1");
    return;
  }
  applyState(r.state);
  showScreen("verdict");
}

// ------------------------------------------------------------------ units (yd·mph / m·km/h)
const metric = () => document.querySelector('input[name="dist-units"]:checked')?.value === "metric";
$$('input[name="dist-units"]').forEach((r) => r.addEventListener("change", () => { syncSliders(); renderHeard(); }));

// ------------------------------------------------------------------ quiz (also the editable profile)
const form = $("#game-form");
const QN = 8;
const units = () => form.querySelector('input[name="units"]:checked').value;
const Q_OF = { score: 1, score_bucket: 1, score_value: 1, score_representative: 1, seven_iron_speed: 2, seven_iron_carry: 2, speed_mph: 2, carry_yd: 2,
  ball_start: 3, ball_curve: 3, ball_height: 3, flight: 3, tempo: 4, characteristics: 5, lose_shots: 5,
  height: 6, height_cm: 6, wrist_to_floor: 6, wrist_to_floor_cm: 6, glove: 7, handedness: 8, age: 8, sex: 8, rounds_per_year: 8 };

$("#quiz-progress").innerHTML = Array.from({ length: QN }, () => "<i></i>").join("");
function showQ(n) {
  S.q = Math.max(1, Math.min(QN, n));
  $$(".q-step", form).forEach((d) => (d.hidden = Number(d.dataset.q) !== S.q));
  $$("#quiz-progress i").forEach((i, k) => i.classList.toggle("on", k < S.q));
  $("#quiz-count").textContent = `Quick quiz · ${S.q} of ${QN}`;
  $("#quiz-back").textContent = S.q === 1 ? "Home" : "Back";
  $("#quiz-next").hidden = S.q === QN;
  $("#quiz-submit").hidden = S.q !== QN;
  syncSliders();
}
$("#quiz-next").addEventListener("click", () => { saveNow(); showQ(S.q + 1); window.scrollTo({ top: 0 }); });
$("#quiz-back").addEventListener("click", () => (S.q === 1 ? showScreen("home") : showQ(S.q - 1)));

function setUnitsLabel(u) { $$(".unit", form).forEach((s) => (s.textContent = `(${u})`)); }
function toggleSpeedCarry() {
  const mode = form.querySelector('input[name="speed_or_carry"]:checked').value;
  $$("[data-for]", form).forEach((l) => (l.hidden = l.dataset.for !== mode));
}
function limitChips() {
  const on = $$('input[name="characteristics"]:checked', form).length;
  $$('input[name="characteristics"]', form).forEach((c) => (c.disabled = !c.checked && on >= 3));
}
function setRadio(name, value) {
  const el = form.querySelector(`input[name="${name}"][value="${CSS.escape(String(value))}"]`);
  if (el) el.checked = true; else $$(`input[name="${name}"]`, form).forEach((r) => (r.checked = false));
}

// carry / speed sliders show yd·mph or m·km/h; the form fields always hold yards and mph
function syncSliders() {
  const f = form.elements, m = metric();
  const carry = Number(f.carry_yd.value) || 150, speed = Number(f.speed_mph.value) || 75;
  const cr = $("#carry-range"), sr = $("#speed-range");
  cr.min = m ? 70 : 80; cr.max = m ? 210 : 230;
  sr.min = m ? 70 : 45; sr.max = m ? 180 : 110;
  cr.value = m ? Math.round(carry * M_PER_YD) : carry;
  sr.value = m ? Math.round(speed * KMH_PER_MPH) : speed;
  $("#carry-out").innerHTML = `${esc(cr.value)} <small>${m ? "m" : "yd"}</small>`;
  $("#speed-out").innerHTML = `${esc(sr.value)} <small>${m ? "km/h" : "mph"}</small>`;
  f.carry_yd.hidden = m; f.speed_mph.hidden = m;
}
$("#carry-range").addEventListener("input", (e) => {
  const v = Number(e.target.value);
  form.elements.carry_yd.value = metric() ? Math.round(v / M_PER_YD) : v;
  $("#carry-out").innerHTML = `${esc(v)} <small>${metric() ? "m" : "yd"}</small>`;
});
$("#speed-range").addEventListener("input", (e) => {
  const v = Number(e.target.value);
  form.elements.speed_mph.value = metric() ? Math.round(v / KMH_PER_MPH * 10) / 10 : v;
  $("#speed-out").innerHTML = `${esc(v)} <small>${metric() ? "km/h" : "mph"}</small>`;
});
for (const id of ["#carry-range", "#speed-range"]) $(id).addEventListener("change", (e) => e.isTrusted && saveSoon());

function fillForm(p) {
  const f = form.elements;
  const active = document.activeElement;
  const put = (el, v) => { if (el !== active) el.value = v ?? ""; };
  put(f.age, p.age);
  if (p.rounds_per_year) f.rounds_per_year.value = p.rounds_per_year;
  setRadio("sex", p.sex);
  setRadio("handedness", p.handedness);
  setRadio("score_bucket", p.score_bucket);
  put(f.score_value, p.score_value);
  if (p.speed_or_carry) setRadio("speed_or_carry", p.speed_or_carry);
  put(f.carry_yd, p.carry_yd);
  put(f.speed_mph, p.speed_mph);
  $$('input[name="characteristics"]', form).forEach((c) => (c.checked = (p.characteristics || []).includes(c.value)));
  setRadio("flight", p.flight);
  setRadio("tempo", p.tempo);
  f.score_representative.value = p.score_representative || "";
  for (const k of ["lose_shots", "ball_start", "ball_curve", "ball_height"]) setRadio(k, p[k] ?? "");
  const k = units() === "in" ? 1 / IN_TO_CM : 1;
  put(f.height, p.height_cm ? Math.round(p.height_cm * k * 10) / 10 : "");
  put(f.wtf, p.wrist_to_floor_cm ? Math.round(p.wrist_to_floor_cm * k * 10) / 10 : "");
  f.glove.value = p.glove || "";
  put(f.current_clubs, p.current_clubs);
  toggleSpeedCarry();
  limitChips();
  syncSliders();
  drawFlight();
}

function readForm() {
  const f = form.elements;
  const radio = (n) => form.querySelector(`input[name="${n}"]:checked`)?.value;
  const toCm = (v) => (v === "" ? null : Math.round(Number(v) * (units() === "in" ? IN_TO_CM : 1) * 10) / 10);
  const num = (v) => (v === "" ? null : Number(v));
  return {
    age: num(f.age.value), sex: radio("sex") ?? null, handedness: radio("handedness") ?? null,
    rounds_per_year: f.rounds_per_year.value, score_bucket: num(radio("score_bucket") ?? ""), score_value: num(f.score_value.value),
    speed_or_carry: radio("speed_or_carry"), carry_yd: num(f.carry_yd.value), speed_mph: num(f.speed_mph.value),
    characteristics: $$('input[name="characteristics"]:checked', form).map((c) => c.value),
    flight: radio("flight") ?? null, tempo: radio("tempo") ?? null,
    score_representative: f.score_representative.value || null, lose_shots: radio("lose_shots") ?? null,
    ball_start: radio("ball_start") ?? null, ball_curve: radio("ball_curve") ?? null, ball_height: radio("ball_height") ?? null,
    height_cm: toCm(f.height.value), wrist_to_floor_cm: toCm(f.wtf.value), glove: f.glove.value || null,
    current_clubs: f.current_clubs.value.trim() || null,
  };
}

// the golfer's own answers in the quiz save straight to the shared profile
let saveTimer = null;
function saveSoon() { clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 250); }
async function saveNow() {
  clearTimeout(saveTimer);
  const values = Object.fromEntries(Object.entries(readForm()).filter(([, v]) => v !== null));
  try { applyState(await api("/api/session/profile", { method: "PUT", json: { values } })); }
  catch (ex) { showFormError(ex.message); }
}
form.addEventListener("change", (e) => {
  if (e.target.name === "speed_or_carry") toggleSpeedCarry();
  if (e.target.name === "characteristics") limitChips();
  if (["ball_start", "ball_curve", "ball_height", "handedness"].includes(e.target.name)) drawFlight();
  if (e.target.name === "score_value" && e.target.value) {
    const n = Number(e.target.value);
    setRadio("score_bucket", n < 71 ? 1 : n <= 77 ? 2 : n <= 84 ? 3 : n <= 92 ? 4 : 5);
  }
  if (e.target.name === "carry_yd" || e.target.name === "speed_mph") syncSliders();
  if (e.target.name === "units") {
    const factor = e.target.value === "in" ? 1 / IN_TO_CM : IN_TO_CM;
    for (const n of ["height", "wtf"]) {
      const el = form.elements[n];
      if (el.value) el.value = Math.round(Number(el.value) * factor * 10) / 10;
    }
    setUnitsLabel(e.target.value);
    return;
  }
  if (e.isTrusted) saveSoon();
});

function showFormError(msg) { const e = $("#form-error"); e.textContent = msg; e.hidden = !msg; }

function missingFields(v) {
  const labels = { score_bucket: "usual score", flight: "flight you want", tempo: "tempo", height_cm: "height",
    wrist_to_floor_cm: "wrist-to-floor", glove: "glove size", handedness: "handedness", age: "age", sex: "fitting rules" };
  const miss = Object.keys(labels).filter((k) => v[k] === null);
  if (v.speed_or_carry === "carry" ? !v.carry_yd : !v.speed_mph) miss.unshift(v.speed_or_carry === "carry" ? "carry_yd" : "speed_mph");
  return { keys: miss, text: miss.map((k) => labels[k] || "7-iron " + (k === "carry_yd" ? "carry" : "speed")) };
}
function firstMissingQ() {
  const m = missingFields(readForm()).keys;
  return m.length ? Math.min(...m.map((k) => Q_OF[k] || 1)) : 1;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const values = readForm();
  const miss = missingFields(values);
  if (miss.keys.length) {
    showFormError(`Please add: ${miss.text.join(", ")}.`);
    showQ(firstMissingQ());
    return;
  }
  showFormError("");
  try {
    applyState(await api("/api/session/profile", { method: "PUT", json: { values } }));
    const r = await api("/api/session/recommendation", { method: "POST" });
    if (!r.tool_result.ok) throw new Error(r.tool_result.detail || "Some answers are missing.");
    applyState(r.state);
    showScreen("verdict");
  } catch (ex) { showFormError(ex.message); }
});

// where the fit stands: which decisions already have the inputs they need
function renderRail() {
  const p = S.state?.profile || {};
  const has = (...k) => k.every((x) => p[x] !== undefined && p[x] !== null && p[x] !== "");
  const items = [
    ["Head", has("score_bucket")],
    ["Shaft", (p.speed_mph || p.carry_yd) && has("tempo", "flight")],
    ["Length", has("height_cm")],
    ["Lie", has("height_cm", "wrist_to_floor_cm")],
    ["Grip", has("glove")],
  ];
  $("#rail").innerHTML = items.map(([n, ok]) => `<span class="${ok ? "settled" : "open"}">${n} · ${ok ? "settled" : "open"}</span>`).join("");
}

// top-down ball flight from behind the golfer: start direction, then curve
function drawFlight() {
  const radio = (n) => form.querySelector(`input[name="${n}"]:checked`)?.value;
  const start = { left: -22, straight: 0, right: 22 }[radio("ball_start")] ?? 0;
  const curve = { left: -30, none: 0, right: 30 }[radio("ball_curve")] ?? 0;
  $("#flight-path").setAttribute("d", `M60 140 Q${60 + start} 75 ${60 + start * 2 + curve} 20`);
  const ev = S.state?.evidence;
  $("#flight-words").textContent = ev?.ball_flight || "Seen from behind you, looking at the target.";
}

// ------------------------------------------------------------------ demo golfers
async function loadDemos() {
  const sel = $("#demo-select");
  try {
    S.demos = await api("/api/demo-golfers");
    sel.innerHTML = '<option value="">Choose…</option>' +
      S.demos.map((d) => `<option value="${esc(d.id)}">${esc(d.persona)}</option>`).join("");
    sel.value = "DEMO-13";
    showDemoStory();
  } catch { sel.innerHTML = '<option value="">Unavailable</option>'; }
}
function showDemoStory() {
  const d = S.demos.find((x) => x.id === $("#demo-select").value);
  $("#demo-story").hidden = !d;
  if (d) $("#demo-story").textContent = d.story;
}
$("#demo-select").addEventListener("change", showDemoStory);
$("#demo-load").addEventListener("click", async () => {
  const d = S.demos.find((x) => x.id === $("#demo-select").value);
  if (!d) return;
  S.demo = d;
  setRadio("units", "cm"); setUnitsLabel("cm");
  try {
    applyState(await api("/api/session/profile", { method: "PUT", json: { values: d.answers } }));
    S.path = "quiz"; S.q = QN; showScreen("step-1");
  } catch (ex) { showFormError(ex.message); }
});

// ------------------------------------------------------------------ "What I've heard so far" (chat)
// summary cards on the conversation screen: what the fit will use, with units, and what is still open.
// Built from the same server state the assistant reads, so its spoken summary matches the cards.
const GAME_CARDS = [
  { key: "score", label: "Usual score", q: 1, staged: ["score", "score_representative"],
    value: (p) => p.score_value ? `${p.score_value} strokes` : p.score_bucket ? `${{ 1: "under 71", 2: "71–77", 3: "78–84", 4: "85–92", 5: "92+" }[p.score_bucket]} strokes` : null,
    src: (p) => ({ several: "over several recent rounds", one: "from one round", guess: "a rough guess" }[p.score_representative] || "") },
  { key: "clubs", label: "Current clubs", q: 5, staged: ["current_clubs"], value: (p) => p.current_clubs || null, src: () => "context for a fitter" },
  { key: "seven", label: "7-iron", q: 2, staged: ["seven_iron_speed", "seven_iron_carry"],
    value: (p) => p.speed_or_carry === "speed" && p.speed_mph ? (metric() ? `${Math.round(p.speed_mph * KMH_PER_MPH)} km/h club speed` : `${p.speed_mph} mph club speed`)
      : p.carry_yd ? (metric() ? `${Math.round(p.carry_yd * M_PER_YD)} m carry` : `${p.carry_yd} yd carry`) : null,
    src: (p) => p.speed_or_carry === "speed" ? ({ simulator: "from a simulator", launch_monitor: "from a launch monitor" }[p.speed_source] || "your answer") : p.carry_yd ? "carry: the kit's fallback for speed" : "" },
  { key: "miss", label: "Common miss", q: 3, staged: ["ball_start", "ball_curve", "ball_height"],
    value: (p) => {
      const bits = [p.ball_start && (p.ball_start === "straight" ? "starts straight" : `starts ${p.ball_start}`),
        p.ball_curve && (p.ball_curve === "none" ? "no curve" : `curves ${p.ball_curve}`),
        p.ball_height && { lower: "lower than wanted", as_wanted: "height about right", higher: "higher than wanted" }[p.ball_height]].filter(Boolean);
      return bits.length ? bits.join(", ") : null;
    }, src: (p) => (p.lose_shots ? `loses most shots: ${p.lose_shots.replace("_", " ")}` : "") },
  { key: "prio", label: "Priorities", q: 5, wide: true, staged: ["characteristics"],
    value: (p) => (p.characteristics?.length ? p.characteristics.join(", ") : null),
    src: (p) => (p.flight ? `wants a ${p.flight === "mid" ? "medium" : p.flight} flight` : "") },
];
function renderHeard() {
  const st = S.state;
  if (!st) return;
  const p = st.profile, staged = st.staged_for_review || {};
  $("#game-cards").innerHTML = GAME_CARDS.map((c) => {
    const heard = c.staged.map((k) => staged[k]).find(Boolean);
    const v = c.value(p);
    const cls = heard ? "heard" : v ? "" : "missing";
    const val = heard ? `Heard: ${heard.read_back}` : v || "Still to confirm";
    const src = heard ? "say yes to confirm, or correct it" : v ? c.src(p) : "";
    return `<button type="button" class="gc ${cls}${c.wide ? " wide" : ""}" data-q="${c.q}" aria-label="${esc(c.label)}: ${esc(val)}. Tap to change.">
      <span class="k">${esc(c.label)}</span><span class="v">${esc(val)}</span>${src ? `<span class="src">${esc(src)}</span>` : ""}</button>`;
  }).join("");
  $("#game-check").hidden = !journeyDone(st).chat;
}
$("#game-cards").addEventListener("click", (e) => {
  const c = e.target.closest(".gc");
  if (!c) return;
  S.returnTo = "chat"; S.q = Number(c.dataset.q); showScreen("step-1");     // tap to correct: the same answer in the quiz
});

// suggestion chips after the verdict ("Ask me anything")
function renderAskChips() {
  if (!S.state?.full_recommendation || $("#ask-chips")) return;
  const r = S.state.full_recommendation.result;
  const qs = [`Why ${r.best.flex_label.toLowerCase()} flex?`, `What does ${fmtLie(r.best.lie_deg).toLowerCase()} lie do?`,
              r.runner_up ? `${r.best.head_name.replace("Iron ", "")} vs ${r.runner_up.head_name.replace("Iron ", "")}?` : null, "Steel or graphite?"].filter(Boolean);
  const div = document.createElement("div");
  div.id = "ask-chips"; div.className = "chips-wrap";
  div.innerHTML = qs.map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join("");
  $("#transcript").before(div);
  div.addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) window.Voice?.ask(c.textContent); });
}

// ------------------------------------------------------------------ verdict
const fmtLen = (v) => (v === 0 ? "Standard" : `${v > 0 ? "+" : "−"}${Math.abs(v)} in`);
const fmtLie = (v) => (v === 0 ? "Standard" : `${v > 0 ? "+" : "−"}${Math.abs(v)}° ${v > 0 ? "upright" : "flat"}`);
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const shaftText = (r) => `${r.shaft_model} · ${r.flex_label} · ${r.shaft_weight_g} g ${r.material}`;
const buildText = (r) => (r.purchase === "stock" ? "Stock set" : "Custom build");
const HEAD_LINE = { "101-mkii": "Max forgiveness", "201-mkii": "Players distance", "201t-mkii": "Forgiving control", "301-cb": "Players cavity", "301-mb": "Forged blade" };

// one row per part of the build: recommendation, one short reason, the evidence it rests on, and any open check
const ROW_DECISION = { Head: ["Iron head"], Shaft: ["Shaft flex", "Shaft model"], Length: ["Length and lie"], Lie: ["Length and lie"], Grip: ["Grip"], Hand: [] };
const STRENGTH_WORD = (s) => (s.startsWith("measured") ? "measured" : s.startsWith("confirmed") ? "your confirmed answer"
  : s.startsWith("fallback") ? "estimate" : s === "product data" ? "product data" : s === "entered" ? "entered" : "self-reported");
function rowEvidence(key, st) {
  const ds = (st.evidence?.decisions || []).filter((d) => ROW_DECISION[key].includes(d.decision));
  const items = ds.flatMap((d) => d.evidence).filter(Boolean)
    .filter((i) => key !== "Length" || /height|length/i.test(i.what))
    .filter((i) => key !== "Lie" || /wrist|height|lie/i.test(i.what));
  return items.slice(0, 3).map((i) => `${i.what} (${STRENGTH_WORD(i.strength)})`).join("; ");
}
// how sure the fit is, and why: the first thing after the iron itself
function trustCard(t) {
  if (!t) return "";
  const c = t.counts, h = t.tested_on_held_out || {};
  const golfers = Math.round(((h.High?.n || 0) + (h.Medium?.n || 0) + (h.Low?.n || 0)) / 6);   // 6 checked parts per golfer
  const tested = h.High ? `Tested on ${golfers.toLocaleString("en")} other golfers: parts marked High matched Takomo's reference fit ${h.High.in_10} times in 10, Medium ${h.Medium?.in_10 ?? "–"} in 10, Low ${h.Low?.in_10 ?? "–"} in 10.` : "";
  return `<section class="trust" aria-labelledby="h-trust">
    <h3 id="h-trust">How sure are we</h3>
    <p class="levels"><span class="conf High">${c.High} High</span> <span class="conf Medium">${c.Medium} Medium</span> <span class="conf Low">${c.Low} Low</span></p>
    ${tested ? `<p class="note">${esc(tested)}</p>` : ""}
    <div class="grid2 sources">
      <div><b>Measured</b>${t.measured.length ? `<ul>${t.measured.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : `<p class="note">Nothing yet</p>`}</div>
      <div><b>From your answers</b><ul>${t.from_answers.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>
    </div>
    ${t.least_certain ? `<p class="least"><b>Least certain: ${esc(t.least_certain.part)}.</b> ${t.least_certain.next_step ? esc(cap(t.least_certain.next_step)) + "." : ""}</p>` : ""}
    <p class="note fineprint">${esc(t.basis)}</p>
  </section>`;
}

function fitCard(rec, st) {
  const r = rec.result.best, w = r.why, b = rec.before?.best;
  const conf = st.evidence?.confidence?.lines || {}, short = st.evidence?.short_whys || {};
  const confFor = (k) => (k === "Shaft"
    ? [conf.Shaft, conf["Shaft model"]].filter(Boolean).sort((x, y) => "HighMediumLow".indexOf(y.level) - "HighMediumLow".indexOf(x.level))[0]
    : conf[k]);
  const rows = [
    ["Head", "Head", r.head_name.replace("Iron ", ""), b && b.head_name.replace("Iron ", ""), w.head],
    ["Shaft", "Shaft", `${r.shaft_model} · ${r.flex_label}`, b && `${b.shaft_model} · ${b.flex_label}`, w.shaft, `${r.shaft_weight_g} g ${r.material}`],
    ["Length", "Length", fmtLen(r.length_in), b && fmtLen(b.length_in), w.length],
    ["Lie", "Lie", fmtLie(r.lie_deg), b && fmtLie(b.lie_deg), w.lie],
    ["Grip", "Grip", cap(r.grip), b && cap(b.grip), w.grip],
    ["Hand", "Hand", `${cap(r.handedness)}-handed`, null, "From your answer: the set is built for the hand you play."],
  ];
  const stock = r.purchase === "stock";
  return `<article class="fit" data-rec-id="${rec.id}">
    ${rec.stale ? `<div class="stale">Your answers changed since this fit.<button type="button" class="btn" data-refit>Update my fit</button></div>` : ""}
    <div class="rec-card"><span class="label">Recommended iron</span><h3>${esc(r.head_name)}</h3>
      <p>${esc(HEAD_LINE[r.head] || "")}</p>
      <span class="build ${r.purchase}">${stock ? "Stock set" : "Custom build"}</span>
      <p style="margin-top:6px">${stock ? "Matches an off-the-shelf set: ships faster, 30-day returns." : esc(`Custom because: ${r.why.custom_reasons.join("; ")}.`)}</p></div>
    ${trustCard(st.evidence?.trust)}
    ${rows.map(([key, k, v, was, why, extra]) => {
      const changed = was != null && was !== v;
      const c = confFor(key);
      const ev = key === "Hand" ? "your answer" : rowEvidence(key, st);
      const open = c && c.level !== "High" ? (c.next_step || c.why) : null;
      return `<details class="${changed ? "changed" : ""}" data-row="${esc(k)}">
        <summary><span class="k">${esc(k)}</span><span class="v">${changed ? `<span class="was">${esc(was)}</span>` : ""}${esc(v)}${extra ? ` <small class="note">${esc(extra)}</small>` : ""}
          <span class="short">${esc(short[key] || why.split(". ")[0])}</span>
          ${ev ? `<span class="src">Evidence: ${esc(ev)}</span>` : ""}
          ${open ? `<span class="open-check">To check: ${esc(open)}</span>` : ""}</span>
          ${c ? `<span class="conf ${c.level}">${c.level}</span>` : ""}</summary>
        <p class="why-long">${esc(why)}</p></details>`;
    }).join("")}
    <details class="trace"><summary>Fit #${rec.id} · the engine's working</summary>
      <ol>${r.trace.map((t) => `<li>${esc(t)}</li>`).join("")}</ol></details>
  </article>`;
}

function lieBar(before, after) {
  const pos = (v) => ((Math.max(-3.5, Math.min(3.5, v)) + 3.5) / 7) * 100;
  const bands = [-3, -2, -1, 0, 1, 2, 3];
  const sg = (v) => (v > 0 ? "+" : "") + v;
  return `<div class="lie-bar" role="img" aria-label="Lie chart reading moved from ${before.raw} to ${after.raw} degrees">
    <div class="track">${bands.map((b) => `<i class="${b === after.lie ? "on" : ""}"></i>`).join("")}</div>
    ${bands.map((b) => `<span class="tick" style="left:${pos(b)}%">${sg(b)}°</span>`).join("")}
    <span class="pin before" style="left:${pos(before.raw)}%"></span><span class="pin after" style="left:${pos(after.raw)}%"></span>
  </div>
  <p class="lie-legend"><span class="before"><i></i>Your answers ${sg(before.raw)}°</span><span class="after"><i></i>Measured ${sg(after.raw)}°</span></p>`;
}

function evidencePanel(st) {
  const ev = st.evidence;
  if (!ev) return "";
  const c = ev.head_comparison;
  const strength = (s) => (s.startsWith("measured") ? "m" : s.startsWith("confirmed") || s === "product data" || s === "entered" ? "c" : s.startsWith("fallback") ? "f" : "s");
  return `<div class="evidence">
    ${ev.player_summary ? `<p class="player">${esc(ev.player_summary)}</p>` : ""}
    ${ev.tempo_effect ? `<p class="effect"><b>Your swing tempo:</b> ${esc(ev.swing_test.text)} ${esc(ev.tempo_effect.text)}</p>` : ""}
    ${c ? `<div class="compare-heads"><h3>Your two head options</h3>
        <p>${esc(c.summary)} ${c.inputs.length ? "Why: " + esc(c.inputs.join("; ")) + "." : ""}</p>
        <p>${esc(c.tradeoff)}</p><p class="note">${esc(c.contact)}</p>
        ${c.fitter_review ? `<p class="review-flag">${esc(c.fitter_review)}</p>` : ""}</div>` : ""}
    <details class="evidence-map"><summary>The evidence behind each choice</summary>
      ${ev.ball_flight ? `<p class="note">${esc(ev.ball_flight)}</p>` : ""}
      ${ev.decisions.map((d) => `<div class="ev-row"><p><b>${esc(d.decision)}</b>${d.choice ? ` · ${esc(d.choice)}` : ""}</p>
        <ul>${d.evidence.map((i) => `<li><span class="ev ${strength(i.strength)}">${esc(i.strength)}</span> ${esc(i.what)}</li>`).join("")}</ul>
        ${d.improve.length ? `<p class="note">To strengthen it: ${esc(d.improve.join("; "))}.</p>` : ""}</div>`).join("")}
    </details>
    <div class="row ev-actions">
      <button type="button" class="btn quiet" data-strike>${ev.strike_test ? "Redo strike test" : "Add a strike test"}</button>
      ${st.measurement ? "" : `<button type="button" class="btn quiet" data-go-measure>Measure wrist-to-floor</button>`}
    </div>
  </div>`;
}

function renderVerdict() {
  const st = S.state, rec = st?.full_recommendation;
  $$(".fit-slot").forEach((slot) => (slot.innerHTML = rec ? fitCard(rec, st) : ""));
  $$(".evidence-slot").forEach((slot) => (slot.innerHTML = rec ? evidencePanel(st) : ""));
  $$("[data-refit]").forEach((b) => b.addEventListener("click", refit));
  $$("[data-strike]").forEach((b) => b.addEventListener("click", () => window.Strike?.open()));
  $$("[data-go-measure]").forEach((b) => b.addEventListener("click", () => show(3)));
  drawFlight();
  if (!rec) { for (const id of ["#callout", "#changes", "#decision", "#close-call"]) $(id).innerHTML = ""; return; }
  const r = rec.result.best, conf = st.evidence?.confidence;
  const close = conf?.close_call;
  const shaftShaky = conf && ["Shaft", "Shaft model"].some((k) => conf.lines[k]?.level !== "High");
  $("#close-call").innerHTML = (close ? `<div class="close-box"><span><b>One close call.</b> ${esc(close.text)}</span></div>` : "")
    + (shaftShaky && !st.evidence.swing_test ? `<div class="tighten"><p><b>Tighten the shaft call · 2 min</b><br>Film a swing and I'll re-check your tempo and flex.</p>
        <button type="button" class="btn quiet" data-screen="swing-screen">Film a swing</button></div>` : "");
  $("#callout").innerHTML = "";
  const cmp = rec.compare;
  const ba = cmp ? [["Head", (x) => x.head_name.replace("Iron ", "")], ["Shaft", (x) => `${x.shaft_model} ${x.flex_label}`],
    ["Length", (x) => fmtLen(x.length_in)], ["Lie", (x) => fmtLie(x.lie_deg)], ["Build", (x) => buildText(x)]] : [];
  $("#changes").innerHTML = !cmp ? "" : `<div class="change"><h3>Before and after measuring</h3>
      <div class="ba"><span class="h"></span><span class="h">Your answers</span><span class="h">Measured</span>
        ${ba.map(([k, f]) => { const x = f(cmp.before.best), y = f(cmp.after.best);
          return `<span class="h">${k}</span><span>${esc(x)}</span><span class="${x !== y ? "chg" : ""}">${esc(y)}</span>`; }).join("")}</div>
      <ul>${cmp.story.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
      ${lieBar(cmp.before.best.why.lie_detail, cmp.after.best.why.lie_detail)}
      <p><span class="badge ${{ "check needed": "check", "close call": "close" }[cmp.confidence.level] || "sure"}">${{ "check needed": "Check with a tape", "close call": "Close call" }[cmp.confidence.level] || "Not near a boundary"}</span> ${esc(cmp.confidence.text)}</p>
    </div>`;
  const stock = r.purchase === "stock";
  const onlyFit = r.why.custom_reasons.length && r.why.custom_reasons.every((x) => /^(lie|length)/.test(x));
  const near = cmp && cmp.confidence.level !== "confident";
  let advice;
  if (cmp && cmp.confidence.tape_check) advice = cmp.confidence.text;
  else if (stock) advice = near ? "Stock set. You're near the next lie band: try the 7-iron within 30 days of delivery. If it feels wrong, you can still return the set as long as no other club has been used."
    : "Stock set: it matches off-the-shelf clubs and ships faster. You can try the 7-iron and return the set within 30 days of delivery if the other clubs are unused.";
  else if (onlyFit && !rec.measured) advice = "Lie is the only reason this is custom. Measure your wrist-to-floor first: it could put you in a stock set. A fitter at a demo bay can also check your lie in a couple of minutes.";
  else if (onlyFit) advice = `Lie is the only reason this is custom, and lie is the least certain part of any fit. Custom builds can't be returned, so it's worth hitting both heads at a demo bay, where a fitter can check your lie, before ordering.`;
  else advice = `Custom: ${r.why.custom_reasons.join("; ")}. Custom builds can't be returned, so it's worth being sure of the measurements.`;
  $("#decision").innerHTML = `<p><b>Stock or custom?</b> ${esc(advice)}</p>`;
}

function renderSummary() {
  const st = S.state, rec = st?.full_recommendation;
  if (!rec) { $("#summary").innerHTML = "<p>No fit yet.</p>"; return; }
  const r = rec.result.best;
  const close = st.evidence?.confidence?.close_call;
  $("#summary").innerHTML = `<span class="label">Your fit summary</span>
    <p><b>${esc(r.head_name)}</b> · ${esc(shaftText(r))} · ${esc(fmtLen(r.length_in).toLowerCase())} length · ${esc(fmtLie(r.lie_deg).toLowerCase())} lie · ${esc(r.grip)} grip · ${esc(r.handedness)}-handed</p>
    <p>${esc(buildText(r))}${r.purchase === "stock" ? ": ships faster; 30 days to return it unused (you may try the 7-iron)." : ": built for you, can't be returned."}</p>
    ${close ? `<p class="note">Open question: ${esc(close.text)}</p>` : ""}
    ${st.evidence?.swing_test ? `<p class="note">Your swing: ${esc(st.evidence.swing_test.text)}</p>` : ""}`;
}

// "Save my fit": a plain-text copy the golfer keeps (nothing is sent anywhere)
function fitText() {
  const st = S.state, rec = st?.full_recommendation;
  if (!rec) return null;
  const r = rec.result.best, conf = st.evidence?.confidence?.lines || {};
  const line = (k, v, c) => `${k.padEnd(8)} ${v}${c ? `  [${c.level} confidence${c.level !== "High" && c.next_step ? `: ${c.next_step}` : ""}]` : ""}`;
  const m = st.measurement;
  return [
    "TAKOMO · YOUR RECOMMENDED FIT", new Date().toLocaleDateString(), "",
    `${r.head_name} · ${buildText(r).toUpperCase()}`, "",
    line("Head", r.head_name, conf.Head),
    line("Shaft", shaftText(r), conf["Shaft model"] || conf.Shaft),
    line("Length", fmtLen(r.length_in), conf.Length),
    line("Lie", fmtLie(r.lie_deg), conf.Lie),
    line("Grip", cap(r.grip), conf.Grip),
    line("Hand", `${cap(r.handedness)}-handed`), "",
    m ? `Measurements: height ${m.height_cm} cm, wrist-to-floor ${m.wrist_to_floor_cm} cm (${m.source === "photo" ? "camera estimate" : m.source}).` : "Measurements: from your answers (not measured).",
    st.evidence?.swing_test ? `Swing: ${st.evidence.swing_test.text}` : "Swing: not recorded; tempo from your answer.",
    st.evidence?.confidence?.close_call ? `Close call: ${st.evidence.confidence.close_call.text}` : "",
    "", r.purchase === "stock" ? "Returns: 30 days from delivery for unused products; you may try the 7-iron of a standard set." : "Custom builds can't be returned.",
    "Policy: https://takomogolf.com/policies/refund-policy", "",
    `Reference: fit #${rec.id}. Hackathon prototype, not an official Takomo product.`,
  ].filter((x, i, a) => x !== "" || a[i - 1] !== "").join("\n");
}
function saveFit() {
  const text = fitText();
  if (!text) { alert("There's no fit to save yet."); return; }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  a.download = "takomo-fit.txt";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  $("#save-fit").textContent = "Saved ✓";
}

async function refit() {
  try {
    const r = await api("/api/session/recommendation", { method: "POST" });
    applyState(r.state);
    if (!r.tool_result.ok) alert("Some answers are still missing: " + (r.tool_result.missing || []).join(", "));
  } catch (ex) { alert(ex.message); }
}

// ------------------------------------------------------------------ restart
$("#restart").addEventListener("click", async () => {
  window.Measure.reset();
  S.demo = null; S.shownRecId = null; S.state = null; S.history = []; S.q = 1;
  form.reset(); setUnitsLabel("cm");
  $("#ask-chips")?.remove();
  await newSession();
  applyState(await api("/api/session"));
  emit("fitter:reset");
  showScreen("home", { remember: false });
});

// ------------------------------------------------------------------ API for the other modules
window.Fitter = {
  api, sync, show, showScreen, applyState, refit, fitText,
  hold: (on) => { S.holdScreen = on; },
  goQ: (n) => { S.q = n; showScreen("step-1"); },
  state: () => S.state,
  token: () => S.token,
  screen: () => S.screen,
  measurementStatus: () => window.Measure.status(),
  // the assistant opens the measuring step; the camera starts straight away only when the height (the scale) is known
  openMeasurement: () => {
    show(3);
    const h = S.state?.profile?.height_cm || Number($("#ms-height").value) || null;
    if (h && !$("#ms-height").value) $("#ms-height").value = h;
    // no height known: the camera measures height too, using a plain A4 sheet on the wall as the scale
    if (!h && window.Measure.status().method === "height") window.Measure.setMethod("paper");
    const ready = window.Measure.canOpen();
    if (ready) window.Measure.openCamera();
    return { camera_started: ready, height_known: !!h, ...window.Measure.status() };
  },
};

(async () => {
  showScreen("home", { remember: false });
  loadDemos();
  try { await ensureSession(); } catch (ex) { showFormError("Can't reach the server: " + ex.message); }
  emit("fitter:ready");
})();
