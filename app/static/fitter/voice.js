"use strict";

// The fitter's spoken lines outside the live call (welcome, meet screen, step lines) in the same ElevenLabs voice as
// the call, instead of the browser's robotic speech. The server (/api/tts) returns MP3 audio and each word's start
// time; the frontend's caption highlights the word being said. The API key never reaches the browser.
window.FitterVoice = (() => {
  const V = { audio: null, raf: null, token: 0, cache: new Map(), ctx: null, analyser: null, level: 0 };

  async function fetchLine(text) {
    if (V.cache.has(text)) return V.cache.get(text);
    const p = fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "voice unavailable"); return r.json(); });
    V.cache.set(text, p);
    p.catch(() => V.cache.delete(text));
    return p;
  }

  // loudness of the voice, for the avatar's rings on the call screen
  function meter(audio) {
    try {
      V.ctx = V.ctx || new (window.AudioContext || window.webkitAudioContext)();
      const src = V.ctx.createMediaElementSource(audio);
      V.analyser = V.ctx.createAnalyser(); V.analyser.fftSize = 256;
      src.connect(V.analyser); V.analyser.connect(V.ctx.destination);
    } catch { V.analyser = null; }
  }
  function level() {
    if (!V.analyser || !V.audio || V.audio.paused) return 0;
    const d = new Uint8Array(V.analyser.fftSize); V.analyser.getByteTimeDomainData(d);
    let s = 0; for (const x of d) s += ((x - 128) / 128) ** 2;
    return Math.min(1, Math.sqrt(s / d.length) * 4);
  }

  function stop() {
    V.token++;
    cancelAnimationFrame(V.raf);
    if (V.audio) { V.audio.pause(); V.audio.src = ""; V.audio = null; }
  }

  // speak a line; onWord(i) fires as word i starts (words split on spaces, like the caption)
  async function speak(text, { onWord, onEnd, onError } = {}) {
    stop();
    const my = V.token;
    let line;
    try { line = await fetchLine(text); }
    catch (e) { if (my === V.token) onError && onError(e); return; }
    if (my !== V.token) return;                       // stopped or replaced while loading
    const audio = new Audio(`data:${line.mime};base64,${line.audio}`);
    V.audio = audio;
    meter(audio);
    let last = -1;
    const tick = () => {
      if (my !== V.token) return;
      const t = audio.currentTime;
      let i = last;
      while (i + 1 < line.word_starts.length && line.word_starts[i + 1] <= t + 0.03) i++;
      if (i !== last) { last = i; onWord && onWord(i); }
      V.raf = requestAnimationFrame(tick);
    };
    audio.onended = () => { if (my !== V.token) return; cancelAnimationFrame(V.raf); V.audio = null; onEnd && onEnd(); };
    audio.onerror = () => { if (my !== V.token) return; cancelAnimationFrame(V.raf); onError && onError(new Error("playback failed")); };
    try {
      if (V.ctx && V.ctx.state === "suspended") await V.ctx.resume();
      await audio.play();
      V.raf = requestAnimationFrame(tick);
    } catch (e) { if (my === V.token) onError && onError(e); }
  }

  return { speak, stop, level, prefetch: (t) => fetchLine(t).catch(() => {}) };
})();
