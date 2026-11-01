"use strict";
/* RU / EN. The app is written in Russian; in English a watcher swaps every Russian text on the page that has a
   translation in en.js (EN = whole phrases, PATS = phrases with numbers, FRAG = pieces glued to other text), as it
   appears, so the screens themselves need no changes. Elements with translate="no" (vacancy texts, names, résumé
   lines) are left as they are. The language: the person's choice (saved on the server and here), else Russian for
   people who already used the app, else the language of their Telegram. */
const I18N = (() => {
  const tgUser = (() => { try { return window.Telegram.WebApp.initDataUnsafe.user || {}; } catch (e) { return {}; } })();
  const RU_FAMILY = /^(ru|uk|be|kk|uz|ky|tg|hy|az)/i;
  let lang = "";
  try { lang = localStorage.getItem("jr-lang") || ""; } catch (e) { /* blocked */ }
  if (lang !== "ru" && lang !== "en") lang = !tgUser.language_code || RU_FAMILY.test(tgUser.language_code) ? "ru" : "en";
  const CYR = /[А-Яа-яЁё]/;
  const missing = new Set();
  let frag = null;  // FRAG keys, longest first, built once

  function look(k) {
    const D = window.EN || {};
    if (Object.prototype.hasOwnProperty.call(D, k)) return D[k];
    for (const [re, rep] of window.EN_PATS || []) if (re.test(k)) return k.replace(re, rep);
    return undefined;
  }
  /* one text in the current language; untranslated Russian stays Russian (and is noted in I18N.missing) */
  function tr(s) {
    if (lang === "ru" || s == null) return s;
    s = String(s);
    if (!CYR.test(s)) return s;
    const raw = s.trim(), k = raw.replace(/\s+/g, " ");  // a text broken over lines in the source reads as one line
    let out = look(k);
    if (out === undefined) {  // «📋 Мои отклики», «Отправить →»: the words without the icon or arrow around them
      const m = k.match(/^([^А-Яа-яЁёA-Za-z0-9«]*)(.*?)([\s.:!?…→←✓]*)$/s);
      if (m && m[2] && (m[1] || m[3])) {
        const core = look(m[2]);
        if (core !== undefined) out = m[1] + core + m[3];
      }
    }
    if (out === undefined) {  // a line glued from parts: every known phrase in it, longest first, whole words only
      if (!frag) {
        const all = { ...(window.EN || {}), ...(window.EN_FRAG || {}) };
        const keys = Object.keys(all).filter((x) => x.length > 1 && CYR.test(x)).sort((a, b) => b.length - a.length);
        const quote = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        frag = { all, re: new RegExp("(?<![А-Яа-яЁё])(?:" + keys.map(quote).join("|") + ")(?![А-Яа-яЁё])", "g") };
      }
      const t = k.replace(frag.re, (x) => frag.all[x]);
      if (!CYR.test(t)) out = t;  // only when every word is known: half-translated text reads worse than Russian
    }
    if (out === undefined || CYR.test(out)) missing.add(k);
    if (out === undefined) return s;
    return s.replace(raw, out);
  }

  const SKIP = "[translate=no],script,style,textarea,code";
  const ATTRS = ["placeholder", "title", "aria-label"];
  function fixText(n) {
    const p = n.parentElement;
    if (!p || p.closest(SKIP) || !CYR.test(n.data)) return;
    const t = tr(n.data);
    if (t !== n.data) n.data = t;
  }
  function fixEl(el) {
    if (el.closest(SKIP)) return;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v && CYR.test(v)) { const t = tr(v); if (t !== v) el.setAttribute(a, t); }
    }
    if ((el.tagName === "INPUT" && /^(button|submit)$/i.test(el.type)) && CYR.test(el.value)) el.value = tr(el.value);
  }
  function fixTree(root) {
    if (root.nodeType === 3) return fixText(root);
    if (root.nodeType !== 1 && root.nodeType !== 11) return;
    if (root.nodeType === 1) { if (root.closest(SKIP)) return; fixEl(root); }
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeType === 1 && n.matches(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    for (let n = w.nextNode(); n; n = w.nextNode()) (n.nodeType === 3 ? fixText : fixEl)(n);
  }
  function watch() {
    document.documentElement.lang = lang;
    if (lang === "ru") return;
    document.title = tr(document.title);
    fixTree(document.body);
    new MutationObserver((list) => {
      for (const r of list) {
        if (r.type === "characterData") fixText(r.target);
        else if (r.type === "attributes") fixEl(r.target);
        else r.addedNodes.forEach(fixTree);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  /* a new language: saved, then the page opens again in it (every screen is drawn afresh) */
  function set(next, save) {
    if (next !== "ru" && next !== "en") return;
    try { localStorage.setItem("jr-lang", next); } catch (e) { /* blocked */ }
    const changed = next !== lang;
    const go = () => { if (changed) location.reload(); };
    if (save && typeof api === "function") api("/api/settings", { lang: next }).catch(() => {}).finally(go);
    else go();
  }
  /* what /api/me says: a saved choice wins; someone who used the app before any choice stays in Russian */
  function fromServer(me) {
    const want = me.lang || (me.onboarded ? "ru" : lang);
    if (!me.lang) api("/api/settings", { lang: want }).catch(() => {});  // a newcomer keeps the language once onboarded
    if (want !== lang) set(want, false);
  }
  return { get lang() { return lang; }, tr, set, fromServer, watch, missing, locale: () => (lang === "en" ? "en-GB" : "ru-RU") };
})();
const tr = I18N.tr;
