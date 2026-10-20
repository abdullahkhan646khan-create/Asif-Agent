// Amana, the prompt assistant: the chat bubble on every page. The client types an idea; Amana answers with
// ready options (Prompt, Message on the post, Service) to copy or to put straight into the Create post form.
// The face shows how Amana feels: idle, happy, thinking, a wink, love (when an idea is used) and sad (on errors).
(() => {
  const $ = (id) => document.getElementById(id);
  const fab = $("ai-fab"), panel = $("ai-panel"), log = $("ai-log"), form = $("ai-form"), input = $("ai-text");
  if (!fab) return;
  const KEY = "amana-chat-v1", FILL = "amana-fill", GREETED = "amana-greeted";
  const keep = {  // this tab only; the chat works fine without it (private windows)
    get(k) { try { return JSON.parse(sessionStorage.getItem(k) || "null"); } catch (e) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    del(k) { try { sessionStorage.removeItem(k); } catch (e) {} },
  };
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let chat = keep.get(KEY) || [];
  let busy = false;

  // ---------- feelings ----------
  const faces = () => document.querySelectorAll(".ai-fab .bot, .ai-head .bot");
  let moodTimer;
  function mood(m, ms) {
    faces().forEach((b) => (b.dataset.mood = m));
    clearTimeout(moodTimer);
    if (ms) moodTimer = setTimeout(() => mood(busy ? "thinking" : "idle"), ms);
  }
  setInterval(() => {  // a little life while waiting: a wink, a smile, a look around
    if (busy || document.hidden || calm) return;
    const r = Math.random();
    if (r < 0.22) mood("wink", 900); else if (r < 0.42) mood("happy", 1500); else if (r < 0.62) mood("curious", 1700);
  }, 6500);
  if (!calm) {  // the eyes follow the pointer
    let frame = 0;
    document.addEventListener("pointermove", (e) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const r = fab.getBoundingClientRect();
        const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
        const d = Math.max(1, Math.hypot(dx, dy)), k = Math.min(1, d / 300);
        faces().forEach((b) => {
          b.style.setProperty("--lx", `${((dx / d) * k * 0.2).toFixed(3)}em`);
          b.style.setProperty("--ly", `${((dy / d) * k * 0.16).toFixed(3)}em`);
        });
      });
    }, { passive: true });
  }
  function burst() {
    fab.classList.remove("burst");
    void fab.offsetWidth;
    fab.classList.add("burst");
    setTimeout(() => fab.classList.remove("burst"), 900);
  }
  function status(thinking) {
    $("ai-status").innerHTML = thinking ? '<i class="dot busy"></i>Thinking of fresh ideas…' : '<i class="dot"></i>Prompt assistant · ready';
  }

  // ---------- the chat ----------
  const FACE = '<span class="bot tiny" data-mood="idle"><span class="bot-antenna"><i></i></span><span class="bot-head"><span class="bot-face">'
    + '<span class="eye l"></span><span class="eye r"></span><span class="mouth"></span></span></span></span>';
  const WELCOME = "Hi, I'm Amana 👋 Tell me an idea for a post, even just a few words, and I'll write 3 ready prompts "
    + "with a message for the picture. Copy them, or tap Use this to fill the Create post form.";

  function option(o, i, j) {
    const svc = o.service ? `<span class="ai-opt-svc" title="${esc(o.service)}">${esc(o.service)}</span>` : '<span class="ai-opt-svc auto">Service: Auto</span>';
    const msg = o.message ? `<div class="ai-opt-label">Message on the post <button type="button" class="ai-opt-copy" data-copy="message" data-i="${i}" data-j="${j}">Copy</button></div>
      <p class="ai-opt-msg">“${esc(o.message)}”</p>` : "";
    return `<div class="ai-opt" style="--d:${j * 110}ms">
      <div class="ai-opt-top"><b>${esc(o.title)}</b>${svc}</div>
      <div class="ai-opt-label">Prompt <button type="button" class="ai-opt-copy" data-copy="prompt" data-i="${i}" data-j="${j}">Copy</button></div>
      <p class="ai-opt-prompt">${esc(o.prompt)}</p>${msg}
      <div class="ai-opt-actions">
        <button type="button" class="btn yellow" data-use data-i="${i}" data-j="${j}">Use this</button>
        <button type="button" class="btn ghost" data-copy="all" data-i="${i}" data-j="${j}">Copy all</button>
      </div></div>`;
  }
  function bubble(m, i) {
    if (m.role === "user") return `<div class="msg me">${esc(m.content)}</div>`;
    return `<div class="msg bot-msg${m.error ? " err" : ""}"><span class="msg-face">${FACE}</span><div class="msg-body">
      <div class="msg-text">${esc(m.content)}</div>${(m.options || []).map((o, j) => option(o, i, j)).join("")}</div></div>`;
  }
  function render(typing) {
    const items = [bubble({ role: "assistant", content: WELCOME }, -1), ...chat.map(bubble)];
    if (typing) items.push(`<div class="msg bot-msg typing"><span class="msg-face">${FACE}</span><div class="msg-body"><div class="msg-text">
      <span class="dots"><i></i><i></i><i></i></span> Writing prompts for you…</div></div></div>`);
    log.innerHTML = items.join("");
    $("ai-quick").hidden = chat.length > 0;
    requestAnimationFrame(() => {
      const mine = log.querySelectorAll(".msg.me"), last = mine[mine.length - 1];
      if (typing || !last) log.scrollTop = log.scrollHeight;
      else log.scrollTop = Math.max(0, last.offsetTop - 12);  // show the answer from its start
    });
  }
  function save() {
    chat = chat.slice(-30);
    keep.set(KEY, chat);
  }
  function forServer(m) {  // Amana's earlier options go back as text, so "make it shorter" knows what "it" is
    if (m.role !== "assistant" || !(m.options || []).length) return { role: m.role, content: m.content };
    const opts = m.options.map((o, k) => `Option ${k + 1} "${o.title}": PROMPT: ${o.prompt} | MESSAGE: ${o.message} | SERVICE: ${o.service || "Auto"}`);
    return { role: "assistant", content: `${m.content}\n${opts.join("\n")}` };
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    chat.push({ role: "user", content: text });
    save();
    input.value = "";
    grow();
    busy = true;
    $("ai-send").disabled = true;
    status(true);
    mood("thinking");
    render(true);
    try {
      const res = await api("POST", "/api/assistant", { messages: chat.filter((m) => !m.error).map(forServer) });
      chat.push({ role: "assistant", content: res.reply, options: res.options || [] });
      busy = false;
      mood("happy", 2400);
      burst();
    } catch (err) {
      chat.push({ role: "assistant", content: err.message || "Something went wrong. Please try again.", error: true });
      busy = false;
      mood("sad", 2800);
    }
    $("ai-send").disabled = false;
    status(false);
    save();
    render();
  }

  // ---------- copy and use ----------
  function pick(el) {
    const m = chat[+el.dataset.i];
    return m && (m.options || [])[+el.dataset.j];
  }
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {  // older browsers, or a page without clipboard access
      const t = document.createElement("textarea");
      t.value = text;
      t.style.position = "fixed";
      t.style.opacity = "0";
      document.body.appendChild(t);
      t.select();
      document.execCommand("copy");
      t.remove();
    }
  }
  function fill(o) {
    const idea = $("idea"), msg = $("message"), svc = document.querySelector('#new-post select[name="service"]');
    idea.value = o.prompt;
    msg.value = (o.message || "").slice(0, 150);
    if (svc) svc.value = [...svc.options].some((x) => x.value === o.service) ? o.service : "";
    [idea, msg].forEach((el) => { el.classList.remove("ai-filled"); void el.offsetWidth; el.classList.add("ai-filled"); });
    idea.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "center" });
    toast("Filled in. Check it, then press Create post.");
    mood("love", 2000);
    burst();
    if (window.matchMedia("(max-width: 640px)").matches) close();
  }
  log.addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-copy], button[data-use]");
    if (!b) return;
    const o = pick(b);
    if (!o) return;
    if (b.hasAttribute("data-use")) {
      if (document.getElementById("new-post")) return fill(o);
      keep.set(FILL, o);  // on another page: open the Posts page, which fills itself in
      location.href = "/";
      return;
    }
    const what = b.dataset.copy;
    const text = what === "prompt" ? o.prompt : what === "message" ? o.message
      : `Prompt: ${o.prompt}\nMessage: ${o.message}\nService: ${o.service || "Auto"}`;
    await copy(text);
    const was = b.textContent;
    b.textContent = "Copied ✓";
    b.classList.add("done");
    mood("happy", 1200);
    setTimeout(() => { b.textContent = was; b.classList.remove("done"); }, 1600);
  });
  const pending = keep.get(FILL);
  if (pending && document.getElementById("new-post")) {
    keep.del(FILL);
    setTimeout(() => fill(pending), 350);
  }

  // ---------- open, close, type ----------
  function open() {
    panel.hidden = false;
    requestAnimationFrame(() => panel.classList.add("open"));
    fab.setAttribute("aria-expanded", "true");
    fab.classList.add("is-open");
    $("ai-bubble").hidden = true;
    mood("wink", 900);
    render();
    setTimeout(() => input.focus({ preventScroll: true }), 180);
  }
  function close() {
    panel.classList.remove("open");
    fab.setAttribute("aria-expanded", "false");
    fab.classList.remove("is-open");
    setTimeout(() => { if (!panel.classList.contains("open")) panel.hidden = true; }, 260);
  }
  fab.addEventListener("click", () => (panel.hidden ? open() : close()));
  fab.addEventListener("mouseenter", () => { if (!busy) mood("happy"); });
  fab.addEventListener("mouseleave", () => { if (!busy) mood("idle"); });
  $("ai-close").addEventListener("click", close);
  $("ai-new").addEventListener("click", () => {
    chat = [];
    save();
    render();
    mood("wink", 900);
    input.focus();
  });
  $("ai-bubble").addEventListener("click", open);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) close(); });
  $("ai-quick").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    send(b.textContent === "Surprise me" ? "Surprise me: a fresh post idea for one of our services" : `A post about: ${b.textContent}`);
  });
  function grow() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }
  input.addEventListener("input", grow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(input.value); }
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });

  if (!keep.get(GREETED)) {  // say hello once per visit
    keep.set(GREETED, true);
    setTimeout(() => {
      if (!panel.hidden) return;
      $("ai-bubble").hidden = false;
      mood("happy", 1600);
      setTimeout(() => ($("ai-bubble").hidden = true), 7000);
    }, 2200);
  }
})();
