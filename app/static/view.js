// Vanish — view page: /n/<id>#<key>
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const API = "";
  const SECTIONS = ["loading", "ready", "locked", "shown", "gone", "broken"];

  const id = (location.pathname.split("/")[2] || "").trim();
  const key = location.hash.slice(1);
  let meta = null, envelope = null, opened = null, timer = null, skew = 0;

  function show(name) {
    SECTIONS.forEach((s) => { $(s).hidden = s !== name; });
    const el = $(name);
    el.classList.remove("appear"); void el.offsetWidth; el.classList.add("appear");
  }

  function remaining(expiresAt) {
    let s = Math.max(0, Math.floor(expiresAt - (Date.now() / 1000 + skew)));
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60); s -= m * 60;
    if (d) return `${d}d ${h}h ${m}m`;
    if (h) return `${h}h ${m}m ${String(s).padStart(2, "0")}s`;
    return `${m}m ${String(s).padStart(2, "0")}s`;
  }

  function renderReady() {
    const left = meta.viewsLeft;
    const views = left === 1
      ? '<strong class="warn">Opening it destroys it.</strong>'
      : `It can be opened <strong>${left} more times</strong>.`;
    $("readyStatus").innerHTML = `${views} It expires in <strong>${remaining(meta.expiresAt)}</strong>.`
      + (meta.protected ? " You'll need the passphrase from the sender." : "");
    if (meta.expiresAt <= Date.now() / 1000 + skew) { clearInterval(timer); show("gone"); }
  }

  async function init() {
    if (!/^[A-Za-z0-9_-]{12}$/.test(id)) return show("gone");
    if (!/^[A-Za-z0-9_-]{43}$/.test(key)) return show("broken");
    if (!window.crypto || !window.crypto.subtle) {
      $("loading").querySelector("p").textContent = "Decryption needs a secure connection. Open this link with https:// instead of http://.";
      return;
    }
    try {
      const res = await fetch(`${API}/api/notes/${id}`);
      if (!res.ok) return show("gone");
      meta = await res.json();
      skew = meta.serverTime - Date.now() / 1000;
      show("ready");
      renderReady();
      timer = setInterval(renderReady, 1000);
    } catch (_) {
      $("loading").querySelector("p").textContent = "Couldn't reach the server. Check your connection and reload the page.";
    }
  }

  $("open").addEventListener("click", async () => {
    const btn = $("open");
    btn.disabled = true;
    btn.textContent = "Opening…";
    $("openError").textContent = "";
    try {
      const res = await fetch(`${API}/api/notes/${id}/open`, { method: "POST" });
      if (res.status === 410 || res.status === 404) { clearInterval(timer); return show("gone"); }
      if (!res.ok) throw new Error("http_" + res.status);
      opened = await res.json();
      clearInterval(timer);
      // Remove the key from the address bar so it doesn't linger in history or screenshots.
      history.replaceState(null, "", location.pathname);
      try {
        envelope = await window.VanishCrypto.openEnvelope(opened.ciphertext, opened.iv, key);
      } catch (_) {
        $("gone").querySelector("p").textContent =
          "The link's key doesn't match this note, so it can't be decrypted. Ask the sender to copy the full link again.";
        return show("gone");
      }
      if (window.VanishCrypto.isProtected(envelope)) { show("locked"); $("pass").focus(); return; }
      reveal(await window.VanishCrypto.readEnvelope(envelope));
    } catch (_) {
      btn.disabled = false;
      btn.textContent = "Open note";
      $("openError").textContent = "Couldn't open the note. Check your connection and try again.";
    }
  });

  $("unlockForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("unlock");
    btn.disabled = true;
    btn.textContent = "Unlocking…";
    $("unlockError").textContent = "";
    try {
      reveal(await window.VanishCrypto.readEnvelope(envelope, $("pass").value));
    } catch (_) {
      $("unlockError").textContent = "That passphrase is wrong. Check with the sender and try again.";
      $("pass").select();
    } finally {
      btn.disabled = false;
      btn.textContent = "Unlock note";
    }
  });

  function reveal(text) {
    $("out").textContent = text;
    $("shownStatus").innerHTML = opened.burned
      ? '<span class="done">That was the last view.</span> The note has been deleted from the server, so copy anything you need before you leave this page.'
      : `It can be opened <strong>${opened.viewsLeft} more time${opened.viewsLeft === 1 ? "" : "s"}</strong> before it's destroyed.`;
    show("shown");
  }

  $("copyText").addEventListener("click", async () => {
    const btn = $("copyText");
    try { await navigator.clipboard.writeText($("out").textContent); btn.textContent = "Copied"; }
    catch (_) { btn.textContent = "Select the text to copy"; }
    setTimeout(() => { btn.textContent = "Copy text"; }, 1600);
  });

  init();
})();
