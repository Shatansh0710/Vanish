// Vanish client-side crypto. Everything here runs in the browser; the server only ever sees ciphertext.
//
// Outer layer (always):   AES-256-GCM with a random key that travels only in the URL fragment (#key).
// Inner layer (optional): AES-256-GCM with a key derived from a passphrase (PBKDF2-SHA256, 310k rounds).
//
// Envelope (JSON, encrypted by the outer layer):
//   unprotected: {"v":1,"t":"<note text>"}
//   protected:   {"v":1,"s":"<salt b64>","i":"<inner iv b64>","d":"<inner ciphertext b64>"}

(function () {
  "use strict";
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const PBKDF2_ROUNDS = 310000;

  function toB64(bytes) {
    let s = "";
    const b = new Uint8Array(bytes);
    for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
    return btoa(s);
  }
  function fromB64(str) {
    const s = atob(str.replace(/-/g, "+").replace(/_/g, "/"));
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  const toB64Url = (bytes) => toB64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const fromB64Url = (str) => fromB64(str + "===".slice((str.length + 3) % 4));

  async function passKey(passphrase, salt) {
    const base = await crypto.subtle.importKey("raw", enc.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey(
      { name: "PBKDF2", salt, iterations: PBKDF2_ROUNDS, hash: "SHA-256" },
      base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }

  async function encryptNote(text, passphrase) {
    let envelope;
    if (passphrase) {
      const salt = crypto.getRandomValues(new Uint8Array(16));
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const key = await passKey(passphrase, salt);
      const inner = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, enc.encode(text));
      envelope = { v: 1, s: toB64(salt), i: toB64(iv), d: toB64(inner) };
    } else {
      envelope = { v: 1, t: text };
    }
    const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, enc.encode(JSON.stringify(envelope)));
    const raw = await crypto.subtle.exportKey("raw", key);
    return { ciphertext: toB64(ct), iv: toB64(iv), key: toB64Url(raw), protected: !!passphrase };
  }

  // Returns the envelope. Throws if the link key is wrong or the data was tampered with.
  async function openEnvelope(ciphertextB64, ivB64, keyB64Url) {
    const raw = fromB64Url(keyB64Url);
    if (raw.length !== 32) throw new Error("bad_key");
    const key = await crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["decrypt"]);
    const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: fromB64(ivB64) }, key, fromB64(ciphertextB64));
    return JSON.parse(dec.decode(pt));
  }

  // Returns the note text. For protected envelopes a passphrase is required; throws "bad_passphrase".
  async function readEnvelope(envelope, passphrase) {
    if (typeof envelope.t === "string") return envelope.t;
    try {
      const key = await passKey(passphrase || "", fromB64(envelope.s));
      const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: fromB64(envelope.i) }, key, fromB64(envelope.d));
      return dec.decode(pt);
    } catch (e) {
      throw new Error("bad_passphrase");
    }
  }

  window.VanishCrypto = { encryptNote, openEnvelope, readEnvelope, isProtected: (e) => typeof e.t !== "string" };
})();
