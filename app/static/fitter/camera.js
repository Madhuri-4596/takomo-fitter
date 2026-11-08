"use strict";

// Real camera for the fitter frontend's measure and swing screens (instead of their drawn, simulated camera).
//   measure: live video in their #mcam card, MediaPipe body tracking on the device, their pill and instruction line
//            driven by what tracking actually sees; when the golfer stands still with head, wrist and feet in view it
//            captures and measures wrist-to-floor scaled from their height (only three points are sent, never the
//            image), then shows it on their read-back (That's right / Say again).
//   swing:   the same camera in their #cam card; tap (or say "ready") -> 3-2-1 -> records one swing on the phone ->
//            hand-height track -> the kit's tempo analysis on the server (only the track is sent) -> their tempo
//            readout. Three swings are combined and saved as the tempo evidence.
//   clips:   an uploaded swing clip is checked for real (frame rate, hands seen, a full swing) on their clip sheet.
// The assistant is told what the camera actually reports, so it never describes what it can't see.
window.FitterCamera = (() => {
  const $ = (id) => document.getElementById(id);
  const UI = () => window.FitterUI;
  const api = (path, opts) => window.FitterBackend.api(path, opts);
  const say = (text, speak) => window.FitterAgent?.notify?.(text, speak);
  const LM = { nose: 0, leye: 2, reye: 5, lsh: 11, rsh: 12, lwr: 15, rwr: 16, lhip: 23, rhip: 24, lank: 27, rank: 28, lheel: 29, rheel: 30, lfoot: 31, rfoot: 32 };
  const C = { stream: null, video: null, overlay: null, raf: null, mode: null, history: [], readySince: 0, busy: false,
              last: null, swings: [], plan: 3, measured: null, lastKey: null, facing: "user", mirror: true };
  // display only: the front camera is shown like a mirror (as every selfie view is); landmarks stay in camera coordinates
  const mx = (x) => (C.mirror ? 1 - x : x);

  // ------------------------------------------------------------------ camera in their card
  async function getStream() {
    // no fixed size: a requested size makes the browser crop the sensor. resizeMode "none" asks for the native frame.
    C.stream = await navigator.mediaDevices.getUserMedia({ audio: false,
      video: { facingMode: { ideal: C.facing }, resizeMode: { ideal: "none" }, width: { ideal: 1920 }, frameRate: { ideal: 60 } } });
    const track = C.stream.getVideoTracks()[0];
    try {                                             // widest view the camera allows (phones that expose zoom)
      const z = track?.getCapabilities?.().zoom;
      if (z && z.min < (track.getSettings().zoom ?? z.min + 1)) await track.applyConstraints({ advanced: [{ zoom: z.min }] });
    } catch { /* zoom not supported */ }
    // laptops' webcams often don't say which way they face: treat anything not clearly the back camera as the front one
    const f = C.stream.getVideoTracks()[0]?.getSettings?.().facingMode;
    C.mirror = f !== "environment";
    document.querySelectorAll("video.realcam").forEach((v) => v.classList.toggle("mirror", C.mirror));
  }
  // front (golfer sees themselves) <-> back (someone else holds the phone)
  async function flipCamera() {
    if (C.frozen || C.busy) return;
    C.facing = C.facing === "user" ? "environment" : "user";
    if (C.stream) C.stream.getTracks().forEach((t) => t.stop());
    try { await getStream(); } catch { C.facing = C.facing === "user" ? "environment" : "user"; await getStream(); }
    if (C.video) { C.video.srcObject = C.stream; await C.video.play().catch(() => {}); }
  }
  async function openCamera(card) {
    if (!C.stream) await getStream();
    let v = card.querySelector("video.realcam");
    if (!v) {
      v = document.createElement("video"); v.className = "realcam"; v.playsInline = true; v.muted = true;
      const ov = document.createElement("canvas"); ov.className = "realcam-overlay";
      card.insertBefore(ov, card.firstChild); card.insertBefore(v, card.firstChild);
      const fb = document.createElement("button");
      fb.type = "button"; fb.className = "realcam-flip"; fb.setAttribute("aria-label", "Switch camera");
      fb.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><path d="M9 13.5a3 3 0 0 1 5-2.2M15 12.5a3 3 0 0 1-5 2.2"/><path d="M14 9.5v2h-2M10 16.5v-2h2"/></svg>';
      fb.addEventListener("click", (e) => { e.stopPropagation(); flipCamera(); });
      card.appendChild(fb);
    }
    v.classList.toggle("mirror", C.mirror);
    v.srcObject = C.stream;
    await v.play().catch(() => {});
    fitCard(card, v);
    v.onresize = () => fitCard(card, v);
    card.classList.add("live");
    C.video = v; C.overlay = card.querySelector("canvas.realcam-overlay");
    return v;
  }
  // the card takes the camera's own shape, so the full frame shows head to feet with no crop and no bars
  function fitCard(card, v) {
    if (!v.videoWidth || !v.videoHeight) return;
    const r = v.videoWidth / v.videoHeight;
    card.style.aspectRatio = `${v.videoWidth} / ${v.videoHeight}`;
    card.style.width = `min(100%, calc(52vh * ${r.toFixed(4)}))`;      // a tall picture stays within the card's 52vh
  }
  function stopCamera() {
    cancelAnimationFrame(C.raf); C.raf = null;
    if (C.stream) C.stream.getTracks().forEach((t) => t.stop());
    C.stream = null; C.mode = null;
    document.querySelectorAll(".cam.live").forEach((c) => { c.classList.remove("live"); c.style.aspectRatio = ""; c.style.width = ""; });
  }

  const seen = (P, i) => (P && P[i] && P[i].v >= 0.6 ? P[i] : null);
  function drawSkeleton(P, color) {
    const c = C.overlay, v = C.video;
    if (!c || !v) return;
    const w = c.width = c.clientWidth, h = c.height = c.clientHeight;
    const ctx = c.getContext("2d"); ctx.clearRect(0, 0, w, h);
    if (!P) return;
    // map from video pixels to the card (object-fit: contain: the whole frame is visible)
    const vw = v.videoWidth || 1, vh = v.videoHeight || 1, s = Math.min(w / vw, h / vh);
    const ox = (w - vw * s) / 2, oy = (h - vh * s) / 2, X = (p) => ox + mx(p.x) * vw * s, Y = (p) => oy + p.y * vh * s;
    ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.lineCap = "round";
    for (const [a, b] of [[11, 12], [11, 23], [12, 24], [23, 24], [11, 13], [13, 15], [12, 14], [14, 16], [23, 25], [25, 27], [24, 26], [26, 28]]) {
      if (seen(P, a) && seen(P, b)) { ctx.beginPath(); ctx.moveTo(X(P[a]), Y(P[a])); ctx.lineTo(X(P[b]), Y(P[b])); ctx.stroke(); }
    }
    ctx.fillStyle = "#FF6E01";
    for (const i of [15, 16]) if (seen(P, i)) { ctx.beginPath(); ctx.arc(X(P[i]), Y(P[i]), 5, 0, 7); ctx.fill(); }
  }

  // ------------------------------------------------------------------ measuring: what tracking actually sees
  function headTop(P) {                       // no landmark marks the top of the head: eyes up by ~0.54 x eye-to-shoulder
    const le = seen(P, LM.leye), re = seen(P, LM.reye), ls = seen(P, LM.lsh), rs = seen(P, LM.rsh);
    if (!(le || re) || !(ls || rs)) return null;
    const ey = ((le || re).y + (re || le).y) / 2, ex = ((le || re).x + (re || le).x) / 2;
    const sy = ((ls || rs).y + (rs || ls).y) / 2;
    return { x: ex, y: ey - 0.54 * (sy - ey) };
  }
  // MediaPipe's wrist point is the joint, above the crease: the crease is estimated part-way toward the hand
  // points (index and pinky). On the sample photo this lands within ~1.5 cm; the golfer can drag it.
  const CREASE_TOWARD_HAND = 0.75;
  function lowerWrist(P) {
    const sides = [[LM.lwr, 17, 19], [LM.rwr, 18, 20]].map(([w, a, b]) => ({ w: seen(P, w), h: [seen(P, a), seen(P, b)].filter(Boolean) })).filter((s) => s.w);
    if (!sides.length) return null;
    const s = sides.reduce((m, x) => (x.w.y > m.w.y ? x : m));
    if (!s.h.length) return s.w;
    const hx = s.h.reduce((a, p) => a + p.x, 0) / s.h.length, hy = s.h.reduce((a, p) => a + p.y, 0) / s.h.length;
    return { x: s.w.x + CREASE_TOWARD_HAND * (hx - s.w.x), y: s.w.y + CREASE_TOWARD_HAND * (hy - s.w.y), v: s.w.v };
  }
  // the foot points sit on top of the foot, not on the ground: the floor is ~2 % of the body length below them
  function floorPoint(P, top) {
    const pts = [LM.lheel, LM.rheel, LM.lfoot, LM.rfoot].map((i) => seen(P, i)).filter(Boolean);
    if (!pts.length) return null;
    const low = pts.reduce((m, p) => (p.y > m.y ? p : m));
    const drop = top ? 0.02 * (low.y - top.y) : 0.004;
    return { x: low.x, y: Math.min(1, low.y + drop) };
  }
  function check(P) {
    if (!P) return { key: "nobody", text: "Step into the frame, head to feet, about three metres back." };
    const top = headTop(P), wr = lowerWrist(P), fl = floorPoint(P, top);
    if (!top || top.y < 0.01) return { key: "head", text: "Your head's cut off. Step back a little." };
    if (!fl || fl.y > 0.97) return { key: "feet", text: "I need your feet too. Step back or tilt the phone down." };
    if (!wr) return { key: "wrist", text: "I can't see a wrist. Let one arm hang by your side." };
    const hips = [seen(P, LM.lhip), seen(P, LM.rhip)].filter(Boolean);
    if (hips.length && wr.y < Math.min(...hips.map((h) => h.y)) - 0.03) return { key: "arms", text: "Let your arms hang, like you've given up on the round." };
    // still: wrist, nose and feet moved less than 1.2 % of the frame over the last ~0.8 s (at least 3 frames)
    const now = performance.now();
    C.history.push({ t: now, p: [wr, seen(P, LM.nose), fl] });
    while (C.history.length > 3 && now - C.history[0].t > 800) C.history.shift();
    let move = 0;
    for (let k = 0; k < 3; k++) {
      const ps = C.history.map((h) => h.p[k]).filter(Boolean);
      if (ps.length > 1) move = Math.max(move, Math.max(...ps.map((p) => p.y)) - Math.min(...ps.map((p) => p.y)), Math.max(...ps.map((p) => p.x)) - Math.min(...ps.map((p) => p.x)));
    }
    if (C.history.length < 3 || move > 0.012) return { key: "still", text: "Good. Now hold still for a second." };
    return { key: "ready", text: "Got you. Hold it…", points: { top, wr, fl } };
  }

  // The screen shows every framing change at once. The assistant only hears one when it has held for 2.5 s,
  // never the same one twice in a row, and at most one every 12 s, so tracking flicker can't flood the call.
  const NOTE = { stable_ms: 2500, gap_ms: 12000 };
  const note = { key: null, since: 0, lastSent: 0, lastKey: null };
  function framingNote(c) {
    const now = performance.now(), problem = ["nobody", "head", "feet", "wrist", "arms"].includes(c.key);
    if (c.key !== note.key) { note.key = c.key; note.since = now; return; }
    if (!problem || now - note.since < NOTE.stable_ms || c.key === note.lastKey || now - note.lastSent < NOTE.gap_ms) return;
    note.lastKey = c.key; note.lastSent = now;
    say(`[camera] ${c.text}`, true);
  }

  async function measure() {
    const card = $("mcam");
    if (!card) return;
    C.mode = "measure"; C.history = []; C.readySince = 0; C.busy = false; C.measured = null;
    $("mcam-pill").textContent = "Starting"; $("mcam-line-t").textContent = "Opening the camera…";
    const have = (window.FitterBackend.state?.() || {}).profile || {};
    say(have.height_cm
      ? `[app] The golfer is on the measuring screen and the camera is starting. Their height is already saved (${Math.round(have.height_cm)} cm): don't ask it. Just tell them to stand back, head to feet in frame, arms hanging.`
      : "[app] The golfer is on the measuring screen and the camera is starting. Height isn't saved yet: ask it now (\"How tall are you, in golf shoes?\"). The camera needs it to scale the wrist-to-floor, which it then measures by itself. One short sentence.", false);
    try {
      await openCamera(card);
      $("mcam-line-t").textContent = "Loading body tracking…";
      await window.Pose.load();
    } catch (e) {
      const denied = e && e.name === "NotAllowedError";
      UI().S.measMode = "tape";                       // no camera: the tape path (handedness is asked here)
      $("mcam-pill").textContent = denied ? "Blocked" : "No camera";
      $("mcam-line-t").textContent = denied ? "Camera blocked. Allow it in the browser, or use a tape." : "No camera found. Use a tape instead.";
      say(`[camera] ${denied ? "Camera access is blocked" : "No camera found"}. Say so in one short sentence and switch to the tape. One question per turn, in this order, skipping any already saved: height in golf shoes, then wrist-to-floor in golf shoes with arms hanging, then glove size, then right- or left-handed. Repeat each number in the next question instead of asking if it's right.`, true);
      return;
    }
    $("mcam-pill").textContent = "Looking";
    loopMeasure();
  }
  function loopMeasure() {
    if (C.mode !== "measure") return;
    const v = C.video;
    if (v && v.readyState >= 2 && !C.busy) {
      const P = window.Pose.detect(v);
      const c = check(P);
      drawSkeleton(P, c.key === "ready" ? "#2BB673" : "rgba(248,248,248,.85)");
      $("mcam").classList.toggle("ok", c.key === "ready");
      $("mcam-pill").textContent = c.key === "ready" ? "Reading" : "Looking";
      $("mcam-line-t").textContent = c.text;
      framingNote(c);
      C.lastKey = c.key;
      if (c.key === "ready") {
        C.readySince = C.readySince || performance.now();
        if (performance.now() - C.readySince > 700) capture(c.points);
      } else C.readySince = 0;
    }
    C.raf = requestAnimationFrame(loopMeasure);
  }

  async function capture(points) {
    const st = window.FitterBackend.state?.() || {};
    const h = st.profile?.height_cm;
    if (!h) {
      $("mcam-line-t").textContent = "Got you. Tell me your height and I'll measure.";
      if (C.lastKey !== "needheight") say("[camera] The golfer is in frame and still, but their height isn't saved yet. Ask it (in golf shoes): the camera needs it to measure the wrist-to-floor.", true);
      C.lastKey = "needheight"; C.readySince = 0;
      return;
    }
    C.busy = true;
    freeze(points);
    await remeasure(h, true);
  }

  // ---- the captured frame stays on screen with its three markers; drag one and the number updates
  function freeze(points) {
    const v = C.video, snap = document.createElement("canvas");
    snap.width = v.videoWidth; snap.height = v.videoHeight;
    const sc = snap.getContext("2d");
    if (C.mirror) { sc.translate(snap.width, 0); sc.scale(-1, 1); }
    sc.drawImage(v, 0, 0);
    C.frozen = { snap, pts: { top: { ...points.top }, wr: { ...points.wr }, fl: { ...points.fl } }, W: v.videoWidth, H: v.videoHeight, drag: null };
    cancelAnimationFrame(C.raf);
    v.pause();
    C.overlay.style.pointerEvents = "auto"; C.overlay.style.touchAction = "none";
    drawFrozen();
  }
  function frozenMap() {
    const c = C.overlay, F = C.frozen, w = c.clientWidth, h = c.clientHeight, s = Math.min(w / F.W, h / F.H);
    return { w, h, s, ox: (w - F.W * s) / 2, oy: (h - F.H * s) / 2 };
  }
  function drawFrozen() {
    const F = C.frozen, c = C.overlay;
    if (!F) return;
    const m = frozenMap(); c.width = m.w; c.height = m.h;
    const ctx = c.getContext("2d");
    ctx.drawImage(F.snap, m.ox, m.oy, F.W * m.s, F.H * m.s);
    const X = (p) => m.ox + mx(p.x) * F.W * m.s, Y = (p) => m.oy + p.y * F.H * m.s;
    ctx.strokeStyle = "rgba(255,110,1,.9)"; ctx.setLineDash([5, 4]); ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(X(F.pts.wr), Y(F.pts.wr)); ctx.lineTo(X(F.pts.wr), Y(F.pts.fl)); ctx.stroke(); ctx.setLineDash([]);
    for (const [k, label] of [["top", "Head top"], ["wr", "Wrist crease"], ["fl", "Floor"]]) {
      const p = F.pts[k];
      ctx.fillStyle = "#FF6E01"; ctx.strokeStyle = "#0B0B0B"; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(X(p), Y(p), 9, 0, 7); ctx.fill(); ctx.stroke();
      ctx.font = "600 12px Barlow, sans-serif"; ctx.fillStyle = "#F8F8F8"; ctx.strokeStyle = "rgba(0,0,0,.7)"; ctx.lineWidth = 3;
      ctx.strokeText(label, X(p) + 13, Y(p) + 4); ctx.fillText(label, X(p) + 13, Y(p) + 4);
    }
  }
  function nearestMarker(e) {
    const F = C.frozen, m = frozenMap(), r = C.overlay.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    let best = null, bd = 34;
    for (const k of ["top", "wr", "fl"]) {
      const p = F.pts[k], d = Math.hypot(m.ox + mx(p.x) * F.W * m.s - x, m.oy + p.y * F.H * m.s - y);
      if (d < bd) { bd = d; best = k; }
    }
    return { k: best, x, y, m };
  }
  document.addEventListener("pointerdown", (e) => {
    if (!C.frozen || e.target !== C.overlay) return;
    const n = nearestMarker(e);
    if (!n.k) return;
    e.preventDefault(); e.stopPropagation();
    C.frozen.drag = n.k; C.overlay.setPointerCapture(e.pointerId);
  }, true);
  document.addEventListener("pointermove", (e) => {
    const F = C.frozen;
    if (!F || !F.drag) return;
    const { x, y, m } = nearestMarker(e);
    F.pts[F.drag] = { x: mx(Math.min(1, Math.max(0, (x - m.ox) / (F.W * m.s)))), y: Math.min(1, Math.max(0, (y - m.oy) / (F.H * m.s))) };
    drawFrozen();
  }, true);
  document.addEventListener("pointerup", async () => {
    const F = C.frozen;
    if (!F || !F.drag) return;
    F.drag = null;
    const h = (window.FitterBackend.state?.() || {}).profile?.height_cm;
    if (h) await remeasure(h, false);
  }, true);

  async function remeasure(h, first) {
    const F = C.frozen, px = (p) => [p.x * F.W, p.y * F.H];
    try {
      const m = await api("/api/photo/measure-height", { method: "POST", json: {
        head_top: px(F.pts.top), wrist: px(F.pts.wr), floor: px(F.pts.fl), height_cm: h, footwear: "golf_shoes" } });
      await api("/api/session/measurement", { method: "POST", json: { height_cm: m.height_cm, wrist_to_floor_cm: m.wrist_to_floor_cm,
        source: "photo", detail: { calibration: "entered_height", entered_height_cm: h, footwear: "golf_shoes", ratio: m.ratio,
        warnings: m.warnings, assumed_error_cm: m.assumed_error_cm, head_top: "estimated from the eyes", image: "live camera" } } });
      C.measured = m;
      showReadback(m, first);
    } catch (e) {
      $("mcam-line-t").textContent = `${e.message} Drag the markers, or Say again to retake.`;
    }
  }
  function unfreeze() {
    C.frozen = null;
    if (C.overlay) { C.overlay.style.pointerEvents = "none"; }
    C.video?.play().catch(() => {});
  }

  function showReadback(m, first = true) {
    const inches = Math.round(m.wrist_to_floor_cm / 2.54);
    const S = UI().S;
    S.profile.wtf = { v: inches, src: "seen", range: 2 };
    S.justFilled = "wtf"; UI().renderMeasure();
    $("mcam-pill").textContent = "Mapped";
    $("mcam-line-t").textContent = "Drag a dot if it's not on your head top, wrist crease or the floor.";
    $("rb-big").textContent = UI().UNIT.inches(inches);
    $("rb-heard").textContent = `I see about ${UI().S.units === "imp" ? `${inches} inches` : `${Math.round(m.wrist_to_floor_cm)} centimetres`}, give or take an inch`;
    $("readback").hidden = false;
    if (first) say(`[camera] Measured from the camera: wrist-to-floor about ${inches} inches (${m.wrist_to_floor_cm} cm), an estimate scaled from their height, give or take an inch. The frozen photo shows three dots they can drag. Say the number in one sentence, mention they can drag a dot if one is off, and call confirm_measurement now (don't ask).`, true);
    else say(`[camera] They moved a marker: wrist-to-floor is now about ${inches} inches (${m.wrist_to_floor_cm} cm).`, false);
    $("rb-yes").onclick = () => confirmMeasurement();
    $("rb-again").onclick = () => { $("readback").hidden = true; unfreeze(); C.busy = false; C.readySince = 0; C.history = []; C.measured = null; $("mcam-pill").textContent = "Looking"; loopMeasure(); };
  }

  async function confirmMeasurement() {
    if (!C.measured) return { ok: false, error: "nothing_measured" };
    const st = await api("/api/session/measurement/confirm", { method: "POST" });
    window.FitterAgent?.apply?.(st);
    const inches = Math.round(C.measured.wrist_to_floor_cm / 2.54);
    const S = UI().S;
    S.profile.wtf = { v: inches, src: "seen", range: 2 }; S.justFilled = "wtf";
    $("readback").hidden = true; UI().renderMeasure();
    $("mcam-pill").textContent = "Mapped"; $("mcam-line-t").textContent = "Stay there. Same spot, same phone, 7-iron in hand.";
    const next = $("a-swing"); if (next) next.disabled = false;
    cancelAnimationFrame(C.raf); unfreeze(); C.mode = "measured";
    // seamless: the golfer is already in frame, so the swing step opens on its own once the profile is complete (the button goes sooner)
    const token = C.autoSwing = {};
    const tryAdvance = (left) => {
      if (C.autoSwing !== token || UI().screen() !== "measure" || UI().S.ended) return;
      const missing = (window.FitterBackend.state?.() || {}).missing_fields || [];
      if (missing.some(f => f === "glove")) { if (left > 0) setTimeout(() => tryAdvance(left - 1), 1500); return; }
      UI().startSwing(true);
    };
    setTimeout(() => tryAdvance(40), 2500);
    return { ok: true, saved: st.measurement, next: "Ask for glove size if you don't have it (not handedness: the swing shows it). Once it's saved the swing step opens by itself; they can also tap 'Grab my 7-iron'." };
  }

  function status() {
    const st = window.FitterBackend.state?.() || {};
    return { ok: true, camera: C.stream ? "live" : "off", mode: C.mode,
      status: st.measurement ? "confirmed" : C.measured ? "measured_awaiting_confirmation" : C.mode === "measure" ? "camera_live" : "not_started",
      instruction: $("mcam-line-t")?.textContent || "", measured: C.measured, confirmed: st.measurement || null,
      swings_recorded: C.swings.length, swing_plan: C.plan,
      note: "Describe only this. Head top is estimated from the eyes; the number is an estimate scaled from the golfer's height." };
  }

  // ------------------------------------------------------------------ swing: record on the phone, analyse the hands
  // true once the frame at the new time is ready; false if it never came (that frame is then skipped, never mis-timed)
  const seeked = (v, ms = 3000) => new Promise((ok) => { const t = setTimeout(() => ok(false), ms); v.addEventListener("seeked", () => { clearTimeout(t); ok(true); }, { once: true }); });
  // A video (filmed here or imported) is read on the phone: its real frame rate, then every frame by its own time,
  // tracked on a small copy (fast). Only the hand track goes to the server.
  async function analyseBlob(blob, timeScale = 1, onProgress) {
    await window.Pose.loadImageMode();
    const v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.preload = "auto"; v.src = URL.createObjectURL(blob);
    v.style.cssText = "position:fixed;left:0;top:0;width:2px;height:2px;opacity:0;pointer-events:none";
    document.body.appendChild(v);                    // some browsers only hand out frames for a video in the page
    try {
      await new Promise((ok, bad) => { v.onloadeddata = ok; v.onerror = () => bad(new Error("This video can't be played here. Try an MP4 or a MOV from your phone.")); });
      let dur = v.duration;
      if (!isFinite(dur)) { v.currentTime = 1e7; await seeked(v); dur = v.currentTime; v.currentTime = 0; await seeked(v); }
      if (!(dur > 0.5)) throw new Error("Too short. One full swing, address to finish.");
      if (dur > 90) throw new Error("That's a long video. Trim it to the swing (a few seconds) and try again.");
      const tr = { t: [], x: [], y: [], vis: [], body: [] };
      const fps0 = await probeFps(v);
      v.currentTime = 0; await seeked(v);
      await seekThrough(v, dur, tr, onProgress, fps0);
      if (tr.t.length < 10) throw new Error("Couldn't read frames from this video. Try an MP4 or a MOV.");
      let r = null;
      try { r = await sendTrack(tr, timeScale); } catch (e) { if (!tr.windowed) throw e; }
      if (tr.windowed && (!r || !plausible(r))) {          // the quick window missed it: read the whole video
        const all = { t: [], x: [], y: [], vis: [], body: [] }, { fine, dur: d } = tr.windowed;
        await readRange(v, 0, d, Math.max(fine, d / 600), all, onProgress, 0, 1);
        r = await sendTrack(all, timeScale);
      }
      return r;
    } finally { v.remove(); URL.revokeObjectURL(v.src); }
  }
  // the tracker works on a small copy of the frame: same landmarks (they're relative), much less to upload
  const small = document.createElement("canvas");
  function shrink(v) {
    const k = Math.min(1, 640 / Math.max(v.videoWidth, v.videoHeight));
    if (k === 1) return v;
    small.width = Math.round(v.videoWidth * k); small.height = Math.round(v.videoHeight * k);
    small.getContext("2d").drawImage(v, 0, 0, small.width, small.height);
    return small;
  }
  async function sendTrack(tr, timeScale = 1) {
    // frame rate from the frames' own timestamps (a low percentile of the real gaps between frames)
    const gaps = tr.t.slice(1).map((t, i) => t - tr.t[i]).filter((g) => g > 2).sort((p, q) => p - q);
    const fps = gaps.length ? Math.min(240, Math.max(10, 1000 / gaps[Math.floor(gaps.length * 0.1)])) : 30;
    const st = window.FitterBackend.state?.() || {};
    const r = await api("/api/swing/tempo", { method: "POST", json: { t_ms: tr.t, y: tr.y, x: tr.x, visibility: tr.vis, time_scale: timeScale,
      body_px: tr.body.length >= 5 ? tr.body.sort((p, q) => p - q)[Math.floor(tr.body.length / 2)] : null,
      height_cm: st.measurement?.height_cm || st.profile?.height_cm || null, answers: st.profile || null } });
    return { ...r, fps: Math.round(fps), seen: tr.vis.filter((q) => q >= 0.5).length / Math.max(1, tr.vis.length) };
  }
  function track(v, P, ms, tr) {
    const seg = (a, b) => Math.hypot((P[a].x - P[b].x) * v.videoWidth, (P[a].y - P[b].y) * v.videoHeight);
    const hands = P ? [P[15], P[16]].filter((p) => p.v >= 0.5) : [];
    tr.t.push(ms);
    tr.y.push(hands.length ? hands.reduce((a, p) => a + p.y, 0) / hands.length * v.videoHeight : 0);
    tr.x.push(hands.length ? hands.reduce((a, p) => a + p.x, 0) / hands.length * v.videoWidth : 0);
    tr.vis.push(hands.length ? Math.max(...hands.map((p) => p.v)) : 0);
    if (P) for (const [sh, hp, kn, an] of [[11, 23, 25, 27], [12, 24, 26, 28]]) if ([sh, hp, kn, an].every((k) => P[k].v >= 0.6)) tr.body.push(seg(sh, hp) + seg(hp, kn) + seg(kn, an));
  }
  // The video's real frame rate: play ~10 frames and read their own timestamps (no seeking, a fraction of a second).
  function probeFps(v) {
    if (!v.requestVideoFrameCallback) return Promise.resolve(30);
    return new Promise((ok) => {
      const ts = [];
      const finish = () => { clearTimeout(cap); v.pause();
        const g = ts.slice(1).map((t, i) => t - ts[i]).filter((d) => d > 0.0015);
        ok(g.length ? Math.min(240, Math.max(10, 1 / Math.min(...g))) : 30); };
      const cap = setTimeout(finish, 1500);
      const onFrame = (_, m) => { ts.push(m.mediaTime); if (ts.length >= 12) finish(); else v.requestVideoFrameCallback(onFrame); };
      v.requestVideoFrameCallback(onFrame);
      v.play().catch(finish);
    });
  }
  // Every frame, in order, by its own time: exact and repeatable. Slow motion is read at up to 120 fps; a long video is
  // read at a coarser step so it never takes more than ~600 frames.
  // An uploaded video: a quick look every 1/8 s finds the swing (the hands' highest point), then only the seconds
  // around it are read frame by frame, at the clip's own rate (up to 120 fps). Short clips are read whole.
  async function seekThrough(v, dur, tr, onProgress, fps = 30) {
    const fine = 1 / Math.min(fps, 120);
    if (dur <= 4.5) return readRange(v, 0, dur, fine, tr, onProgress, 0, 1);
    const look = { t: [], x: [], y: [], vis: [], body: [] };
    await readRange(v, 0, dur, 1 / 8, look, onProgress, 0, 0.3);
    let best = -1, by = Infinity;
    look.y.forEach((y, i) => { if (look.vis[i] >= 0.5 && y < by) { by = y; best = i; } });
    if (best < 0) return readRange(v, 0, dur, Math.max(fine, dur / 600), tr, onProgress, 0.3, 1);
    // back from the highest hands to where they were lowest just before: the address. Read from just before it, so the
    // window holds one swing (address, takeaway, top, impact) even when the video has several in a row.
    let i = best;
    while (i > 0 && look.vis[i - 1] >= 0.5 && look.y[i - 1] >= look.y[i] - 2 && look.t[best] - look.t[i - 1] < 3000) i--;
    const start = Math.max(0, look.t[i] / 1000 - 0.6), peak = look.t[best] / 1000;
    tr.windowed = { fine, dur };
    return readRange(v, start, Math.min(dur, peak + 1.2), fine, tr, onProgress, 0.3, 1);
  }
  async function readRange(v, from, to, step, tr, onProgress, p0, p1) {
    for (let ts = from, i = 0; ts <= to + 1e-6; ts += step, i++) {
      v.currentTime = ts;
      if (!(await seeked(v, 10000))) continue;
      track(v, window.Pose.detectImage(shrink(v)), v.currentTime * 1000, tr);
      if (onProgress && i % 6 === 0) onProgress(p0 + (p1 - p0) * (ts - from) / Math.max(0.001, to - from));
    }
  }



  // Live, like a video call: the golfer just swings. Every camera frame is copied (small) into a ring holding the last
  // few seconds; body tracking runs a few times a second for the on-screen skeleton and to spot a swing (hands at
  // address, then up to the shoulders and beyond). The frames around that swing are then read one by one, each with
  // its own camera timestamp: no tapping, no recording, no seeking.
  const SMALL = 384, RING_S = 4.2;
  const L = { pool: [], ring: [], head: 0, lastT: 0, lastDetect: 0, detectMs: 40, st: "idle", addrSince: 0, top: 0, topY: 0, upAt: 0, seen: 0 };
  function ringPush(v, ms) {
    const k = Math.min(1, SMALL / Math.max(v.videoWidth, v.videoHeight));
    const w = Math.round(v.videoWidth * k), h = Math.round(v.videoHeight * k);
    if (!L.pool.length) {
      L.warm = (L.warm || []).concat(ms);                    // the first frames tell the camera's frame rate
      if (L.warm.length < 15) return;
      const fps = 1000 * (L.warm.length - 1) / (L.warm[L.warm.length - 1] - L.warm[0]);
      L.n = Math.max(90, Math.min(300, Math.round(RING_S * Math.min(fps, 72))));
      L.pool = Array.from({ length: L.n }, () => document.createElement("canvas")); L.ring = new Array(L.n); L.fps = fps;
    }
    const RING = L.n, c = L.pool[L.head % RING];
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    c.getContext("2d").drawImage(v, 0, 0, w, h);            // the camera's own pixels (the mirror is display only)
    L.ring[L.head % RING] = { t: ms, c }; L.head++;
  }
  function frameTime(now, meta) {                            // the frame's own time when the browser gives it
    let ms = meta && meta.captureTime ? meta.captureTime : meta && meta.mediaTime ? meta.mediaTime * 1000 : now;
    if (!(ms > L.lastT)) ms = L.lastT + 1;
    L.lastT = ms; return ms;
  }
  function liveLoop() {
    const v = C.video;
    if (!v || C.mode !== "swing") return;
    const next = (now, meta) => {
      if (C.mode !== "swing" || C.video !== v) return;
      if (!C.busy && v.readyState >= 2) {
        const ms = frameTime(now, meta);
        ringPush(v, ms);
        // tracking blocks the page for a few tens of ms, so it runs only a few times a second: most frames stay in the ring
        const gap = L.st === "up" ? Infinity : L.st === "address" ? Math.max(200, 4 * L.detectMs) : Math.min(500, Math.max(120, 3 * L.detectMs));
        if (L.st === "up" && ms - L.upAt > 1300) { L.st = "idle"; L.addrSince = 0; readLiveSwing(L.upAt - 2600, L.upAt + 1300); }
        else if (now - L.lastDetect > gap) {
          const t0 = performance.now(), P = window.Pose.detect(v), took = performance.now() - t0;
          L.detectMs = Math.min(400, 0.5 * L.detectMs + 0.5 * took); L.lastDetect = performance.now();
          drawSkeleton(P, "rgba(248,248,248,.7)");
          watchSwing(P, ms);
        }
      }
      if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(next); else requestAnimationFrame((t) => next(t, null));
    };
    if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(next); else requestAnimationFrame((t) => next(t, null));
  }
  // address (hands still for 0.3 s) -> hands rise half a torso or more -> 1.3 s later the swing is over: read it.
  // Only movement counts, so short backswings work too; the analysis finds the exact takeaway, top and impact.
  function watchSwing(P, ms) {
    const ok = (i) => P && P[i] && P[i].v >= 0.5;
    if (!P || !ok(15) || !ok(16) || !(ok(11) || ok(12)) || !(ok(23) || ok(24))) {
      if (ms - L.seen > 2500 && L.st !== "up") { L.st = "idle"; L.addrSince = 0; setLine("Step into frame, whole body, 7-iron in hand."); }
      return;
    }
    L.seen = ms;
    const avg = (a, b) => (ok(a) && ok(b) ? (P[a].y + P[b].y) / 2 : ok(a) ? P[a].y : P[b].y);
    const hy = (P[15].y + P[16].y) / 2, torso = Math.max(0.05, avg(23, 24) - avg(11, 12));
    L.dbg = [+hy.toFixed(2), +torso.toFixed(2), L.st];
    if (L.st === "idle" || L.st === "address") {
      const still = L.lastHy != null && Math.abs(hy - L.lastHy) < 0.06 * torso;
      if (still) {
        if (!L.addrSince) { L.addrSince = ms; L.addrY = hy; }
        L.addrY = 0.7 * L.addrY + 0.3 * hy;
        if (L.st === "idle" && ms - L.addrSince > 300) { L.st = "address"; setLine(`Ready when you are. Swing ${C.swings.length + 1} of ${C.plan}.`); }
      } else if (L.st === "address" && L.addrY - hy > 0.5 * torso) {
        L.st = "up"; L.top = ms; L.topY = hy; L.upAt = ms; window.FitterAgent?.quiet?.(true);
        L.top = Math.max(L.addrSince, ms - 600);              // the takeaway began a little before the hands showed it
      } else if (L.st === "idle") L.addrSince = 0;
      else if (Math.abs(hy - L.addrY) > 0.25 * torso && hy > L.addrY) { L.st = "idle"; L.addrSince = 0; }   // moved away, not a swing
    } else if (L.st === "up") {
      // (no tracking while up: the frame loop reads the swing 1.3 s after the hands started up)
    }
    L.lastHy = hy;
  }
  const setLine = (t) => { const el = $("cam-line"); if (el && el.textContent !== t) el.textContent = t; };
  async function readLiveSwing(from, to) {
    if (C.busy || C.swings.length >= C.plan) return;
    const frames = L.ring.filter((f) => f && f.t >= from && f.t <= to).sort((a, b) => a.t - b.t);
    if (frames.length < 15) { window.FitterAgent?.quiet?.(false); return; }
    C.busy = true;                                           // the ring stops filling, so these frames stay as they are
    const n = C.swings.length + 1, v = C.video, dims = { videoWidth: v.videoWidth, videoHeight: v.videoHeight };
    $("cam-pill").textContent = `Reading · swing ${n} of ${C.plan}`;
    try {
      await window.Pose.loadImageMode();
      const tr = { t: [], x: [], y: [], vis: [], body: [] };
      for (const [i, f] of frames.entries()) {
        track(dims, window.Pose.detectImage(f.c), f.t - frames[0].t, tr);
        if (i % 10 === 0) { setLine(`Reading swing ${n}… ${Math.round((100 * i) / frames.length)} %`); await new Promise((ok) => setTimeout(ok, 0)); }
      }
      const r = await sendTrack(tr, 1);
      if (!plausible(r)) { setLine(`Didn't catch a full swing there. Swing ${n} when you're ready.`); return; }
      accept(r, n);
    } catch (e) {
      setLine(`Didn't catch a full swing there (${e.message.split(".")[0].toLowerCase()}). Swing ${n} when you're ready.`);
    } finally {
      C.busy = false; L.st = "idle"; L.lastHy = null; L.addrSince = 0; window.FitterAgent?.quiet?.(false);
      if (C.swings.length < C.plan) $("cam-pill").textContent = `Swing ${C.swings.length + 1} of ${C.plan}`;
    }
  }

  async function swing(cont) {
    const card = $("cam");
    if (!card) return;
    C.mode = "swing"; C.plan = Math.max(5, C.swings.length + 1);
    const S = UI().S; S.swingPlan = C.plan; S.swings = [];
    $("cam-pill").textContent = `Swing ${C.swings.length + 1} of ${C.plan}`; $("cam-line").textContent = "Opening the camera…";
    try {
      await openCamera(card);
      $("cam-line").textContent = "Starting body tracking…";
      await window.Pose.load();
      await new Promise((ok) => setTimeout(ok, 50));
      if (C.video && C.video.readyState >= 2) window.Pose.detect(C.video);          // warm-up: the first call is the slow one
      await window.Pose.loadImageMode().catch(() => {});
      const warm = document.createElement("canvas"); warm.width = 64; warm.height = 64;
      try { window.Pose.detectImage(warm); } catch { /* fine */ }
    }
    catch (e) {
      $("cam-line").textContent = e && e.name === "NotAllowedError" ? "Camera blocked. Use your own swing videos, or skip." : "No camera. Use your own swing videos, or skip.";
      say("[camera] The swing camera couldn't start. Offer uploading their own swing videos (the Use a slow-mo clip button, as many as they like), or skipping (the shaft model becomes a guess).", true);
      return;
    }
    card.classList.add("ok");
    L.st = "idle"; L.addrSince = 0; L.seen = performance.now();
    $("cam-line").textContent = "7-iron, facing me, whole body in frame. Just swing: I catch each one.";
    say(`[app] Swing step, camera live, like a video call. ${C.plan - C.swings.length} swings, face-on, normal effort. No tapping: the camera spots each swing by itself and reads it. They can also upload as many of their own swing videos as they like (the Use a slow-mo clip button); every one counts. Stay silent while they swing; after each one I'll send you what was measured.`, false);
    liveLoop();
  }
  function loopPreview() { liveLoop(); }
  // tapping the camera no longer starts a recording: swings are caught by themselves
  function recordSwing() { if (C.mode === "swing" && !C.busy) setLine("No need to tap: just swing, I catch each one."); }

  // a real swing's backswing is longer than its downswing; anything else means the camera missed part of it
  const plausible = (r) => r.backswing_s >= 0.4 && r.downswing_s >= 0.12 && r.backswing_s / r.downswing_s >= 1.5;
  function accept(r, n) {
    if (!plausible(r)) {
      $("cam-pill").textContent = `Swing ${n} of ${C.plan}`;
      $("cam-line").textContent = "That one didn't look like a full swing to me. Go again: whole body in frame, address to finish.";
      say(`[camera] Swing ${n} wasn't a clean full swing on camera (the timing didn't add up). Ask them to go again, whole body in frame.`, true);
      return;
    }
    const ratio = (r.backswing_s / r.downswing_s).toFixed(1);
    C.swings.push({ backswing_s: r.backswing_s, downswing_s: r.downswing_s, fps: r.fps, tempo: r.tempo });
    if (r.handedness_seen) (C.sides = C.sides || []).push(r.handedness_seen);   // filmed here: the camera's own pixels
    const S = UI().S; S.swings.push({ ratio, line: "" });
    $("cam-pill").textContent = n < C.plan ? `Swing ${n + 1} of ${C.plan}` : `Done · ${C.plan} swings`;
    $("cam-tempo").textContent = `${ratio} : 1`; $("cam-tempo").classList.remove("todo");
    $("cam-line").textContent = n < C.plan ? `Got it. Swing ${n + 1} whenever you're ready.` : `All ${C.plan} read.`;
    say(`[camera] Swing ${n} of ${C.plan} measured: backswing ${r.backswing_s.toFixed(2)} s, downswing ${r.downswing_s.toFixed(2)} s, ratio ${ratio} to 1, ${r.total_s.toFixed(2)} s in total (${r.tempo} by the kit's rule${r.warnings.length ? "; " + r.warnings[0] : ""}). Say one short thing about it, observation only, no swing tips.${n < C.plan ? " Then tell them to go again." : ""}`, true);
    if (C.swings.length >= C.plan) finishSwings();
  }

  async function finishSwings() {
    if (!C.swings.length) return;
    try {
      const st = await api("/api/session/swings", { method: "POST", json: { swings: C.swings } });
      const tempo = st.evidence?.swing_test?.tempo;
      // tempo isn't asked on the call: the measured tempo is the answer, tagged as seen
      // handedness read off the filmed swings (camera path), only if it wasn't given and every swing agrees
      const sides = [...new Set(C.sides || [])], hand = !st.profile?.handedness && sides.length === 1 && (C.sides || []).length >= 2 ? sides[0] : null;
      const vals = { ...(tempo ? { tempo } : {}), ...(hand ? { handedness: hand, handedness_seen: true } : {}) };
      const st2 = Object.keys(vals).length ? await api("/api/session/profile", { method: "PUT", json: { values: vals } }) : st;
      if (hand) say(`[camera] The swings show they're ${hand}-handed; saved as seen. Mention it in a few words.`, false);
      window.FitterAgent?.apply?.(st2);
      UI().actions([{ id: "a-fit", t: "See your fit →", cls: "primary", on: UI().startFit }]);
      say(`[camera] All ${C.swings.length} swings saved: ${st.evidence?.swing_test?.text || ""} The fit is ready: say so and that they can tap See your fit, or call get_fit_recommendation.`, true);
    } catch (e) { UI().toast(e.message); }
  }

  // ------------------------------------------------------------------ imported videos, checked and timed for real
  // Pick one or many. Each is read on the phone (only the hand track goes to the server) and every good one is added
  // to the swings the tempo is built from: more swings, tighter tempo.
  async function clips(files) {
    const list = [...files].filter((f) => f && (f.type.startsWith("video/") || /\.(mp4|mov|m4v|webm|avi|mkv|3gp)$/i.test(f.name || "")));
    if (!list.length) return;
    const rows = $("gate"), S = UI().S, ROWS = ["Frame rate", "Framing", "Hands", "Swing", "Light"];
    $("clip-ask").hidden = true; $("clip-busy").hidden = false; $("clip-result").hidden = true;
    if ($("clip-warn")) $("clip-warn").hidden = true;
    const good = [], bad = [], wasBusy = C.busy;
    C.busy = true; cancelAnimationFrame(C.raf);            // the live preview pauses while videos are read
    for (const [i, file] of list.entries()) {
      const tag = list.length > 1 ? ` (${i + 1} of ${list.length})` : "";
      $("sheet-clip-h").textContent = `Reading it${tag}.`;
      rows.innerHTML = ROWS.map((k) => `<div class="g wait"><span>${k}</span><b>…</b></div>`).join("");
      const set = (j, ok, text) => { const r = rows.querySelectorAll(".g")[j]; r.className = `g ${ok ? "ok" : "bad"}`; r.querySelector("b").textContent = text; };
      try {
        const r = await analyseBlob(file, 1, (f) => ($("sheet-clip-h").textContent = `Reading it${tag}… ${Math.round(f * 100)} %`));
        if (!plausible(r)) throw new Error("The timing didn't add up to one full swing. Use a clip with one swing, address to finish.");
        set(0, r.fps >= 50, r.fps >= 50 ? `${r.fps} fps` : `${r.fps} fps: slow motion is better`);
        set(1, r.seen > 0.6, r.seen > 0.6 ? "Body found" : "Body not found in most frames");
        set(2, r.seen >= 0.7, `Seen in ${Math.round(r.seen * 100)}% of frames`);
        set(3, true, "One full swing found"); set(4, r.seen > 0.6, r.seen > 0.6 ? "Fine" : "Too dark?");
        const ratio = (r.backswing_s / r.downswing_s).toFixed(1);
        C.swings.push({ backswing_s: r.backswing_s, downswing_s: r.downswing_s, fps: r.fps, tempo: r.tempo });
        S.clips.push({ name: file.name || "clip", ratio });
        good.push({ name: file.name || "clip", ratio, r });
      } catch (e) {
        set(0, true, "Read"); set(3, false, e.message.split(".")[0]);
        bad.push({ name: file.name || "clip", why: e.message });
      }
    }
    C.busy = wasBusy;
    $("clip-busy").hidden = true; $("clip-result").hidden = false; $("gate-done").innerHTML = rows.innerHTML;
    if (!good.length) {
      $("sheet-clip-h").textContent = list.length > 1 ? "None of those worked. Here's why." : "That one won't work. Here's why.";
      $("clip-p").textContent = `${bad[0].why} Film it again, or film with me at the swing step.`;
      $("clip-row").innerHTML = '<button type="button" class="btn" id="clip-later">Film it later</button><button type="button" class="btn primary" id="clip-again">Try other videos</button>';
      $("clip-later").onclick = UI().hideSheets; $("clip-again").onclick = () => $("clip-file").click();
      return;
    }
    const n = C.swings.length, last = good[good.length - 1];
    $("sheet-clip-h").textContent = n === 1 ? "Good clip. Swing read." : `${n} swings read.`;
    $("clip-p").textContent = (good.length === 1
        ? `Tempo ${last.ratio} to 1 (${last.r.total_s.toFixed(2)} s, ${last.r.tempo}).`
        : `Tempos ${good.map((g) => g.ratio).join(", ")} to 1.`)
      + (bad.length ? ` ${bad.length} couldn't be read (${bad.map((b) => b.name).join(", ")}): ${bad[0].why}` : "")
      + " Every swing you add tightens the tempo. The videos stay on your phone; only the hand track was sent.";
    $("clip-row").innerHTML = `<button type="button" class="btn" id="clip-more">Add more</button><button type="button" class="btn primary" id="clip-done">Done</button>`;
    $("clip-more").onclick = () => $("clip-file").click();
    $("clip-done").onclick = async () => { UI().hideSheets(); await finishSwings(); };
    say(`[camera] ${good.length} imported swing video${good.length > 1 ? "s" : ""} read (${good.map((g) => g.ratio + " to 1").join(", ")}), ${n} swing${n > 1 ? "s" : ""} in total so far.${bad.length ? ` ${bad.length} couldn't be read.` : ""} Say one short thing, observation only.`, true);
    UI().renderHello?.();
  }
  const clip = (file) => clips([file]);
  // their clip picker takes one file: allow many, and read them all
  const picker = $("clip-file");
  if (picker) {
    picker.multiple = true;
    picker.addEventListener("change", (e) => {
      e.stopImmediatePropagation();
      const files = [...picker.files]; picker.value = "";
      if (files.length) clips(files);
    }, true);
  }

  // tapping their swing camera records a real swing (instead of their simulated one)
  document.addEventListener("click", (e) => {
    if (e.target.closest(".realcam-flip")) return;
    if (C.mode === "swing" && e.target.closest("#cam")) { e.stopImmediatePropagation(); recordSwing(); }
    if (C.mode === "measure" && e.target.closest("#mcam")) { e.stopImmediatePropagation(); if (!C.frozen) C.readySince = performance.now() - 1000; }
  }, true);
  // the tape path: stop the camera, the assistant asks for the numbers
  $("m-tape")?.addEventListener("click", (e) => {
    if (!window.FitterAgent?.used()) return;
    e.stopImmediatePropagation(); stopCamera(); C.mode = null;
    $("mcam").hidden = true; $("m-tapeline").hidden = true;
    UI().S.measMode = "tape";
    say("[app] The golfer wants to use a tape instead of the camera. One question per turn, in this order, skipping any already saved: height in golf shoes, then wrist-to-floor in golf shoes with arms hanging, then glove size, then right- or left-handed. Repeat each number in the next question instead of asking if it's right.", true);
  }, true);
  window.addEventListener("pagehide", stopCamera);
  // camera off as soon as neither camera screen is showing (fit, ask, hello…): never left running in the background
  const camScreens = ["st-measure", "st-swing"].map((id) => $(id)).filter(Boolean);
  new MutationObserver(() => { if (C.stream && camScreens.every((el) => el.hidden)) stopCamera(); })
    .observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden"] });

  // the stylesheet for the real video inside their camera cards
  const css = document.createElement("style");
  css.textContent = `.cam video.realcam,.cam canvas.realcam-overlay{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
    .cam.live{background:#0B0B0B;margin-left:auto;margin-right:auto}
    .cam canvas.realcam-overlay{pointer-events:none;z-index:1} .cam.live .golfer{display:none}
    .cam video.realcam.mirror{transform:scaleX(-1)}
    .cam .realcam-flip{display:none;position:absolute;top:46px;left:10px;z-index:3;width:36px;height:36px;border-radius:50%;
      border:1px solid rgba(248,248,248,.35);background:rgba(11,11,11,.55);color:#F8F8F8;align-items:center;justify-content:center;cursor:pointer}
    .cam.live .realcam-flip{display:flex}
    .cam.live .pill,.cam.live .line,.cam.live .tempo,.cam.live .framesvg{z-index:2}
    .cam.live .framesvg,.cam.live .pill,.cam.live .line,.cam.live .tempo,.cam.live .flash{pointer-events:none}`;
  document.head.appendChild(css);

  return { measure, swing, clip, clips, confirmMeasurement, status, stop: stopCamera, flip: flipCamera, recordSwing,
    _debug: () => ({ live: { st: L.st, head: L.head, detectMs: Math.round(L.detectMs), busy: C.busy, dbg: L.dbg }, mode: C.mode, swings: C.swings, measured: C.measured, busy: C.busy, frozen: C.frozen ? C.frozen.pts : null }) };
})();
