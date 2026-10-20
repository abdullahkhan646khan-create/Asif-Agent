// Upload page: the client's own photo, carousel or video. Everything is checked here first against Buffer's
// limits (RULES, from app/uploads.py), so the client knows before uploading what each network will get.
(() => {
  const $ = (id) => document.getElementById(id);
  const MB = 1024 * 1024;
  const mb = (n) => `${(n / MB).toFixed(n < 10 * MB ? 1 : 0)} MB`;
  const secs = (s) => { s = Math.round(s); const m = Math.floor(s / 60), r = s % 60; return m ? `${m} min${r ? ` ${r} s` : ""}` : `${r} s`; };
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
  const PHOTO_TYPES = /^image\/(jpeg|png|webp|heic|heif)$/, PHOTO_EXT = ["jpg", "jpeg", "png", "webp", "heic", "heif"];
  const VIDEO_EXT = ["mp4", "mov"];
  const ext = (name) => (name.split(".").pop() || "").toLowerCase();

  let kind = "image";
  let pics = [];   // {id, name, size, w, h, blob, url, problems}
  let vid = null;  // {name, size, file, url, duration, w, h, codec, frames, cover, coverUrl, cover_ms, problems}
  let sending = false;

  // ---------- reading files ----------
  function toJpeg(src, max, quality) {
    const w = src.videoWidth || src.naturalWidth || src.width, h = src.videoHeight || src.naturalHeight || src.height;
    const k = Math.min(1, max / Math.max(w, h));
    const c = document.createElement("canvas");
    c.width = Math.round(w * k);
    c.height = Math.round(h * k);
    const g = c.getContext("2d");
    g.fillStyle = "#fff";  // transparent parts of a PNG become white, not black
    g.fillRect(0, 0, c.width, c.height);
    g.drawImage(src, 0, 0, c.width, c.height);
    return new Promise((ok, bad) => c.toBlob((b) => (b ? ok(b) : bad(new Error("no picture"))), "image/jpeg", quality));
  }
  async function decode(file) {
    if (window.createImageBitmap) {
      try { return await createImageBitmap(file, { imageOrientation: "from-image" }); } catch (e) { /* try <img> */ }
    }
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.src = url;
    try { await img.decode(); } finally { setTimeout(() => URL.revokeObjectURL(url), 5000); }
    return img;
  }
  async function readPicture(file) {
    const p = { id: Math.random().toString(36).slice(2), name: file.name, size: file.size, problems: [] };
    if (!PHOTO_TYPES.test(file.type) && !PHOTO_EXT.includes(ext(file.name))) {
      p.problems.push("Not a photo. Use JPG, PNG, WebP or HEIC.");
      return p;
    }
    if (file.size > RULES.max_image_mb * MB) {
      p.problems.push(`Bigger than ${RULES.max_image_mb} MB. Use a smaller copy.`);
      return p;
    }
    try {
      const src = await decode(file);
      p.w = src.naturalWidth || src.width;
      p.h = src.naturalHeight || src.height;
      p.blob = await toJpeg(src, 2160, 0.92);  // a smaller copy: uploads in seconds, still sharp
      if (src.close) src.close();
      p.url = URL.createObjectURL(p.blob);
    } catch (e) {
      p.problems.push(/hei[cf]/.test(ext(file.name) + file.type)
        ? "This browser can't open HEIC photos. Use Safari, or save the photo as JPG."
        : "This photo can't be opened. Use JPG, PNG or WebP.");
    }
    return p;
  }
  function once(el, event, ms) {
    return new Promise((ok, bad) => {
      const t = setTimeout(() => bad(new Error("timeout")), ms);
      el.addEventListener(event, () => { clearTimeout(t); ok(); }, { once: true });
      el.addEventListener("error", () => { clearTimeout(t); bad(new Error("error")); }, { once: true });
    });
  }
  async function seek(el, t) {
    const done = once(el, "seeked", 8000);
    el.currentTime = Math.max(0, Math.min(t, el.duration - 0.05));
    await done;
  }
  async function sniffCodec(file) {  // which video codec: read the start and end of the file, where MP4/MOV say it
    const parts = [file.slice(0, MB)];
    if (file.size > 2 * MB) parts.push(file.slice(file.size - MB));
    let text = "";
    for (const part of parts) text += new TextDecoder("latin1").decode(new Uint8Array(await part.arrayBuffer()));
    if (/hvc1|hev1/.test(text)) return "hevc";
    if (/av01/.test(text)) return "av1";
    if (/avc1/.test(text)) return "h264";
    return "unknown";
  }
  async function readVideo(file) {
    const v = { name: file.name, size: file.size, file, problems: [], codec: "unknown" };
    if (!VIDEO_EXT.includes(ext(file.name))) {
      v.problems.push("Use an MP4 or MOV video (the formats every network accepts).");
      return v;
    }
    if (file.size > RULES.max_video_mb * MB) {
      v.problems.push(`This video is ${mb(file.size)}; the limit is ${RULES.max_video_mb} MB. Trim it, or export it at 1080p.`);
      return v;
    }
    v.url = URL.createObjectURL(file);
    const el = document.createElement("video");
    el.muted = true;
    el.playsInline = true;
    el.preload = "auto";
    el.src = v.url;
    try {
      await once(el, "loadedmetadata", 20000);
    } catch (e) {
      v.problems.push("This video can't be played in this browser, so it can't be checked. Export it as MP4 (H.264) and try again.");
      return v;
    }
    v.duration = el.duration;
    v.w = el.videoWidth;
    v.h = el.videoHeight;
    if (!v.w || !v.h || !isFinite(v.duration)) {
      v.problems.push("This video's picture can't be read in this browser (often HEVC from an iPhone). Try Safari, or export it as MP4 (H.264).");
      return v;
    }
    v.codec = await sniffCodec(file);
    try {
      try { await el.play(); el.pause(); } catch (e) { /* some phones only give frames after a play */ }
      v.frames = [];
      for (const at of [0.15, 0.5, 0.85]) {  // what the AI looks at: the beginning, middle and end
        await seek(el, v.duration * at);
        v.frames.push(await toJpeg(el, 768, 0.8));
      }
      v.cover_ms = Math.round(Math.min(1, v.duration * 0.1) * 1000);
      await seek(el, v.cover_ms / 1000);
      v.cover = await toJpeg(el, 1080, 0.9);
      v.coverUrl = URL.createObjectURL(v.cover);
    } catch (e) {
      v.problems.push("The frames of this video can't be read in this browser. Try Chrome or Safari, or export it as MP4 (H.264).");
    }
    el.removeAttribute("src");
    el.load();
    return v;
  }

  // ---------- checks ----------
  const ratio = (w, h) => w / h;
  function pictureChecks() {
    const out = [];
    const need = kind === "carousel" ? 2 : 1;
    if (!pics.length) return [["wait", kind === "carousel" ? `Add 2 to ${RULES.max_images} photos` : "Add a photo to check it"]];
    if (pics.length < need) out.push(["bad", `A carousel needs at least 2 photos (you have ${pics.length})`]);
    let first = null;
    pics.forEach((p, i) => {
      const n = pics.length > 1 ? `Photo ${i + 1}: ` : "";
      p.problems.forEach((t) => out.push(["bad", n + t]));
      if (p.problems.length) return;
      if (Math.min(p.w, p.h) < RULES.min_side) out.push(["bad", `${n}too small (${p.w}×${p.h}). Use at least ${RULES.min_side} px on each side.`]);
      else if (p.w < 1080) out.push(["warn", `${n}low resolution (${p.w}×${p.h}); it may look soft on big screens`]);
      const r = ratio(p.w, p.h);
      const shape = Math.min(Math.max(r, RULES.tallest), RULES.widest);
      if (i === 0) first = shape;
      if (i === 0 && r < RULES.tallest) out.push(["info", `${n}tall photo: it gets a soft blurred background to fit Instagram's 4:5 shape`]);
      else if (i === 0 && r > RULES.widest) out.push(["info", `${n}very wide photo: it gets a soft background to fit Instagram's 1.91:1 shape`]);
      else if (i > 0 && Math.abs(r - first) / first > 0.01) out.push(["info", `${n}gets a soft background to match photo 1's shape (Instagram shows a carousel in one shape)`]);
    });
    if (!out.some(([t]) => t === "bad")) {
      const total = pics.reduce((s, p) => s + p.size, 0);
      out.unshift(["ok", `${pics.length} photo${pics.length > 1 ? "s" : ""} ready (${mb(total)}); sent as smaller, sharp copies`]);
    }
    return out;
  }
  function videoChecks() {
    if (!vid) return [["wait", "Add a video to check it"]];
    const out = vid.problems.map((t) => ["bad", t]);
    if (vid.problems.length) return out;
    if (vid.duration < RULES.min_seconds) out.push(["bad", `Too short (${secs(vid.duration)}). Videos must be at least ${RULES.min_seconds} s.`]);
    if (vid.duration > RULES.max_seconds) out.push(["bad", `Too long (${secs(vid.duration)}). Videos can be at most ${secs(RULES.max_seconds)} (Instagram Reels).`]);
    if (Math.min(vid.w, vid.h) < RULES.min_video_side) out.push(["bad", `Too small (${vid.w}×${vid.h}). Use at least ${RULES.min_video_side} px on each side.`]);
    else if (Math.min(vid.w, vid.h) < 540) out.push(["warn", `Low resolution (${vid.w}×${vid.h}); it may look blurry`]);
    if (vid.codec === "hevc") out.push(["warn", "HEVC (H.265) video, common on iPhones. Buffer converts it, and some networks may refuse it. Safer: export as H.264 (iPhone: Settings → Camera → Formats → Most Compatible)."]);
    if (vid.codec === "av1") out.push(["warn", "AV1 video: some networks may refuse it. Export as MP4 (H.264) to be safe."]);
    const r = ratio(vid.w, vid.h);
    if (r < 0.7) out.push(["info", "Vertical video: perfect for Reels and TikTok"]);
    else out.push(["info", "Horizontal or square video: fine on Facebook and LinkedIn; as an Instagram Reel a vertical 9:16 video looks best"]);
    if (!out.some(([t]) => t === "bad")) out.unshift(["ok", `Video ready: ${clock(vid.duration)} · ${vid.w}×${vid.h} · ${mb(vid.size)}`]);
    return out;
  }
  function destinations() {  // what each connected channel will get: [status, label, name, text]
    const rows = [];
    for (const c of RULES.channels) {
      if (kind === "video") {
        if (!vid || vid.problems.length) { rows.push(["", c.label, c.name, c.no_video ? "No videos" : "Video"]); continue; }
        const [lo, hi] = c.video_seconds || [0, 4 * 3600];
        if (c.no_video) rows.push(["skip", c.label, c.name, `Skipped: ${c.no_video}`]);
        else if (vid.size > c.video_mb * MB) rows.push(["skip", c.label, c.name, `Skipped: at most ${c.video_mb} MB there`]);
        else if (vid.duration < lo || vid.duration > hi) rows.push(["skip", c.label, c.name, `Skipped: videos there must be ${secs(lo)} to ${secs(hi)}`]);
        else if (c.service === "instagram") rows.push(["ok", c.label, c.name, "Reel"]);
        else if (c.service === "facebook" && vid.h / vid.w >= 1.7 && vid.duration > 90) rows.push(["warn", c.label, c.name, "Vertical videos become Reels there, which must be 90 s or shorter; it may be refused"]);
        else if (c.service === "linkedin" && (ratio(vid.w, vid.h) < 9 / 16 - 0.01 || ratio(vid.w, vid.h) > 16 / 9 + 0.01)) rows.push(["warn", c.label, c.name, "Needs a shape between 9:16 and 16:9; it may be refused"]);
        else if (c.service === "tiktok") rows.push(["ok", c.label, c.name, "Video (its text is kept under 150 characters)"]);
        else rows.push(["ok", c.label, c.name, "Video post"]);
      } else {
        const n = Math.max(pics.length, kind === "carousel" ? 2 : 1);
        if (kind === "image" || n <= 1) rows.push(["ok", c.label, c.name, "Photo post"]);
        else if (c.images < n) rows.push(["warn", c.label, c.name, c.images === 1 ? "First photo only" : `First ${c.images} photos only`]);
        else rows.push(["ok", c.label, c.name, c.service === "instagram" ? `Carousel of ${n} photos` : `Post with ${n} photos`]);
      }
    }
    return rows;
  }

  // ---------- drawing the page ----------
  const ICON = { ok: "✓", warn: "!", bad: "✕", info: "i", wait: "•", skip: "–" };
  function render() {
    const media = kind === "video" ? videoChecks() : pictureChecks();
    const dest = destinations();
    const allSkipped = kind === "video" && vid && !vid.problems.length && dest.length && dest.every(([s]) => s === "skip");
    if (allSkipped) media.push(["bad", "None of your connected channels can take this video."]);
    const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
    $("checks").innerHTML = media.map(([t, text]) => `<li class="${t}"><i>${ICON[t]}</i><span>${esc(cap(text))}</span></li>`).join("");
    $("dest").innerHTML = !CHANNELS_KNOWN
      ? '<p class="muted small">Buffer could not be reached just now. The post goes to every channel connected in Buffer.</p>'
      : dest.length ? dest.map(([s, label, name, text]) => `<div class="dest-row ${s}"><b>${esc(label)}</b><span class="dest-name">${esc(name)}</span><span class="dest-what">${esc(text)}</span></div>`).join("")
        : '<p class="muted small">No channels are connected in Buffer yet. Connect them in Buffer first.</p>';
    const ready = kind === "video"
      ? !!(vid && vid.cover && !media.some(([t]) => t === "bad"))
      : pics.length >= (kind === "carousel" ? 2 : 1) && !media.some(([t]) => t === "bad");
    $("go").disabled = !ready || sending;

    $("previews").hidden = kind === "video";
    $("previews").innerHTML = pics.map((p, i) => `<div class="pv${p.problems.length ? " bad" : ""}">
      <div class="pv-img">${p.url ? `<img src="${p.url}" alt="">` : `<span>${esc(p.name)}</span>`}
        <span class="pv-n">${kind === "carousel" ? (i === 0 ? "1 · Cover" : i + 1) : "Your photo"}</span></div>
      <div class="pv-meta"><span>${p.w ? `${p.w}×${p.h} · ` : ""}${mb(p.size)}</span>
        <span class="pv-btns">${kind === "carousel" ? `<button type="button" data-move="-1" data-id="${p.id}" ${i === 0 ? "disabled" : ""} aria-label="Move left">←</button>
          <button type="button" data-move="1" data-id="${p.id}" ${i === pics.length - 1 ? "disabled" : ""} aria-label="Move right">→</button>` : ""}
          <button type="button" data-remove="${p.id}" aria-label="Remove">✕</button></span></div></div>`).join("")
      + (kind === "carousel" && pics.length && pics.length < RULES.max_images ? `<button type="button" class="pv-add" id="pv-add">＋<span>Add photos</span></button>` : "");
    $("pv-add")?.addEventListener("click", () => $("file").click());

    const showVideo = kind === "video" && vid && vid.url && !vid.problems.length;
    $("vprev").hidden = !showVideo;
    if (showVideo) {
      if ($("vplayer").dataset.src !== vid.url) { $("vplayer").src = vid.url; $("vplayer").dataset.src = vid.url; }
      $("vcover").src = vid.coverUrl || "";
      $("vfacts").innerHTML = `<b>${esc(vid.name)}</b><span>${clock(vid.duration)} · ${vid.w}×${vid.h} · ${mb(vid.size)}${vid.codec !== "unknown" ? ` · ${vid.codec.toUpperCase()}` : ""}</span>`;
    }
    const hasMedia = kind === "video" ? !!vid : pics.length > 0;
    $("drop").hidden = (kind === "image" && pics.length > 0) || (kind === "video" && showVideo) || (kind === "carousel" && pics.length > 0);
    $("drop").classList.toggle("has", hasMedia);
  }
  function setKind(k) {
    kind = k;
    pics.forEach((p) => p.url && URL.revokeObjectURL(p.url));
    pics = [];
    clearVideo();
    const f = $("file");
    f.value = "";
    f.multiple = k === "carousel";
    f.accept = k === "video" ? "video/mp4,video/quicktime,.mp4,.mov" : "image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif";
    $("add-title").textContent = { image: "Add your photo", carousel: "Add your photos", video: "Add your video" }[k];
    $("drop-title").textContent = { image: "Drop your photo here, or tap to choose", carousel: `Drop 2 to ${RULES.max_images} photos here, or tap to choose`,
      video: "Drop your video here, or tap to choose" }[k];
    $("drop-sub").textContent = { image: "We check its size and shape, and fit it for Instagram when needed.",
      carousel: "The first photo is the cover. You can change the order after adding them.",
      video: `MP4 or MOV, up to ${RULES.max_video_mb} MB, ${RULES.min_seconds} s to ${secs(RULES.max_seconds)}. Vertical 9:16 is best for Reels.` }[k];
    render();
  }
  function clearVideo() {
    if (vid) { [vid.url, vid.coverUrl].forEach((u) => u && URL.revokeObjectURL(u)); }
    vid = null;
    $("vplayer").removeAttribute("src");
    delete $("vplayer").dataset.src;
    $("vplayer").load();
  }

  async function addFiles(list) {
    const files = [...list];
    if (!files.length) return;
    $("drop").classList.add("busy");
    $("checks").innerHTML = '<li class="wait"><i>•</i><span>Checking…</span></li>';
    try {
      if (kind === "video") {
        clearVideo();
        vid = await readVideo(files[0]);
      } else {
        if (kind === "image") { pics.forEach((p) => p.url && URL.revokeObjectURL(p.url)); pics = []; }
        const room = (kind === "image" ? 1 : RULES.max_images) - pics.length;
        if (files.length > room) toast(kind === "image" ? "One photo only. For more, choose Photo carousel." : `A carousel holds ${RULES.max_images} photos at most; the extra ones were left out.`, true);
        for (const f of files.slice(0, room)) pics.push(await readPicture(f));
      }
    } finally {
      $("drop").classList.remove("busy");
      $("file").value = "";
      render();
    }
  }

  // ---------- events ----------
  document.querySelectorAll('input[name="kind"]').forEach((r) => r.addEventListener("change", () => {
    if ((pics.length || vid) && !confirm("Switch? The files you added are removed.")) {
      document.querySelector(`input[name="kind"][value="${kind}"]`).checked = true;
      return;
    }
    setKind(r.value);
  }));
  const drop = $("drop");
  drop.addEventListener("click", () => $("file").click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  $("file").addEventListener("change", (e) => addFiles(e.target.files));
  $("previews").addEventListener("click", (e) => {
    const rm = e.target.closest("button[data-remove]"), mv = e.target.closest("button[data-move]");
    if (rm) {
      const p = pics.find((x) => x.id === rm.dataset.remove);
      if (p && p.url) URL.revokeObjectURL(p.url);
      pics = pics.filter((x) => x.id !== rm.dataset.remove);
    } else if (mv) {
      const i = pics.findIndex((x) => x.id === mv.dataset.id), j = i + +mv.dataset.move;
      if (i < 0 || j < 0 || j >= pics.length) return;
      [pics[i], pics[j]] = [pics[j], pics[i]];
    } else return;
    render();
  });
  $("set-cover").addEventListener("click", async () => {
    const pl = $("vplayer");
    if (!vid || !pl.videoWidth) return;
    try {
      const blob = await toJpeg(pl, 1080, 0.9);
      if (vid.coverUrl) URL.revokeObjectURL(vid.coverUrl);
      vid.cover = blob;
      vid.coverUrl = URL.createObjectURL(blob);
      vid.cover_ms = Math.round(pl.currentTime * 1000);
      toast(`Cover set to ${clock(pl.currentTime)}`);
      render();
    } catch (e) { toast("That moment can't be used as the cover. Try another one.", true); }
  });
  $("vremove").addEventListener("click", () => { clearVideo(); render(); });
  $("when-mode").addEventListener("change", (e) => { $("when-at-wrap").hidden = e.target.value !== "at"; });

  // ---------- sending ----------
  function progress(part, text) {
    $("progress").hidden = false;
    $("bar").style.width = `${Math.round(Math.max(0.03, Math.min(1, part)) * 100)}%`;
    $("progress-text").textContent = text;
  }
  function request(method, url, body, headers, onProgress) {
    return new Promise((ok, bad) => {
      const x = new XMLHttpRequest();
      x.open(method, url);
      Object.entries(headers || {}).forEach(([k, v]) => x.setRequestHeader(k, v));
      if (onProgress) x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      x.onload = () => {
        let data = {};
        try { data = JSON.parse(x.responseText || "{}"); } catch (e) { /* not JSON */ }
        if (x.status >= 200 && x.status < 300) return ok(data);
        if (x.status === 413) return bad(new Error(`The file is too big for the storage (at most ${RULES.max_video_mb} MB).`));
        bad(new Error(data.detail || data.message || `Upload failed (error ${x.status}). Please try again.`));
      };
      x.onerror = () => bad(new Error("The upload was interrupted. Check the internet connection and try again."));
      x.send(body);
    });
  }
  $("up-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("go").disabled) return;
    const at = $("when-mode").value === "at" ? $("publish-at").value : "";
    if ($("when-mode").value === "at" && !at) return toast("Pick the date and time to publish", true);
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("note", $("note").value.trim());
    fd.append("service", $("service").value);
    if (at) fd.append("publish_at", at);
    sending = true;
    render();
    try {
      let post;
      if (kind === "video") {
        progress(0.02, "Getting the upload ready…");
        const target = await api("POST", "/api/uploads/video-target", { name: vid.name, size: vid.size });
        await request(target.method || "PUT", target.url, vid.file, target.headers,
          (p) => progress(p * 0.9, `Uploading the video… ${Math.round(p * 100)}% (${mb(vid.size * p)} of ${mb(vid.size)})`));
        fd.append("video_path", target.path);
        fd.append("video_meta", JSON.stringify({ duration: vid.duration, width: vid.w, height: vid.h, codec: vid.codec, name: vid.name, cover_ms: vid.cover_ms }));
        fd.append("poster", vid.cover, "cover.jpg");
        vid.frames.forEach((b, i) => fd.append("frames", b, `frame${i + 1}.jpg`));
        progress(0.93, "Checking the video…");
        post = await request("POST", "/api/uploads", fd);
      } else {
        pics.forEach((p, i) => fd.append("images", p.blob, `${i + 1}.jpg`));
        post = await request("POST", "/api/uploads", fd, null, (p) => progress(p * 0.85,
          p < 1 ? `Uploading ${pics.length > 1 ? "the photos" : "the photo"}… ${Math.round(p * 100)}%` : "Getting the photos ready for every network…"));
      }
      progress(1, "Done! The AI is writing the captions. Opening your post…");
      location.href = `/posts/${post.id}`;
    } catch (err) {
      sending = false;
      $("progress").hidden = true;
      toast(err.message, true);
      render();
    }
  });

  setKind("image");
})();
