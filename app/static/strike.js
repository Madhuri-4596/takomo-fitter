"use strict";

// Optional strike test: a clubface photo with impact-tape or powder marks. The golfer taps the
// heel and toe ends of the grooves, the server suggests the marks, and the golfer confirms them
// (tap a mark to remove it, tap elsewhere to add one). Uses globals from app.js.

window.Strike = (() => {
  const canvas = $("#strike-canvas");
  const ctx = canvas.getContext("2d");
  const K = { base: null, blob: null, heel: null, toe: null, marks: [], analysis: null };

  function open() {
    $("#strike").hidden = false;
    $("#strike").scrollIntoView({ behavior: "smooth" });
  }

  function prompt() {
    const p = !K.heel ? "Tap the <b>heel end</b> of the grooves (nearest the shaft)."
      : !K.toe ? "Tap the <b>toe end</b> of the grooves."
      : `Check the marks: tap a mark to remove it, tap a strike we missed to add it. ${K.marks.length} marked.`;
    $("#strike-prompt").innerHTML = p;
    $("#strike-save").disabled = !(K.heel && K.toe && K.marks.length >= 3);
  }

  function draw() {
    if (!K.base) return;
    ctx.drawImage(K.base, 0, 0);
    const r = canvas.getBoundingClientRect();
    const u = r.width ? 8 * canvas.width / r.width : canvas.width / 80;
    ctx.lineWidth = u / 3;
    if (K.heel && K.toe) {
      ctx.strokeStyle = "#b8964a"; ctx.setLineDash([u, u / 2]);
      ctx.beginPath(); ctx.moveTo(K.heel.x, K.heel.y); ctx.lineTo(K.toe.x, K.toe.y); ctx.stroke(); ctx.setLineDash([]);
    }
    for (const [p, label] of [[K.heel, "Heel"], [K.toe, "Toe"]]) {
      if (!p) continue;
      ctx.fillStyle = "#123d2c"; ctx.beginPath(); ctx.arc(p.x, p.y, u * 0.8, 0, 7); ctx.fill();
      ctx.font = `700 ${u * 1.5}px system-ui`; ctx.fillText(label, p.x + u, p.y - u);
    }
    ctx.strokeStyle = "#a63d2f";
    K.marks.forEach((m, i) => {
      ctx.beginPath(); ctx.arc(m.x, m.y, u * 1.6, 0, 7); ctx.stroke();
      ctx.fillStyle = "#a63d2f"; ctx.font = `700 ${u * 1.3}px system-ui`; ctx.fillText(String(i + 1), m.x + u * 1.8, m.y);
    });
  }

  function pos(e) {
    const r = canvas.getBoundingClientRect();
    const fit = Math.min(r.width / canvas.width, r.height / canvas.height);
    const ox = (r.width - canvas.width * fit) / 2, oy = (r.height - canvas.height * fit) / 2;
    return { x: (e.clientX - r.left - ox) / fit, y: (e.clientY - r.top - oy) / fit, fit };
  }

  async function detect() {
    const fd = new FormData();
    fd.append("image", K.blob, "face.jpg");
    fd.append("heel", JSON.stringify([K.heel.x, K.heel.y]));
    fd.append("toe", JSON.stringify([K.toe.x, K.toe.y]));
    try {
      const r = await api("/api/strike/detect", { method: "POST", body: fd });
      K.marks = r.marks.map(([x, y]) => ({ x, y }));
      $("#strike-result").textContent = K.marks.length ? `We found ${K.marks.length} possible marks. Please check them.` : "We couldn't find marks. Tap each one yourself.";
    } catch (ex) { $("#strike-result").textContent = ex.message; }
    draw(); prompt(); analyse();
  }

  async function analyse() {
    if (!(K.heel && K.toe && K.marks.length >= 3)) return;
    try {
      K.analysis = await api("/api/strike/analyse", { method: "POST", json: body() });
      $("#strike-result").textContent = K.analysis.text + " " + K.analysis.notes.join(" ");
      $("#strike-error").hidden = true;
    } catch (ex) { $("#strike-error").textContent = ex.message; $("#strike-error").hidden = false; }
  }
  const body = () => ({ marks: K.marks.map((m) => [m.x, m.y]), heel: [K.heel.x, K.heel.y], toe: [K.toe.x, K.toe.y] });

  canvas.addEventListener("pointerdown", (e) => {
    if (!K.base) return;
    const p = pos(e);
    if (!K.heel) { K.heel = p; }
    else if (!K.toe) { K.toe = p; draw(); prompt(); detect(); return; }
    else {
      const grab = 22 / p.fit;
      const i = K.marks.findIndex((m) => Math.hypot(m.x - p.x, m.y - p.y) < grab);
      if (i >= 0) K.marks.splice(i, 1); else K.marks.push({ x: p.x, y: p.y });
      analyse();
    }
    draw(); prompt();
  });

  $("#strike-input").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    const bmp = await createImageBitmap(f, { imageOrientation: "from-image" });
    const k = Math.min(1, 1800 / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    Object.assign(K, { base: c, heel: null, toe: null, marks: [], analysis: null });
    K.blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.9));
    canvas.width = c.width; canvas.height = c.height;
    $("#strike-edit").hidden = false; $("#strike-result").textContent = "";
    draw(); prompt();
  });
  $("#strike-reset").addEventListener("click", () => { Object.assign(K, { heel: null, toe: null, marks: [], analysis: null }); $("#strike-result").textContent = ""; draw(); prompt(); });
  $("#strike-save").addEventListener("click", async (e) => {
    if (!e.isTrusted) return;                        // the golfer confirms their own marks
    try {
      applyState(await api("/api/session/strike", { method: "POST", json: body() }));
      $("#strike").hidden = true;
      document.querySelector(".evidence-slot").scrollIntoView({ behavior: "smooth" });
    } catch (ex) { $("#strike-error").textContent = ex.message; $("#strike-error").hidden = false; }
  });

  return { open, _debug: () => JSON.parse(JSON.stringify({ heel: K.heel, toe: K.toe, marks: K.marks, analysis: K.analysis, size: [canvas.width, canvas.height] })) };
})();
