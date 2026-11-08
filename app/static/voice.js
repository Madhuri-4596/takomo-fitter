"use strict";

// Voice and text conversation with the ElevenLabs agent (@elevenlabs/client, loaded as
// window.ElevenLabsClient). Client tools act on the same session as the form, via
// window.Fitter from app.js. The tools can only stage answers: confirming them needs a
// real tap on the review card, handled here and enforced with event.isTrusted.

(() => {
  const $ = (sel) => document.querySelector(sel);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const V = {
    conv: null, mode: null, lastMode: "voice", connected: false, muted: false, config: null, consented: false,
    switching: false, lastCam: null, lastProfile: null, poll: null, lastTyped: null,
  };

  // ------------------------------------------------------------------ microphone: which one, and how it's processed
  // The SDK opens the microphone itself (LiveKit on the WebRTC path). Wrap getUserMedia to see the track it
  // actually gets, make sure echo cancellation, noise suppression and voice isolation are on where the browser
  // supports them, and show the golfer which device is really in use.
  const MIC_KEY = "takomo_mic";
  const gum = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
  if (gum) {
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await gum(constraints);
      const track = stream.getAudioTracks()[0];
      if (track && V.capturing) captureMic(track);
      return stream;
    };
  }
  async function captureMic(track) {
    const supported = navigator.mediaDevices.getSupportedConstraints?.() || {};
    const want = {};
    for (const k of ["echoCancellation", "noiseSuppression", "autoGainControl", "voiceIsolation"]) if (supported[k]) want[k] = true;
    try { await track.applyConstraints(want); } catch { /* keep what the browser gave */ }
    V.micTrack = track;
    showMicInfo();
    track.addEventListener("ended", () => { if (V.micTrack === track) { V.micTrack = null; showMicInfo(); } });
  }
  function micInfo() {
    const t = V.micTrack;
    if (!t) return null;
    const s = t.getSettings ? t.getSettings() : {};
    return { label: t.label || "microphone", deviceId: s.deviceId || null, echoCancellation: s.echoCancellation ?? null,
             noiseSuppression: s.noiseSuppression ?? null, autoGainControl: s.autoGainControl ?? null,
             voiceIsolation: s.voiceIsolation ?? null, chosen: selectedMic() || "default" };
  }
  function showMicInfo() {
    const box = $("#mic-info"), m = micInfo();
    if (!m) { box.textContent = ""; box.classList.remove("warn"); return; }
    const flag = (v, name) => (v === true ? `${name} on` : v === false ? `${name} off` : null);
    const parts = [flag(m.echoCancellation, "echo cancellation"), flag(m.noiseSuppression, "noise suppression"),
                   flag(m.voiceIsolation, "voice isolation")].filter(Boolean);
    const wrong = m.chosen !== "default" && m.deviceId && m.deviceId !== m.chosen;
    box.textContent = `Using: ${m.label}${parts.length ? " · " + parts.join(" · ") : ""}`
      + (wrong ? " · not the microphone you picked: check the headset is connected" : "");
    box.classList.toggle("warn", !!wrong || m.echoCancellation === false || m.noiseSuppression === false);
  }
  const selectedMic = () => $("#mic-select").value;
  async function listMics() {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput" && d.deviceId !== "default");
    let saved = null;
    try { saved = localStorage.getItem(MIC_KEY); } catch { /* storage off */ }
    const sel = $("#mic-select"), cur = sel.value || saved || "";
    sel.innerHTML = '<option value="">Default microphone</option>' +
      devs.map((d, i) => `<option value="${esc(d.deviceId)}">${esc(d.label || `Microphone ${i + 1}`)}</option>`).join("");
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  navigator.mediaDevices?.addEventListener?.("devicechange", listMics);
  $("#mic-select").addEventListener("change", async () => {
    try { localStorage.setItem(MIC_KEY, selectedMic()); } catch { /* storage off */ }
    if (V.connected && V.mode === "voice" && V.conv?.changeInputDevice) {
      V.capturing = true;
      try { await V.conv.changeInputDevice({ inputDeviceId: selectedMic() || "default" }); }
      catch (e) { line("sys", `Couldn't switch microphone: ${e.message}`); }
      finally { V.capturing = false; }
    }
    showMicInfo();
  });
  // live input level, so the golfer can see they're being heard (and how loud the room is)
  function meter() {
    const bar = $("#mic-level");
    if (V.connected && V.mode === "voice" && V.conv?.getInputVolume) {
      bar.style.width = `${Math.round(Math.min(1, V.conv.getInputVolume() * 1.4) * 100)}%`;
      requestAnimationFrame(meter);
    } else bar.style.width = "0";
  }

  // ------------------------------------------------------------------ the chat screen
  function openPanel() { if (window.Fitter.screen() !== "chat") window.Fitter.showScreen("chat"); }

  // one status everywhere: the pill on the conversation screen and the dock on the other steps
  function setStatus(text) {
    V.status = text;
    const pill = $("#voice-status");
    pill.textContent = text;
    pill.classList.toggle("speaking", text === "Speaking");
    pill.classList.toggle("muted", text === "Mic muted");
    $("#dock-status").textContent = text;
    $("#voice-dock").classList.toggle("speaking", text === "Speaking");
    $("#voice-dock").classList.toggle("muted", text === "Mic muted");
    showDock();
  }
  function showDock() {
    $("#voice-dock").hidden = !V.connected || window.Fitter.screen() === "chat";
    $("#dock-mute").hidden = V.mode !== "voice";
    $("#dock-mute").textContent = V.muted ? "Unmute" : "Mute";
  }
  document.addEventListener("fitter:screen", showDock);
  $("#voice-dock").addEventListener("click", (e) => {
    if (e.target.closest("#dock-mute")) { e.stopPropagation(); toggleMute(); return; }
    window.Fitter.showScreen("chat");
  });
  $("#dock-mute").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); toggleMute(); } });
  function setLive(live) {
    $("#voice-status").classList.toggle("live", live);
    $("#mic").classList.toggle("live", live && V.mode === "voice" && !V.muted);
    $("#mic").classList.toggle("muted", live && V.muted);
    $("#mic").setAttribute("aria-label", !live ? "Start talking" : V.muted ? "Unmute" : "Mute");
  }

  function line(role, text) {
    const last = $("#transcript").lastElementChild;
    if (role === "sys" && last?.className === "sys" && last.textContent === text) return;   // no repeated status lines
    const li = document.createElement("li");
    li.className = role;
    li.textContent = text;
    $("#transcript").appendChild(li);
    li.scrollIntoView({ block: "nearest" });
  }

  function alertBox(text, actions = []) {
    $("#voice-alert-text").textContent = text;
    const row = $("#voice-alert-actions");
    row.innerHTML = "";
    for (const [label, fn, quiet] of actions) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn" + (quiet ? " quiet" : ""); b.textContent = label;
      b.addEventListener("click", fn);
      row.appendChild(b);
    }
    $("#voice-alert").hidden = false;
    V.consented = true;
    showControls();                                  // the alert lives in the conversation area: make sure it's showing
    openPanel();
  }
  const hideAlert = () => ($("#voice-alert").hidden = true);
  const useForm = ["Use the quiz", () => { hideAlert(); window.Fitter.show(1); }, true];

  function showControls() {
    const on = V.connected;
    $("#consent").hidden = on || V.consented;
    $("#talk").hidden = !$("#consent").hidden;
    $("#composer").hidden = !(V.mode === "text" || V.typing);
    $("#voice-controls").hidden = !on;
    $("#mute").hidden = V.mode !== "voice";
    $("#switch-mode").textContent = V.mode === "text" ? "Talk instead" : "Type instead";
  }

  // ------------------------------------------------------------------ consent / privacy
  async function loadConfig() {
    try { V.config = await (await fetch("/api/voice/config")).json(); } catch { V.config = { configured: false }; }
    const list = $("#consent-list");
    if (!V.config.configured) {
      list.innerHTML = "<li>The voice assistant isn't set up on this server. The quick quiz uses the same fitting engine.</li>";
      $("#start-voice").hidden = $("#start-text").hidden = true;
      if (!$("#consent-quiz")) $("#start-text").insertAdjacentHTML("afterend", '<button type="button" class="btn big" id="consent-quiz" data-screen="step-1">Use the quick quiz</button>');
      return;
    }
    // read from the agent's actual ElevenLabs settings, not hard-coded
    const p = V.config.privacy || {};
    const days = p.retention_days > 0 ? `${p.retention_days} days` : p.retention_days === 0 ? "no time at all" : "ElevenLabs' default period";
    list.innerHTML = [
      "<b>Voice:</b> what you say is sent to ElevenLabs, which turns it into text and speaks the replies. Typing goes to ElevenLabs too.",
      p.record_voice === false ? "Audio recording is switched off for this assistant." : "This assistant's audio may be recorded by ElevenLabs.",
      p.zero_retention_mode ? "Transcripts are not kept (zero-retention mode)." : `The conversation transcript is kept by ElevenLabs for ${days}, then deleted.`,
      "Your fitting answers are kept on our server for 3 hours after your last change, so a reload doesn't lose them, then deleted.",
    ].map((t) => `<li>${t}</li>`).join("");
  }
  $("#start-voice").addEventListener("click", () => { V.consented = true; start("voice"); });
  $("#start-text").addEventListener("click", () => { V.consented = true; V.typing = true; showControls(); start("text"); });

  // ------------------------------------------------------------------ client tools
  const agentState = (st) => { const { full_recommendation, ...rest } = st; return { ok: true, ...rest }; };

  function tool(label, fn) {
    return async (params) => {
      line("sys", label);
      try { return await fn(params || {}); }
      catch (e) {
        line("sys", `That didn't work: ${e.message}`);
        return { ok: false, error: e.status === 504 ? "timeout" : "tool_failed", message: e.message,
                 say: "Tell the golfer briefly that this step failed and offer to continue by typing or with the form. Confirmed answers are kept." };
      }
    };
  }

  const clientTools = {
    get_fitting_state: tool("Checking your answers…", async () => agentState(await window.Fitter.sync())),

    propose_profile_update: tool("Noting what you said…", async ({ updates }) => {
      if (!Array.isArray(updates) || !updates.length || updates.length > 12 ||
          updates.some((u) => !u || typeof u.field !== "string" || u.value === undefined || u.value === null)) {
        return { ok: false, error: "invalid_parameters", message: "updates must be a list of {field, value, unit?, kind?}" };
      }
      const clean = updates.map((u) => ({ field: u.field, value: String(u.value), unit: u.unit || null, kind: u.kind || null, heard_text: u.heard_text || null }));
      const r = await window.Fitter.api("/api/session/propose", { method: "POST", json: { updates: clean } });
      window.Fitter.applyState(r.state);
      return { ok: true, results: r.results,
               note: "Not saved yet. Read back each read_back text and ask if it's right. Only when the golfer says yes, call confirm_profile_update." };
    }),

    open_measurement_step: tool("Opening body measurement…", async () => {
      const r = window.Fitter.openMeasurement();      // the measuring screen; the camera starts only if the height is known
      return { ok: true, opened: true, ...r,
               note: !r.camera_started ? "The measurement screen is open but the camera hasn't started. Call get_measurement_status and help with what it reports."
                 : r.method === "paper" ? "No height was given, so the camera measures height and wrist-to-floor using a blank A4 sheet as the scale. "
                   + "Tell the golfer: tape any blank A4 sheet upright on the wall beside you, stand with your back flat against the wall, phone propped at waist height 3 to 4 metres away. "
                   + "After the capture they tap the sheet's four corners. If they'd rather just give their height, they can pick 'I know my height' on screen. "
                   + "Camera changes arrive as [camera] messages; describe only what get_measurement_status reports."
                 : "Camera starting, using the golfer's height as the scale; the browser may ask for permission. Camera changes arrive as [camera] messages; describe only what get_measurement_status reports." };
    }),

    // the golfer said yes to the measurement read-back: save it (only a measurement on screen can be saved)
    confirm_measurement: tool("Saving your measurement…", async () => {
      if (!window.Fitter.state()?.measurement_staged) return { ok: false, error: "nothing_measured", message: "There's no measurement waiting. Call get_measurement_status." };
      const r = await window.Measure.commit();
      if (r?.ok) line("sys", "Saved: your measurements");
      return r?.ok ? { ok: true, saved: r.saved, next: "The swing check is on screen now." } : { ok: false, error: r?.error || "failed" };
    }),

    get_measurement_status: tool("Checking the camera…", async () => ({ ok: true, ...window.Fitter.measurementStatus() })),

    // the golfer said "yes" to the read-back: save the staged answers (only staged ones can be saved)
    confirm_profile_update: tool("Saving your answers…", async ({ fields }) => {
      const staged = Object.keys(window.Fitter.state().staged_for_review || {});
      const keys = Array.isArray(fields) && fields.length ? fields.filter((f) => staged.includes(f)) : staged;
      if (!keys.length) return { ok: false, error: "nothing_staged", message: "There is nothing waiting to be saved. Stage the answer with propose_profile_update first." };
      const r = await window.Fitter.api("/api/session/confirm", { method: "POST", json: { keys } });
      V.ownChange = true;
      window.Fitter.applyState(r.state);
      line("sys", `Saved: ${r.confirmed.map((k) => FIELD_LABEL[k] || k).join(", ")}`);
      return { ok: true, saved: r.confirmed, profile: r.state.profile, missing_fields: r.state.missing_fields };
    }),

    get_fit_recommendation: tool("Working out your fit…", async () => {
      const r = await window.Fitter.api("/api/session/recommendation", { method: "POST" }, 25000);
      window.Fitter.applyState(r.state);           // the card on screen now shows this exact result
      return { ...r.tool_result, shown_on_screen: !!r.tool_result.ok };
    }),
  };

  // ------------------------------------------------------------------ session lifecycle
  async function micCheck() {
    V.capturing = false;
    if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error("No microphone"), { name: "NotFoundError" });
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
  }

  function setStarting(on) {
    V.starting = on;
    for (const id of ["#start-voice", "#start-text", "#switch-mode"]) $(id).disabled = on;
    document.querySelectorAll("#voice-alert-actions .btn").forEach((b) => (b.disabled = on));
  }

  // the first thing the assistant says, from what the app already knows: never "let me check what you filled in"
  // when nothing was filled in
  const ASK_FIRST = { score_bucket: "What do you usually shoot over 18 holes?",
    seven_iron_speed_or_carry: "How far does your 7-iron carry on a normal shot?",
    characteristics: "What should your new irons do for you: forgiveness, distance, feel?",
    flight: "Would you like your irons to fly lower, higher or about the same?",
    glove: "What glove size do you buy?", age: "How old are you?", sex: "Are you looking for men's or women's fitting?",
    handedness: "Are you right- or left-handed?", tempo: "Would you say your swing is smooth, moderate or quick?",
    height_cm: "How tall are you?", wrist_to_floor_cm: "Do you know your wrist-to-floor measurement?" };
  function greeting(reconnect) {
    const st = window.Fitter.state() || {};
    const p = st.profile || {}, miss = st.missing_fields || [];
    const answered = Object.entries(p).filter(([k, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length) && k !== "speed_or_carry").length;
    const next = ASK_FIRST[miss.find((k) => ASK_FIRST[k])] || "";
    if (reconnect) return `I'm back. Let's carry on. ${next}`.trim();
    if (st.full_recommendation && !miss.length) return "Hi, I'm Takomo's fitting assistant. Your fit is ready on screen. Ask me anything about it, or I can explain the least certain part.";
    if (!answered) return `Hi, I'm Takomo's fitting assistant. I'll ask a few quick questions about your game, one at a time. ${ASK_FIRST.score_bucket}`;
    return `Hi, I'm Takomo's fitting assistant. You've already given me ${answered} answer${answered > 1 ? "s" : ""}, so I'll only ask for what's missing. ${next}`.trim();
  }

  async function start(mode, { reconnect = false } = {}) {
    if (V.starting) return;                          // never two conversations at once (they talk over each other)
    V.userEnded = false;
    setStarting(true);
    try { await startSession(mode, { reconnect }); }
    finally { setStarting(false); }
  }

  async function startSession(mode, { reconnect }) {
    hideAlert();
    openPanel();
    $("#consent").hidden = true;
    V.lastMode = mode;
    if (V.conv) {                                    // end whatever is still open before starting again
      V.switching = true;
      try { await V.conv.endSession(); } catch { /* already closed */ }
      V.switching = false;
      V.conv = null; V.connected = false;
    }
    if (!window.ElevenLabsClient?.Conversation) {
      alertBox("The voice library didn't load. Check your internet connection.", [["Try again", () => start(mode)], useForm]);
      return;
    }
    setStatus("Connecting…");
    if (mode === "voice") {
      try { await micCheck(); }
      catch (e) {
        const denied = e.name === "NotAllowedError" || e.name === "SecurityError";
        setStatus("Not connected");
        alertBox(denied ? "Microphone access is blocked. You can allow it in your browser settings, or type to the fitter instead."
                        : "No microphone was found. You can type to the fitter instead.",
          [["Type instead", () => start("text")], ["Try again", () => start("voice"), true], useForm]);
        return;
      }
    }
    if (mode === "voice") await listMics().catch(() => {});        // device names are only visible after permission
    let auth;
    try { auth = await window.Fitter.api("/api/voice/session", { method: "POST", json: { mode } }); }
    catch (e) {
      setStatus("Not connected");
      alertBox(e.message, [["Try again", () => start(mode)], useForm]);
      return;
    }
    V.capturing = mode === "voice";                  // see the microphone track the SDK opens
    try {
      V.conv = await window.ElevenLabsClient.Conversation.startSession({
        // voice over WebRTC: the browser's echo cancellation keeps the agent from hearing (and answering) itself
        ...(mode === "voice" ? { conversationToken: auth.conversation_token, connectionType: "webrtc",
                                 ...(selectedMic() ? { inputDeviceId: selectedMic() } : {}) }
                             : { signedUrl: auth.signed_url, connectionType: "websocket" }),
        textOnly: mode === "text",
        dynamicVariables: { secret__fitting_session: window.Fitter.token(), greeting: greeting(reconnect) },
        clientTools,
        onConnect: () => {
          V.connected = true; V.mode = mode; V.lastCam = null; V.camSent = {}; setLive(true); showControls(); showDock();
          setTimeout(() => { V.capturing = false; }, 3000);         // the SDK opens its mic during connect
          if (mode === "voice") { meter(); showMicInfo(); }
          setStatus(mode === "voice" ? "Listening" : "Connected · typing");
          if (reconnect) {
            const recent = [...document.querySelectorAll("#transcript li:not(.sys)")].slice(-6).map((li) => `${li.className}: ${li.textContent}`).join("\n");
            V.conv.sendContextualUpdate(`[app] Reconnected after a dropped connection. Call get_fitting_state and carry on. Recent transcript:\n${recent}`);
          }
          startPolling();
        },
        onDisconnect: onDisconnect,
        onError: (message) => line("sys", `Problem: ${message}`),
        onMessage: ({ role, source, message }) => {
          const who = role || (source === "ai" ? "agent" : "user");
          if (who === "user" && V.lastTyped && message.trim() === V.lastTyped) { V.lastTyped = null; return; }
          line(who === "agent" ? "agent" : "user", message);
        },
        onModeChange: ({ mode: m }) => V.mode === "voice" && setStatus(m === "speaking" ? "Speaking" : "Listening"),
        onInterruption: () => { V.stats.interruptions++; },
        onAgentToolResponse: (r) => { if (r?.tool_name === "get_fit_recommendation") window.Fitter.sync().catch(() => {}); },
      });
    } catch (e) {
      setStatus("Not connected");
      const denied = e?.name === "NotAllowedError";
      alertBox(denied ? "Microphone access is blocked. Type to the fitter instead." : `Couldn't connect to the fitter: ${e?.message || e}`,
        [[denied ? "Type instead" : "Try again", () => start(denied ? "text" : mode)], useForm]);
    }
  }

  function onDisconnect(details) {
    const wasMode = V.mode;
    V.connected = false; V.conv = null; V.mode = null; setLive(false); stopPolling(); showControls(); showDock();
    if (V.switching) return;
    // dropped without the golfer ending it (time limit, network blip): reconnect quietly with the context,
    // at most 3 times in 5 minutes so a real outage still surfaces
    const recent = (V.autoReconnects || []).filter((t) => Date.now() - t < 300000);
    if (!V.userEnded && wasMode && recent.length < 3) {
      V.autoReconnects = [...recent, Date.now()];
      setStatus("Reconnecting…");
      line("sys", "The connection ended. Reconnecting…");
      start(wasMode, { reconnect: true });
      return;
    }
    setStatus("Not connected");
    if (details?.reason === "error") {
      alertBox("The connection dropped. Your confirmed answers are safe.",
        [["Reconnect", () => start(wasMode || V.lastMode, { reconnect: true })], ["Type instead", () => start("text", { reconnect: true }), true], useForm]);
    } else {
      line("sys", details?.reason === "agent" ? "The fitter ended the conversation." : "Conversation ended.");
      alertBox("Conversation ended. Your confirmed answers are kept.",
        [["Talk again", () => start("voice", { reconnect: true })], ["Type", () => start("text", { reconnect: true }), true]]);
    }
  }

  // webhook mode: the fit is computed server-side, so keep the card in sync while talking
  function startPolling() {
    stopPolling();
    V.poll = setInterval(() => window.Fitter.sync().catch(() => {}), 2500);
  }
  function stopPolling() { clearInterval(V.poll); V.poll = null; }

  $("#end").addEventListener("click", async () => { V.userEnded = true; try { await V.conv?.endSession(); } catch { onDisconnect({ reason: "user" }); } });
  function toggleMute() {
    if (!V.conv) return;
    V.muted = !V.muted;
    V.conv.setMicMuted(V.muted);
    $("#mute").setAttribute("aria-pressed", String(V.muted));
    $("#mute").textContent = V.muted ? "Unmute" : "Mute";
    setStatus(V.muted ? "Mic muted" : "Listening");
    $("#dock-mute").textContent = V.muted ? "Unmute" : "Mute";
    setLive(true);
  }
  $("#mute").addEventListener("click", toggleMute);
  // the big mic: start talking; while talking it mutes; from typing it switches to voice
  $("#mic").addEventListener("click", () => {
    V.consented = true;
    if (!V.connected) { if (!V.starting) start("voice"); return; }
    if (V.mode === "voice") toggleMute();
    else { line("sys", "Switched to voice."); start("voice", { reconnect: true }); }
  });
  $("#switch-mode").addEventListener("click", () => {
    V.consented = true;
    if (!V.connected) { V.typing = true; showControls(); $("#composer-input").focus(); return; }
    const next = V.mode === "voice" ? "text" : "voice";
    if (next === "text") V.typing = true;            // keep the text box once the golfer has chosen to type
    line("sys", next === "voice" ? "Switched to voice." : "Switched to typing.");
    start(next, { reconnect: true });                // start() ends the current session first
  });

  // suggestion chips ("Why stiff?") and anything else that should be asked as a typed message
  async function ask(text) {
    openPanel();
    if (!V.connected) {
      V.consented = true;
      if (!V.starting) await start("text");
      for (let i = 0; i < 60 && !V.connected; i++) await new Promise((ok) => setTimeout(ok, 250));
      if (!V.connected) return;
    }
    line("user", text);
    V.lastTyped = text;
    V.conv.sendUserMessage(text);
  }
  V.stats = { interruptions: 0 };
  // test hook: how often the golfer (or noise) cut the agent off
  window.Voice = { ask, micInfo, listMics, stats: () => ({ ...V.stats }), mode: () => (V.connected ? V.mode : null) };
  document.addEventListener("fitter:fit-ready", () => {
    const li = document.createElement("li");
    li.className = "sys";
    li.innerHTML = '<button type="button" class="link" data-screen="verdict">Your fit is ready: see it →</button>';
    $("#transcript").appendChild(li);
    li.scrollIntoView({ block: "nearest" });
  });

  $("#composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#composer-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    if (!V.connected) {
      if (!V.starting) await start("text");
      for (let i = 0; i < 60 && !V.connected; i++) await new Promise((ok) => setTimeout(ok, 250));
      if (!V.connected) { input.value = text; return; }
    }
    line("user", text);
    V.lastTyped = text;
    V.conv.sendUserMessage(text);
  });
  $("#composer-input").addEventListener("input", () => V.conv?.sendUserActivity());

  // ------------------------------------------------------------------ app -> agent context
  document.addEventListener("fitter:camera", (e) => {
    const { key, instruction } = e.detail;
    if (!V.connected || key === V.lastCam) return;       // never repeat the same camera state
    // and don't re-announce an instruction given recently, even if the state flickered in between
    V.camSent = V.camSent || {};
    if (key === "starting") delete V.camSent.ready;     // a new camera session may announce "ready" once more
    if (key === "ready" && V.camSent.ready) return;      // once per camera session: the green pill shows it after that
    if (Date.now() - (V.camSent[key] || 0) < 20000) return;
    V.camSent[key] = Date.now();
    V.lastCam = key;
    if (key === "countdown" || key === "starting" || key === "swing_recording") {
      // context only: the agent must not talk while the golfer holds still
      V.conv.sendContextualUpdate(`[camera] ${instruction} Do not speak until the photo is taken.`);
      return;
    }
    // a state change the golfer should hear: an app message prompts one short spoken instruction
    V.conv.sendUserMessage(`[camera] (message from the app, not the golfer) ${instruction}`);
  });
  document.addEventListener("fitter:quiet", () => V.connected && V.conv.sendUserActivity());

  const FIELD_LABEL = { age: "age", sex: "fitting rules", handedness: "handedness", rounds_per_year: "rounds a year",
    score: "usual score", seven_iron_speed: "7-iron club speed", seven_iron_carry: "7-iron carry", characteristics: "priorities",
    flight: "ball flight", tempo: "tempo", height: "height", wrist_to_floor: "wrist-to-floor", glove: "glove size",
    score_representative: "score rounds", lose_shots: "where you lose shots", ball_start: "start direction",
    ball_curve: "curve", ball_height: "flight height", current_clubs: "current clubs" };

  document.addEventListener("fitter:state", (e) => {
    const st = e.detail;
    const profile = JSON.stringify(st.profile);
    const own = V.ownChange;
    V.ownChange = false;
    if (V.connected && V.lastProfile !== null && profile !== V.lastProfile && !own) {
      V.conv.sendContextualUpdate("[app] The golfer's confirmed answers changed. Any earlier recommendation may be out of date; call get_fitting_state before describing it.");
    }
    V.lastProfile = profile;
  });
  document.addEventListener("fitter:reset", async () => {
    V.userEnded = true;
    try { await V.conv?.endSession(); } catch { /* closed */ }
    $("#transcript").innerHTML = "";
  });

  document.addEventListener("fitter:ready", () => { loadConfig(); showControls(); });
})();
