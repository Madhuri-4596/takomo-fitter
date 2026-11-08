"use strict";

// Step 3: measure height and wrist-to-floor from a calibrated photo.
//   live camera  -> MediaPipe pose (on device) checks framing; the server finds the sheet's markers
//   frozen photo -> the golfer places/confirms head top, wrist crease and floor; sheet markers or
//                   four hand-marked paper corners give the scale (kit camera/sheet.py geometry)
//   result       -> estimates, cross-check against the entered answers, explicit confirmation
// Uses globals from app.js: $, $$, esc, emit, api, S, applyState, show.

window.Measure = (() => {
  // Prototype thresholds: engineering guesses tried on synthetic images only, NOT validated
  // measurement-error limits. The server's own thresholds are at /api/photo/thresholds.
  const T = {
    visibility_min: 0.6,         // MediaPipe landmark visibility to count a body point as seen
    headroom_min: 0.01,          // estimated head top must be this far (frame fraction) inside the frame
    floor_margin: 0.04,          // lowest heel must be this far above the bottom edge, so the floor shows
    still_window_ms: 800,        // how long the points must hold still...
    still_max_move: 0.012,       // ...moving less than this (frame-height fraction)
    sheet_fresh_ms: 6000,        // a sheet detection counts for this long (checks are slow while tracking runs)
    ready_hold_ms: 700,          // "Ready to capture" must hold this long before the countdown
    countdown_s: 3,
    bad_frames_to_cancel: 2,     // consecutive failing frames before readiness or a countdown is dropped
    dark_brightness: 45,
    sheet_frac_close: 0.35, sheet_frac_far: 0.05,
    cross_check_cm: 4.0,
  };
  const LM = { nose: 0, lsh: 11, rsh: 12, lwrist: 15, rwrist: 16, lpinky: 17, rpinky: 18, lindex: 19, rindex: 20,
               lhip: 23, rhip: 24, lankle: 27, rankle: 28, lheel: 29, rheel: 30, lfoot: 31, rfoot: 32 };
  const INSTRUCTIONS = {
    starting: "Starting the camera…",
    too_dark: "It's too dark. Turn on a light or face a window.",
    sheet_missing: "Point the camera so the whole printed sheet is in view.",
    sheet_partly: "Only part of the sheet is in view. Get all four corner squares in the picture.",
    too_close: "Step back so your whole body is visible.",
    too_far: "Move the phone a little closer. The sheet looks very small.",
    no_person: "Step back so your whole body is visible.",
    head_cut_off: "Step back so your whole body is visible. Your head is cut off.",
    feet_cut_off: "Step back so your whole body is visible. Your feet need to show.",
    wrist_hidden: "Keep your wrist visible.",
    arms_not_relaxed: "Let your arms hang naturally.",
    moving: "Hold still.",
    ready: "Ready to capture",
    ready_manual: "Sheet found. Body tracking is off, so check you're fully in view and tap Capture.",
    ready_manual_nosheet: "Body tracking is off. Check your head, a wrist and your feet are in view, then tap Capture.",
    countdown: "Hold still…",
    cancelled: "Countdown stopped: hold your position and it will start again.",
    captured: "Captured. Check the markers on your photo.",
    denied: "Camera access is blocked. Allow it in your browser settings, or upload a photo instead.",
    unavailable: "No camera found. Upload a photo or use a tape measure instead.",
  };

  // ------------------------------------------------------------------ state reported to the assistant
  const M = {
    step_open: false, view: "intro", status: "not_started", last_error: null, key: null,
    camera: { permission: "not_requested", running: false, facing: "environment", mirrored: false,
              sheet_detected: false, markers_found: 0, issues: [], instruction: "" },
    person: { detected: false, head: false, wrist: null, feet: false, still: false },
    countdown: null, photo: "none", points_placed: 0,
  };

  function setKey(key, extra) {
    if (extra) Object.assign(M.camera, extra);
    M.camera.instruction = INSTRUCTIONS[key] || "";
    const box = $("#cam-instruction");
    box.textContent = M.camera.instruction;
    box.classList.toggle("ok", key === "ready" || key === "countdown");
    if (M.key !== key) { M.key = key; emit("fitter:camera", { key, instruction: M.camera.instruction }); }
  }

  function status() {
    const st = S.state || {};
    return {
      step_open: M.step_open, view: M.view,
      body_tracking: window.Pose.state(),
      camera: { ...M.camera },
      person: { ...M.person, note: "From on-device pose tracking. Stable tracking does not prove the measurement is accurate." },
      countdown_seconds_left: M.countdown,
      photo: M.photo, points_placed: M.points_placed,
      method: method(), calibration: E.calib, markers_from_body_tracking: { wrist: !!E.suggestion, floor: !!E.floorSuggestion },
      status: st.measurement_staged ? "measured_awaiting_confirmation" : M.status,
      last_error: M.last_error,
      measured_awaiting_confirmation: st.measurement_staged || null,
      confirmed_measurement: st.measurement || null,
    };
  }

  // how the scale is set: "height" (entered height, nothing uploaded), "paper" (plain A4, corners marked
  // by hand), "sheet" (the kit's printed ArUco sheet)
  const method = () => document.querySelector('input[name="ms-method"]:checked').value;
  function showMethod() {
    const m = method();
    $$("#ms-intro [data-method]").forEach((d) => (d.hidden = d.dataset.method !== m));
    $('#cam-checks li[data-check="sheet"]').hidden = m !== "sheet";
    if (m === "height" && !$("#ms-height").value && S.state?.profile?.height_cm) $("#ms-height").value = S.state.profile.height_cm;
  }
  $$('input[name="ms-method"]').forEach((r) => r.addEventListener("change", showMethod));
  function heightInput() {
    const h = Number($("#ms-height").value);
    return h >= 120 && h <= 220 ? h : null;
  }
  function needHeight() {
    if (method() !== "height" || heightInput()) return true;
    const e = $("#ms-intro-error");
    e.textContent = "Enter your height first (120–220 cm): it sets the scale.";
    M.last_error = "height_needed: the golfer must enter their height, or choose the paper or printed-sheet method";
    e.hidden = false;
    $("#ms-height").focus();
    return false;
  }

  function view(v) {
    M.view = v;
    $("#ms-intro").hidden = v !== "intro";
    $("#cam").hidden = v !== "live";
    $("#tap-card").hidden = v !== "edit";
  }

  // ------------------------------------------------------------------ body tracking (MediaPipe)
  async function loadPose() {
    const el = $("#pose-status");
    if (window.Pose.state() === "ready") { el.textContent = ""; return true; }
    el.textContent = "Loading body tracking (about 6 MB, first time only)…";
    try {
      await window.Pose.load();
      el.textContent = "";
      return true;
    } catch (e) {
      el.textContent = "Body tracking couldn't load. You can still take the photo and place the markers yourself.";
      M.last_error = "pose_unavailable: " + (e?.message || e);
      return false;
    }
  }

  const seen = (L, i) => (L && L[i] && L[i].v >= T.visibility_min ? L[i] : null);

  // pick the clearly visible wrist; the golfer can override it
  function pickWrist(L, pref) {
    const l = seen(L, LM.lwrist), r = seen(L, LM.rwrist);
    if (pref === "left") return l ? { side: "left", p: l, i: LM.lwrist } : null;
    if (pref === "right") return r ? { side: "right", p: r, i: LM.rwrist } : null;
    if (l && r) return l.v >= r.v ? { side: "left", p: l, i: LM.lwrist } : { side: "right", p: r, i: LM.rwrist };
    if (l) return { side: "left", p: l, i: LM.lwrist };
    if (r) return { side: "right", p: r, i: LM.rwrist };
    return null;
  }

  // ------------------------------------------------------------------ live camera
  const video = $("#cam-video");
  const overlay = $("#cam-overlay");
  const octx = overlay.getContext("2d");
  const L = { stream: null, raf: 0, sheetTimer: null, sheetBusy: false, lastTime: -1, pose: null, history: [],
              sheet: { n: 0, markers: [], at: 0, frac: 0 }, brightness: 255, wristPref: "auto", wrist: null,
              readySince: 0, count: null };

  async function openCamera() {
    show(3);
    if (!needHeight()) return;
    $("#ms-intro-error").hidden = true;
    L.method = method();
    view("live");
    $("#cam-error").hidden = true;
    M.last_error = null;
    if (!navigator.mediaDevices?.getUserMedia) return cameraFailed("unavailable");
    M.status = "camera_starting";
    setKey("starting", { permission: "requesting", running: false });
    const posePromise = loadPose();
    try {
      L.stream = await navigator.mediaDevices.getUserMedia({ audio: false,
        // no aspect-ratio constraint: forcing one can crop the frame and cut off head or feet
        video: { facingMode: { ideal: M.camera.facing }, width: { ideal: 1920 } } });
    } catch (e) {
      M.last_error = e.name;
      return cameraFailed(e.name === "NotAllowedError" || e.name === "SecurityError" ? "denied" : "unavailable");
    }
    const settings = L.stream.getVideoTracks()[0]?.getSettings?.() || {};
    M.camera.mirrored = (settings.facingMode || M.camera.facing) === "user";
    $("#cam-view").classList.toggle("mirrored", M.camera.mirrored);      // preview only; captured pixels stay unmirrored
    video.srcObject = L.stream;
    await video.play().catch(() => {});
    Object.assign(M.camera, { permission: "granted", running: true });
    M.status = "camera_live";
    navigator.mediaDevices.enumerateDevices?.().then((d) => {
      $("#cam-switch").hidden = d.filter((x) => x.kind === "videoinput").length < 2;
    }).catch(() => {});
    const poseOk = await posePromise;
    $("#cam-shoot").hidden = poseOk;                  // without body tracking, capture by hand
    L.history = []; L.readySince = 0; L.lastTime = -1;
    if (L.method === "sheet") { L.sheetTimer = setInterval(checkSheet, 1000); checkSheet(); }   // other methods send nothing
    L.raf = requestAnimationFrame(loop);
  }

  function cameraFailed(kind) {
    M.camera.permission = kind === "denied" ? "denied" : "unavailable";
    M.status = kind === "denied" ? "camera_denied" : "camera_unavailable";
    setKey(kind, { running: false });
    stopCamera();
    view("intro");
    const e = $("#cam-error"); e.textContent = INSTRUCTIONS[kind]; e.hidden = false;
  }

  function stopCamera() {
    cancelAnimationFrame(L.raf); L.raf = 0;
    clearInterval(L.sheetTimer); L.sheetTimer = null;
    cancelCountdown(null);
    if (L.stream) L.stream.getTracks().forEach((t) => t.stop());
    L.stream = null;
    video.srcObject = null;
    M.camera.running = false;
  }

  // every second: send a downscaled frame to the server to find the sheet's markers
  async function checkSheet() {
    if (L.method !== "sheet") return;                // privacy: only the printed-sheet method ever sends frames
    if (L.sheetBusy || !video.videoWidth || M.countdown !== null) return;
    L.sheetBusy = true;
    try {
      const k = Math.min(1, 1280 / Math.max(video.videoWidth, video.videoHeight));
      const c = document.createElement("canvas");
      c.width = Math.round(video.videoWidth * k); c.height = Math.round(video.videoHeight * k);
      const cx = c.getContext("2d", { willReadFrequently: true });
      cx.drawImage(video, 0, 0, c.width, c.height);
      const px = cx.getImageData(0, 0, c.width, c.height).data;
      let sum = 0, n = 0;
      for (let i = 0; i < px.length; i += 4 * 97) { sum += 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2]; n++; }
      L.brightness = sum / n;
      const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.85));
      const fd = new FormData(); fd.append("image", blob, "frame.jpg");
      const sheet = await api("/api/photo/sheet", { method: "POST", body: fd }, 8000);
      if (!L.stream) return;
      const markers = sheet.markers.map((m) => ({ id: m.id, corners: m.corners.map(([x, y]) => [x / k, y / k]) }));
      const xs = markers.flatMap((m) => m.corners.map((p) => p[0]));
      L.sheet = { n: markers.length, markers, at: performance.now(), frac: xs.length ? (Math.max(...xs) - Math.min(...xs)) / video.videoWidth : 0 };
      Object.assign(M.camera, { sheet_detected: sheet.ok, markers_found: markers.length, brightness: Math.round(L.brightness) });
    } catch (ex) { M.last_error = ex.message; }
    finally { L.sheetBusy = false; }
  }

  function loop() {
    if (!L.stream) return;
    if (video.readyState >= 2 && video.currentTime !== L.lastTime) {
      L.lastTime = video.currentTime;
      if (window.Pose.state() === "ready") {
        try { L.pose = window.Pose.detect(video); } catch (e) { L.pose = null; M.last_error = String(e); }
      }
      const now = performance.now();
      L.history.push({ t: now, pose: L.pose });
      // keep the stillness window, but never fewer than 3 frames: on a slow phone (~2 fps) the window alone can hold
      // just two frames, and stillness would flicker even when the golfer stands perfectly still
      while (L.history.length > 3 && now - L.history[0].t > T.still_window_ms) L.history.shift();
      evaluate(now);
      drawOverlay();
    }
    L.raf = requestAnimationFrame(loop);
  }

  function evaluate(now) {
    const P = L.pose, tracking = window.Pose.state() === "ready";
    const useSheet = L.method === "sheet";
    const sheetFresh = now - L.sheet.at < T.sheet_fresh_ms;
    const checks = { sheet: !useSheet || (sheetFresh && L.sheet.n >= 3), head: false, wrist: false, feet: false, still: false };
    const issues = [];
    if (useSheet && L.brightness < T.dark_brightness) issues.push("too_dark");
    if (tracking && !P) issues.push("no_person");          // nobody in view: the most useful thing to say first
    if (useSheet) {
      if (!sheetFresh || L.sheet.n === 0) issues.push("sheet_missing");
      else if (L.sheet.n < 3) issues.push("sheet_partly");
      else if (L.sheet.frac > T.sheet_frac_close) issues.push("too_close");
      else if (L.sheet.frac < T.sheet_frac_far) issues.push("too_far");
    }
    let wrist = null;
    if (tracking) {
      if (P) {
        const nose = seen(P, LM.nose), ls = seen(P, LM.lsh), rs = seen(P, LM.rsh);
        const sh = ls && rs ? (ls.y + rs.y) / 2 : (ls || rs)?.y;
        // top of the head is NOT a landmark: this only checks there is room above the face
        const headTopEst = nose && sh ? nose.y - 0.9 * (sh - nose.y) : null;
        checks.head = !!(nose && headTopEst !== null && headTopEst > T.headroom_min);
        const feet = [LM.lheel, LM.rheel, LM.lankle, LM.rankle].map((i) => seen(P, i)).filter(Boolean);
        checks.feet = feet.length > 0 && Math.max(...feet.map((f) => f.y)) < 1 - T.floor_margin;
        wrist = pickWrist(P, L.wristPref);
        const hips = [seen(P, LM.lhip), seen(P, LM.rhip)].filter(Boolean);
        const hipY = hips.length ? hips.reduce((a, h) => a + h.y, 0) / hips.length : null;
        const relaxed = wrist && (hipY === null || wrist.p.y > hipY - 0.03);
        checks.wrist = !!(wrist && relaxed);
        // stillness: nose, chosen wrist and feet move less than the limit over the window
        const span = L.history.length > 1 ? L.history[L.history.length - 1].t - L.history[0].t : 0;
        let move = 0;
        // only clearly seen points: faint landmarks jitter even when the golfer is perfectly still
        const footIds = [LM.lankle, LM.rankle, LM.lheel, LM.rheel].filter((i) => seen(P, i));
        const bestFoot = footIds.sort((a, b) => P[b].v - P[a].v)[0];
        const ids = [LM.nose, wrist ? wrist.i : LM.lwrist, bestFoot].filter((i) => i !== undefined);
        for (const id of ids) {
          const ys = L.history.map((h) => h.pose?.[id]).filter((p) => p && p.v >= T.visibility_min);
          if (ys.length > 1) {
            const xs = ys.map((p) => p.x), yy = ys.map((p) => p.y);
            move = Math.max(move, Math.max(...xs) - Math.min(...xs), Math.max(...yy) - Math.min(...yy));
          }
        }
        checks.still = span >= T.still_window_ms * 0.75 && move < T.still_max_move &&
                       L.history.every((h) => h.pose);
        if (!checks.head) issues.push("head_cut_off");
        if (!checks.feet) issues.push("feet_cut_off");
        if (!wrist) issues.push("wrist_hidden");
        else if (!relaxed) issues.push("arms_not_relaxed");
        if (!checks.still) issues.push("moving");
      }
    }
    L.wrist = wrist;
    Object.assign(M.person, { detected: !!P, head: checks.head, wrist: wrist ? wrist.side : null, feet: checks.feet, still: checks.still });
    M.camera.issues = issues;
    $$("#cam-checks li").forEach((li) => li.classList.toggle("ok", !!checks[li.dataset.check]));
    const ws = $("#wrist-switch");
    const bothSeen = P && seen(P, LM.lwrist) && seen(P, LM.rwrist);
    ws.hidden = !tracking || !(bothSeen || L.wristPref !== "auto");
    if (wrist) ws.textContent = `Using your ${wrist.side} wrist · switch`;

    let key = issues[0] || (tracking ? "ready" : "ready_manual");
    // hysteresis: one bad frame doesn't drop readiness or a running countdown; two in a row do
    if (key !== "ready" && (M.countdown !== null || L.readySince)) {
      L.badFrames = (L.badFrames || 0) + 1;
      if (L.badFrames < T.bad_frames_to_cancel) key = "ready";
    } else if (key === "ready") L.badFrames = 0;
    if (M.countdown !== null) {
      if (key !== "ready") { L.badFrames = 0; cancelCountdown("cancelled"); }
      return;
    }
    if (key === "ready_manual") { setKey(useSheet ? "ready_manual" : "ready_manual_nosheet"); return; }
    setKey(key);
    if (key === "ready") {
      if (!L.readySince) L.readySince = now;
      if (now - L.readySince >= T.ready_hold_ms) startCountdown();
    } else L.readySince = 0;
  }

  function drawOverlay() {
    const w = video.videoWidth, h = video.videoHeight;
    if (!w) return;
    if (overlay.width !== w || overlay.height !== h) { overlay.width = w; overlay.height = h; }
    octx.clearRect(0, 0, w, h);
    const u = Math.max(w, h) / 160;
    if (performance.now() - L.sheet.at < T.sheet_fresh_ms) {
      octx.strokeStyle = "#b8964a"; octx.lineWidth = u / 2;
      for (const m of L.sheet.markers) {
        octx.beginPath(); m.corners.forEach(([x, y], i) => (i ? octx.lineTo(x, y) : octx.moveTo(x, y))); octx.closePath(); octx.stroke();
      }
    }
    const P = L.pose;
    if (!P) return;
    const bones = [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24], [23, 25], [25, 27], [24, 26], [26, 28], [27, 29], [28, 30]];
    octx.strokeStyle = "rgba(247,244,236,.75)"; octx.lineWidth = u / 2.5;
    for (const [a, b] of bones) {
      if (P[a].v < 0.5 || P[b].v < 0.5) continue;
      octx.beginPath(); octx.moveTo(P[a].x * w, P[a].y * h); octx.lineTo(P[b].x * w, P[b].y * h); octx.stroke();
    }
    const dot = (p, r, fill) => { octx.fillStyle = fill; octx.beginPath(); octx.arc(p.x * w, p.y * h, r, 0, 7); octx.fill(); };
    for (const i of [LM.nose, LM.lheel, LM.rheel]) if (P[i].v >= T.visibility_min) dot(P[i], u * 0.9, "#f7f4ec");
    if (L.wrist) { dot(L.wrist.p, u * 1.6, "#b8964a"); dot(L.wrist.p, u * 0.7, "#123d2c"); }
  }

  function startCountdown() {
    M.countdown = T.countdown_s; M.status = "countdown";
    setKey("countdown");
    const box = $("#countdown");
    box.hidden = false; box.textContent = String(M.countdown);
    emit("fitter:quiet");
    L.count = setInterval(() => {
      M.countdown -= 1;
      emit("fitter:quiet");                    // keeps the assistant from talking during the countdown
      if (M.countdown > 0) { box.textContent = String(M.countdown); return; }
      clearInterval(L.count); L.count = null; box.hidden = true; M.countdown = null;
      capture();
    }, 1000);
  }

  function cancelCountdown(reason) {
    if (L.count) clearInterval(L.count);
    L.count = null;
    if (M.countdown !== null) {
      M.countdown = null; M.status = "camera_live"; L.readySince = 0;
      $("#countdown").hidden = true;
      if (reason) setKey(reason);
      if (L.stream) checkSheet();                 // sheet checks pause during a countdown: refresh now
    }
  }

  async function capture() {
    const c = document.createElement("canvas");
    c.width = video.videoWidth; c.height = video.videoHeight;
    c.getContext("2d").drawImage(video, 0, 0);          // raw frame: never the mirrored preview
    const suggestion = L.wrist ? { x: L.wrist.p.x * c.width, y: L.wrist.p.y * c.height, side: L.wrist.side } : null;
    const pose = L.pose;
    stopCamera();
    M.photo = "captured"; M.status = "photo_captured_place_points";
    setKey("captured", { running: false });
    const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.92));
    openEditor(blob, { source: "camera", wristSuggestion: suggestion, pose });
  }

  $("#cam-open").addEventListener("click", openCamera);
  $("#cam-close").addEventListener("click", () => { stopCamera(); view("intro"); M.status = "not_started"; });
  $("#cam-shoot").addEventListener("click", () => startCountdown());
  $("#cam-switch").addEventListener("click", () => {
    M.camera.facing = M.camera.facing === "user" ? "environment" : "user";
    stopCamera();
    openCamera();
  });
  $("#wrist-switch").addEventListener("click", () => {
    const cur = L.wrist ? L.wrist.side : "left";
    L.wristPref = cur === "left" ? "right" : "left";
    L.readySince = 0;
  });

  // ------------------------------------------------------------------ frozen photo editor
  const canvas = $("#photo-canvas");
  const ctx = canvas.getContext("2d");
  const loupe = $("#loupe");
  const lctx = loupe.getContext("2d");
  const NAMES = { head: "Head top", wrist: "Wrist crease", floor: "Floor" };
  const E = { base: null, blob: null, markers: [], calib: null, corners: [], points: { head: null, wrist: null, floor: null },
              suggestion: null, floorSuggestion: null, measuredKey: null, actions: [], drag: null, view: { s: 1, x0: 0, y0: 0 }, source: null, panFrom: null };

  let measureTimer = null;
  function resetEditor() {
    Object.assign(E, { base: null, blob: null, markers: [], calib: null, corners: [], points: { head: null, wrist: null, floor: null },
                       suggestion: null, floorSuggestion: null, measuredKey: null, actions: [], drag: null, view: { s: 1, x0: 0, y0: 0 }, source: null });
    clearTimeout(measureTimer);
    $("#measure-error").hidden = true;
    M.points_placed = 0;
  }

  async function loadImage(fileOrBlob) {
    let bmp;
    try { bmp = await createImageBitmap(fileOrBlob, { imageOrientation: "from-image" }); }   // respects EXIF rotation
    catch {
      bmp = await new Promise((ok, bad) => {
        const img = new Image();
        img.onload = () => ok(img);
        img.onerror = () => bad(new Error("We couldn't open that image."));
        img.src = URL.createObjectURL(fileOrBlob);
      });
    }
    const k = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));   // keep detail for marker corners, limit upload size
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return c;
  }

  async function openEditor(fileOrBlob, { source, wristSuggestion = null } = {}) {
    show(3);
    if (!needHeight()) return;
    $("#ms-intro-error").hidden = true;
    resetEditor();
    E.method = method();
    E.source = source;
    view("edit");
    const status = $("#sheet-status");
    status.className = "status"; status.textContent = "Finding the sheet…";
    M.photo = "captured"; M.status = "photo_captured_place_points";
    try {
      E.base = await loadImage(fileOrBlob);
      E.blob = await new Promise((ok) => E.base.toBlob(ok, "image/jpeg", 0.92));   // what the server measures: same pixels as shown
      canvas.width = E.base.width; canvas.height = E.base.height;
      E.view = { s: 1, x0: 0, y0: 0 };
      draw();
      let sheet = null;
      if (E.method === "height") {
        E.calib = "height";
        status.textContent = `Scale from your height (${heightInput()} cm). This photo stays on your device.`;
        status.classList.add("ok");
      } else if (E.method === "paper") {
        E.calib = "corners";
        status.textContent = "Mark the four outside corners of your A4 paper, starting top-left.";
      } else {
        const fd = new FormData(); fd.append("image", E.blob, "photo.jpg");
        sheet = await api("/api/photo/sheet", { method: "POST", body: fd });
        E.markers = sheet.markers;
      }
      if (!sheet) { /* scale set above */ }
      else if (sheet.ok) {
        E.calib = "markers";
        status.textContent = sheet.message + (sheet.sharpness < 100 ? " The photo looks a little blurry." : "");
        status.classList.add("ok");
      } else {
        E.calib = "corners";
        status.textContent = "We couldn't find the sheet's corner squares. Mark the four outside corners of the paper instead, or retake the photo.";
        status.classList.add("bad");
      }
      updatePrompt();                                   // say what to tap while body tracking loads
      // suggestions from body tracking on this photo: the wrist (or the live camera's) and the floor under the heel.
      // The top of the head is always tapped by hand: no landmark marks it.
      if (wristSuggestion) E.suggestion = wristSuggestion;
      if (await loadPose()) {
        const P = window.Pose.detect(E.base);
        if (!E.suggestion) {
          const w = pickWrist(P, "auto");
          if (w) E.suggestion = { x: w.p.x * E.base.width, y: w.p.y * E.base.height, side: w.side };
        }
        E.floorSuggestion = floorFrom(P);
      }
      if (E.suggestion) E.points.wrist = { x: E.suggestion.x, y: E.suggestion.y };
      if (E.floorSuggestion && !E.points.floor) E.points.floor = { ...E.floorSuggestion };
      draw();
      $("#tap-card").scrollIntoView({ behavior: "smooth" });
    } catch (ex) {
      status.textContent = ex.message; status.classList.add("bad");
      M.status = "error"; M.last_error = ex.message;
    }
    updatePrompt();
  }

  // the floor under the lower heel (the heel landmark sits at the back of the heel, near the ground)
  function floorFrom(P) {
    if (!P) return null;
    const sides = [[LM.lheel, LM.lfoot], [LM.rheel, LM.rfoot]]
      .map(([h, f]) => [P[h], P[f]]).filter(([h, f]) => h && f && h.v >= T.visibility_min && f.v >= T.visibility_min);
    if (!sides.length) return null;
    const [h, f] = sides.sort((a, b) => Math.max(b[0].y, b[1].y) - Math.max(a[0].y, a[1].y))[0];
    const y = Math.min(1, Math.max(h.y, f.y) + 0.004);
    return { x: h.x * E.base.width, y: y * E.base.height };
  }

  function nextNeeded() {
    if (E.calib === "corners" && E.corners.length < 4) return "corner";
    for (const k of ["head", "wrist", "floor"]) if (!E.points[k]) return k;
    return null;
  }

  function updatePrompt() {
    const need = nextNeeded();
    const order = ["top-left", "top-right", "bottom-right", "bottom-left"];
    const msgs = {
      corner: `Tap the paper's <b>${order[E.corners.length]}</b> corner (${E.corners.length + 1} of 4).`,
      head: "Tap the <b>top of the head</b>, not the eyes or forehead.",
      wrist: "Tap the <b>wrist crease</b>, where the hand meets the arm.",
      floor: E.calib === "height" ? "Tap the <b>floor right under your heel</b>." : "Tap the <b>floor where it meets the wall</b>, beside your heel.",
    };
    let html = need ? msgs[need] : "Measured below. Drag any marker to adjust it: the result updates straight away.";
    const auto = [E.suggestion && "wrist", E.floorSuggestion && "floor"].filter(Boolean);
    if (auto.length && need !== "corner")
      html += ` The ${auto.join(" and ")} marker${auto.length > 1 ? "s were" : " was"} placed by body tracking: check ${auto.length > 1 ? "they sit" : "it sits"} on the wrist crease${auto.includes("floor") ? " and the floor under the heel" : ""}.`;
    $("#tap-prompt").innerHTML = html;
    M.points_placed = ["head", "wrist", "floor"].filter((k) => E.points[k]).length;
    if (!need && E.calib && !E.drag) measureSoon();
    else if (need) { clearTimeout(measureTimer); E.pending = false; E.queuedKey = null; }   // a marker was removed
  }

  // measure as soon as every marker is in place, and again whenever one is moved
  function measureSoon() {
    const key = JSON.stringify([E.points, E.corners, E.calib, heightInput(), $("#ms-footwear").value]);
    if (key === E.measuredKey) return;
    clearTimeout(measureTimer);
    E.pending = true; E.queuedKey = key;
    measureTimer = setTimeout(async () => {
      if (nextNeeded() || !E.calib) { E.pending = false; return; }    // markers changed while waiting
      E.measuredKey = key; await runMeasure(key); if (E.queuedKey === key) E.pending = false;
    }, 250);
  }

  // view transform: image px -> canvas px = (p - x0) * s
  function toImage(e) {
    const r = canvas.getBoundingClientRect();
    const fit = Math.min(r.width / canvas.width, r.height / canvas.height);
    const ox = (r.width - canvas.width * fit) / 2, oy = (r.height - canvas.height * fit) / 2;
    const cx = (e.clientX - r.left - ox) / fit, cy = (e.clientY - r.top - oy) / fit;
    return { x: E.view.x0 + cx / E.view.s, y: E.view.y0 + cy / E.view.s, fit };
  }

  function clampView() {
    const v = E.view;
    v.x0 = Math.max(0, Math.min(canvas.width - canvas.width / v.s, v.x0));
    v.y0 = Math.max(0, Math.min(canvas.height - canvas.height / v.s, v.y0));
  }

  function zoom(factor, center) {
    const v = E.view;
    const c = center || { x: v.x0 + canvas.width / v.s / 2, y: v.y0 + canvas.height / v.s / 2 };
    v.s = Math.max(1, Math.min(4, v.s * factor));
    v.x0 = c.x - canvas.width / v.s / 2; v.y0 = c.y - canvas.height / v.s / 2;
    clampView(); draw();
  }

  function draw() {
    if (!E.base) return;
    const v = E.view;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#e8e3d6"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(v.s, 0, 0, v.s, -v.x0 * v.s, -v.y0 * v.s);
    ctx.drawImage(E.base, 0, 0);
    const r = canvas.getBoundingClientRect();
    const fit = r.width ? Math.min(r.width / canvas.width, r.height / canvas.height) : 0;
    const u = fit ? 8 / (fit * v.s) : canvas.width / 90;          // marker size in screen pixels
    ctx.lineWidth = Math.max(1, u / 3);
    ctx.strokeStyle = "#b8964a";
    for (const m of E.markers) {
      ctx.beginPath(); m.corners.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.stroke();
    }
    if (E.corners.length) {
      ctx.strokeStyle = "#1d5640"; ctx.beginPath();
      E.corners.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      if (E.corners.length === 4) ctx.closePath();
      ctx.stroke();
      E.corners.forEach((p, i) => label(p, String(i + 1), u, "#1d5640"));
    }
    for (const k of ["head", "wrist", "floor"]) {
      const p = E.points[k];
      if (!p) continue;
      const sug = k === "wrist" ? E.suggestion : k === "floor" ? E.floorSuggestion : null;
      const tentative = !!sug && Math.hypot(sug.x - p.x, sug.y - p.y) < 1;      // still where body tracking put it
      ctx.setLineDash([u, u / 2]); ctx.strokeStyle = "rgba(184,150,74,.95)";
      ctx.beginPath(); ctx.moveTo(p.x - 5 * u, p.y); ctx.lineTo(p.x + 5 * u, p.y); ctx.stroke();
      ctx.setLineDash([]);
      label(p, tentative ? `${NAMES[k]} · check` : NAMES[k], u, tentative ? "#c2500c" : "#123d2c");
    }
    function label(p, text, u, color) {
      ctx.fillStyle = color; ctx.strokeStyle = "#f7f4ec"; ctx.lineWidth = u / 3;
      ctx.beginPath(); ctx.arc(p.x, p.y, u * 0.9, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
      ctx.font = `700 ${u * 1.6}px system-ui, sans-serif`;
      const tw = ctx.measureText(text).width;
      const tx = p.x + u * 1.6 + tw > canvas.width ? p.x - u * 1.6 - tw : p.x + u * 1.6, ty = p.y - u * 1.2;
      ctx.lineWidth = u / 2.5; ctx.strokeText(text, tx, ty);
      ctx.fillStyle = color; ctx.fillText(text, tx, ty);
    }
  }

  function showLoupe(p, e) {
    const size = 150, src = 40 / (p.fit * E.view.s);          // 3x the current zoom on a 120 px loupe
    lctx.fillStyle = "#fff"; lctx.fillRect(0, 0, size, size);
    lctx.drawImage(E.base, p.x - src / 2, p.y - src / 2, src, src, 0, 0, size, size);
    lctx.strokeStyle = "#a63d2f"; lctx.lineWidth = 1.5;
    lctx.beginPath(); lctx.moveTo(size / 2, 0); lctx.lineTo(size / 2, size); lctx.moveTo(0, size / 2); lctx.lineTo(size, size / 2); lctx.stroke();
    const r = canvas.getBoundingClientRect();
    const leftSide = e.clientX - r.left > r.width / 2;
    loupe.style.left = leftSide ? "8px" : "auto"; loupe.style.right = leftSide ? "auto" : "8px";
    loupe.hidden = false;
  }

  function nearest(p) {
    const grab = 28 / (p.fit * E.view.s);
    let best = null, bd = grab;
    for (const k of ["head", "wrist", "floor"]) {
      const q = E.points[k];
      if (q && Math.hypot(q.x - p.x, q.y - p.y) < bd) { best = { kind: k }; bd = Math.hypot(q.x - p.x, q.y - p.y); }
    }
    E.corners.forEach((q, i) => { if (Math.hypot(q.x - p.x, q.y - p.y) < bd) { best = { kind: "corner", i }; bd = Math.hypot(q.x - p.x, q.y - p.y); } });
    return best;
  }

  canvas.addEventListener("pointerdown", (e) => {
    if (!E.base) return;
    const p = toImage(e);
    let target = nearest(p);
    const need = nextNeeded();
    if (!target && need) {
      if (need === "corner") {
        E.corners.push({ x: p.x, y: p.y });
        E.actions.push("corner");
        target = { kind: "corner", i: E.corners.length - 1 };
        if (E.corners.length === 4) checkCorners();
      } else {
        E.points[need] = { x: p.x, y: p.y };
        E.actions.push(need);
        target = { kind: need };
      }
    }
    if (!target) {                                            // empty area with nothing to place: pan when zoomed
      if (E.view.s > 1) { E.panFrom = { cx: e.clientX, cy: e.clientY, x0: E.view.x0, y0: E.view.y0 }; canvas.setPointerCapture(e.pointerId); }
      return;
    }
    E.drag = target;
    canvas.setPointerCapture(e.pointerId);
    draw(); showLoupe(p, e); updatePrompt();
  });
  canvas.addEventListener("pointermove", (e) => {
    if (E.panFrom) {
      const fit = toImage(e).fit;
      E.view.x0 = E.panFrom.x0 - (e.clientX - E.panFrom.cx) / (fit * E.view.s);
      E.view.y0 = E.panFrom.y0 - (e.clientY - E.panFrom.cy) / (fit * E.view.s);
      clampView(); draw(); return;
    }
    if (!E.drag) return;
    const p = toImage(e);
    const q = { x: Math.max(0, Math.min(canvas.width, p.x)), y: Math.max(0, Math.min(canvas.height, p.y)) };
    if (E.drag.kind === "corner") E.corners[E.drag.i] = q; else E.points[E.drag.kind] = q;
    E.moved = true;
    draw(); showLoupe(p, e);
  });
  const endDrag = () => {
    if (E.drag?.kind === "corner" && E.corners.length === 4) checkCorners();
    E.drag = null; E.panFrom = null; loupe.hidden = true; draw(); updatePrompt();
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  // quick client-side check; the server repeats it (and more) before measuring
  function checkCorners() {
    const c = E.corners;
    let sign = 0, ok = true;
    for (let i = 0; i < 4; i++) {
      const a = c[i], b = c[(i + 1) % 4], d = c[(i + 2) % 4];
      const cross = (b.x - a.x) * (d.y - b.y) - (b.y - a.y) * (d.x - b.x);
      if (Math.abs(cross) < 1e-6) ok = false;
      if (cross > 0 && sign < 0 || cross < 0 && sign > 0) ok = false;
      sign = sign || Math.sign(cross);
      if (Math.hypot(b.x - a.x, b.y - a.y) < 30) ok = false;
    }
    if (!ok || sign < 0) {
      const err = $("#measure-error");
      err.textContent = "Those corners cross or are out of order. Tap top-left, top-right, bottom-right, then bottom-left.";
      err.hidden = false;
      E.corners = []; E.actions = E.actions.filter((a) => a !== "corner");
    } else $("#measure-error").hidden = true;
  }

  $("#zoom-in").addEventListener("click", () => zoom(1.5, E.drag ? null : focusPoint()));
  $("#zoom-out").addEventListener("click", () => zoom(1 / 1.5));
  function focusPoint() {
    const need = nextNeeded();
    if (need === "wrist" || (!need && E.points.wrist && E.suggestion)) return E.points.wrist || E.suggestion;
    const last = E.actions[E.actions.length - 1];
    if (last === "corner") return E.corners[E.corners.length - 1];
    return (last && E.points[last]) || E.points.wrist || null;
  }
  $("#tap-undo").addEventListener("click", () => {
    const last = E.actions.pop();
    if (last === "corner") E.corners.pop();
    else if (last === "wrist" && E.suggestion) E.points.wrist = { x: E.suggestion.x, y: E.suggestion.y };
    else if (last === "floor" && E.floorSuggestion) E.points.floor = { ...E.floorSuggestion };
    else if (last) E.points[last] = null;
    draw(); updatePrompt();
  });
  $("#tap-reset").addEventListener("click", () => {
    E.corners = []; E.actions = []; E.measuredKey = null;
    E.points = { head: null, wrist: E.suggestion ? { x: E.suggestion.x, y: E.suggestion.y } : null,
                 floor: E.floorSuggestion ? { ...E.floorSuggestion } : null };
    E.view = { s: 1, x0: 0, y0: 0 };
    draw(); updatePrompt();
  });
  $("#retake").addEventListener("click", () => { resetEditor(); view("intro"); M.photo = "none"; M.status = "not_started"; });

  $("#photo-input").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) { stopCamera(); openEditor(f, { source: "upload" }); } e.target.value = ""; });
  $("#use-sample").addEventListener("click", async () => {
    stopCamera();
    openEditor(await (await fetch("/sample/sample-body.jpg")).blob(), { source: "sample" });
  });

  async function runMeasure(key) {
    const stale = () => key && key !== E.queuedKey;     // the markers moved again: a newer measurement is coming
    const err = $("#measure-error");
    err.hidden = true;
    const { head, wrist, floor } = E.points;
    const fd = new FormData();
    fd.append("image", E.blob, "photo.jpg");
    fd.append("points", JSON.stringify({ head_top: [head.x, head.y], wrist: [wrist.x, wrist.y], floor: [floor.x, floor.y] }));
    if (E.calib === "corners") fd.append("corners", JSON.stringify(E.corners.map((c) => [c.x, c.y])));
    M.status = "measuring";
    try {
      if (E.calib === "height") {
        const h = heightInput(), footwear = $("#ms-footwear").value;
        const m = await api("/api/photo/measure-height", { method: "POST", json: {
          head_top: [head.x, head.y], wrist: [wrist.x, wrist.y], floor: [floor.x, floor.y], height_cm: h, footwear } });
        if (stale()) return;
        if (!S.state.profile.height_cm) {                    // the height they just gave is their answer too
          applyState(await api("/api/session/profile", { method: "PUT", json: { values: { height_cm: m.height_cm } } }));
        }
        await stage(m.height_cm, m.wrist_to_floor_cm, "photo", {
          calibration: "entered_height", ratio: m.ratio, body_tilt_deg: m.body_tilt_deg, footwear, entered_height_cm: h,
          warnings: m.warnings, assumed_error_cm: m.assumed_error_cm, wrist_side: E.suggestion?.side || null,
          wrist_from_suggestion: !!E.suggestion, image: E.source });
        return;
      }
      const m = await api("/api/photo/measure", { method: "POST", body: fd });
      if (stale()) return;
      await stage(m.height_cm, m.wrist_to_floor_cm, "photo", {
        calibration: m.calibration, sharpness: m.sharpness, reprojection_mm: m.reprojection_mm, warnings: m.warnings,
        wrist_side: E.suggestion?.side || null, wrist_from_suggestion: !!E.suggestion, image: E.source,
      });
    } catch (ex) { err.textContent = ex.message; err.hidden = false; M.status = "error"; M.last_error = ex.message; }
  }

  // ------------------------------------------------------------------ result and confirmation
  async function stage(height_cm, wrist_to_floor_cm, source, detail) {
    applyState(await api("/api/session/measurement", { method: "POST", json: { height_cm, wrist_to_floor_cm, source, detail } }));
    M.status = "measured_awaiting_confirmation";
    emit("fitter:camera", { key: `measured:${height_cm}:${wrist_to_floor_cm}`, instruction: `Measured from the photo: height ${height_cm} cm and wrist-to-floor ${wrist_to_floor_cm} cm (estimates). `
      + `Read them back and ask the golfer to check the markers on screen. If they say yes, call confirm_measurement; they can also tap Confirm & continue.` });
    // while the golfer is placing markers the photo must stay put under their finger; otherwise show the result
    if (M.view !== "edit") $("#measure-result").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function render(st) {
    $("#use-simulated").hidden = !S.demo?.simulated_photo;
    if (S.demo?.simulated_photo) $("#use-simulated").textContent = `${S.demo.persona.split(",")[0]}'s photo result (simulated)`;
    const box = $("#measure-result");
    const m = st.measurement_staged || st.measurement;
    if (!m) { box.hidden = true; box.innerHTML = ""; return; }
    const p = st.profile, staged = !!st.measurement_staged;
    const cal = m.detail?.calibration;
    const source = m.source === "photo"
      ? { entered_height: "Camera estimate using your height", paper_corners: "Camera estimate · A4 paper scale" }[cal] || "Camera estimate · printed sheet scale"
      : { simulated: "Simulated photo result (demo data)", tape: "Tape measure" }[m.source] || m.source;
    const issues = [...(m.detail?.warnings || [])];
    const big = [];
    if (cal !== "entered_height" && p.height_cm && Math.abs(m.height_cm - p.height_cm) > T.cross_check_cm) big.push(`height (${Math.abs(m.height_cm - p.height_cm).toFixed(1)} cm)`);
    if (p.wrist_to_floor_cm && Math.abs(m.wrist_to_floor_cm - p.wrist_to_floor_cm) > T.cross_check_cm) big.push(`wrist-to-floor (${Math.abs(m.wrist_to_floor_cm - p.wrist_to_floor_cm).toFixed(1)} cm)`);
    if (big.length && m.source !== "tape") issues.push(`Big difference from your answers: ${big.join(" and ")}. Common causes: standing in front of the wall, the head marker on the forehead, the floor marker under the toes, or a home measurement taken without shoes.`);
    const chk = staged ? st.measurement_check : null;
    const boundary = chk?.tape_check ? `<p class="boundary">A small measurement change could affect your build. Please check with a tape.</p>
        <p class="note">${esc(chk.text)}</p>` : chk?.level === "close call" ? `<p class="note">${esc(chk.text)}</p>` : "";
    const heightLabel = cal === "entered_height" ? "Entered height" : "Height";
    const heightNote = cal === "entered_height" ? `${m.detail.footwear === "barefoot" ? "barefoot, +2.5 cm for golf shoes" : "in golf shoes"}: sets the scale, not measured` : "";
    const estimate = m.source === "photo" ? (cal === "entered_height"
      ? "Estimated using your entered height: we assume about ±3 cm. Entering your height doesn't measure it."
      : cal === "paper_corners" ? "Scale from the paper corners you marked: we assume about ±2.5 cm." : "Scale from the sheet's printed markers: we assume about ±2 cm.") : "";
    box.innerHTML = `<span class="source-label">${esc(source)}</span>
      <div class="results">
        <div class="metric"><div class="k">${heightLabel}</div><div class="v">${esc(m.height_cm)} cm</div><div class="d">${esc(heightNote)}</div></div>
        <div class="metric"><div class="k">Wrist-to-floor</div><div class="v">${esc(m.wrist_to_floor_cm)} cm</div><div class="d">${m.source === "photo" ? "estimate" : m.source === "tape" ? "measured" : "demo"}</div></div>
      </div>
      ${issues.map((w) => `<p class="crosscheck">${esc(w)}</p>`).join("")}
      ${boundary}
      ${estimate ? `<p class="note">${esc(estimate)} Not yet checked against tape measurements on real people.</p>` : ""}
      ${staged ? `<button type="button" class="btn big" id="measure-confirm">Confirm &amp; continue</button>
        <div class="row">${E.base ? '<button type="button" class="btn quiet" id="adjust-markers">Adjust markers</button>' : ""}
          ${m.source === "photo" ? '<button type="button" class="btn quiet" id="result-retake">Retake</button>' : ""}
          <button type="button" class="btn quiet" id="open-tape">Use a tape</button></div>`
        : `<p class="note">✓ Saved: height ${esc(m.height_cm)} cm and wrist-to-floor ${esc(m.wrist_to_floor_cm)} cm (${esc(source.toLowerCase())}).</p>
          <button type="button" class="btn big" id="see-fit">Continue to your swing</button>`}`;
    box.hidden = false;
    $("#measure-confirm")?.addEventListener("click", confirm);
    $("#see-fit")?.addEventListener("click", () => window.Fitter.showScreen("swing-screen"));
    $("#adjust-markers")?.addEventListener("click", () => { view("edit"); $("#tap-card").scrollIntoView({ behavior: "smooth" }); });
    $("#result-retake")?.addEventListener("click", () => { resetEditor(); view("intro"); openCamera(); });
    $("#open-tape")?.addEventListener("click", () => { $("#tape").open = true; $("#tape").scrollIntoView({ behavior: "smooth" }); });
  }

  async function confirm(e) {
    if (!e.isTrusted) return;                       // a scripted click never commits; a real tap or a spoken yes does
    return commit();
  }
  async function commit() {
    if (!S.state?.measurement_staged) return { ok: false, error: "nothing_measured" };
    try {
      applyState(await api("/api/session/measurement/confirm", { method: "POST" }));
      M.status = "confirmed";
      emit("fitter:camera", { key: "confirmed", instruction: "The measurement is saved and the golfer is moving on to the swing check." });
      // refresh the fit quietly when everything else is answered, then move on to the swing
      if (!(S.state.missing_fields || []).length) {
        const r = await api("/api/session/recommendation", { method: "POST" }).catch(() => null);
        if (r) applyState(r.state);
      }
      window.Fitter.showScreen("swing-screen");
      return { ok: true, saved: S.state.measurement };
    } catch (ex) { alert(ex.message); return { ok: false, error: ex.message }; }
  }

  $("#use-simulated").addEventListener("click", () => {
    const m = S.demo.simulated_photo;
    stage(m.height_cm, m.wrist_to_floor_cm, "simulated", { quality: m.quality }).catch((ex) => alert(ex.message));
  });
  $("#manual-use").addEventListener("click", () => {
    const h = Number($("#manual-height").value), w = Number($("#manual-wtf").value);
    if (!(h >= 120 && h <= 220 && w >= 55 && w <= 120)) { alert("Enter a height of 120–220 cm and a wrist-to-floor of 55–120 cm."); return; }
    stage(h, w, "tape").catch((ex) => alert(ex.message));
  });

  // ------------------------------------------------------------------ lifecycle
  function onStep(n) {
    M.step_open = n === 3;
    if (n === 3) showMethod();
    if (n !== 3) { stopCamera(); if (M.view === "live") view("intro"); }
  }
  document.addEventListener("visibilitychange", () => { if (document.hidden && L.stream) { stopCamera(); view("intro"); } });
  window.addEventListener("pagehide", stopCamera);

  function reset() {
    stopCamera(); resetEditor(); view("intro");
    Object.assign(M, { photo: "none", status: "not_started", points_placed: 0, last_error: null, key: null, countdown: null });
  }

  view("intro");
  showMethod();
  return {
    setMethod: (m) => { const r = document.querySelector(`input[name="ms-method"][value="${m}"]`); if (r) { r.checked = true; showMethod(); } },
    openCamera, status, render, onStep, reset, thresholds: T,
    canOpen: () => method() !== "height" || !!heightInput(),
    commit,
    setFacing: (f) => { M.camera.facing = f === "user" ? "user" : "environment"; },
    // read-only snapshot for tests and debugging
    _debug: () => JSON.parse(JSON.stringify({ view: E.view, points: E.points, corners: E.corners, suggestion: E.suggestion,
      size: [canvas.width, canvas.height], calib: E.calib, key: M.key, mirrored: M.camera.mirrored, floorSuggestion: E.floorSuggestion, pending: !!E.pending })),
  };
})();
