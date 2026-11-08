"use strict";

// Step 3: swing rhythm from up to three face-on swing videos, and 7-iron clubhead speed kept separate.
//   tempo  - MediaPipe Pose (on device, IMAGE mode) tracks the hands frame by frame; only the hand-height
//            track goes to the server, where the kit's camera/tempo.py finds takeaway, top and impact.
//            The golfer reviews each swing on playback, can drag the three markers, and accepts or retakes it.
//            Accepted swings are combined (median) by the kit's tempo rule. Tempo is evidence for the shaft
//            model only; it fills the tempo answer only when the golfer taps "Use … as my tempo".
//   speed  - from a simulator or launch monitor (typed, optionally read off a screenshot that stays on
//            the phone), or the kit's carry fallback. Never derived from body tempo.
// Uses globals from app.js.

window.Swing = (() => {
  const WANT = 3;
  const K = { result: null, track: null, stream: null, busy: false, recorder: null, videoUrl: null, duration: 0,
              events: null, accepted: [], combined: null };
  const status = (t) => ($("#swing-status").textContent = t);
  const seeked = (v) => new Promise((ok) => {
    const done = () => { clearTimeout(timer); v.removeEventListener("seeked", done); ok(); };
    const timer = setTimeout(done, 3000);
    v.addEventListener("seeked", done);
  });

  function open() {
    $("#swing").hidden = false;
    $("#swing").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function progress() {
    const n = K.accepted.length, cur = Math.min(n + 1, WANT);
    $("#swing-progress").innerHTML = `<span>${n >= WANT ? `${WANT} swings recorded` : `Swing ${cur} of ${WANT}`}</span>
      <span class="pips" aria-hidden="true">${Array.from({ length: WANT }, (_, i) => `<i class="${i < n ? "on" : i === n ? "cur" : ""}"></i>`).join("")}</span>`;
    $("#swing-record").lastChild.textContent = n ? "Record next swing" : "Record swing";     // button: svg + text node
    $("#swing-actions").hidden = n >= WANT || !!K.result;
  }

  // frame rate from the decoder's own frame times, when the browser exposes them
  async function estimateFps(v) {
    if (!v.requestVideoFrameCallback) return 30;
    const times = [];
    await new Promise((ok) => {
      const stop = setTimeout(ok, 1500);
      const cb = (_, meta) => { times.push(meta.mediaTime); if (times.length >= 12) { clearTimeout(stop); ok(); } else v.requestVideoFrameCallback(cb); };
      v.requestVideoFrameCallback(cb);
      v.play().catch(ok);
    });
    v.pause();
    const d = times.slice(1).map((t, i) => t - times[i]).filter((x) => x > 0).sort((a, b) => a - b);
    return d.length ? Math.min(240, Math.max(10, 1 / d[Math.floor(d.length / 2)])) : 30;
  }

  async function analyseVideo(blob) {
    if (K.busy) return;
    K.busy = true;
    K.result = null; K.events = null;
    $("#swing-result").hidden = true;
    $("#swing-actions").hidden = true;
    if (K.videoUrl) URL.revokeObjectURL(K.videoUrl);
    K.videoUrl = URL.createObjectURL(blob);           // kept on this phone for playback, never uploaded
    try {
      status("Loading body tracking…");
      try { await window.Pose.loadImageMode(); }       // per-frame, unsmoothed: smoothing would delay the takeaway
      catch { throw new Error("Body tracking couldn't load on this device, so we can't time the swing. Check your connection and try again, or continue without it."); }
      const v = document.createElement("video");
      v.muted = true; v.playsInline = true; v.preload = "auto";
      v.src = K.videoUrl;
      await new Promise((ok, bad) => { v.onloadeddata = ok; v.onerror = () => bad(new Error("This video can't be played in your browser. Try an MP4 or WebM file.")); });
      let dur = v.duration;
      if (!isFinite(dur)) { v.currentTime = 1e7; await seeked(v); dur = v.currentTime; }   // recorded WebM has no duration until seeked
      if (!(dur > 0.5)) throw new Error("The video is too short. Record one full swing, from address to finish.");
      K.duration = dur;
      const fps = await estimateFps(v);
      const step = 1 / Math.min(fps, 120);
      const t = [], x = [], y = [], vis = [], body = [];
      const seg = (P, a, b) => Math.hypot((P[a].x - P[b].x) * v.videoWidth, (P[a].y - P[b].y) * v.videoHeight);
      for (let ts = 0, i = 0; ts <= dur; ts += step, i++) {
        v.currentTime = ts;
        await seeked(v);
        const P = window.Pose.detectImage(v);
        const hands = P ? [P[15], P[16]].filter((p) => p.v >= 0.5) : [];
        t.push(v.currentTime * 1000);
        y.push(hands.length ? hands.reduce((a, p) => a + p.y, 0) / hands.length * v.videoHeight : 0);
        x.push(hands.length ? hands.reduce((a, p) => a + p.x, 0) / hands.length * v.videoWidth : 0);
        if (P) for (const [sh, hp, kn, an] of [[11, 23, 25, 27], [12, 24, 26, 28]]) {
          if ([sh, hp, kn, an].every((k) => P[k].v >= 0.6)) body.push(seg(P, sh, hp) + seg(P, hp, kn) + seg(P, kn, an));
        }
        vis.push(hands.length ? Math.max(...hands.map((p) => p.v)) : 0);
        if (i % 5 === 0) status(`Tracking your hands… ${Math.round(100 * ts / dur)} %`);
      }
      K.track = { t_ms: t, y, visibility: vis, time_scale: Number($("#swing-speed").value) };
      // for the EXPERIMENTAL clubhead-speed range only: hand x, body length in px, the golfer's height
      const heightCm = S.state?.measurement?.height_cm || S.state?.profile?.height_cm || null;
      const bodyPx = body.length >= 5 ? body.sort((a, b) => a - b)[Math.floor(body.length / 2)] : null;
      K.speedInput = { x, body_px: bodyPx, height_cm: heightCm };
      status("Finding takeaway, top and impact…");
      K.result = await api("/api/swing/tempo", { method: "POST", json: { ...K.track, ...K.speedInput, answers: S.state?.profile || null } });
      const at = (frame) => K.result.track.t[Math.min(frame, K.result.track.t.length - 1)] / K.track.time_scale;   // video seconds
      K.events = { takeaway: at(K.result.takeaway_frame), top: at(K.result.top_frame), impact: at(K.result.impact_frame) };
      K.result.adjusted = false;
      status("");
      render();
    } catch (ex) {
      // unclear swing events: no tempo is shown, only the reason and a retake
      K.result = null;
      $("#swing-result").innerHTML = `<p class="error">${esc(ex.message)}</p>
        <button type="button" class="btn big" id="swing-retake">Retake this swing</button>`;
      $("#swing-result").hidden = false;
      $("#swing-retake").addEventListener("click", retake);
      status("");
    } finally { K.busy = false; }
  }

  // EXPERIMENTAL clubhead-speed range from hand speed: shown, never used by the fit
  function speedBlock(e) {
    if (!e) return "";
    if (!e.available) return `<p class="note"><b>Experimental club speed:</b> not available. ${esc(e.reason)}</p>`;
    return `<div class="experimental"><span class="tag">Experimental · not used for your fit</span>
      <p><b>Club speed roughly ${e.club_mph_low}–${e.club_mph_high} mph</b> <span class="note">(hands about ${Math.round(e.hand_mph)} mph)</span></p>
      <p class="note">${esc(e.text)}</p>${e.warnings.map((w) => `<p class="note warn">${esc(w)}</p>`).join("")}</div>`;
  }

  function chart(r) {
    const { t, y } = r.track, w = 300, h = 90;
    const tmax = t[t.length - 1] || 1, ymin = Math.min(...y), ymax = Math.max(...y), span = ymax - ymin || 1;
    const X = (v) => (v / tmax) * w, Y = (v) => 6 + ((v - ymin) / span) * (h - 12);     // y down = hands low
    const pts = t.map((v, i) => `${X(v).toFixed(1)},${Y(y[i]).toFixed(1)}`).join(" ");
    const mark = (sec, label) => { const x = X(sec * K.track.time_scale); return `<line x1="${x}" x2="${x}" y1="0" y2="${h}" class="ev-line"/><text x="${x + 3}" y="${h - 3}">${label}</text>`; };
    return `<svg class="swing-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Hand height through the swing, with the takeaway, top and impact markers">
      <polyline points="${pts}"/>${mark(K.events.takeaway, "takeaway")}${mark(K.events.top, "top")}${mark(K.events.impact, "impact")}</svg>`;
  }

  function render() {
    const r = K.result, n = K.accepted.length + 1;
    const box = $("#swing-result");
    const slider = (k, label) => `<label>${label}<input type="range" data-ev="${k}" min="0" max="${K.duration.toFixed(3)}" step="0.005" value="${K.events[k].toFixed(3)}"
        aria-label="${label} time"><output id="ev-${k}">${K.events[k].toFixed(2)} s</output></label>`;
    box.innerHTML = `<p class="kicker">Review swing ${n}</p>
      <video class="swing-play" id="swing-play" src="${K.videoUrl}" controls playsinline muted></video>
      ${chart(r)}
      <div class="markers">${slider("takeaway", "Takeaway")}${slider("top", "Top")}${slider("impact", "Impact")}</div>
      <p class="note">Drag a marker to show that frame. Move it if it isn't on the takeaway, the top of the backswing or impact.</p>
      <div class="results">
        <div class="metric"><div class="k">Backswing</div><div class="v">${r.backswing_s.toFixed(2)} s</div><div class="d">downswing ${r.downswing_s.toFixed(2)} s · ${esc(r.ratio)}:1</div></div>
        <div class="metric"><div class="k">Tempo</div><div class="v">${esc(r.tempo)}</div><div class="d">${r.total_s.toFixed(2)} s total · ${esc(r.fps)} fps${r.adjusted ? " · your markers" : ""}</div></div>
      </div>
      <p class="note">${esc(r.text)}</p>
      ${r.tempo_effect ? `<p class="effect"><b>Shaft and ball height:</b> ${esc(r.tempo_effect.text)}</p>` : ""}
      ${r.warnings.map((w) => `<p class="error">${esc(w)}</p>`).join("")}
      ${speedBlock(r.speed_experimental)}
      <div class="row">
        <button type="button" class="btn quiet" id="swing-retake">Retake</button>
        <button type="button" class="btn" id="swing-accept">Use swing ${n}</button>
      </div>`;
    box.hidden = false;
    $("#swing-retake").addEventListener("click", retake);
    $("#swing-accept").addEventListener("click", accept);
    const play = $("#swing-play");
    $$("[data-ev]", box).forEach((s) => {
      s.addEventListener("input", () => { $(`#ev-${s.dataset.ev}`).textContent = `${Number(s.value).toFixed(2)} s`; play.currentTime = Number(s.value); });
      s.addEventListener("change", () => adjust(s.dataset.ev, Number(s.value)));
    });
    progress();
  }

  async function adjust(which, sec) {
    K.events[which] = sec;
    const ts = K.track.time_scale;
    try {
      const r = await api("/api/swing/events", { method: "POST", json: { takeaway_s: K.events.takeaway * ts, top_s: K.events.top * ts,
        impact_s: K.events.impact * ts, fps: K.result.fps, answers: S.state?.profile || null } });
      K.result = { ...K.result, ...r, track: K.result.track };
      render();
    } catch (ex) { status(ex.message); }
  }

  function retake() {
    K.result = null; K.events = null;
    $("#swing-result").hidden = true;
    status("");
    progress();
    $("#swing-actions").hidden = false;
  }

  async function accept(e) {
    if (!e.isTrusted) return;
    const r = K.result;
    K.accepted.push({ backswing_s: r.backswing_s, downswing_s: r.downswing_s, fps: r.fps, adjusted: !!r.adjusted, tempo: r.tempo, total_s: r.total_s,
                      hand_mph: r.speed_experimental?.available ? r.speed_experimental.hand_mph : null });
    K.result = null;
    $("#swing-result").hidden = true;
    await summarise();
    progress();
    if (K.accepted.length < WANT) status(`Swing ${K.accepted.length} saved. Take your address position for swing ${K.accepted.length + 1}.`);
    else status("Three swings recorded.");
  }

  async function summarise() {
    const box = $("#swing-summary");
    if (!K.accepted.length) { box.hidden = true; return; }
    try {
      K.combined = await api("/api/swing/combine", { method: "POST", json: { swings: K.accepted, answers: S.state?.profile || null } });
    } catch (ex) { box.innerHTML = `<p class="error">${esc(ex.message)}</p>`; box.hidden = false; return; }
    const c = K.combined, mine = S.state?.profile?.tempo, saved = S.state?.evidence?.swing_test;
    box.innerHTML = `<ul class="accepted">${K.accepted.map((a, i) => `<li><span>Swing ${i + 1}: ${a.total_s.toFixed(2)} s · ${esc(a.tempo)}${a.adjusted ? " · adjusted" : ""}</span>
        <button type="button" class="link" data-drop="${i}" aria-label="Remove swing ${i + 1}">Remove</button></li>`).join("")}</ul>
      <div class="results">
        <div class="metric"><div class="k">Your rhythm</div><div class="v">${esc(c.tempo)}</div><div class="d">${c.total_s.toFixed(2)} s · ${esc(c.ratio)}:1 · median of ${c.swings}</div></div>
        <div class="metric"><div class="k">Consistency</div><div class="v">${c.swings > 1 ? `±${(c.spread_s / 2).toFixed(2)} s` : "–"}</div><div class="d">${c.swings > 1 ? "spread of total time" : "needs 2+ swings"}</div></div>
      </div>
      <p class="note">${esc(c.text)}</p>
      ${c.tempo_effect ? `<p class="effect"><b>Shaft and ball height:</b> ${esc(c.tempo_effect.text)}</p>` : ""}
      ${c.warnings.map((w) => `<p class="note warn">${esc(w)}</p>`).join("")}
      ${speedBlock(c.speed_experimental)}
      <div class="row">
        <button type="button" class="btn quiet" id="swing-save">${saved ? "Saved ✓" : "Save to my fitting"}</button>
        ${mine !== c.tempo ? `<button type="button" class="btn" id="swing-use">Use “${esc(c.tempo)}” as my tempo</button>` : ""}
      </div>`;
    box.hidden = false;
    renderSpeed();
    $("#swing-save").addEventListener("click", save);
    $("#swing-use")?.addEventListener("click", useTempo);
    $$("[data-drop]", box).forEach((b) => b.addEventListener("click", async () => {
      K.accepted.splice(Number(b.dataset.drop), 1); await summarise(); progress();
    }));
  }

  async function save(e) {
    if (!e.isTrusted) return;
    try {
      applyState(await api("/api/session/swings", { method: "POST", json: { swings: K.accepted } }));
      status("Saved: your swing rhythm is now part of the evidence for your shaft.");
      summarise();
    } catch (ex) { status(ex.message); }
  }

  async function useTempo(e) {
    if (!e.isTrusted) return;                  // the golfer chooses; the measurement never changes the answer by itself
    try {
      applyState(await api("/api/session/profile", { method: "PUT", json: { values: { tempo: K.combined.tempo } } }));
      applyState(await api("/api/session/swings", { method: "POST", json: { swings: K.accepted } }));
      status(`Your tempo is now “${K.combined.tempo}”.`);
      summarise();
    } catch (ex) { status(ex.message); }
  }

  // ------------------------------------------------------------------ record a swing
  async function record() {
    if (K.busy) return;
    const view = $("#swing-view"), video = $("#swing-video"), count = $("#swing-countdown");
    try {
      K.stream = await navigator.mediaDevices.getUserMedia({ audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, frameRate: { ideal: 60 } } });
    } catch (ex) {
      status(ex.name === "NotAllowedError" ? "Camera access is blocked. Allow it in your browser settings, or upload a video instead." : "No camera found. Upload a video instead.");
      return;
    }
    view.hidden = false;
    video.srcObject = K.stream;
    await video.play().catch(() => {});
    status("Take your address position.");
    for (let n = 3; n > 0; n--) {
      count.hidden = false; count.textContent = String(n);
      emit("fitter:quiet");
      await new Promise((ok) => setTimeout(ok, 1000));
    }
    count.textContent = "●"; status("Recording… swing now.");
    emit("fitter:camera", { key: "swing_recording", instruction: "Recording a swing." });
    const chunks = [];
    const type = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm", "video/mp4"].find((t) => window.MediaRecorder?.isTypeSupported?.(t)) || "";
    K.recorder = new MediaRecorder(K.stream, type ? { mimeType: type } : undefined);
    K.recorder.ondataavailable = (ev) => ev.data.size && chunks.push(ev.data);
    const stopped = new Promise((ok) => (K.recorder.onstop = ok));
    K.recorder.start(250);
    const quiet = setInterval(() => emit("fitter:quiet"), 500);           // keeps the assistant from talking over the swing
    await new Promise((ok) => setTimeout(ok, 6000));
    clearInterval(quiet);
    K.recorder.stop();
    await stopped;
    stopCamera();
    count.hidden = true; view.hidden = true;
    emit("fitter:camera", { key: "swing_recorded", instruction: "Swing recorded. Working out the timing." });
    analyseVideo(new Blob(chunks, { type: K.recorder.mimeType || "video/webm" }));
  }

  function stopCamera() {
    if (K.stream) K.stream.getTracks().forEach((tr) => tr.stop());
    K.stream = null;
    $("#swing-video").srcObject = null;
  }

  // ------------------------------------------------------------------ 7-iron clubhead speed (separate from tempo)
  function renderSpeed() {
    const p = S.state?.profile || {};
    const src = { simulator: "from a simulator", launch_monitor: "from a launch monitor" }[p.speed_source];
    $("#speed-now").innerHTML = p.speed_or_carry === "speed" && p.speed_mph
      ? `<p class="note"><b>Now using:</b> ${esc(p.speed_mph)} mph club speed, ${src ? esc(src) : "from your answer"}.</p>`
      : p.carry_yd ? `<p class="note"><b>Now using:</b> an estimate from your ${esc(p.carry_yd)} yd carry (the kit's fallback when no speed is measured).</p>`
      : `<p class="note warn">No 7-iron speed or carry yet.</p>`;
    const e = K.combined?.speed_experimental;
    if (e?.available) $("#speed-now").innerHTML += `<p class="note">For comparison only: your swing video suggests roughly ${e.club_mph_low}–${e.club_mph_high} mph (experimental, not used).</p>`;
  }
  $("#sim-shot").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const img = $("#sim-shot-img");
    if (img.src) URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(f);              // shown on this phone only; not uploaded or read automatically
    img.hidden = false; $("#sim-shot-note").hidden = false;
    $("#sim-speed").focus();
  });
  $("#sim-save").addEventListener("click", async () => {
    const v = Number($("#sim-speed").value);
    if (!(v >= 40 && v <= 130)) { $("#sim-status").textContent = "Enter a 7-iron club speed between 40 and 130 mph (club speed, not ball speed)."; return; }
    try {
      applyState(await api("/api/session/profile", { method: "PUT", json: { values: { speed_or_carry: "speed", speed_mph: v, speed_source: $("#sim-source").value } } }));
      $("#sim-status").textContent = `Saved: ${v} mph ${$("#sim-source").value === "simulator" ? "from a simulator" : "from a launch monitor"}.`;
    } catch (ex) { $("#sim-status").textContent = ex.message; }
  });
  document.addEventListener("fitter:state", renderSpeed);

  $("#swing-open").addEventListener("click", open);
  $("#swing-record").addEventListener("click", record);
  $("#swing-input").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) analyseVideo(f); });
  window.addEventListener("pagehide", stopCamera);
  document.addEventListener("fitter:screen", (e) => { if (e.detail !== "swing-screen") stopCamera(); });
  document.addEventListener("fitter:reset", () => { K.accepted = []; K.result = null; K.combined = null; $("#swing-summary").hidden = true; $("#swing-result").hidden = true; progress(); });
  progress();
  renderSpeed();

  return { open, _debug: () => JSON.parse(JSON.stringify({ result: K.result ? { ...K.result, track: undefined } : null, busy: K.busy,
    accepted: K.accepted, combined: K.combined, events: K.events })) };
})();
