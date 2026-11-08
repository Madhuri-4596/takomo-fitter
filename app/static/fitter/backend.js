"use strict";

// Connects the fitter frontend (app/static/fitter/fitter.html, from feature/ai-fitter-prototype) to this backend.
// The frontend's look and flow are untouched; this file only supplies real data:
//   its profile tiles  -> our session profile (the same answers the classic UI and the voice assistant use)
//   our engine         -> its verdict card: real spec, real whys, calibrated confidence as Sure / Hmm / Guess
// Phase 1: verdict. Voice (ElevenLabs), camera measurement and swing tempo come next.

window.FitterBackend = (() => {
  const B = { token: null, rec: null, state: null, assumed: [], guessed: {} };

  async function api(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (B.token) headers["X-Session-Token"] = B.token;
    if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts = { ...opts, body: JSON.stringify(opts.json) }; }
    const res = await fetch(path, { ...opts, headers });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401 && B.token && !opts._retried) { await newSession(); return api(path, { ...opts, _retried: true }); }
    if (!res.ok) throw new Error(body.detail || `Something went wrong (${res.status}).`);
    return body;
  }
  async function newSession() {
    B.token = null;
    B.token = (await api("/api/session", { method: "POST" })).session_id;
    try { localStorage.setItem("fitter_session", B.token); } catch { /* private mode */ }
  }
  async function ensureSession() {
    if (B.token) return;
    try { B.token = localStorage.getItem("fitter_session"); } catch { B.token = null; }
    if (B.token) { try { await api("/api/session"); return; } catch { /* expired */ } }
    await newSession();
  }

  // ------------------------------------------------------------------ their profile -> our answers
  const text = (e) => (e == null ? "" : String(e.v ?? ""));
  const num = (t) => { const m = String(t).replace(",", ".").match(/\d+(\.\d+)?/); return m ? Number(m[0]) : null; };

  function heightCm(e) {                                  // scripted: inches (71); typed: "5 11", "5'11", "180 cm", "71"
    if (typeof e.v === "number") return e.v * 2.54;
    const t = String(e.v).toLowerCase(), ns = (t.match(/\d+(\.\d+)?/g) || []).map(Number);
    if (/cm/.test(t) || ns[0] > 100) return ns[0];
    if (/\bm\b/.test(t) && ns[0] < 3) return ns[0] * 100;
    if (ns.length >= 2 && ns[0] <= 7) return (ns[0] * 12 + ns[1]) * 2.54;
    if (ns[0] <= 7) return ns[0] * 30.48;
    return ns[0] * 2.54;                                  // inches
  }
  function inchesCm(e) {                                  // wrist-to-floor: scripted inches; typed "33", "84 cm"
    if (typeof e.v === "number") return e.v * 2.54;
    const t = String(e.v).toLowerCase(), n = num(t);
    return /cm/.test(t) || n > 50 ? n : n * 2.54;
  }
  function glove(t) {
    t = String(t).toUpperCase().trim();
    const words = { "EXTRA SMALL": "XS", "EXTRA LARGE": "XL", "MEDIUM LARGE": "M/L", "MEDIUM-LARGE": "M/L", "LARGE": "L", "MEDIUM": "M", "SMALL": "S" };
    for (const [w, g] of Object.entries(words)) if (t.includes(w)) return g;
    const m = t.match(/\b(XS|XL|M\/L|ML|S|M|L)\b/);
    return m ? (m[1] === "ML" ? "M/L" : m[1]) : null;
  }
  function characteristics(t) {
    t = t.toLowerCase(); const out = [];
    if (/forgiv|slice|mishit|miss/.test(t)) out.push("forgiveness");
    if (/distance|longer|long irons|further|farther/.test(t)) out.push("distance");
    if (/feel|forged/.test(t)) out.push("forged feel");
    if (/control|gapping/.test(t)) out.push("distance control");
    if (/shape|work|draw|fade/.test(t)) out.push("workability");
    if (/less offset/.test(t)) out.push("less offset");
    if (/more offset/.test(t)) out.push("more offset");
    return out.slice(0, 3);
  }
  function tempoFrom(S) {                                 // their handover's ratio buckets: >=3.2 smooth, 2.6-3.2 moderate, <2.6 quick
    const r = (S.swings || []).map((s) => parseFloat(s.ratio)).filter((x) => x > 0);
    if (!r.length) return null;
    const mean = r.reduce((a, b) => a + b, 0) / r.length;
    return mean >= 3.2 ? "smooth" : mean >= 2.6 ? "moderate" : "quick";
  }

  async function answers(S) {
    const p = S.profile, v = {}, assumed = [], guessed = {};
    const score = p.score && num(text(p.score)); if (score) v.score_value = score;
    if (p.speed_mph) Object.assign(v, { speed_or_carry: "speed", speed_mph: num(text(p.speed_mph)),
      speed_source: p.speed_mph.src === "sim" ? "simulator" : p.speed_mph.src === "estimated" ? "own_estimate" : null });
    else if (p.carry) { const c = num(text(p.carry)); if (c) Object.assign(v, /mph/i.test(text(p.carry)) ? { speed_or_carry: "speed", speed_mph: c } : { speed_or_carry: "carry", carry_yd: c }); }
    // the tile reads "Mid → higher" or "Mid, keep it": the rules use the wanted flight (the part after the arrow)
    const fl = text(p.flight).toLowerCase(), want = fl.includes("→") ? fl.split("→")[1] : fl, now = fl.split("→")[0];
    const lvl = (t) => (/high/.test(t) ? "high" : /low|flat/.test(t) ? "low" : /mid|medium/.test(t) ? "mid" : null);
    const order = ["low", "mid", "high"], cur = lvl(now) || "mid";
    v.flight = /higher/.test(want) ? order[Math.min(2, order.indexOf(cur) + 1)] : /lower/.test(want) ? order[Math.max(0, order.indexOf(cur) - 1)] : lvl(want) || cur;
    if (lvl(now)) v.flight_current = lvl(now);
    if (!p.flight) guessed.flight = true;
    v.characteristics = [];                      // Your game never asks what the irons should do: score and speed decide the head
    if (p.height) v.height_cm = Math.round(heightCm(p.height) * 10) / 10;
    if (p.wtf && !p.wtf.skipped) v.wrist_to_floor_cm = Math.round(inchesCm(p.wtf) * 10) / 10;
    else if (v.height_cm) { v.wrist_to_floor_cm = (await api(`/api/lie-neutral?height_cm=${v.height_cm}`)).wrist_to_floor_cm; guessed.lie = true; }
    if (p.glove) { const g = glove(text(p.glove)); if (g) v.glove = g; }
    v.handedness = /left/i.test(text(p.hand)) ? "left" : "right";
    if (!p.hand) assumed.push("right-handed");
    const t = !S.swingSkipped && tempoFrom(S);
    v.tempo = t || "moderate";
    if (!t) guessed.tempo = true;
    // Your game asks age, fit as, rounds: use the tiles; anything missing is assumed, and said so on the shaft line
    const age = p.age && num(text(p.age));
    v.age = age || 45; if (!age) assumed.push("under 70");
    v.sex = /wom|lad|female/i.test(text(p.sex)) ? "female" : "male"; if (!p.sex) assumed.push("men's fitting");
    const r = text(p.rounds), rn = num(r);
    v.rounds_per_year = /under|<\s*10/i.test(r) ? "Under 10" : /50\+|over 50/i.test(r) ? "Over 50" : /31|50/.test(r) ? "31-50"
      : rn ? (rn < 10 ? "Under 10" : rn <= 30 ? "10-30" : rn <= 50 ? "31-50" : "Over 50") : "10-30";
    return { values: v, assumed, guessed };
  }

  // ------------------------------------------------------------------ our verdict -> their card
  const PILL = { High: "sure", Medium: "hmm", Low: "guess" };
  const RANK = { sure: 0, hmm: 1, guess: 2 };
  const worst = (...cs) => cs.filter(Boolean).sort((a, b) => RANK[b] - RANK[a])[0] || "hmm";
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
  const sentence = (s) => (s ? cap(String(s).trim().replace(/\.?$/, ".")) : "");
  const lenText = (x) => (x === 0 ? "Standard" : `${x > 0 ? "+" : "−"}${Math.abs(x)} in ${x > 0 ? "longer" : "shorter"}`);
  const lieText = (x) => (x === 0 ? "Standard" : `${x > 0 ? "+" : "−"}${Math.abs(x)}° ${x > 0 ? "upright" : "flat"}`);

  function verdict() {
    const rec = B.rec, st = B.state;
    if (!rec) return null;
    const best = rec.result.best, ru = rec.result.runner_up;
    const lines = st.evidence?.confidence?.lines || {}, short = st.evidence?.short_whys || {};
    const level = (k) => PILL[lines[k]?.level] || "hmm";
    const why = (k, own) => {
      const c = lines[k];
      const extra = c && c.level !== "High" && c.why ? ` ${sentence(c.why)}` : "";
      return `${sentence(short[k] || own)}${extra}`.trim();
    };
    const g = B.guessed;
    const shaftWhy = `${why("Shaft", best.why.shaft)}${g.tempo ? " I never saw your tempo, so the shaft model is a guess until I watch a swing." : ""}${B.assumed.length ? ` Assumes ${B.assumed.join(", ")}: tell me if not.` : ""}`;
    const close = st.evidence?.confidence?.close_call;
    const cmp = st.evidence?.head_comparison;
    const cc = close ? { t: close.text.split(" is inside")[0], p: close.text.slice(close.text.indexOf(" is inside") + 1).replace(/^is inside/, "It’s inside") }
      : ru ? { t: `${best.head_name.replace("Iron ", "")} vs ${ru.head_name.replace("Iron ", "")}`, p: cmp?.tradeoff || "The runner-up head. A demo bay settles it in ten balls." }
      : null;
    return {
      head: { v: best.head_name, ru: ru ? ru.head_name.replace("Iron ", "") : "", why: why("Head", best.why.head), conf: level("Head") },
      shaft: { v: `${best.shaft_model} · ${best.flex_label}`, why: shaftWhy,
               conf: worst(level("Shaft"), level("Shaft model"), g.tempo ? "guess" : null) },
      length: { v: lenText(best.length_in), why: why("Length", best.why.length), conf: level("Length") },
      lie: g.lie ? { v: "Standard", why: "No wrist-to-floor, so standard is the safe guess. A tape fixes this in a minute.", conf: "guess" }
                 : { v: lieText(best.lie_deg), why: why("Lie", best.why.lie), conf: level("Lie") },
      grip: { v: cap(best.grip), why: why("Grip", best.why.grip), conf: level("Grip") },
      cc: cc || { t: "", p: "" },
      build: best.purchase,
      trust: st.evidence?.trust || null,
    };
  }

  // the assistant collected the answers on the server: fill only what the call never covers, and say so
  async function serverSide() {
    const st = await api("/api/session");
    const p = st.profile || {}, fill = {}, guessed = {};
    if (p.wrist_to_floor_cm == null && p.height_cm) { fill.wrist_to_floor_cm = (await api(`/api/lie-neutral?height_cm=${p.height_cm}`)).wrist_to_floor_cm; guessed.lie = true; }
    if (!p.tempo) { fill.tempo = "moderate"; guessed.tempo = true; }
    if (!p.flight) { fill.flight = "mid"; guessed.flight = true; }
    if (Object.keys(fill).length) await api("/api/session/profile", { method: "PUT", json: { values: fill } });
    B.assumed = []; B.guessed = { ...B.guessed, ...guessed };
  }

  // send the answers, get the fit, redraw
  async function sync(S) {
    await ensureSession();
    if (window.FitterAgent && window.FitterAgent.used()) {
      await serverSide();
      const r = await api("/api/session/recommendation", { method: "POST" });
      if (!r.tool_result.ok) throw new Error(`I still need: ${(r.tool_result.missing || []).join(", ").replace(/_/g, " ")}.`);
      B.state = r.state; B.rec = r.state.full_recommendation;
      window.FitterUI?.renderVerdict();
      return verdict();
    }
    const a = await answers(S);
    B.assumed = a.assumed; B.guessed = a.guessed;
    const missing = ["score_value", "height_cm", "glove"].filter((k) => a.values[k] == null);
    if (a.values.speed_mph == null && a.values.carry_yd == null) missing.unshift("7-iron carry or speed");
    if (missing.length) throw new Error(`I still need: ${missing.map((m) => m.replace("_value", "").replace("_cm", "").replace("_", " ")).join(", ")}.`);
    await api("/api/session/profile", { method: "PUT", json: { values: a.values } });
    const r = await api("/api/session/recommendation", { method: "POST" });
    if (!r.tool_result.ok) throw new Error(`I still need: ${(r.tool_result.missing || []).join(", ").replace(/_/g, " ")}.`);
    B.state = r.state; B.rec = r.state.full_recommendation;
    window.FitterUI?.renderVerdict();
    return verdict();
  }

  // the assistant shares this session: same answers, same fit
  async function session() { await ensureSession(); return B.token; }
  function setState(st) { B.state = st; B.rec = st.full_recommendation || B.rec; }

  return { verdict, sync, session, api, setState, state: () => B.state, _debug: () => ({ token: B.token, rec: B.rec && B.rec.result.best, assumed: B.assumed, guessed: B.guessed }) };
})();
