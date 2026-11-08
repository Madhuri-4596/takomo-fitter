"use strict";

// The real Takomo fitter on the frontend's call screen: our ElevenLabs Conversational AI agent instead of the
// scripted demo golfer. Hands-free voice over WebRTC (browser echo cancellation, noise suppression and auto gain;
// the agent filters background voices and waits on half-said numbers), or typing if the mic was declined.
// It shares the backend session with backend.js, so what it confirms fills the frontend's tiles and its verdict.
// Uses the frontend's own elements (#cap-fit captions, #mic, #form, tiles) through window.FitterUI.

window.FitterAgent = (() => {
  const A = { conv: null, mode: null, live: false, used: false, starting: false, muted: false, userEnded: false,
              retries: [], state: null, lastTyped: null };
  const $ = (id) => document.getElementById(id);
  const UI = () => window.FitterUI;
  const api = (path, opts) => window.FitterBackend.api(path, opts);

  // ------------------------------------------------------------------ our answers -> the frontend's tiles
  const BUCKET = { 1: "under 71", 2: "71–77", 3: "78–84", 4: "85–92", 5: "92+" };
  const ROUNDS_TILE = { "Under 10": "under 10", "10-30": "10–30", "31-50": "31–50", "Over 50": "50+" };
  const FLIGHT_WORD = { low: "Low", mid: "Mid", high: "High" };
  function flightTile(p) {
    const cur = p.flight_current, want = p.flight;
    if (cur && want) {
      if (cur === want) return `${FLIGHT_WORD[cur]}, keep it`;
      const up = ["low", "mid", "high"].indexOf(want) > ["low", "mid", "high"].indexOf(cur);
      return `${FLIGHT_WORD[cur]} → ${up ? "higher" : "lower"}`;
    }
    return cur ? FLIGHT_WORD[cur] : want ? `→ ${FLIGHT_WORD[want].toLowerCase()}` : null;
  }
  function tiles(st) {
    const p = st.profile || {}, staged = st.staged_for_review || {}, out = {};
    const said = (v, src = "said") => ({ v, src });
    if (p.age) out.age = said(p.age);
    if (p.sex) out.sex = said(p.sex === "female" ? "Women’s" : "Men’s");
    if (p.rounds_per_year) out.rounds = said(ROUNDS_TILE[p.rounds_per_year] || p.rounds_per_year);
    if (p.score_value || p.score_bucket) out.score = said(p.score_value || BUCKET[p.score_bucket]);
    // 7-iron: carry in yards, or club speed tagged by where it came from (sim / estimated from a driver speed)
    if (p.speed_or_carry === "speed" && p.speed_mph) {
      const src = p.speed_source === "simulator" || p.speed_source === "launch_monitor" ? "sim" : p.speed_source === "own_estimate" ? "estimated" : "said";
      out.speed_mph = { v: p.speed_mph, src };
    } else if (p.carry_yd) out.carry = said(p.carry_yd);
    const fl = flightTile(p);
    if (fl) out.flight = said(fl);
    if (p.height_cm) out.height = { v: Math.round(p.height_cm / 2.54), src: st.measurement ? "measured" : "said" };
    if (p.wrist_to_floor_cm) out.wtf = { v: Math.round(p.wrist_to_floor_cm / 2.54), src: st.measurement ? "measured" : "said" };
    if (p.glove) out.glove = said(p.glove);
    if (p.handedness) out.hand = { v: p.handedness === "left" ? "Left" : "Right", src: p.handedness_seen ? "seen" : "said" };
    // heard but not yet confirmed: shown on the tile being asked, so the golfer sees what was understood
    const STAGED_TILE = { age: "age", sex: "sex", rounds_per_year: "rounds", score: "score", seven_iron_carry: "carry", seven_iron_speed: "carry",
                          flight_current: "flight", flight_desired: "flight", height: "height", wrist_to_floor: "wtf",
                          glove: "glove", handedness: "hand" };
    for (const [k, v] of Object.entries(staged)) if (STAGED_TILE[k]) out[STAGED_TILE[k]] = { v: `${v.read_back} ?`, src: "heard" };
    return out;
  }
  function apply(st) {
    if (!st) return;
    A.state = st;
    window.FitterBackend.setState(st);
    const S = UI().S;
    const t = tiles(st);
    if (t.carry) delete S.profile.speed_mph;
    if (t.speed_mph) delete S.profile.carry;
    S.profile = { ...S.profile, ...t };
    UI().renderTiles(); UI().renderMeasure();
    const next = $("a-next");
    if (next) next.disabled = !gameDone(st) || !!Object.keys(st.staged_for_review || {}).length;
  }
  async function sync() { const st = await api("/api/session"); apply(st); return st; }

  // Your game: the quiz's inputs in six questions (Q1 asks two). Tempo comes from the swings; height, wrist-to-floor
  // and glove from Your body; handedness from the swing (camera) or asked on the tape path. Each entry: field to save,
  // done-check, the question in the fitter's words, the answer options, what to say if challenged.
  const OPENER = "Your game first. Six quick ones, and I don’t care what your mates shoot.";
  const CLOSE = "Got your game. Now let me see you. Stand up, phone at hip height.";
  const QUIZ = [
    ["age_and_sex", (p) => p.age && p.sex, "Two boring ones so the shaft maths works. How old are you, and do I fit you men’s or women’s?",
     "age as a number; men's or women's. Save both: fields age and sex",
     "Age and men’s or women’s decide when graphite or a softer flex comes in. Nothing else."],
    ["rounds_per_year", (p) => p.rounds_per_year, "How many rounds a year, roughly?", "under 10, 10 to 30, 31 to 50, over 50",
     "How much you play tells me how much game you’ve got to work with."],
    ["score", (p) => p.score_bucket, "What do you shoot on a normal day? Not your best day.",
     "a usual 18-hole score; a handicap is fine (save it as said, the app adds 72). Vague? Probe once: \"Break 90 most days?\"",
     "Your usual score sets how much forgiveness the head needs."],
    ["seven_iron", (p) => (p.speed_or_carry === "speed" && p.speed_mph) || (p.speed_or_carry === "carry" && p.carry_yd),
     "Know your 7-iron swing speed from a sim? If not, how far does a normal 7-iron carry in the air?",
     "speed in mph (field seven_iron_speed; say \"sim\" in the value if it came from a sim or launch monitor; a driver speed goes in as \"driver 100\") or carry in yards (field seven_iron_carry). If it sounds like a best one, ask once: \"Normal one or your best one?\"",
     "Speed picks the shaft flex. Carry works too, it’s just a rougher guess at speed."],
    ["flight_current", (p) => p.flight_current, "What does your ball flight look like with your irons? Low, mid or high?", "low, mid or high",
     "Different shafts launch it differently, so I need to know where you start."],
    ["flight_desired", (p) => p.flight, "And what would you like it to be? Same, lower or higher?", "same, lower or higher",
     "The shaft is picked for the flight you want, not the one you’ve got."],
  ];
  // said out loud when the golfer is unsure or confused: the quiz's own brackets, in plain words
  // For a golfer who says "I don't know" or "I don't get it": first what the question means in everyday words, then a
  // few easy picks (said the way people talk), then what to do if they still can't say. Picks map to the quiz's brackets.
  const UNSURE = {
    age_and_sex: { explain: "Oh, just how old you are, and whether you'd play men's or women's clubs. Women's are a touch lighter and softer.",
                   picks: "So something like: forty-five, men's.", still_unsure: "A rough age is fine, like 'mid forties'." },
    rounds_per_year: { explain: "Just how often you get out for a full round. A guess is totally fine.",
                       picks: "Would you say hardly ever, once or twice a month, about once a week, or more than that?",
                       still_unsure: "Map it: hardly ever is under 10, once or twice a month is 10 to 30, once a week is 31 to 50, more is over 50." },
    score: { explain: "No stress, it's just roughly what you'd card on a normal round. Not your best day, your normal one.",
             picks: "Are we talking under seventy-one, seventy-one to seventy-seven, high seventies to mid eighties, mid eighties to low nineties, or over ninety-two? Or just give me your handicap.",
             still_unsure: "Ask: 'Do you break ninety most days?' Yes means about 85 to 92 (save 88); no means over 92 (save 95); breaking eighty means 78 to 84 (save 81)." },
    seven_iron: { explain: "Yeah, swing speed's a simulator number, most people don't have it. No problem at all. I just need how far a normal 7-iron flies before it lands. Not the roll, just the air.",
                  picks: "Would you say about a hundred and twenty, about a hundred and forty, about a hundred and fifty-five, or a hundred and seventy plus?",
                  still_unsure: "Ask which club they hit from 150 yards: a 6-iron or longer means a 7-iron carries about 140; a 7-iron means about 150; an 8-iron or shorter means about 160. Save that carry." },
    flight_current: { explain: "Just picture a normal 7-iron in the air. How high does it go?",
                      picks: "Pretty low and flat, a normal middle height, or high and floaty?",
                      still_unsure: "If they don't notice it either way, that's mid." },
    flight_desired: { explain: "Just whether you're happy with how high it flies, or you'd like it to change.",
                      picks: "Keep it as it is, bring it down a bit, or get it up a bit higher?",
                      still_unsure: "If they don't mind, that's 'same'." },
  };
  const gameDone = (st) => QUIZ.every(([, has]) => has((st && st.profile) || {}));
  function nextQuestion(st) {
    const p = st.profile || {}, q = QUIZ.find(([, has]) => !has(p));
    const u = UNSURE[q?.[0]];
    if (q) return { field: q[0], question: q[2], answer_options: q[3], if_challenged: q[4],
                    if_unsure_say: u ? `${u.explain} ${u.picks}` : null, if_still_unsure: u ? u.still_unsure : null };
    const screen = UI()?.screen?.();
    if (screen === "chat" || screen === "hello")
      return { field: null, question: null, game_done: true,
               then: `Your game is complete. Say the close line: "${CLOSE}" Then call open_measurement_step straight away.` };
    // Your body: one at a time (the camera measures height and wrist-to-floor on the camera path)
    const body = [["height", (x) => x.height_cm, "How tall are you, in golf shoes?"],
                  ["wrist_to_floor", (x) => x.wrist_to_floor_cm, "Tape at the crease of your wrist, arms hanging. What does it say at the floor?"],
                  ["glove", (x) => x.glove, "Glove size?"],
                  ["handedness", (x) => x.handedness, "Right- or left-handed?"]];
    const b = body.find(([, has]) => !has(p));
    if (b && !(b[0] === "handedness" && UI()?.S?.measMode === "cam"))      // camera path: the swing shows handedness
      return { field: b[0], question: b[2], body: true };
    if (!p.tempo && !UI()?.S?.swingSkipped)
      return { field: null, question: null, body_done: true,
               then: "That's you measured. Say so in one line and move to the swing: \"Now show me your swing, so the shaft comes from how you actually move.\" Don't ask tempo: the swings measure it. They tap Watch me swing (or it opens by itself on the camera path)." };
    if (!p.tempo) return { field: "tempo", question: "Smooth and rhythmic, or quick?" };
    if (!p.handedness) return { field: "handedness", question: "Right- or left-handed?" };
    return { field: null, question: null, all_done: true, then: "Everything's in. Offer the fit: they tap See your fit, or call get_fit_recommendation." };
  }

  // ------------------------------------------------------------------ what the assistant can do
  const agentState = (st) => { const { full_recommendation, ...rest } = st; return { ok: true, ...rest }; };
  function tool(fn) {
    return async (params) => {
      try { return await fn(params || {}); }
      catch (e) { return { ok: false, error: "tool_failed", message: e.message, say: "Tell the golfer briefly that this step failed and offer to carry on by typing. Confirmed answers are kept." }; }
    };
  }

  // Safety net: the model sometimes reacts to an answer ("Forty-one, nice") without calling save_profile_answer.
  // When its reply comes and nothing was saved that turn, our own parser tries the golfer's words against the question
  // that was open. Only a clean parse is saved (anything unclear is left for the assistant); the assistant is told quietly.
  function heard(text) {
    const nq = nextQuestion(A.state || {});
    A.turn = { text: String(text || "").trim(), field: nq.field, saved: false };
  }
  async function catchBoth(t) {                      // Q1 asks two things: save whichever came through cleanly
    const st0 = A.state?.profile || {}, ups = [];
    if (!st0.age) ups.push({ field: "age", value: t.text, unit: null, kind: null, heard_text: t.text });
    if (!st0.sex && /\b(men|man|male|guy|mens|men'?s|women|woman|female|ladies|lady|womens|women'?s)\b/i.test(t.text))
      ups.push({ field: "sex", value: t.text, unit: null, kind: null, heard_text: t.text });
    if (!ups.length || !/\d|\b(men|women|male|female|ladies)/i.test(t.text)) return;
    try {
      const r = await api("/api/session/propose", { method: "POST", json: { updates: ups } });
      const ok = (r.results || []).filter((x) => x.status === "staged_for_review").map((x) => x.field);
      const drop = Object.keys(r.state.staged_for_review || {}).filter((k) => ups.some((u) => u.field === k) && !ok.includes(k));
      if (drop.length) await api("/api/session/discard", { method: "POST", json: { keys: drop } }).catch(() => {});
      if (!ok.length) return;
      const c = await api("/api/session/confirm", { method: "POST", json: { keys: ok } });
      apply(c.state);
      A.conv?.sendContextualUpdate(`[app] Saved from the golfer's last answer: ${ok.join(" and ")}. Don't ask again. Next: ${nextQuestion(c.state).question || "Your game is complete"}.`);
    } catch { /* the assistant can still save it */ }
  }
  async function catchUnsaved() {
    const t = A.turn;
    A.turn = null;
    if (!t || t.saved || !t.field || !t.text || /\?\s*$|^(why|what|how|which|can|could|sorry|pardon)\b/i.test(t.text)) return;
    let field = t.field;
    if (field === "age_and_sex") return catchBoth(t);
    if (field === "seven_iron") {                      // only with a unit: a bare number could be speed or carry
      field = /yard|yds?\b|carr|met(er|re)|\bm\b/i.test(t.text) ? "seven_iron_carry"
            : /mph|km|speed|miles/i.test(t.text) ? "seven_iron_speed" : null;
      if (!field) return;
    }
    try {
      const r = await api("/api/session/propose", { method: "POST", json: { updates: [{ field, value: t.text, unit: null, kind: null, heard_text: t.text }] } });
      const ok = (r.results || []).filter((x) => x.status === "staged_for_review" && x.field === field);
      if (!ok.length) {                                // unclear: leave nothing staged behind
        const left = Object.keys(r.state.staged_for_review || {}).filter((k) => k === field);
        if (left.length) await api("/api/session/discard", { method: "POST", json: { keys: left } }).catch(() => {});
        return;
      }
      const c = await api("/api/session/confirm", { method: "POST", json: { keys: [field] } });
      apply(c.state);
      A.conv?.sendContextualUpdate(`[app] Saved from the golfer's last answer: ${ok[0].read_back || field}. It's already saved, don't ask again. Next: ${nextQuestion(c.state).question || "Your game is complete: close line, then open_measurement_step"}.`);
    } catch { /* the assistant can still save it */ }
  }

  const clientTools = {
    get_fitting_state: tool(async () => { const st = await sync(); return { ...agentState(st), next_question: nextQuestion(st) }; }),
    propose_profile_update: tool(async ({ updates }) => {
      if (!Array.isArray(updates) || !updates.length) return { ok: false, error: "invalid_parameters" };
      const clean = updates.map((u) => ({ field: u.field, value: String(u.value), unit: u.unit || null, kind: u.kind || null, heard_text: u.heard_text || null }));
      const r = await api("/api/session/propose", { method: "POST", json: { updates: clean } });
      apply(r.state);
      return { ok: true, results: r.results, note: "Not saved yet. Read back each read_back text and ask if it's right. Only when the golfer says yes, call confirm_profile_update." };
    }),
    // the default: parse and save in one step; only an unclear answer comes back as a question
    save_profile_answer: tool(async ({ updates }) => {
      if (!Array.isArray(updates) || !updates.length) return { ok: false, error: "invalid_parameters" };
      const clean = updates.map((u) => ({ field: u.field, value: String(u.value), unit: u.unit || null, kind: u.kind || null, heard_text: u.heard_text || null }));
      if (A.turn) A.turn.saved = true;
      const r = await api("/api/session/propose", { method: "POST", json: { updates: clean } });
      const keys = Object.keys(r.state.staged_for_review || {}).filter((k) => r.results.some((x) => x.status === "staged_for_review" && x.field === k));
      let st = r.state, saved = [];
      if (keys.length) { const c = await api("/api/session/confirm", { method: "POST", json: { keys } }); st = c.state; saved = c.confirmed; }
      apply(st);
      return { ok: true, saved, results: r.results.map((x) => ({ field: x.field, status: x.status === "staged_for_review" ? "saved" : x.status, read_back: x.read_back, ask: x.ask })),
               next_question: nextQuestion(st), missing_fields: st.missing_fields,
               note: "read_back is for you, never say it. Repeat the number the way a person would, with one short dry reaction "
                     + "(\"Forty-five, men's.\", \"Twenty a year, a regular.\", \"Eighty-eight.\", \"A hundred and sixty in the air.\"), "
                     + "then ask next_question.question. Don't ask if it's right. For needs_clarification, ask its question instead." };
    }),
    confirm_profile_update: tool(async ({ fields }) => {
      const staged = Object.keys(A.state?.staged_for_review || {});
      const keys = Array.isArray(fields) && fields.length ? fields.filter((f) => staged.includes(f)) : staged;
      if (!keys.length) return { ok: false, error: "nothing_staged" };
      if (A.turn) A.turn.saved = true;
      const r = await api("/api/session/confirm", { method: "POST", json: { keys } });
      apply(r.state); UI().S.justFilled = null;
      return { ok: true, saved: r.confirmed, missing_fields: r.state.missing_fields };
    }),
    open_measurement_step: tool(async () => {
      if (UI().screen() !== "measure") UI().startMeasure();
      const p = (A.state || {}).profile || {};
      const saved = [p.height_cm && `height ${Math.round(p.height_cm)} cm`, p.wrist_to_floor_cm && `wrist-to-floor ${Math.round(p.wrist_to_floor_cm)} cm`,
                     p.glove && `glove ${p.glove}`, p.handedness && `${p.handedness}-handed`].filter(Boolean);
      return { ok: true, opened: true, already_saved: saved, next_question: nextQuestion(A.state || {}),
               note: (p.height_cm ? "Height is already saved: don't ask it. " : "Height isn't saved: ask it first, in golf shoes (the camera needs it to scale the wrist-to-floor). ")
                     + "Never ask for anything in already_saved. Camera changes arrive as [camera] messages; describe only what they and get_measurement_status say." };
    }),
    get_measurement_status: tool(async () => window.FitterCamera ? window.FitterCamera.status() : { ok: true, status: "not_available" }),
    confirm_measurement: tool(async () => window.FitterCamera ? window.FitterCamera.confirmMeasurement() : { ok: false, error: "nothing_measured" }),
    get_fit_recommendation: tool(async () => {
      await window.FitterBackend.sync(UI().S);             // fills only what the call never covers, and marks it as a guess
      const r = await api("/api/session/recommendation", { method: "POST" });
      apply(r.state);
      if (r.tool_result.ok && UI().screen() !== "fit") UI().startFit();
      return { ...r.tool_result, shown_on_screen: !!r.tool_result.ok };
    }),
  };

  // ------------------------------------------------------------------ the call
  function greeting(reconnect) {
    const st = A.state || {}, nq = nextQuestion(st), next = nq.question || "";
    if (reconnect) return `Sorry, lost you for a second. ${next}`.trim();
    const answered = Object.values(st.profile || {}).filter((v) => v !== null && v !== "" && !(Array.isArray(v) && !v.length)).length;
    if (!answered) return `${OPENER} ${next}`;
    return `Right, I've got a few of your answers already, so I'll only ask what's missing. ${next}`.trim();
  }

  // the frontend disables the mic and Send between scripted questions; on a real call they always work
  function enableControls() {
    if (!A.used) return;
    const mic = $("mic"), send = $("form")?.querySelector("button");
    if (mic) mic.disabled = false;
    if (send) send.disabled = false;
  }
  new MutationObserver(enableControls).observe(document.getElementById("bar-io") || document.body,
    { subtree: true, attributes: true, attributeFilter: ["disabled", "style"] });

  // what the dock's status line should say on a live call (their caption player also writes it; ours wins while live)
  function dockText() {
    if (!A.live) return null;
    if (A.muted) return "Mic muted";
    if (A.mode === "text") return "Typing";
    return A.lastMode === "speaking" ? "Speaking" : "Listening";
  }
  const dockLine = document.getElementById("dock-status");
  if (dockLine) new MutationObserver(() => { const w = dockText(); if (w && dockLine.textContent !== w) dockLine.textContent = w; })
    .observe(dockLine, { childList: true, characterData: true, subtree: true });

  function status(mode) {                                        // the frontend's call dock: status line, listening style, mute
    enableControls();
    const mic = $("mic"), dock = $("dock"), line = $("dock-status");
    if (mode) A.lastMode = mode;
    const m = A.lastMode;
    const listening = A.live && A.mode === "voice" && m === "listening" && !A.muted;
    dock?.classList.toggle("listening", listening);
    const want = dockText();
    if (line && want && line.textContent !== want) line.textContent = want;
    if (!mic) return;
    mic.classList.toggle("muted", A.muted);
    mic.setAttribute("aria-label", A.muted ? "Unmute microphone" : "Mute microphone");
    mic.setAttribute("aria-pressed", String(A.muted));
  }

  async function start(mode, { reconnect = false } = {}) {
    if (A.starting) return;
    A.starting = true; A.used = true; A.userEnded = false;
    const want = mode === "type" ? "text" : "voice";
    try {
      if (A.conv) { const c = A.conv; A.conv = null; A.switching = true; try { await c.endSession(); } catch { /* closed */ } A.switching = false; }
      await window.FitterBackend.session();
      await sync();
      if (!window.ElevenLabsClient) throw new Error("The voice library didn't load. Check your connection.");
      const auth = await api("/api/voice/session", { method: "POST", json: { mode: want } });
      A.conv = await window.ElevenLabsClient.Conversation.startSession({
        ...(want === "voice" ? { conversationToken: auth.conversation_token, connectionType: "webrtc" }
                             : { signedUrl: auth.signed_url, connectionType: "websocket" }),
        textOnly: want === "text",
        dynamicVariables: { secret__fitting_session: await window.FitterBackend.session(), greeting: greeting(reconnect) },
        clientTools,
        onConnect: () => {
          A.live = true; A.mode = want;
          UI().S.mode = want === "text" ? "type" : "talk";
          $("dock")?.classList.toggle("typing", want === "text"); $("kbd")?.setAttribute("aria-pressed", String(want === "text"));
          UI().hint(want === "voice" ? "" : "Typing with the fitter");
          status("listening");
          if (reconnect) A.conv?.sendContextualUpdate("[app] Reconnected after a dropped connection. Call get_fitting_state and carry on.");
          pausedUI(false);
        },
        onDisconnect: onDisconnect,
        onError: (m) => UI().toast(`Call problem: ${m}`),
        onMessage: ({ source, message }) => {
          if (source === "ai") { UI().capFit.show(message); setTimeout(catchUnsaved, 1500); }
          else {
            if (A.lastTyped && message.trim() === A.lastTyped) { A.lastTyped = null; return; }
            heard(message);
            UI().said(UI().screen() === "measure" ? "said-measure" : "said-chat", message);
          }
        },
        onModeChange: ({ mode: m }) => status(m),
      });
    } catch (e) {
      A.live = false;
      const denied = e?.name === "NotAllowedError";
      UI().toast(denied ? "Microphone blocked. You can type to the fitter instead." : `Couldn't start the call: ${e.message || e}`);
      if (denied && want === "voice") { A.starting = false; return start("type"); }
    } finally { A.starting = false; }
  }

  function onDisconnect(details) {
    const was = A.mode;
    if (details && (details.reason === "agent" || details.reason === "user")) A.userEnded = true;   // ended on purpose
    A.live = false; A.conv = null; A.mode = null; status(null);
    if (A.switching) return;
    const recent = A.retries.filter((t) => Date.now() - t < 300000);
    if (!A.userEnded && was && recent.length < 3) { A.retries = [...recent, Date.now()]; start(was === "text" ? "type" : "talk", { reconnect: true }); return; }
    UI().hint("Call paused. Your answers are kept. Tap resume, the mic, or type to carry on.");
    pausedUI(true);
  }

  // the frontend's own pause button (#end) is the call's end button: keep its look in step with the real call
  function pausedUI(on) {
    const S = UI().S, dock = $("dock"), btn = $("end");
    if (!S || !btn) return;
    S.ended = on; dock?.classList.toggle("ended", on);
    btn.setAttribute("aria-pressed", String(on)); btn.setAttribute("aria-label", on ? "Resume call" : "Pause call");
    if (on && $("dock-status")) $("dock-status").textContent = "Call paused";
  }
  async function end() {
    A.userEnded = true;
    try { await A.conv?.endSession(); } catch { onDisconnect({ reason: "user" }); }
  }

  // the mic is open on a call: the big button mutes and unmutes (or starts the call if it isn't running)
  $("mic")?.addEventListener("click", () => {
    if (!A.used) return;                                       // before the call starts, the frontend's own flow runs
    if (!A.live) { start("talk", { reconnect: true }); return; }
    if (A.mode !== "voice") { start("talk", { reconnect: true }); return; }
    // their own handler flips the mic's look; we mute the real call and keep the dock's status honest
    A.muted = !A.muted; A.conv.setMicMuted(A.muted); setTimeout(() => status(), 0);
    A.conv.sendContextualUpdate(A.muted
      ? "[app] The golfer muted their mic (they may be setting up the camera). Stay quiet: don't ask if they're still there. They'll unmute when ready."
      : "[app] The golfer unmuted their mic. Carry on where you left off, in one short sentence.");
    UI().hint("");
  });
  // typing during the call: sent to the same assistant (also works while it's talking)
  $("form")?.addEventListener("submit", (e) => {
    if (!A.used) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const inp = $("inp"), t = inp.value.trim();
    if (!t) return;
    inp.value = "";
    if (!A.live) { start("type").then(() => A.conv?.sendUserMessage(t)); return; }
    A.lastTyped = t; heard(t); A.conv.sendUserMessage(t);
    UI().said(UI().screen() === "measure" ? "said-measure" : "said-chat", t);
  }, true);
  $("inp")?.addEventListener("input", () => A.conv?.sendUserActivity());
  // their pause button hangs up the real call (resume goes through start(), already wired in their page)
  window.FitterCall = window.FitterCall || {};
  window.FitterCall.onEnd = end;

  // camera events for the assistant: speak=true makes it say something now; otherwise it's quiet context
  function notify(text, speak) {
    if (!A.live || !A.conv) return;
    if (speak) A.conv.sendUserMessage(`${text} (message from the app, not the golfer)`);
    else A.conv.sendContextualUpdate(text);
  }
  // while the golfer swings: tell the assistant someone is active so it doesn't talk over the swing
  let quietTimer = null;
  function quiet(on) {
    clearInterval(quietTimer); quietTimer = null;
    if (on && A.conv) { A.conv.sendContextualUpdate("[camera] Recording a swing now. Stay completely silent until the result arrives."); quietTimer = setInterval(() => A.conv?.sendUserActivity(), 500); }
  }

  return { start, end, live: () => A.live, used: () => A.used, sync, notify, quiet, apply, _debug: () => ({ live: A.live, mode: A.mode, used: A.used, muted: A.muted }) };
})();
