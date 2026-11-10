// Vanish — create page.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const API = "";
  const MAX_CHARS = 4000;
  const MAX_CIPHERTEXT = 16000;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const note = $("note"), count = $("count"), form = $("form"), err = $("error"), createBtn = $("create");
  let current = null; // { id, deleteToken }

  // ---------------------------------------------------------------- composing
  function updateCount() {
    const n = note.value.length;
    count.textContent = `${n.toLocaleString()} / ${MAX_CHARS.toLocaleString()}`;
    count.classList.toggle("over", n >= MAX_CHARS);
  }
  note.addEventListener("input", updateCount);

  function toggle(checkId, fieldId, inputId) {
    $(checkId).addEventListener("change", (e) => {
      $(fieldId).hidden = !e.target.checked;
      if (e.target.checked) $(inputId).focus();
    });
  }
  toggle("wantPass", "passField", "pass");

  // Size each in-sentence dropdown to its chosen option, so the sentence reads naturally.
  const measure = document.createElement("canvas").getContext("2d");
  function fitSelect(sel) {
    const cs = getComputedStyle(sel);
    measure.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const w = measure.measureText(sel.options[sel.selectedIndex].text).width;
    sel.style.width = `${Math.ceil(w) + 5}px`;
  }
  document.querySelectorAll(".rule select").forEach((sel) => {
    fitSelect(sel);
    sel.addEventListener("change", () => fitSelect(sel));
  });
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => document.querySelectorAll(".rule select").forEach(fitSelect));
  }

  // ---------------------------------------------------------------- the dissolve
  function dissolve(text) {
    if (reduceMotion || !text) return Promise.resolve();
    const field = $("noteField");
    const layer = document.createElement("div");
    layer.className = "dissolve";
    layer.setAttribute("aria-hidden", "true");
    const visible = text.slice(0, 700);
    const frag = document.createDocumentFragment();
    for (const ch of visible) {
      const s = document.createElement("span");
      s.textContent = ch;
      s.style.setProperty("--d", `${Math.floor(Math.random() * 520)}ms`);
      frag.appendChild(s);
    }
    layer.appendChild(frag);
    layer.scrollTop = note.scrollTop;
    field.appendChild(layer);
    field.classList.add("is-dissolving");
    return new Promise((r) => setTimeout(r, 1300)).then(() => {
      layer.remove();
      field.classList.remove("is-dissolving");
    });
  }

  // ---------------------------------------------------------------- helpers
  function humanDuration(sec) {
    if (sec < 3600) return `${Math.round(sec / 60)} minutes`;
    if (sec < 86400) { const h = Math.round(sec / 3600); return h === 1 ? "1 hour" : `${h} hours`; }
    const d = Math.round(sec / 86400); return d === 1 ? "1 day" : `${d} days`;
  }
  function fmtTime(epoch) {
    return new Date(epoch * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }
  async function api(path, opts) {
    const res = await fetch(API + path, opts);
    let body = null;
    if (res.status !== 204) { try { body = await res.json(); } catch (_) { body = null; } }
    return { ok: res.ok, status: res.status, body };
  }

  // ---------------------------------------------------------------- create
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    const text = note.value;
    const ttl = parseInt($("ttl").value, 10);
    const maxViews = parseInt($("views").value, 10);
    const pass = $("wantPass").checked ? $("pass").value : "";

    if (!window.crypto || !window.crypto.subtle) {
      err.textContent = "Encryption needs a secure connection. Open this page with https:// (or on localhost).";
      return;
    }
    if (!text.trim()) { err.textContent = "Write something first."; note.focus(); return; }
    if ($("wantPass").checked && pass.length < 4) { err.textContent = "Use a passphrase of at least 4 characters."; $("pass").focus(); return; }

    createBtn.disabled = true;
    createBtn.textContent = "Encrypting…";
    try {
      const sealed = await window.VanishCrypto.encryptNote(text, pass);
      if (sealed.ciphertext.length > MAX_CIPHERTEXT) {
        err.textContent = "This note is too long once encrypted. Shorten it and try again.";
        return;
      }
      const [res] = await Promise.all([
        api("/api/notes", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            ciphertext: sealed.ciphertext, iv: sealed.iv, ttlSeconds: ttl, maxViews,
            protected: sealed.protected,
          }),
        }),
        dissolve(text),
      ]);
      if (!res.ok) {
        err.textContent = (res.body && res.body.message) || `The server returned an error (${res.status}). Try again.`;
        return;
      }
      showResult(res.body, sealed.key, { ttl, maxViews, protected: sealed.protected });
    } catch (ex) {
      err.textContent = "Couldn't reach the server. Check your connection and try again.";
    } finally {
      createBtn.disabled = false;
      createBtn.textContent = "Create secret link";
    }
  });

  function showResult(body, key, opts) {
    current = { id: body.id, deleteToken: body.deleteToken };
    const link = `${location.origin}/n/${body.id}#${key}`;
    note.value = ""; $("pass").value = ""; updateCount();

    $("link").value = link;
    $("factViews").innerHTML = opts.maxViews === 1
      ? "<strong>Works once.</strong> Opening it destroys it."
      : `<strong>Works ${opts.maxViews} times</strong>, then it's destroyed.`;
    $("factExpiry").innerHTML = `<strong>Expires in ${humanDuration(opts.ttl)}</strong>, at ${fmtTime(body.expiresAt)}.`;
    $("factPass").hidden = !opts.protected;

    try {
      const qr = window.qrcode(0, "M");
      qr.addData(link);
      qr.make();
      $("qr").innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true, alt: "QR code for the secret link" });
    } catch (_) { $("qr").hidden = true; }

    $("compose").hidden = true;
    const r = $("result");
    r.hidden = false;
    r.classList.add("appear");
    $("resultError").textContent = "";
    $("destroy").disabled = false;
    $("destroy").textContent = "Destroy now";
    $("link").focus();
    $("link").select();
  }

  // ---------------------------------------------------------------- result actions
  $("copy").addEventListener("click", async () => {
    const btn = $("copy");
    try {
      await navigator.clipboard.writeText($("link").value);
    } catch (_) {
      $("link").select(); document.execCommand && document.execCommand("copy");
    }
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = "Copy link"; }, 1600);
  });

  $("destroy").addEventListener("click", async () => {
    if (!current) return;
    const btn = $("destroy");
    btn.disabled = true;
    try {
      const res = await api(`/api/notes/${current.id}`, {
        method: "DELETE", headers: { "x-delete-token": current.deleteToken },
      });
      if (res.ok || res.status === 404) {
        btn.textContent = "Destroyed";
        $("link").value = "This link no longer works.";
        $("qr").innerHTML = "";
        $("resultError").textContent = "";
        current = null;
      } else {
        btn.disabled = false;
        $("resultError").textContent = "Couldn't destroy the note. Try again.";
      }
    } catch (_) {
      btn.disabled = false;
      $("resultError").textContent = "Couldn't reach the server. Try again.";
    }
  });

  $("again").addEventListener("click", () => {
    current = null;
    $("result").hidden = true;
    $("compose").hidden = false;
    note.focus();
  });

  updateCount();
})();
