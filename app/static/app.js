const WORKING = ["queued", "writing", "imaging", "designing", "emailing", "publishing"];
const STATUS = {
  queued: ["Starting", "work"], writing: ["Writing", "work"], imaging: ["Creating image", "work"],
  designing: ["Designing", "work"], emailing: ["Emailing", "work"], publishing: ["Publishing", "work"],
  pending_review: ["Waiting for approval", "wait"], scheduled: ["Scheduled", "sched"], published: ["Published", "ok"],
  partly_published: ["Partly published", "bad"], publish_failed: ["Publish failed", "bad"], failed: ["Failed", "bad"],
  missed: ["Missed its time", "bad"], cancelled: ["Cancelled", ""],
};
const FILTERS = {
  all: () => true,
  approval: (p) => p.status === "pending_review",
  scheduled: (p) => p.status === "scheduled",
  working: (p) => WORKING.includes(p.status),
  published: (p) => p.status === "published",
  problems: (p) => ["failed", "publish_failed", "partly_published", "missed"].includes(p.status),
};

const DUBAI = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Dubai", weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true,
});
function dubai(iso) {
  return iso ? DUBAI.format(new Date(iso)).replace(/\b(am|pm)\b/, (m) => m.toUpperCase()) : "";
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  return data;
}

function toast(msg, bad = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), 4500);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function badge(status) {
  const [label, cls] = STATUS[status] || [status, ""];
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

async function copyText(btn, id) {
  await navigator.clipboard.writeText(document.getElementById(id).innerText);
  btn.textContent = "Copied ✓";
  setTimeout(() => (btn.textContent = "Copy"), 1500);
}

// Big pictures: show the small thumbnail at once, then swap in the full picture when it has fully arrived
// (so a slow connection never shows half a picture).
function sharpenImages() {
  document.querySelectorAll("img[data-full]").forEach((el) => {
    const full = el.dataset.full;
    if (!full || el.src === full) return;
    const big = new Image();
    big.onload = () => { el.src = full; };
    big.src = full;
  });
}
// start only after the page (thumbnail included) has loaded, so nothing competes with it on a slow connection
if (document.readyState === "complete") sharpenImages();
else window.addEventListener("load", sharpenImages);
