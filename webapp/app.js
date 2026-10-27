"use strict";
/* Job Radar mini app: talks to the bot's API (webapp.py). Every request carries
   Telegram's signed launch data, so each person sees only their own vacancies. */

const tg = window.Telegram && window.Telegram.WebApp;
const INIT = tg ? tg.initData : "";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const SKINS = {  // three cat themes; the old skin names still open the closest new one
  hybrid: { name: "Гибрид", bg: "#f2f0ec", colors: ["#f2f0ec", "#7d8590", "#2f8f83"] },
  remote: { name: "Удалёнка", bg: "#fff3d6", colors: ["#fff3d6", "#26b6a6", "#ff7a2f"] },
  office: { name: "Офис", bg: "#eceff3", colors: ["#eceff3", "#3c4656", "#1d3a6e"] },
};
const OLD_SKINS = { radar: "hybrid", tropic: "remote", dossier: "office" };
const MATCH = { exact: "точно" };
const st = { me: null, feed: [], total: 0, sort: "date", age: "", grade: "", unscored: 0, loading: false, filter: "all", saved: "fav", mode: "exact", off: new Set(), results: [], sources: null, sheet: null, scoring: new Set(), tzHidden: 0, view: "cards" };
try { if (localStorage.getItem("jr-feed") === "list") st.view = "list"; } catch (e) { /* blocked */ }
const STAGES = { applied: "Откликнулся", test: "Тестовое", interview: "Собеседование", offer: "Оффер", rejected: "Отказ" };

/* ---------------------------------------------------------------- helpers */

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { Authorization: "tma " + INIT, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || "Ошибка " + res.status);
  return data;
}
function haptic(kind) {
  try {
    if (kind === "ok" || kind === "error") tg.HapticFeedback.notificationOccurred(kind === "ok" ? "success" : "error");
    else tg.HapticFeedback.impactOccurred(kind || "light");
  } catch (e) { /* not in Telegram */ }
}
function toast(text) {
  if (!text) return;
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("on");
  clearTimeout(toast.h);
  toast.h = setTimeout(() => t.classList.remove("on"), 1900);
}
function fail(e) { haptic("error"); toast(e.message || String(e)); }
function plural(n, one, few, many) {
  const a = n % 10, b = n % 100;
  return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many;
}
function when(iso) {
  const d = new Date(iso), now = new Date();
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const diff = Math.round((day(now) - day(d)) / 864e5);
  const hm = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (diff <= 0) return "сегодня " + hm;
  if (diff === 1) return "вчера " + hm;
  if (diff < 7) return diff + " " + plural(diff, "день", "дня", "дней") + " назад";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}
function seenAt(iso) {  // «сегодня 14:32», «вчера 09:10», «3 окт. 18:05»: always with the time
  const d = new Date(iso), w = when(iso);
  return /^(сегодня|вчера)/.test(w) ? w : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}
function ago(iso) {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso)) / 6e4));
  if (min < 1) return "только что";
  if (min < 60) return min + " мин назад";
  const h = Math.round(min / 60);
  return h + " " + plural(h, "час", "часа", "часов") + " назад";
}
const kindOf = (i) => (i.source === "tg" ? "tg" : i.origin === "hh.ru" ? "hh" : "web");
const originOf = (i) => (i.source === "tg" ? "💬 " + i.origin : i.origin === "hh.ru" ? "hh.ru" : "🌐 " + i.origin);
const tagsHtml = (tags) => (tags || []).map((t) =>
  `<span class="tag${t === "ru-company" ? " ru" : ""}">#${esc(t.replace(/-/g, "_"))}</span>`).join("");
/* the pay in one currency: «≈ $2 700/мес» next to what the vacancy says, unless it is dollars already */
const salaryUsd = (i) => (i.salary_usd && !/\$|usd|доллар/i.test(i.salary || "") ? ` ≈ $${i.salary_usd.toLocaleString("ru-RU")}/мес` : "");
const metaHtml = (i) => [i.location && "📍 " + i.location, i.salary && "💰 " + i.salary + salaryUsd(i), i.tz && "🕒 " + i.tz, i.old && "🕰 старше 2 недель"].filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join("");
const alsoText = (i) => (i.also && i.also.length ? `＋ ещё в: ${i.also.map((a) => (a.source === "tg" ? "💬 " : "🌐 ") + a.origin + (a.location ? " · " + a.location : "")).join(", ")}` : "");
function aiLine(i) {
  if (i.ai) return `<div class="ai"><b>✨ ${i.ai.score}/5</b> ${esc(i.ai.verdict)}</div>`;
  const algo = i.algo && i.algo.score ? `<div class="algo" title="${esc(i.algo.why.join("; "))}">⚖️ ${i.algo.score}/5 по совпадению слов${i.algo.why.length ? " · " + esc(i.algo.why[0]) : ""}</div>` : "";
  if (st.scoring.has(i.key)) return '<div class="ai busy">✨ ИИ оценивает…</div>';
  return algo + (st.me && st.me.ai ? '<button class="ai-btn" data-score>✨ Оценить ИИ: подходит ли мне</button>' : "");
}
function openLink(url) {
  if (!url) return;
  if (tg && /^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
  else if (tg) tg.openLink(url);
  else window.open(url, "_blank");
}

/* ---------------------------------------------------------------- skins */

function setSkin(skin, save) {
  skin = OLD_SKINS[skin] || skin;
  if (!SKINS[skin]) skin = "hybrid";
  const html = document.documentElement;
  if (save && html.dataset.skin !== skin) {  // a picked theme flows in instead of snapping
    html.classList.add("skinning");
    setTimeout(() => html.classList.remove("skinning"), 600);
  }
  html.dataset.skin = skin;
  try { tg.setHeaderColor(SKINS[skin].bg); tg.setBackgroundColor(SKINS[skin].bg); } catch (e) { /* old client */ }
  try { tg.setBottomBarColor(SKINS[skin].bg); } catch (e) { /* old client */ }
  try { localStorage.setItem("jr-skin", skin); } catch (e) { /* blocked */ }
  $$(".skin").forEach((b) => b.setAttribute("aria-pressed", b.dataset.skin === skin));
  if (save) api("/api/settings", { skin }).catch(() => {});
}
function skinsHtml() {
  const cur = document.documentElement.dataset.skin;
  return Object.entries(SKINS).map(([id, s]) =>
    `<button class="skin" data-skin="${id}" aria-pressed="${id === cur}"><span class="sw3">${s.colors.map((c) => `<i style="background:${c}"></i>`).join("")}</span>${s.name}</button>`).join("");
}
function bindSkins(root) { $$(".skin", root).forEach((b) => (b.onclick = () => { haptic(); setSkin(b.dataset.skin, true); })); }

/* ---------------------------------------------------------------- feed */

async function loadMe() {
  st.me = await api("/api/me?since=" + encodeURIComponent(VISIT));
  const m = st.me, s = m.stats;
  $("#hello").innerHTML = s.new
    ? `${esc(m.name)}, <em>${s.new}</em> ${plural(s.new, "новая", "новые", "новых")}`
    : `${esc(m.name)}, всё разобрано`;
  $("#checked").textContent = (m.checked_at ? "Проверено " + ago(m.checked_at) : "Первая проверка скоро")
    + (m.next_check ? ` · следующая через ${m.next_check} мин` : "");
  const f = m.funnel || {};
  for (const k of ["jobs", "applied", "interview", "offer"]) $("#fn-" + k).textContent = f[k] ?? "–";
  const wb = $("#watchbar");
  wb.hidden = !m.watch_new;
  wb.textContent = m.watch_new ? `⭐ ${m.watch_new} ${plural(m.watch_new, "новая вакансия", "новые вакансии", "новых вакансий")} у избранных компаний →` : "";
  $("#badge").hidden = !s.new;
  $("#badge").textContent = s.new > 99 ? "99+" : s.new;
}
/* the feed comes in pages (PAGE at a time) so thousands of vacancies never load at once */
const PAGE = 40;
/* 👁 «Просмотрено»: a vacancy opened in full (a tap or «Подробнее») is marked seen. This visit it stays,
   next visit the feed has only what is new; the seen ones wait in «Мои → Просмотрено» with the time. */
const VISIT = new Date().toISOString();
const seenQueue = new Set();
let seenTimer = null;
function markSeen(key) {
  if (!key) return;
  seenQueue.add(key);
  clearTimeout(seenTimer);
  seenTimer = setTimeout(flushSeen, 1500);
}
function flushSeen() {
  if (!seenQueue.size) return;
  const keys = [...seenQueue];
  seenQueue.clear();
  api("/api/seen", { keys }).catch(() => keys.forEach((k) => seenQueue.add(k)));
}
document.addEventListener("visibilitychange", () => { if (document.hidden) flushSeen(); });
const feedUrl = (offset) => `/api/jobs?view=new&since=${encodeURIComponent(VISIT)}&sort=${st.sort}&f=${st.filter}&age=${st.age}&grade=${st.grade}&offset=${offset}&limit=${PAGE}`;
async function loadFeed(report) {
  const had = st.feed && st.feed.length >= st.total ? st.feed.map((i) => i.key) : null;  // the whole feed was on hand
  const d = await api(feedUrl(0));
  if (report && had && d.total <= PAGE) {  // cards the bot took away by itself (archived: office, other country, closed)
    const keys = new Set(d.items.flatMap((i) => [i.key, ...(i.also || []).map((a) => a.key)]));
    const gone = had.filter((k) => !keys.has(k)).length;
    if (gone) toast(`${gone} ${plural(gone, "вакансию", "вакансии", "вакансий")} бот убрал в архив: офис, другая страна или закрыта. Они в «Ещё → Архив»`);
  }
  st.feed = d.items;
  st.total = d.total;
  st.tzHidden = d.tz_hidden || 0;
  st.unscored = d.unscored || 0;
  renderExclude(d);
  renderScoreNew();
  renderStack();
}
/* the feed did not load: say so in the card area (never a blank space under «Фильтры») and offer another try */
function feedFailed(e) {
  if ((st.feed || []).length) return fail(e);
  $("#stack").hidden = false;
  $("#stack").innerHTML = `<div class="empty"><b>Лента не загрузилась</b>${esc(e && e.message ? e.message : "Нет связи с ботом.")}<br><button class="link" id="feed-retry">Повторить</button></div>`;
  $("#feed-retry").onclick = () => loadFeed().catch(feedFailed);
  $("#left").textContent = "";
  $("#acts").hidden = true;
}
/* «Исключить источник»: whole channels / sites left out of the feed (kept on the server, so the counters follow) */
const RVC = /^RVC \|/;
function renderExclude(d) {
  const gone = d.excluded || [];
  $("#ex-list").innerHTML = gone.map((n) => `<button class="chip on-ex" data-exoff="${esc(n)}" aria-pressed="true">✕ ${esc(n.length > 28 ? n.slice(0, 27) + "…" : n)}</button>`).join("");
  $("#ex-pick").innerHTML = '<option value="">Исключить источник…</option>' + (d.origins || []).filter((o) => !gone.includes(o.name))
    .map((o) => `<option value="${esc(o.name)}">${esc(o.name.length > 34 ? o.name.slice(0, 33) + "…" : o.name)} · ${o.n}</option>`).join("");
  const rvc = gone.find((n) => RVC.test(n)) || ((d.origins || []).find((o) => RVC.test(o.name)) || {}).name;
  $("#ex-rvc").hidden = !rvc;
  $("#ex-rvc").dataset.name = rvc || "";
  $("#ex-rvc").setAttribute("aria-pressed", gone.some((n) => RVC.test(n)));
  $$("[data-exoff]").forEach((b) => (b.onclick = () => excludeSource(b.dataset.exoff, false)));
  st.excluded = gone.length;
  renderFilterSum();
}
/* the folded «Фильтры» block says how many filters are on, so a narrowed feed is never a surprise */
function renderFilterSum() {
  const n = (st.filter === "all" ? 0 : st.filter.split(",").length) + (st.excluded || 0)
    + (st.sort !== "date" ? 1 : 0) + (st.grade ? 1 : 0) + (st.age ? 1 : 0);
  $("#sum-filters").textContent = n ? "вкл. " + n : "нет";
}
async function excludeSource(origin, on) {
  try {
    haptic();
    await api("/api/feed/exclude", { origin, on });
    await Promise.all([loadFeed(), loadMe()]);
  } catch (e) { fail(e); }
}
async function moreFeed() {
  if (st.loading || st.feed.length >= st.total) return;
  st.loading = true;
  try {
    const d = await api(feedUrl(st.feed.length));
    const have = new Set(st.feed.map((i) => i.key));
    st.feed = st.feed.concat(d.items.filter((i) => !have.has(i.key)));
    st.total = d.total;
    renderStack();
  } catch (e) { /* the next scroll tries again */ }
  st.loading = false;
}
const keep = () => true;  // the server filters now (chips go with each page request)
function renderScoreNew() {
  const b = $("#score-new");
  b.hidden = !(st.me && st.me.ai) || !st.unscored;
  if (!st.scoringAll) b.textContent = `✨ Оценить ИИ (${st.unscored})`;
}
function renderStack() {
  const items = st.feed.filter(keep), box = $("#stack");
  const list = st.view === "list";
  $("#view-toggle").innerHTML = list ? '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><rect x="6" y="4" width="12" height="16" rx="2.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M3 7v10M21 7v10" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>' : '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M5 7h14M5 12h14M5 17h14" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';
  $("#view-toggle").setAttribute("aria-label", list ? "Показать карточками" : "Показать списком");
  box.hidden = list;
  $("#feed-list").hidden = !list;
  if (list) {
    $("#acts").hidden = true;
    $("#left").textContent = items.length ? `Показано ${items.length} из ${st.total} · тап — подробно` + (st.tzHidden ? ` · не подходят по поясу или стране: ${st.tzHidden}` : "") : "";
    renderList(items);
    if (items.length) return;
    box.hidden = false;
  }
  if (!items.length) {
    const filtered = st.feed.length && st.filter !== "all";
    box.innerHTML = `<div class="empty"><b>${filtered ? "Тут пусто" : "Всё разобрано"}</b>${filtered
      ? "С этим фильтром новых нет."
      : "Новые вакансии появятся после следующей проверки."}${st.tzHidden ? `<br>Не подходят по поясу или стране: ${st.tzHidden}.` : ""}<br><button class="link" id="empty-act">${filtered ? "Показать все" : "Проверить сейчас"}</button></div>`;
    $("#empty-act").onclick = filtered ? () => setFilter("all") : refresh;
    $("#left").textContent = "";
    $("#acts").hidden = true;
    return;
  }
  $("#acts").hidden = false;
  if (items.length < 8) moreFeed();
  $("#left").textContent = `Ещё ${Math.max(items.length, st.total)} · → ★ · ← скрыть · ↑ ИИ · ↓ отклик` + (st.tzHidden ? ` · не по поясу или стране: ${st.tzHidden}` : "");
  box.innerHTML = items.slice(0, 3).reverse().map((i, n, arr) => {
    const depth = arr.length - 1 - n;
    return `<article class="card" data-key="${esc(i.key)}" style="transform:translateY(${depth * 10}px) scale(${1 - depth * 0.04});opacity:${1 - depth * 0.25};z-index:${10 - depth}">
      <span class="verdict yes">В ИЗБРАННОЕ</span><span class="verdict no">СКРЫТЬ</span><span class="verdict up">✨ ОЦЕНКА ИИ</span><span class="verdict down">✉️ ОТКЛИК</span>
      <div class="src"><span>${esc(originOf(i))}</span><span>${esc(when(i.date))}${i.fav ? " · ★" : ""}${i.seen ? " · 👁" : ""}</span></div>
      <h3>${esc(i.title)}</h3>${i.company ? `<div class="co">${esc(i.company)}</div>` : ""}
      <div class="meta">${metaHtml(i)}</div><div class="tags">${tagsHtml(i.tags)}</div>${aiLine(i)}
      ${i.also ? `<div class="also">${esc(alsoText(i))}</div>` : ""}
      ${summaryHtml(i)}<p>${esc(i.text || (i.summary && i.summary.length ? "" : "Полное описание на сайте."))}</p><button type="button" class="more">Подробнее →</button></article>`;
  }).join("");
  const top = box.querySelector(".card:last-child");
  if (top) drag(top);
}
const byKey = (key) => st.feed.find((i) => i.key === key);
const summaryHtml = (i) => (i.summary && i.summary.length ? `<div class="sum">${i.summary.map((l) => `<span>${esc(l)}</span>`).join("")}</div>` : "");

/* the whole feed as a scrolling list (the other view of the same vacancies) */
function renderList(items) {
  const box = $("#feed-list");
  box.innerHTML = items.map((i, n) => {
    const facts = [i.company, originOf(i), when(i.date)].filter(Boolean).join(" · ");
    const extra = [i.salary && "💰 " + i.salary + salaryUsd(i), i.tz && "🕒 " + i.tz, i.also && "＋" + i.also.length].filter(Boolean).join(" · ");
    return `<div class="lrow" data-n="${n}"><button class="lmain" data-open="${n}"><b>${esc(i.title)}${i.fav ? " ★" : ""}</b>
      <span class="s">${esc(facts)}</span>${extra ? `<span class="s x">${esc(extra)}</span>` : ""}${i.summary && i.summary[0] ? `<span class="s x">${esc(i.summary[0])}</span>` : ""}
      ${i.ai ? `<span class="sc">✨ ${i.ai.score}</span>` : ""}</button>
      <span class="lacts"><button class="no" data-no="${n}" aria-label="Скрыть">✕</button><button class="yes" data-yes="${n}" aria-label="В избранное">★</button></span></div>`;
  }).join("") + (st.feed.length < st.total ? '<div class="more-load" id="more-load">Загружаю ещё…</div>' : "");
  const sentinel = $("#more-load");
  if (sentinel && "IntersectionObserver" in window) {
    new IntersectionObserver((es, ob) => { if (es.some((x) => x.isIntersecting)) { ob.disconnect(); moreFeed(); } }).observe(sentinel);
  }
  $$("[data-open]", box).forEach((b) => (b.onclick = () => openSheet(items[+b.dataset.open], false)));
  $$("[data-yes]", box).forEach((b) => (b.onclick = () => quick(items[+b.dataset.yes], true, b.closest(".lrow"))));
  $$("[data-no]", box).forEach((b) => (b.onclick = () => quick(items[+b.dataset.no], false, b.closest(".lrow"))));
}
async function quick(item, yes, row) {
  haptic(yes ? "ok" : "medium");
  if (row) row.classList.add(yes ? "gone-yes" : "gone-no");
  dropCard(item);
  setTimeout(renderStack, 180);
  try {
    const d = await api(`/api/jobs/${encodeURIComponent(item.key)}/${yes ? "take" : "hide"}`, {});
    toast(d.note);
    loadMe().catch(() => {});
  } catch (e) { fail(e); loadFeed().catch(() => {}); }
}
function topCard() { return $("#stack .card:last-child"); }
/* a decided card leaves the feed and the «Ещё N» count at once (the count used to lag behind, so the last
   few cards looked like they vanished: «Ещё 5» with 3 left) */
function dropCard(item) {
  const n = st.feed.length;
  st.feed = st.feed.filter((i) => i !== item);
  if (st.feed.length < n) st.total = Math.max(st.feed.length, (st.total || 0) - 1);
}

async function decide(card, yes) {
  const item = byKey(card.dataset.key);
  card.style.transform = `translateX(${yes ? 480 : -480}px) rotate(${yes ? 18 : -18}deg)`;
  card.style.opacity = 0;
  haptic(yes ? "ok" : "medium");
  dropCard(item);
  setTimeout(() => { renderStack(); const next = topCard(); if (next) next.classList.add("rise"); }, 260);
  try {
    const d = await api(`/api/jobs/${encodeURIComponent(item.key)}/${yes ? "take" : "hide"}`, {});
    toast(d.note);
    loadMe().catch(() => {});
  } catch (e) { fail(e); loadFeed().catch(() => {}); }
}
function drag(card) {
  let x0 = null, y0 = 0, dx = 0, dy = 0, down = null, axis = "";
  const more = $(".more", card);  // «Подробнее» is a plain button: a finger that moves a little still opens the vacancy
  if (more) more.onclick = (e) => { e.stopPropagation(); openSheet(byKey(card.dataset.key), false); };
  card.addEventListener("pointerdown", (e) => { if (e.target.closest(".more")) return; x0 = e.clientX; y0 = e.clientY; dx = dy = 0; axis = ""; down = e.target; card.classList.add("drag"); card.setPointerCapture(e.pointerId); });
  card.addEventListener("pointermove", (e) => {
    if (x0 === null) return;
    dx = e.clientX - x0; dy = e.clientY - y0;
    if (!axis && Math.max(Math.abs(dx), Math.abs(dy)) > 10) axis = Math.abs(dy) > Math.abs(dx) * 1.2 ? "v" : "h";  // the first moves decide: sideways or up / down
    if (axis === "v") {
      card.style.transform = `translateY(${dy / 3}px)`;
      $(".verdict.up", card).style.opacity = Math.max(0, -dy / 90);
      $(".verdict.down", card).style.opacity = Math.max(0, dy / 90);
      return;
    }
    card.style.transform = `translateX(${dx}px) rotate(${dx / 18}deg)`;
    $(".verdict.yes", card).style.opacity = Math.max(0, dx / 90);
    $(".verdict.no", card).style.opacity = Math.max(0, -dx / 90);
  });
  const end = (e) => {
    if (x0 === null) return;
    const tap = Math.abs(dx) < 12 && Math.abs((e.clientY || y0) - y0) < 12;  // a finger tap wobbles a few pixels
    x0 = null;
    card.classList.remove("drag");
    if (axis === "h" && Math.abs(dx) > 90) return decide(card, dx > 0);
    const vertical = axis === "v" && Math.abs(dy) > 90 && e.type === "pointerup" ? (dy < 0 ? "up" : "down") : "";
    card.style.transform = "";
    $$(".verdict", card).forEach((v) => (v.style.opacity = 0));
    if (vertical) {  // the card stays: «up» asks the AI, «down» opens the response package (nothing is sent)
      haptic();
      const item = byKey(card.dataset.key);
      if (vertical === "up") return st.me && st.me.ai ? scoreJob(item) : toast("ИИ для вас пока не включён");
      return st.me && st.me.owner ? applyFlow(item) : toast("Отклики пока только у владельца");
    }
    if (tap && e.type === "pointerup") {
      if (down && down.closest && down.closest("[data-score]")) scoreJob(byKey(card.dataset.key));
      else openSheet(byKey(card.dataset.key), false);
    }
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}
/* st.filter is a comma list: channel / site / hh.ru chips add up, #ru_company and part-time narrow, «Без hh.ru» stands alone */
function setFilter(f) {
  let set = new Set(st.filter === "all" ? [] : st.filter.split(","));
  if (f === "all") set = new Set();
  else if (f === "nohh") { const had = set.has("nohh"); set = new Set(had ? [] : ["nohh"]); }
  else {
    set.delete("nohh");
    if (set.has(f)) set.delete(f); else set.add(f);
  }
  st.filter = set.size ? [...set].join(",") : "all";
  $$("#filters .chip").forEach((c) => c.setAttribute("aria-pressed", c.dataset.f === "all" ? st.filter === "all" : set.has(c.dataset.f)));
  renderFilterSum();
  loadFeed().catch(fail);
}
/* «Избранное → Оценить все ИИ»: first tap says how many will be scored (the day's limit counts), the second runs it */
async function scoreFav() {
  const b = $("#score-fav");
  if (st.scoringAll) return;
  try {
    if (!b.dataset.sure) {
      const q = await api("/api/score/new", { view: "fav", dry: true });
      b.dataset.sure = "1";
      b.textContent = q.will < q.unscored ? `Оценю ${q.will} из ${q.unscored} (лимит на сегодня). Нажмите ещё раз` : `Оценю ${q.will}. Нажмите ещё раз`;
      setTimeout(() => { if (b.isConnected) { delete b.dataset.sure; b.textContent = `✨ Оценить все ИИ (${q.unscored})`; } }, 6000);
      return;
    }
    st.scoringAll = true;
    const d = await api("/api/score/new", { view: "fav" });
    for (let n = 0; n < 400; n++) {
      b.textContent = `✨ Оцениваю…`;
      await new Promise((r) => setTimeout(r, 3000));
      const j = await api("/api/resume/ai/" + d.job);
      if (j.progress) b.textContent = `✨ Оцениваю ${j.progress.done} из ${j.progress.total}…`;
      if (j.status === "error") throw new Error(j.error);
      if (j.status === "done") { haptic("ok"); toast(`Оценил ${j.result.scored}` + (j.result.failed ? `, не вышло: ${j.result.failed}` : "")); break; }
    }
  } catch (e) { fail(e); }
  st.scoringAll = false;
  loadSaved().catch(() => {});
}
async function scoreNew() {
  if (st.scoringAll) return;
  const b = $("#score-new");
  st.scoringAll = true;
  try {
    const d = await api("/api/score/new", {});
    b.textContent = `✨ Оцениваю 0 из ${d.total}…`;
    for (let n = 0; n < 400; n++) {
      await new Promise((r) => setTimeout(r, 3000));
      const j = await api("/api/resume/ai/" + d.job);
      if (j.progress) b.textContent = `✨ Оцениваю ${j.progress.done} из ${j.progress.total}…`;
      if (j.status === "error") throw new Error(j.error);
      if (j.status === "done") { haptic("ok"); toast(`Оценил ${j.result.scored}` + (j.result.failed ? `, не вышло: ${j.result.failed}` : "")); break; }
    }
  } catch (e) { fail(e); }
  st.scoringAll = false;
  await loadFeed().catch(() => {});
}
async function refresh() {
  const b = $("#refresh");
  if (b.classList.contains("spin")) return;
  b.classList.add("spin");
  haptic();
  const slow = setTimeout(() => toast("Проверяю каналы — это до пары минут"), 3000);
  try {
    const d = await api("/api/refresh", {});
    clearTimeout(slow);
    toast(d.busy ? "Проверка уже идёт, загляни через минуту" : d.waited ? "Только что проверил все источники — лента свежая"
      : (d.new ? `В каналах ${d.new} ${plural(d.new, "новая", "новые", "новых")}` : "В каналах новых нет")
        + (d.more ? (d.full ? " · настройки менялись: проверяю всё заново, пара минут" : " · сайты и hh.ru догружаю")
          : d.ages && d.ages.sites != null ? ` · сайты свежие, обновлены ${d.ages.sites} мин назад` : ""));
    const before = st.me ? st.me.stats.new : 0;
    await Promise.all([loadMe(), loadFeed()]);
    if (d.more) followRefresh(before);
  } catch (e) { clearTimeout(slow); fail(e); }
  b.classList.remove("spin");
}
/* hh.ru and the sites go on in the background (minutes): look every 15 s until the bot says it's done, show what came */
async function followRefresh(before) {
  const t0 = Date.now();
  while (Date.now() - t0 < 6 * 60000) {
    await new Promise((r) => setTimeout(r, 15000));
    try { await loadMe(); } catch (e) { return; }
    if (!st.me.refreshing) break;
  }
  await loadFeed().catch(() => {});
  const got = st.me.stats.new - before;
  toast(got > 0 ? `Сайты и hh.ru проверены: ${got} ${plural(got, "новая вакансия", "новые вакансии", "новых вакансий")}` : "Сайты и hh.ru проверены: новых нет");
}

/* ---------------------------------------------------------------- detail sheet */

async function openSheet(item, viaSearch) {
  if (!item) return;
  st.sheet = { item, viaSearch };
  if (!viaSearch && item.status === "new" && !item.seen) markSeen(item.key);
  renderSheet();
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
  if (!viaSearch || item.status !== "found") {
    try {
      const full = await api(`/api/jobs/${encodeURIComponent(item.key)}`);
      if (st.sheet && st.sheet.item.key === item.key) { st.sheet.item = { ...item, text: full.text }; renderSheet(); }
    } catch (e) { /* keep the short text */ }
  }
}
function renderSheet() {
  const { item: i } = st.sheet;
  const fresh = i.status === "new" || i.status === "found";
  const status = i.archived ? "🗄 В архиве" : { hidden: "✕ Скрыта" }[i.status] || (i.fav ? "★ В избранном" : "");
  $("#sheet").innerHTML = `<div class="grip"></div>
    <div class="src">${esc(originOf(i))} · ${esc(when(i.date))}</div>
    <h3>${esc(i.title)}</h3>${i.company ? `<div class="co">${esc(i.company)}</div>` : ""}
    <div class="meta">${metaHtml(i)}</div>${flBox(i)}<div class="tags" style="margin-top:8px">${tagsHtml(i.tags)}</div>
    ${i.summary && i.summary.length ? `<div class="sum" style="margin-top:8px">${i.summary.map((l) => `<span>${esc(l)}</span>`).join("")}</div>` : ""}
    ${status ? `<div class="status">${status}</div>` : ""}
    ${aiBox(i)}
    ${i.also ? `<div class="also">Эта же вакансия ещё в: ${i.also.map((a, n) => a.link ? `<button class="link" data-also="${n}">${esc(a.origin + (a.location ? " · " + a.location : ""))}</button>` : esc(a.origin)).join(", ")}</div>` : ""}
    <div class="track"><div class="lbl">📋 Мой отклик${i.silent >= 7 ? ` · <span class="warn">⏰ тишина ${i.silent} дн.</span>` : ""}</div><div class="chiprow">${[["", "—"], ...Object.entries(STAGES)].map(([k, v]) =>
      `<button class="stage" data-stage="${k}" aria-pressed="${((i.track && i.track.stage) || "") === k}">${v}</button>`).join("")}</div></div>
    <div class="body">${esc(i.text || "Полное описание на сайте: кнопка ниже.")}</div>
    <div class="btns">
      ${fresh ? `<button class="yes" data-a="take">★ В избранное</button><button data-a="hide">✕ Скрыть</button>` : ""}
      ${i.status === "hidden" ? `<button data-a="restore" class="wide">↩ Вернуть в новые</button>` : ""}
      ${i.seen && i.status === "new" && !i.archived && st.saved === "seen" && !$("#s-saved").hidden ? `<button data-a="unsee" class="wide">↩ Вернуть в ленту</button>` : ""}
      ${fresh ? "" : `<button data-a="fav" class="wide">${i.fav ? "★ Убрать из избранного" : "☆ В избранное"}</button>`}
      ${st.me && st.me.owner && !i.fl ? '<button data-a="apply" class="yes wide">✉️ Откликнуться</button>' : ""}
      ${i.link ? `<button data-a="open" class="wide">${i.source === "tg" ? "🔗 Пост" : "🔗 Вакансия"}</button>` : ""}
      ${viaSearchOrFresh(i) ? "" : `<button data-a="${i.archived ? "unarchive" : "archive"}" class="wide">${i.archived ? "↩ Вернуть из архива" : "🗄 В архив"}</button>`}
    </div>`;
  $$("#sheet [data-a]").forEach((b) => (b.onclick = () => sheetAction(b.dataset.a)));
  $$("#sheet [data-stage]").forEach((b) => (b.onclick = () => setStage(st.sheet.item, b.dataset.stage)));
  $$("#sheet [data-also]").forEach((b) => (b.onclick = () => openLink(i.also[+b.dataset.also].link)));
  wireFl(i);
}
const viaSearchOrFresh = (i) => (st.sheet && st.sheet.viaSearch) || i.status === "new" || i.status === "found";
function aiBox(i) {
  if (i.ai) {
    return `<div class="aibox"><b>✨ ${i.ai.score}/5</b> ${esc(i.ai.verdict)}${i.ai.thin ? " <small>(оценка по названию: полный текст не подтянулся)</small>" : ""}
      <ul>${(i.ai.pros || []).map((x) => `<li class="pro">${esc(x)}</li>`).join("")}${(i.ai.cons || []).map((x) => `<li class="con">${esc(x)}</li>`).join("")}</ul>
      <button class="link" data-a="score">Оценить заново</button></div>`;
  }
  if (st.scoring.has(i.key)) return '<div class="aibox busy">✨ ИИ сравнивает вакансию с вашим резюме…</div>';
  return st.me && st.me.ai ? '<button class="ai-btn" data-a="score">✨ Оценить ИИ: подходит ли мне</button>' : "";
}

/* ---------------------------------------------------------------- ✨ AI score of a vacancy */

async function waitJob(job) {
  for (let n = 0; n < 160; n++) {
    await new Promise((r) => setTimeout(r, 2500));
    const j = await api("/api/resume/ai/" + job);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error);
  }
  throw new Error("ИИ не ответил. Попробуйте позже.");
}
function patchItem(key, fields) {
  const fix = (i) => (i.key === key ? { ...i, ...fields } : i);
  st.feed = st.feed.map(fix);
  st.results = st.results.map(fix);
  if (st.sheet && st.sheet.item && st.sheet.item.key === key) { st.sheet.item = { ...st.sheet.item, ...fields }; renderSheet(); }
  renderStack();
}
async function scoreJob(item) {
  if (!item || st.scoring.has(item.key)) return;
  st.scoring.add(item.key);
  patchItem(item.key, {});
  haptic();
  try {
    const d = await api("/api/score", { key: item.key });
    if (d.queued) toast("ИИ занят другой задачей: поставил в очередь");
    const ai = await waitJob(d.job);
    st.scoring.delete(item.key);
    patchItem(item.key, { ai });
    haptic("ok");
  } catch (e) { st.scoring.delete(item.key); patchItem(item.key, {}); fail(e); }
}

/* ---------------------------------------------------------------- ✉️ «Откликнуться» (owner only) */

async function applyFlow(item) {
  st.sheet = { apply: item };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>✉️ Отклик: ${esc(item.title)}</h3>
    <div class="empty"><div class="spinner"></div><b>Собираю письмо</b>По шаблону из вашего резюме, на языке вакансии. Без ИИ.</div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
  try {
    const d = await api("/api/apply/prepare", { key: item.key });
    const r = d.result || (await waitJob(d.job));
    if (!st.sheet || st.sheet.apply !== item) return;
    renderApply(item, r);
  } catch (e) { closeSheet(); fail(e); }
}
function renderApply(item, r) {
  const ch = r.channels;
  const mails = ch.email.map((m) => `<button class="${r.smtp || r.dry ? "yes" : ""}" data-mail="${esc(m)}">📧 ${r.smtp || r.dry ? "Отправить на" : "Письмо на"} ${esc(m)}</button>`).join("");
  const tgs = ch.tg.map((t) => `<button data-tg="${esc(t)}">💬 Открыть чат @${esc(t)}</button>`).join("");
  const dataBlocks = (r.blocks || []).map((b, n) => [b, n]).filter(([b]) => b.key !== "about");
  $("#sheet").innerHTML = `<div class="grip"></div><h3>✉️ Отклик: ${esc(item.title)}</h3>
    <div class="ap-btns top">${ch.link ? '<button class="yes" data-site="1">🔗 Открыть вакансию</button>' : ""}${mails}${tgs}</div>
    ${r.tones ? `<div class="seg two" id="ap-lang" role="group" aria-label="Язык отклика"><button data-al="ru" aria-pressed="${r.lang === "ru"}">RUS</button><button data-al="en" aria-pressed="${r.lang === "en"}">ENG</button></div>` : ""}
    ${r.warning ? `<p class="hint">⚠️ ${esc(r.warning)}</p>` : ""}
    ${dataBlocks.length ? `<div class="lbl" style="margin-top:12px">Данные для формы на сайте (${r.lang === "ru" ? "RUS" : "ENG"})</div>
      ${dataBlocks.map(([b, n]) => `<div class="pk-row"><div class="pk-t"><small>${esc(b.label)}</small><span>${esc(b.text)}</span></div><button data-pk-copy="${n}" aria-label="Скопировать">📋</button></div>`).join("")}
      <p class="hint">Заполняются в «Ещё → Профиль для откликов».</p>` : (r.tones ? '<p class="hint">Ваши данные (ФИО, город, зарплата…) можно заполнить в «Ещё → Профиль для откликов»: они появятся здесь готовыми блоками.</p>' : "")}
    ${ch.email.length ? `<label class="fld"><span>Тема письма</span><input class="inp" id="ap-subj" value="${esc(r.subject)}" maxlength="200"></label>` : ""}
    <label class="fld"><span>Письмо</span><textarea class="inp big" id="ap-text">${esc(r.letter)}</textarea></label>
    <div class="ap-btns">
      <button data-copy="1">📋 Скопировать письмо</button>
      ${r.tones ? '<button data-pdf="1">📎 Резюме (PDF) в чат с ботом</button>' : ""}</div>
    ${!r.smtp && !r.dry && ch.email.length ? '<p class="hint">Чтобы я отправлял письма сам, нужен «пароль приложения» Gmail в config.yml — спросите меня в чате, покажу по шагам.</p>' : ""}
    ${r.tones ? `<details class="group fold" id="ap-more"><summary><span class="sn">⚙️ О себе, тон и ИИ</span></summary><div class="pad" style="padding:2px 14px 14px">
      <div class="tzrow" style="padding:0 0 8px"><label>Тон письма<select class="inp" id="ap-tone">${r.tones.map((t) => `<option value="${esc(t.id)}" ${t.id === r.tone ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select></label>
        <label>О себе<select class="inp" id="ap-preset"><option value="">Из резюме</option>${r.presets.filter((p) => p.id !== "resume").map((p) => `<option value="${esc(p.id)}" ${p.id === r.preset ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label></div>
      <label class="fld"><span>Абзац «о себе» (можно написать свой)</span><textarea class="inp" id="ap-about" rows="3" maxlength="700">${esc((r.presets.find((p) => p.id === (r.preset || "resume")) || {}).text || "")}</textarea></label>
      <div class="ap-btns"><button id="ap-rebuild">🔄 Пересобрать письмо</button><button id="ap-savepreset">💾 Сохранить «о себе»</button>
        <button data-ai="1">✨ Переписать с ИИ${r.ai_left != null ? ` (осталось ${r.ai_left})` : ""}</button></div>
      <div class="rl-own" id="ap-nameRow" hidden><input class="inp" id="ap-pname" maxlength="40" placeholder="Название, например: Коротко про меня"><button id="ap-psave">OK</button></div>
    </div></details>` : ""}
    <p class="hint">Ничего не уйдёт, пока вы не нажмёте кнопку. Резюме: «${esc(r.resume.name)}»${r.resume.lang !== r.lang ? " (другой язык, чем вакансия)" : ""}.${r.dry ? " <b>Сухой режим: письма не отправляются.</b>" : ""} После «Открыть…» письмо и PDF резюме придут в чат с ботом, а вакансия отметится «Откликнулся».</p>
    <div class="btns"><button class="wide" id="ap-cancel">Отмена</button></div>`;
  const text = () => $("#ap-text").value;
  const rebuild = async (aboutText) => {
    try {
      const body = { key: item.key, tone: $("#ap-tone").value, preset: $("#ap-preset").value, lang: r.lang };
      if (aboutText != null) body.about = aboutText;
      const d = await api("/api/apply/compose", body);
      $("#ap-text").value = d.letter;
      haptic(); toast("Письмо пересобрано");
    } catch (e) { fail(e); }
  };
  $$("#ap-lang button").forEach((b) => (b.onclick = async () => {
    if (b.dataset.al === r.lang) return;
    try {
      const d = await api("/api/apply/compose", { key: item.key, tone: $("#ap-tone").value, preset: "", lang: b.dataset.al });
      haptic();
      renderApply(item, { ...r, lang: d.lang, letter: d.letter, subject: d.subject, blocks: d.blocks, presets: d.presets, preset: "" });
    } catch (e) { fail(e); }
  }));
  $$("[data-pk-copy]").forEach((b) => (b.onclick = async () => {
    try { await navigator.clipboard.writeText(r.blocks[+b.dataset.pkCopy].text); haptic(); toast("Скопировано"); } catch (e) { toast("Не получилось скопировать — выделите текст вручную"); }
  }));
  $$("[data-pdf]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Готовлю PDF…";
    try { await api("/api/resume/export", { vid: r.resume.vid, format: "pdf" }); haptic("ok"); toast("PDF резюме в чате с ботом"); } catch (e) { fail(e); }
    b.disabled = false; b.textContent = "📎 Резюме (PDF) в чат с ботом";
  }));
  if ($("#ap-rebuild")) {
    $("#ap-tone").onchange = () => rebuild($("#ap-about").value);
    $("#ap-preset").onchange = () => {
      const p = r.presets.find((x) => x.id === $("#ap-preset").value) || r.presets.find((x) => x.id === "resume");
      $("#ap-about").value = p ? p.text : "";
      rebuild($("#ap-about").value);
    };
    $("#ap-rebuild").onclick = () => rebuild($("#ap-about").value);
    $("#ap-savepreset").onclick = () => { $("#ap-nameRow").hidden = !$("#ap-nameRow").hidden; $("#ap-pname").focus(); };
    $("#ap-psave").onclick = async () => {
      const name = $("#ap-pname").value.trim();
      if (!name) return toast("Напишите название");
      try { await api("/api/apply/presets", { name, text: $("#ap-about").value }); haptic("ok"); toast("Сохранил. Он будет в списке «О себе»"); applyFlow(item); } catch (e) { fail(e); }
    };
  }
  $$("[data-ai]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Пишу с ИИ…";
    try {
      const d = await api("/api/apply/prepare", { key: item.key, mode: "ai" });
      const ai = await waitJob(d.job);
      $("#ap-text").value = ai.letter;
      if (ai.warning) toast("⚠️ " + ai.warning);
      b.disabled = false; b.textContent = "✨ Переписать с ИИ";
    } catch (e) { b.disabled = false; b.textContent = "✨ Переписать с ИИ"; fail(e); }
  }));
  const done = async (how) => {
    try {
      const d = await api("/api/apply/done", { key: item.key, how, text: text(), vid: r.resume.vid });
      haptic("ok"); toast("Отмечено: Откликнулся · письмо и резюме в чате с ботом");
      afterApply(item, d.item);
    } catch (e) { fail(e); }
  };
  $("#ap-cancel").onclick = closeSheet;
  $$("[data-mail]").forEach((b) => (b.onclick = async () => {
    const to = b.dataset.mail;
    if (!(r.smtp || r.dry)) {  // no mailbox connected: a ready draft in the person's mail app
      openLink(`mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent($("#ap-subj").value)}&body=${encodeURIComponent(text())}`);
      return done("письмо " + to);
    }
    if (!b.dataset.sure) { b.dataset.sure = "1"; b.textContent = `Точно отправить на ${to}? Нажмите ещё раз`; return; }
    b.disabled = true; b.textContent = "Отправляю…";
    try {
      const d = await api("/api/apply/send", { key: item.key, to, subject: $("#ap-subj").value, text: text(), vid: r.resume.vid });
      haptic("ok"); toast(d.result === "dry" ? "Сухой режим: письмо не ушло, но всё готово" : `Отправлено на ${to} · отмечено «Откликнулся»`);
      afterApply(item, d.item);
    } catch (e) { b.disabled = false; delete b.dataset.sure; b.textContent = `📧 Отправить на ${to}`; fail(e); }
  }));
  $$("[data-tg]").forEach((b) => (b.onclick = () => { navigator.clipboard && navigator.clipboard.writeText(text()).catch(() => {}); openLink("https://t.me/" + b.dataset.tg); done("Telegram @" + b.dataset.tg); }));
  $$("[data-site]").forEach((b) => (b.onclick = () => { navigator.clipboard && navigator.clipboard.writeText(text()).catch(() => {}); openLink(ch.link); done("на сайте"); }));
  $$("[data-copy]").forEach((b) => (b.onclick = async () => {
    try { await navigator.clipboard.writeText(text()); toast("Письмо скопировано"); } catch (e) { toast("Не получилось скопировать — выделите текст вручную"); }
  }));
}
function afterApply(item, fresh) {
  closeSheet();
  if (fresh) st.feed = st.feed.filter((i) => i.key !== item.key);
  renderStack();
  loadMe().catch(() => {});
  loadFeed().catch(() => {});
  if (!$("#s-more").hidden) loadTrack();
}

/* ---------------------------------------------------------------- 📋 my responses (tracker) */

async function setStage(item, stage) {
  try {
    const d = await api("/api/track", { key: item.key, stage });
    haptic("ok");
    toast(stage ? "Этап: " + STAGES[stage] : "Убрал из откликов");
    st.feed = st.feed.filter((i) => i.key !== item.key || d.item.status === "new");
    if (st.sheet && st.sheet.item) { st.sheet.item = { ...st.sheet.item, ...d.item, text: st.sheet.item.text, also: st.sheet.item.also }; renderSheet(); }
    renderStack();
    if (!$("#s-more").hidden) loadTrack();
    loadMe().catch(() => {});
  } catch (e) { fail(e); }
}
let trackAll = false;
const TRACK_SHOWN = 10;
async function loadTrack() {
  const box = $("#track-list");
  try {
    const d = await api("/api/track");
    const order = Object.keys(STAGES);
    const items = d.items.sort((a, b) => order.indexOf(a.track.stage) - order.indexOf(b.track.stage));
    const row = (i, n, list) => `<button class="row" data-list="${list}" data-n="${n}"><b>${esc(i.title)}</b>
      <span class="m${i.silent >= 7 ? " warn" : ""}">${i.track ? esc(STAGES[i.track.stage]) + (i.silent >= 7 ? " · ⏰ " + i.silent + " дн." : "") : "отметить"}</span>
      <span class="s">${esc([i.company, originOf(i)].filter(Boolean).join(" · "))}</span></button>`;
    const counts = order.map((k) => [k, items.filter((i) => i.track.stage === k).length]).filter(([, n]) => n);
    const cut = (list) => (trackAll ? list : list.slice(0, TRACK_SHOWN));
    $("#sum-track").textContent = items.length + d.taken.length ? String(items.length) : "пусто";
    box.innerHTML = (counts.length ? `<div class="hint">${counts.map(([k, n]) => `${STAGES[k]}: <b>${n}</b>`).join(" · ")}</div>` : "")
      + (items.length ? cut(items).map((i, n) => row(i, n, "t")).join("")
        : '<div class="hint">Пока пусто. Откройте вакансию и отметьте этап внизу карточки: «Откликнулся», «Тестовое»… Если неделю нет новостей, бот напомнит.</div>')
      + (d.taken.length ? `<div class="lbl">Избранное, без отметки</div>${cut(d.taken).map((i, n) => row(i, n, "k")).join("")}` : "")
      + (!trackAll && (items.length > TRACK_SHOWN || d.taken.length > TRACK_SHOWN) ? `<button class="link block" id="track-all">Показать все (${items.length + d.taken.length})</button>` : "");
    const all = $("#track-all");
    if (all) all.onclick = () => { trackAll = true; loadTrack(); };
    $$(".row", box).forEach((r) => (r.onclick = () => openSheet((r.dataset.list === "t" ? items : d.taken)[+r.dataset.n], false)));
  } catch (e) { box.innerHTML = ""; fail(e); }
}
function addTrackSheet() {
  st.sheet = { form: true };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>＋ Отклик вручную</h3>
    <p class="hint" style="font-size:14px">Если откликнулись не из ленты: на сайте, по почте, через знакомых.</p>
    <label class="fld"><span>Должность *</span><input class="inp" id="ta-title" maxlength="120" placeholder="Data Analyst"></label>
    <label class="fld"><span>Компания</span><input class="inp" id="ta-company" maxlength="80"></label>
    <label class="fld"><span>Ссылка на вакансию</span><input class="inp" id="ta-link" maxlength="500" placeholder="https://…"></label>
    <label class="fld"><span>Этап</span><select class="inp" id="ta-stage">${Object.entries(STAGES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
    <div class="btns"><button class="yes" id="ta-save">Добавить</button><button id="ta-cancel">Отмена</button></div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  $("#ta-cancel").onclick = closeSheet;
  $("#ta-save").onclick = async () => {
    try {
      await api("/api/track/add", { title: $("#ta-title").value, company: $("#ta-company").value, link: $("#ta-link").value, stage: $("#ta-stage").value });
      haptic("ok"); toast("Добавил в отклики"); closeSheet(); loadTrack();
    } catch (e) { fail(e); }
  };
  try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
}
async function sheetAction(action) {
  const { item, viaSearch } = st.sheet;
  if (action === "open") return openLink(item.link);
  if (action === "score") return scoreJob(item);
  if (action === "apply") return applyFlow(item);
  try {
    const path = viaSearch ? `/api/search/${encodeURIComponent(item.key)}/${action}` : `/api/jobs/${encodeURIComponent(item.key)}/${action}`;
    const d = await api(path, {});
    haptic(action === "take" ? "ok" : "light");
    toast(d.note);
    st.sheet.item = { ...item, ...d.item, text: item.text, match: item.match };
    const was = st.feed.length;
    st.feed = st.feed.map((i) => (i.key === item.key ? { ...i, ...d.item, text: i.text } : i)).filter((i) => i.status === "new");
    if (st.feed.length < was) st.total = Math.max(st.feed.length, (st.total || 0) - (was - st.feed.length));
    st.results = st.results.map((i) => (i.key === item.key ? { ...i, status: d.item.status, fav: d.item.fav } : i));
    if (action === "unsee") { closeSheet(); loadSaved(); loadMe().catch(() => {}); loadFeed().catch(() => {}); return; }
    if (action === "archive" || action === "unarchive") { closeSheet(); loadSaved(); loadArchive().catch(() => {}); loadMe().catch(() => {}); return; }
    renderSheet();
    renderStack();
    renderResults();
    if (!$("#s-saved").hidden) loadSaved();
    if (gigs.d && gigs.d.items.some((i) => i.key === item.key)) loadGigs();
    loadMe().catch(() => {});
  } catch (e) { fail(e); }
}
new MutationObserver(() => { $("#sheet").classList.remove("closing"); $("#sheet-bg").classList.remove("closing"); })
  .observe(document.getElementById("sheet"), { childList: true });  // a new sheet while the old one slides away: show it at once
function closeSheet() {
  st.sheet = null;
  const sheet = $("#sheet"), bg = $("#sheet-bg");
  if (!sheet.hidden && !matchMedia("(prefers-reduced-motion: reduce)").matches) {  // slide away, then hide
    const was = sheet.firstChild;  // another sheet opened meanwhile replaces the content: then it stays
    sheet.classList.add("closing"); bg.classList.add("closing");
    setTimeout(() => {
      if (!sheet.classList.contains("closing")) return;
      sheet.classList.remove("closing"); bg.classList.remove("closing");
      if (sheet.firstChild === was) sheet.hidden = bg.hidden = true;
    }, 200);
  } else {
    sheet.hidden = bg.hidden = true;
  }
  try { tg.BackButton.hide(); } catch (e) { /* not in Telegram */ }
}

/* ---------------------------------------------------------------- search */

function renderMode() {
  $("#m-exact").setAttribute("aria-pressed", st.mode === "exact");
  $("#m-similar").setAttribute("aria-pressed", st.mode === "similar");
  $("#mode-hint").textContent = st.mode === "exact"
    ? "Ищу фразу ровно как написано: в названиях и текстах вакансий. Регистр, дефис и окончания не важны."
    : "Ищу саму роль, её синонимы и смежные профессии по графу ролей. Ближние совпадения выше.";
}
async function runSearch() {
  const q = $("#q").value.trim();
  if (!q) return;
  $("#q").blur();
  $("#res-head").textContent = "Ищу в ленте, сохранённых каналах и на сайтах…";
  $("#results").innerHTML = '<div class="skeleton"></div>'.repeat(4);
  const seq = (st.searchSeq = (st.searchSeq || 0) + 1);
  const base = `/api/search?q=${encodeURIComponent(q)}&mode=${st.mode}&off=${encodeURIComponent([...st.off].join(","))}`;
  /* in portions: first what is at hand (feed, saved channel posts, site feeds), then the slower sources one by one;
     every later portion only adds what the earlier ones did not find */
  const LATER = [["chan", "свежие посты каналов"], ["hh", "hh.ru"], ["hima", "Himalayas"]];
  const src = {}, names = [["inbox", "лента"], ["channels", "каналы"], ["sites", "сайты"], ["hh", "hh.ru"]];
  let noRole = false, waitingFor = "";
  const head = (waiting) => {
    waitingFor = waiting || "";
    const parts = names.filter(([k]) => src[k]).map(([k, n]) => `${n} ${src[k]}`);
    $("#res-head").innerHTML = (noRole ? "Этой роли пока нет в графе: показываю точные совпадения. Граф можно дополнить в rolegraph.yml.<br>" : "")
      + `Найдено: <b>${Math.min(st.reveal, st.results.length)}</b>` + (parts.length ? ` · просмотрел: ${esc(parts.join(", "))}` : "")
      + (waiting ? ` · ещё ищу: ${esc(waiting)}…` : "");
  };
  /* the counter climbs one vacancy at a time, smoothly (a whole portion takes about a second), and the rows appear with it */
  st.reveal = 0;
  st.results = [];
  clearInterval(st.revealT);
  st.revealT = setInterval(() => {
    if (seq !== st.searchSeq) return clearInterval(st.revealT);
    const left = st.results.length - st.reveal;
    if (left <= 0) return;
    st.reveal += Math.max(1, Math.ceil(left / 25));  // 25 ticks of ~60 ms at most per portion
    head(waitingFor);
    renderResults();
  }, 60);
  try {
    const d = await api(base + "&part=fast");
    if (seq !== st.searchSeq) return;  // a newer search is on its way: drop this answer
    st.results = d.items;
    if (!d.items.length) $("#results").innerHTML = "";  // no skeleton while the slower sources are still searching
    noRole = d.no_role;
    renderGraph(d.graph);
    for (const [k, n] of Object.entries(d.sources || {})) src[k] = (src[k] || 0) + n;
    head(LATER[0][1]);
    haptic();
    for (let i = 0; i < LATER.length; i++) {
      let more;
      try { more = await api(`${base}&part=${LATER[i][0]}`); } catch (e) { continue; }  // a slow source failing does not spoil the rest
      if (seq !== st.searchSeq) return;
      st.results = st.results.concat(more.items);
      for (const [k, n] of Object.entries(more.sources || {})) src[k] = (src[k] || 0) + n;
      head(LATER[i + 1] && LATER[i + 1][1]);
    }
    head("");
    while (st.reveal < st.results.length && seq === st.searchSeq) await new Promise((r) => setTimeout(r, 50));  // let the counter finish
    clearInterval(st.revealT);
    st.reveal = Infinity;
    head("");
    renderResults();
  } catch (e) { clearInterval(st.revealT); $("#results").innerHTML = ""; $("#res-head").textContent = ""; fail(e); }
}
function renderResults() {
  const shown = st.results.slice(0, st.reveal ?? st.results.length);  // while searching, the rows appear with the counter
  $("#results").innerHTML = shown.length || st.results.length ? shown.map((i, n) => {
    const mark = i.status === "hidden" ? " ✕" : i.fav ? " ★" : "";
    return `<button class="row" data-n="${n}"><b>${esc(i.title)}${mark}</b><span class="m ${i.match.kind === "exact" ? "exact" : ""}">${esc(i.match.label)}</span>
      <span class="s">${esc([i.company, originOf(i), when(i.date)].filter(Boolean).join(" · "))}</span></button>`;
  }).join("") : ($("#q").value.trim() && st.reveal === Infinity && $("#res-head").textContent.startsWith("Найдено") ? '<div class="empty"><b>Ничего</b>Попробуйте «Похожие роли» или другое слово.</div>' : "");
  $$("#results .row").forEach((r) => (r.onclick = () => openSheet(st.results[+r.dataset.n], true)));
}
function renderGraph(g) {
  const box = $("#graphbox");
  if (st.mode !== "similar" || !g) { box.hidden = true; return; }
  box.hidden = false;
  const nodes = g.nodes.slice(0, 12), n = nodes.length;
  const pts = nodes.map((_, k) => {
    const a = -Math.PI / 2 + (k * 2 * Math.PI) / Math.max(n, 1);
    return [50 + 34 * Math.cos(a), 50 + 38 * Math.sin(a)];
  });
  $("#graph").innerHTML = `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${pts.map((p, k) =>
    `<line x1="50" y1="50" x2="${p[0]}" y2="${p[1]}" class="${nodes[k].kind}${st.off.has(nodes[k].id) ? " off" : ""}"/>`).join("")}</svg>
    <span class="node root" style="left:50%;top:50%">${esc(g.root.label)}</span>` +
    nodes.map((nd, k) => `<button class="node ${nd.kind}${st.off.has(nd.id) ? " off" : ""}" data-id="${esc(nd.id)}" style="left:${pts[k][0]}%;top:${pts[k][1]}%">${esc(nd.label)}</button>`).join("");
  $$("#graph button.node").forEach((b) => (b.onclick = () => {
    const id = b.dataset.id;
    st.off.has(id) ? st.off.delete(id) : st.off.add(id);
    haptic();
    b.classList.toggle("off", st.off.has(id));  // shown at once; the search waits for a pause in tapping
    clearTimeout(st.graphTimer);
    st.graphTimer = setTimeout(runSearch, 600);
  }));
}

/* ---------------------------------------------------------------- my vacancies */

/* ⭐ Избранные компании: companies of favourites (or added by hand) and their NEW vacancies */
async function loadWatch() {
  const d = await api("/api/watch");
  const box = $("#watch");
  $("#sum-watch").textContent = d.new ? `${d.new} ${plural(d.new, "новая", "новые", "новых")}` : d.companies.length ? `${d.companies.length}` : "";
  // long lists fold: 4 vacancies and 8 companies (those with news first), the rest behind «Показать все»
  const cos = [...d.companies].sort((a, b) => (b.new ? 1 : 0) - (a.new ? 1 : 0));
  const jobs = st.watchAll ? d.items : d.items.slice(0, 4), shownCos = st.watchAll ? cos : cos.slice(0, 8);
  const hiddenN = d.items.length - jobs.length + cos.length - shownCos.length;
  box.innerHTML = (d.new ? `<div class="lbl">Новые вакансии</div>${jobs.map((i) => `<button class="row" data-wn="${d.items.indexOf(i)}"><b>${esc(i.title)}</b><span class="m">${esc(when(i.date))}</span><span class="s">${esc([i.company, originOf(i)].filter(Boolean).join(" · "))}</span></button>`).join("")}` : "")
    + `<div class="lbl">${d.companies.length ? "Следим за компаниями (из избранного и добавленные)" : "Пока никого: добавьте вакансию в избранное или впишите компанию"}</div>`
    + `<div class="wcos">${shownCos.map((c) => `<span class="wco"><span>${esc(c.name)}${c.new ? ` <b>+${c.new}</b>` : ""}</span><button data-wx="${esc(c.key)}" aria-label="Убрать ${esc(c.name)}">✕</button></span>`).join("")}</div>`
    + (hiddenN || st.watchAll ? `<button class="link block" id="watch-all">${st.watchAll ? "Свернуть" : `Показать всё: ещё ${hiddenN}`}</button>` : "")
    + `<div class="wadd"><input class="inp" id="watch-name" maxlength="80" placeholder="Название компании"><button class="chip" id="watch-add">Добавить</button></div>`;
  $$("#watch [data-wn]").forEach((b) => (b.onclick = () => openSheet(d.items[+b.dataset.wn], false)));
  if ($("#watch-all")) $("#watch-all").onclick = () => { st.watchAll = !st.watchAll; loadWatch().catch(fail); };
  $$("#watch [data-wx]").forEach((b) => (b.onclick = async () => { try { await api("/api/watch", { op: "remove", key: b.dataset.wx }); loadWatch(); loadMe().catch(() => {}); } catch (e) { fail(e); } }));
  $("#watch-add").onclick = async () => {
    const name = $("#watch-name").value.trim();
    if (!name) return;
    try { await api("/api/watch", { op: "add", name }); toast("Слежу за компанией"); loadWatch(); } catch (e) { fail(e); }
  };
}

/* 🗄 «Ещё → Архив»: put away by hand, office / hybrid / other-country ones and ghosts; 10 shown, the rest on a tap */
async function loadArchive() {
  const items = (await api("/api/jobs?view=archive")).items, box = $("#archive-list");
  $("#sum-archive").textContent = items.length || "";
  const shown = st.archiveAll ? items : items.slice(0, 10);
  box.innerHTML = items.length ? '<p class="hint" style="margin:0 0 8px">Сюда сами уходят офисные и гибридные вакансии, вакансии для другой страны и «вечные». Откройте вакансию, чтобы вернуть её.</p>'
    + shown.map((i, n) => `<button class="row" data-an="${n}"><b>${esc(i.title)}</b><span class="m">${esc(when(i.date))}</span><span class="s">${esc([i.company, originOf(i)].filter(Boolean).join(" · "))}</span></button>`).join("")
    + (items.length > 10 ? `<button class="link block" id="archive-all">${st.archiveAll ? "Свернуть" : `Показать все (${items.length})`}</button>` : "")
    : '<div class="hint" style="margin:0">Пусто. Вакансию можно убрать сюда кнопкой «🗄 В архив».</div>';
  $$("#archive-list [data-an]").forEach((b) => (b.onclick = () => openSheet(shown[+b.dataset.an], false)));
  if ($("#archive-all")) $("#archive-all").onclick = () => { st.archiveAll = !st.archiveAll; loadArchive().catch(fail); };
}

async function loadSaved() {
  loadWatch().catch(() => {});
  $$("#s-saved .seg button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === st.saved));
  const box = $("#saved");
  try {
    const items = (await api("/api/jobs?view=" + st.saved)).items;
    const empty = { fav: "Свайпните карточку вправо или нажмите ★, и вакансия окажется здесь.", hidden: "Скрытых нет.",
      seen: "Здесь будут вакансии, которые вы уже видели в ленте, но не отметили: с датой и временем просмотра.",
    };
    const unscored = st.saved === "fav" && st.me && st.me.ai ? items.filter((i) => !i.ai).length : 0;
    box.innerHTML = (unscored ? `<div class="batchbar"><button class="ai-btn" id="score-fav">✨ Оценить все ИИ (${unscored})</button></div>` : "")
      + (items.length ? items.map((i, n) => {
        const done = i.track && i.track.stage;
        return `<button class="row" data-n="${n}"><b>${esc(i.title)}</b><span class="m">${done ? "✓ " + esc(STAGES[i.track.stage].toLowerCase()) : st.saved === "seen" && i.seen ? "👁 " + esc(seenAt(i.seen)) : esc(when(i.date))}</span><span class="s">${esc([i.company, originOf(i)].filter(Boolean).join(" · "))}</span></button>`;
      }).join("") : `<div class="empty"><b>Пока пусто</b>${empty[st.saved]}</div>`);
    $$("#saved .row").forEach((r) => (r.onclick = () => openSheet(items[+r.dataset.n], false)));
    if ($("#score-fav")) $("#score-fav").onclick = scoreFav;
  } catch (e) { fail(e); }
}

/* ---------------------------------------------------------------- 🧾 profile for responses («Ещё») */

let pf = null;
let pfLang = "ru";   // the language the form edits: every field keeps a Russian and an English text
const pfText = (p, key, lang) => ((p.values[key] || {})[lang] || "");
function pfRender() {
  const f = pf.fields, p = pf.profile, other = pfLang === "ru" ? "en" : "ru";
  $$("#pf-lang button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.l === pfLang));
  $("#pf-list").innerHTML = f.map((x) => `<div class="pf-row"><label>${esc(x[pfLang])}</label>
      ${x.key === "about" ? `<textarea class="inp" data-pk="${x.key}" rows="3" maxlength="700" placeholder="${esc(pfText(p, x.key, other).slice(0, 80))}">${esc(pfText(p, x.key, pfLang))}</textarea>` : `<input class="inp" data-pk="${x.key}" maxlength="700" placeholder="${esc(pfText(p, x.key, other).slice(0, 60))}" value="${esc(pfText(p, x.key, pfLang))}">`}
      <label class="pf-use"><input type="checkbox" data-pu="${x.key}" ${p.use.includes(x.key) ? "checked" : ""}> в письмо</label></div>`).join("")
    + p.custom.map((c, n) => `<div class="pf-row own"><input class="inp" data-cl="${n}" maxlength="40" placeholder="Название поля" value="${esc(c.label)}">
      <input class="inp" data-cv="${n}" maxlength="700" placeholder="Значение" value="${esc(c.value)}">
      <label class="pf-use"><input type="checkbox" data-cu="${n}" ${c.use ? "checked" : ""}> в письмо</label><button class="rm" data-cx="${n}" aria-label="Убрать поле">✕</button></div>`).join("");
  $("#sum-profile").textContent = Object.keys(p.values).length ? `заполнено ${Object.keys(p.values).length}` : "пусто";
  $("#pf-add").hidden = p.custom.length >= pf.custom_max;
  $$("[data-cx]").forEach((b) => (b.onclick = () => { pfRead(); pf.profile.custom.splice(+b.dataset.cx, 1); pfRender(); }));
}
function pfRead() {   // the form -> pf.profile (before it is saved or redrawn)
  const p = pf.profile;
  $$("[data-pk]").forEach((e) => {
    const cell = p.values[e.dataset.pk] || (p.values[e.dataset.pk] = { ru: "", en: "" });
    cell[pfLang] = e.value.trim();
    if (!cell.ru && !cell.en) delete p.values[e.dataset.pk];
  });
  p.use = $$("[data-pu]").filter((c) => c.checked).map((c) => c.dataset.pu);
  p.custom = $$("[data-cl]").map((e) => ({ label: e.value, value: $(`[data-cv="${e.dataset.cl}"]`).value, use: $(`[data-cu="${e.dataset.cl}"]`).checked }));
}
async function loadProfile() {
  const g = $("#g-profile");
  if (!st.me || !st.me.owner) { g.hidden = true; return; }
  try { pf = await api("/api/apply/profile"); } catch (e) { return; }
  g.hidden = false;
  pfRender();
}
$$("#pf-lang button").forEach((b) => (b.onclick = () => { pfRead(); pfLang = b.dataset.l; pfRender(); }));
$("#pf-add").onclick = () => { pfRead(); pf.profile.custom.push({ label: "", value: "", use: true }); pfRender(); };
$("#pf-save").onclick = async () => {
  pfRead();
  try { pf = await api("/api/apply/profile", pf.profile); pfRender(); haptic("ok"); toast("Профиль сохранён"); } catch (e) { fail(e); }
};
$("#pf-fill").onclick = async () => {
  pfRead();
  try {
    await api("/api/apply/profile", pf.profile);
    pf = await api("/api/apply/profile", { fill: true });
    pfRender(); haptic("ok");
    toast(pf.found && pf.found.length ? `Нашёл в резюме и вписал: ${pf.found.length}. Проверьте и сохраните` : "Всё, что нашлось в резюме, уже заполнено");
  } catch (e) { fail(e); }
};

/* ---------------------------------------------------------------- settings */

const TRASH = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>`;

async function loadSources(data) {
  const s = data || (await api("/api/sources"));
  st.sources = s;
  if (s.note) toast(s.note);
  $("#skins").innerHTML = skinsHtml();
  bindSkins($("#skins"));
  for (const g of ["role", "negative", "companies"]) {
    $("#w-" + g).innerHTML = s.words[g].length ? s.words[g].map((w, n) => `<button class="word" data-g="${g}" data-n="${n}">${esc(w)} ✕</button>`).join("")
      : `<span class="hint">${g === "role" ? "Пусто: добавьте профессию и навыки." : g === "companies" ? "Пусто: вакансии этих компаний не будут показываться." : "Пусто."}</span>`;
  }
  $$(".word").forEach((b) => (b.onclick = () => change({ type: "words", group: b.dataset.g, remove: +b.dataset.n }, "Удалил")));
  $$(".reset-words").forEach((b) => { b.hidden = !s.words[b.dataset.g].length; });
  const on = s.channels.filter((c) => c.on).length;
  $("#ch-head").innerHTML = `<span class="sn">📡 Каналы</span><small>${on} из ${s.channels.length}</small>`;
  $("#sum-role").textContent = s.words.role.length ? s.words.role[0] + (s.words.role.length > 1 ? ` +${s.words.role.length - 1}` : "") : "пусто";
  $("#sum-companies").textContent = s.words.companies.length ? `${s.words.companies.length}` : "пусто";
  $("#sum-negative").textContent = s.words.negative.length ? `${s.words.negative.length}` : "пусто";
  $("#sum-sites").textContent = `${s.sites.filter((x) => x.on).length} из ${s.sites.length}`;
  $("#sum-tz").textContent = s.tz.on ? `UTC${s.tz.mine >= 0 ? "+" : "−"}${Math.abs(s.tz.mine)} ±${s.tz.spread} ч` : "выключено";
  $("#ch-list").innerHTML = s.channels.map((c) => `<div class="tg"><span>${esc(c.title)}<small>@${esc(c.key)}</small></span>
      <span class="ctl">${c.removable ? `<button class="rm" data-rm="${esc(c.key)}" aria-label="Убрать из общего списка">${TRASH}</button>` : ""}<button class="sw" data-ch="${esc(c.key)}" aria-pressed="${c.on}" aria-label="${esc(c.title)}"></button></span></div>`).join("");
  $$("[data-ch]").forEach((b) => (b.onclick = () => change({ type: "channel", key: b.dataset.ch, on: b.getAttribute("aria-pressed") !== "true" })));
  $$("[data-rm]").forEach((b) => (b.onclick = () => {
    if (b.dataset.sure) return change({ type: "channel_remove", key: b.dataset.rm }, "Убрал из общего списка");
    b.dataset.sure = "1"; b.textContent = "Убрать?"; setTimeout(() => { delete b.dataset.sure; b.innerHTML = TRASH; }, 3000);
  }));
  $("#site-list").innerHTML = s.sites.map((x) => `<div class="tg"><span>${esc(x.name)}</span><button class="sw" data-site="${esc(x.id)}" aria-pressed="${x.on}" aria-label="${esc(x.name)}"></button></div>`).join("");
  $$("[data-site]").forEach((b) => (b.onclick = () => change({ type: "site", id: b.dataset.site, on: b.getAttribute("aria-pressed") !== "true" })));
  $("#hhq").value = s.hh.query;
  $("#hh-remote").setAttribute("aria-pressed", s.hh.remote);
  $("#hh-box").hidden = !s.sites.some((x) => x.id === "hh" && x.on);
  const tz = s.tz, offs = [];
  for (let h = -12; h <= 14; h++) { offs.push(h); if (h === 3 || h === 5 || h === 9) offs.push(h + 0.5); }
  const utc = (o) => "UTC" + (o >= 0 ? "+" : "−") + Math.trunc(Math.abs(o)) + (o % 1 ? ":30" : "");
  $("#tz-on").setAttribute("aria-pressed", tz.on);
  $("#tz-mine").innerHTML = offs.map((o) => `<option value="${o}" ${o === tz.mine ? "selected" : ""}>${utc(o)}</option>`).join("");
  $("#tz-spread").innerHTML = [1, 2, 3, 4, 6, 8].map((n) => `<option value="${n}" ${n === tz.spread ? "selected" : ""}>±${n} ч</option>`).join("");
  $("#sum-country").textContent = s.country.on ? s.country.name : "выключено";
  $("#country-on").setAttribute("aria-pressed", s.country.on);
  $("#country-key").innerHTML = s.countries.map((c) => `<option value="${esc(c.key)}" ${c.key === s.country.key ? "selected" : ""}>${esc(c.name)}</option>`).join("");
}
async function change(body, note) {
  try {
    haptic();
    const d = await api("/api/sources", body);
    await loadSources(d);
    if (note && !d.note) toast(note);
    loadMe().catch(() => {});
  } catch (e) { fail(e); }
}

/* ---------------------------------------------------------------- résumé -> preset */

const cvState = { data: null, roles: new Set(), channels: new Set(), skills: true, busy: false };

async function loadCv() {
  try { setCv((await api("/api/cv")).cv); } catch (e) { fail(e); }
}
function setCv(cv) {
  cvState.data = cv;
  if (cv) {
    cvState.roles = new Set(cv.roles.filter((r) => r.primary).map((r) => r.id));
    cvState.channels = new Set(cv.channels.filter((c) => c.suggested || c.on).map((c) => c.key));
  }
  renderCv();
}
function uploadHtml(again) {
  return `<label class="cta file-btn">${again ? "Загрузить другое резюме" : "📄 Загрузить резюме"}
    <input type="file" id="cv-file" accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"></label>`;
}
function renderCv() {
  const box = $("#cv-box");
  if (!box) return;
  const cv = cvState.data;
  if (cvState.busy) { box.innerHTML = '<div class="cv"><div class="busy">Читаю резюме и подбираю роли…</div></div>'; return; }
  if (!cv) {
    box.innerHTML = `<div class="cv"><p>Загрузите резюме (PDF, DOCX или TXT). Я найду в нём ваши роли и навыки и предложу настройки поиска: слова и запрос для hh.ru. Ничего не поменяется, пока вы не нажмёте «Применить».</p>
      ${uploadHtml(false)}<span class="muted">Хранится только текст резюме, на компьютере владельца бота. Удалить можно в любой момент.</span></div>`;
  } else {
    const chosen = cv.roles.filter((r) => cvState.roles.has(r.id));
    const words = [...new Set(chosen.flatMap((r) => r.words).concat(cvState.skills ? cv.skill_words : []))];
    const hh = [...new Set(chosen.map((r) => r.hh).filter(Boolean))].join(" OR ");
    const facts = [cv.level && "уровень: " + cv.level, cv.english && "английский: " + cv.english, cv.skills.length + " " + plural(cv.skills.length, "навык", "навыка", "навыков")].filter(Boolean);
    box.innerHTML = `<div class="cv">
      <span class="muted">${esc(cv.file)} · ${esc(facts.join(" · "))}</span>
      <div class="lbl">Похоже, вы ищете</div>
      <div class="chiprow">${cv.roles.length ? cv.roles.map((r) => `<button class="rolechip" data-role="${esc(r.id)}" aria-pressed="${cvState.roles.has(r.id)}">${esc(r.label)}<small>${esc(r.why[0] || "")}</small></button>`).join("")
        : '<span class="muted">Роли не распознал. Добавьте слова вручную ниже.</span>'}</div>
      ${cv.skills.length ? `<div class="lbl">Нашёл навыки</div><div class="chiprow">${cv.skills.map((s) => `<span class="skill">${esc(s.label)}</span>`).join("")}</div>` : ""}
      <label class="check"><input type="checkbox" id="cv-skills" ${cvState.skills ? "checked" : ""}> Искать и по инструментам (${esc(cv.skill_words.join(", ") || "нет")})</label>
      <div class="preview"><b>Слова для поиска</b>${chosen.length ? esc(words.join(", ") || "—") : "пока никаких: отметьте роль выше, тогда слова и запрос hh.ru попадут в ленту"}</div>
      <div class="preview"><b>Запрос на hh.ru</b>${esc(hh || "—")}</div>
      <p class="muted" id="cv-sync">${cvState.synced || "Отметки сразу меняют ленту: слова поиска и запрос hh.ru. Слова, которые вы вписали сами, остаются. Каналы — во вкладке «Поиск»."}</p>
      ${uploadHtml(true)}
      <button class="link" id="cv-delete">Удалить резюме</button></div>`;
    $$("[data-role]", box).forEach((b) => (b.onclick = () => { const id = b.dataset.role; cvState.roles.has(id) ? cvState.roles.delete(id) : cvState.roles.add(id); haptic(); renderCv(); syncCv(); }));
    $("#cv-skills").onchange = (e) => { cvState.skills = e.target.checked; renderCv(); syncCv(); };
    $("#cv-delete").onclick = deleteCv;
  }
  const file = $("#cv-file");
  if (file) file.onchange = () => file.files[0] && uploadCv(file.files[0]);
}
async function uploadCv(file) {
  if (file.size > 5 * 1024 * 1024) return fail(new Error("Файл больше 5 МБ."));
  cvState.busy = true;
  renderCv();
  try {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch("/api/cv", { method: "POST", headers: { Authorization: "tma " + INIT }, body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Ошибка " + res.status);
    cvState.busy = false;
    setCv(data.cv);
    haptic("ok");
    if (typeof loadResume === "function" && !$("#s-resume").hidden) loadResume(true);
    loadOriginal();
    if (data.cv.roles.some((r) => r.primary)) offerPreset(data.cv);
    else toast("Резюме прочитал, но роли не распознал");
  } catch (e) { cvState.busy = false; renderCv(); fail(e); }
}
/* the role chips change the feed at once («sync»: the résumé's words are swapped, typed words stay) */
function syncCv() {
  clearTimeout(cvState.syncT);
  cvState.syncT = setTimeout(async () => {
    if (!cvState.roles.size) { cvState.synced = "Отметьте хотя бы одну роль — тогда лента подстроится."; return renderCv(); }
    try {
      const d = await api("/api/cv/apply", { roles: [...cvState.roles], mode: "sync", skill_words: cvState.skills });
      cvState.synced = `✓ Лента обновлена: ${d.words} ${plural(d.words, "слово", "слова", "слов")} для поиска${d.hh_query ? ", запрос hh.ru" : ""}. Новые вакансии подтянутся при следующей проверке (или нажмите ↻ в ленте).`;
      renderCv();
      loadSources().catch(() => {});
      loadMe().catch(() => {});
    } catch (e) { fail(e); }
  }, 700);
}
async function applyCv(mode) {
  if (!cvState.roles.size) return fail(new Error("Отметьте хотя бы одну роль."));
  try {
    const d = await api("/api/cv/apply", { roles: [...cvState.roles], channels: [...cvState.channels], mode, skill_words: cvState.skills });
    haptic("ok");
    toast(`Готово: ${d.words} ${plural(d.words, "слово", "слова", "слов")} для поиска, hh.ru настроен`);
    await loadSources();
    loadMe().catch(() => {});
  } catch (e) { fail(e); }
}
async function deleteCv() {
  const b = $("#cv-delete");
  if (!b.dataset.sure) { b.dataset.sure = "1"; b.textContent = "Точно удалить? Нажмите ещё раз"; return; }
  try { await api("/api/cv/delete", {}); setCv(null); toast("Резюме удалено"); } catch (e) { fail(e); }
}

/* ---------------------------------------------------------------- 📡 radar settings («Ещё») */

function renderRadar() {
  const m = st.me;
  if (!m) return;
  $("#alerts").setAttribute("aria-pressed", !!m.alerts);
  const sel = $("#interval");
  sel.innerHTML = m.intervals.map((n) => `<option value="${n}" ${n === m.interval ? "selected" : ""}>${n < 60 ? n + " мин" : n / 60 + " ч"}</option>`).join("");
  sel.disabled = !m.owner;
  const ra = m.roles_ai;
  $("#g-roleai").hidden = !ra;
  if (ra) {
    $("#roleai").innerHTML = `<div class="stats"><span>вызовов сегодня ${ra.calls + ra.deep} из ${ra.calls_cap + ra.deep_cap}</span><span>в словаре ${ra.dict} названий</span><span>запомнено ${ra.cache}</span><span>✓ ${ra.checked}</span></div>
      <p class="hint">ИИ спрашиваем только про названия, которых нет ни в словаре, ни в памяти, пачкой, и раз в день не больше ${ra.calls_cap} вызовов (${ra.deep_cap} на чтение описаний). Один ответ запоминается навсегда: со временем неизвестных названий всё меньше, а вызовов тратится меньше.</p>`;
  }
  $("#int-note").textContent = m.owner ? "для всех, кто пользуется Purrfect Job" : "меняет владелец бота";
}

/* ---------------------------------------------------------------- 📊 sources: answered / failed / gone quiet («Ещё») */

const SRC_ICON = { fail: "❌", quiet: "🔇", empty: "⚪", unknown: "⏳", ok: "✅" };

async function loadSourceHealth(all) {
  const g = $("#g-src");
  let d;
  try { d = await api("/api/source-health"); } catch (e) { g.hidden = true; return; }
  if (!d.items.length && !d.filter) { g.hidden = true; return; }
  g.hidden = false;
  const f = d.filter;
  const filt = f ? `<p class="hint"><b>🧪 Проверка фильтра каналов</b> (раз в неделю Claude читает до 15 постов, которые фильтр отбросил,
      хотя в них есть ваша роль). За ${f.days} дн. проверено ${f.checked}, из них настоящих вакансий: ${f.lost}${f.checked ? ` (${Math.round(100 * f.lost / f.checked)}%)` : ""}.
      Ждут проверки: ${f.waiting}.${Object.keys(f.why).length ? " Почему отбросил: " + Object.entries(f.why).map(([k, n]) => `${esc(k)} — ${n}`).join(", ") + "." : ""}</p>
    ${f.items.map((i) => `<div class="tg"><span>${i.link ? `<a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(i.title)}</a>` : esc(i.title)}<small>${esc(i.chat)} · ${esc(i.why)}</small></span></div>`).join("")}` : "";
  const bad = d.items.filter((i) => i.state === "fail" || i.state === "quiet").length;
  const shown = all ? d.items : d.items.slice(0, Math.max(6, bad));
  const rows = shown.map((i) => `<div class="tg"><span>${SRC_ICON[i.state] || ""} ${esc(i.name)}<small>${esc(i.label)}</small></span></div>`).join("");
  $("#src-list").innerHTML = `<p class="hint">❌ — источник не ответил (сайт упал, сменил страницу или просит капчу): вакансий там может быть много, мы их просто не видим.
      🔇 — отвечает, но уже несколько дней пусто, хотя раньше было. ⚪ — проверен, вакансий правда нет. Сначала проблемные.</p>
    <p class="hint">${bad ? `Требуют внимания: ${bad}` : "Все источники отвечают"} · всего ${d.items.length}</p>${rows}
    ${shown.length < d.items.length ? `<button class="link block" id="src-all">Показать все (${d.items.length})</button>` : ""}${filt}`;
  const more = $("#src-all");
  if (more) more.onclick = () => loadSourceHealth(true);
}

/* ---------------------------------------------------------------- 🤖 roles Claude guessed (owner, «Ещё») */

let rolesShown = 8;
let rolesAll = false;   // false: one vacancy at a time (carousel), true: the whole list
let rolesAt = 0;
async function loadRoles(body) {
  const g = $("#g-roles");
  if (!st.me || !st.me.owner) { g.hidden = true; return; }
  let d;
  try { d = await api("/api/roles", body); } catch (e) { fail(e); return; }
  g.hidden = false;
  const s = d.stats;
  if (rolesAt >= d.items.length) rolesAt = Math.max(0, d.items.length - 1);
  const shownItems = rolesAll ? d.items.slice(0, rolesShown) : d.items.slice(rolesAt, rolesAt + 1);
  const rows = shownItems.map((i) => `<div class="rl"><div class="t"><b>${esc(i.title)}</b>
      <small>${i.roles.map((r) => `<span class="role">${esc(r)}</span>`).join("")}${i.sure ? "" : "<span>· догадка</span>"}${i.jd ? "<span>· по описанию</span>" : ""}</small></div>
    <div class="yn"><button class="y" data-rk="${esc(i.key)}" data-ok="1" aria-label="Верно">✓</button><button class="n" data-rk="${esc(i.key)}" data-ok="" aria-label="Неверно">✗</button><button class="e" data-own="${esc(i.key)}" aria-label="Написать свою роль">✎</button></div></div>
    <form class="rl-own" data-ownform="${esc(i.key)}" hidden><input class="inp" list="role-names" maxlength="60" placeholder="Своя роль, например: аналитик данных"><button>OK</button></form>`).join("");
  const more = rolesAll ? Math.min(d.items.length, d.pending) - rolesShown : 0;
  const nav = !d.items.length ? "" : rolesAll
    ? '<button class="link block" id="roles-one">Показать по одной</button>'
    : `<div class="rl-nav"><button id="roles-prev" aria-label="Назад" ${rolesAt ? "" : "disabled"}>‹</button><span>${rolesAt + 1} из ${d.items.length}</span><button id="roles-next" aria-label="Дальше" ${rolesAt < d.items.length - 1 ? "" : "disabled"}>›</button><button class="link" id="roles-every">Все</button></div>`;
  $("#roles-list").innerHTML = `<p class="hint">Вакансии, которые словарь не узнал, ИИ относит к роли сам (в поиске они с 🤖 и ниже точных).
      ✓ верно, ✗ ошибка: ошибка больше не используется, ✎ — написать роль самому.</p>
    <datalist id="role-names">${(d.names || []).map((n) => `<option value="${esc(n)}">`).join("")}</datalist>
    <div class="stats"><span>в поиске ${s.used}</span><span>✓ ${s.checked}</span><span>✗ ${s.rejected}</span><span>ждут проверки ${d.pending}</span></div>
    ${nav && !rolesAll ? nav : ""}${rows || '<p class="hint">Пока нечего проверять.</p>'}${rolesAll ? nav : ""}
    ${more > 0 ? '<button class="link block" id="roles-more">Показать ещё</button>' : ""}
    ${s.total ? '<button class="link block" id="roles-reset">Забыть все догадки ИИ (проверенные ✓ останутся)</button>' : ""}`;
  $("#roles-list").querySelectorAll("[data-rk]").forEach((b) => {
    b.onclick = () => { haptic(); loadRoles({ key: b.dataset.rk, ok: !!b.dataset.ok }); };
  });
  $("#roles-list").querySelectorAll("[data-own]").forEach((b) => {
    b.onclick = () => {
      const f = $("#roles-list").querySelector(`[data-ownform="${CSS.escape(b.dataset.own)}"]`);
      f.hidden = !f.hidden;
      if (!f.hidden) f.querySelector("input").focus();
    };
  });
  $("#roles-list").querySelectorAll("[data-ownform]").forEach((f) => {
    f.onsubmit = (e) => {
      e.preventDefault();
      const role = f.querySelector("input").value.trim();
      if (!role) return;
      haptic();
      loadRoles({ key: f.dataset.ownform, role }).then(() => toast("Запомнил вашу роль"));
    };
  });
  const go = (id, fn) => { const b = $("#" + id); if (b) b.onclick = () => { haptic(); fn(); loadRoles(); }; };
  go("roles-prev", () => { rolesAt = Math.max(0, rolesAt - 1); });
  go("roles-next", () => { rolesAt += 1; });
  go("roles-every", () => { rolesAll = true; });
  go("roles-one", () => { rolesAll = false; });
  const m = $("#roles-more");
  if (m) m.onclick = () => { rolesShown += 8; loadRoles(); };
  const r = $("#roles-reset");
  if (r) r.onclick = () => {
    if (!r.dataset.sure) { r.dataset.sure = "1"; r.textContent = "Точно забыть? Нажмите ещё раз"; return; }
    loadRoles({ reset: true }).then(() => toast("Догадки ИИ забыты"));
  };
}

/* ---------------------------------------------------------------- 👥 people and their AI use (owner, «Ещё») */

let usersShown = 10;
const tok = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n || 0));
const dmy = (iso) => (iso ? new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }) : "—");
function usageLine(u) {
  return `${u.total.n} ${plural(u.total.n, "запрос", "запроса", "запросов")} · ${tok(u.total.in + u.total.out)} токенов`;
}
function bars(u) { // the last 14 days, oldest on the left
  const by = Object.fromEntries((u.days || []).map((d) => [d.day, d.n]));
  const days = [...Array(14)].map((_, i) => { const d = new Date(Date.now() - (13 - i) * 864e5); return by[d.toISOString().slice(0, 10)] || 0; });
  const top = Math.max(1, ...days);
  return `<div class="bars" aria-hidden="true">${days.map((n) => `<i class="${n ? "" : "z"}" style="height:${n ? Math.max(12, Math.round((n / top) * 100)) : 8}%"></i>`).join("")}</div>`;
}
async function loadUsers(body) {
  const g = $("#g-users");
  if (!st.me || !st.me.owner) { g.hidden = true; return; }
  let d;
  try { d = await api("/api/admin/users", body); } catch (e) { fail(e); return; }
  g.hidden = false;
  const on = d.people.filter((p) => !p.blocked).length;
  $("#sum-users").textContent = `${d.people.length}` + (on < d.people.length ? `, откл. ${d.people.length - on}` : "");
  const today = d.people.reduce((a, p) => a + p.usage.today.n, 0);
  const rows = d.people.slice(0, usersShown).map((p) => {
    const lim = p.limit_now, used = p.usage.today.n;
    const who = p.name || (p.username ? "@" + p.username : "id " + p.id);
    return `<div class="us${p.blocked ? " off" : ""}">
      <div class="t"><b>${esc(who)}</b>${p.username && p.name ? `<small>@${esc(p.username)}</small>` : ""}</div>
      <div class="m">${p.blocked ? "⛔ отключён · " : ""}с ${dmy(p.joined)} · заходил ${p.seen ? dmy(p.seen) : "—"} · вакансий ${p.jobs}</div>
      <div class="m">🤖 сегодня ${used} из ${lim}${p.limit !== null && p.limit !== undefined ? " (свой лимит)" : ""} · за 14 дней ${usageLine(p.usage)}</div>
      ${p.usage.total.n ? bars(p.usage) : ""}
      <div class="acts">
        <button data-ub="${p.id}" class="${p.blocked ? "on" : "warn"}">${p.blocked ? "Включить доступ" : "Отключить"}</button>
        <button data-ua="${p.id}" class="${p.ai ? "on" : ""}">ИИ ${p.ai ? "вкл" : "выкл"}</button>
        <button data-ul="${p.id}">Лимит ИИ: ${lim}</button>
      </div>
      <form class="us-lim" data-ulf="${p.id}" hidden><span>Запросов в день:</span><input class="inp" type="number" min="0" max="${d.max_limit}" inputmode="numeric" value="${lim}">
        <button>OK</button>${p.limit !== null && p.limit !== undefined ? `<button type="button" class="link" data-ulr="${p.id}">как у всех</button>` : ""}</form>
    </div>`;
  }).join("");
  $("#users-list").innerHTML = `<p class="hint">Ботом может пользоваться любой, кто его открыл. «Отключить» — человек сразу видит вежливый отказ и в боте, и в приложении; его данные остаются. Запросы ИИ идут через вашу подписку Claude.</p>
    <div class="stats"><span>людей ${d.people.length}</span><span>ИИ сегодня: люди ${today}</span><span>вы ${d.owner.today.n}</span><span>бот ${d.system.today.n}</span></div>
    <div class="stats"><span>за 14 дней: люди ${tok(d.people.reduce((a, p) => a + p.usage.total.in + p.usage.total.out, 0))} ток.</span><span>вы ${tok(d.owner.total.in + d.owner.total.out)}</span><span>бот ${tok(d.system.total.in + d.system.total.out)}</span></div>
    <form class="us-def" id="us-def"><span>Лимит ИИ на человека, запросов в день</span><input class="inp" type="number" min="0" max="${d.max_limit}" inputmode="numeric" value="${d.default_limit}"><button class="link">Сохранить</button></form>
    ${rows || '<p class="hint">Пока никого.</p>'}
    ${d.people.length > usersShown ? `<button class="link block" id="users-more">Показать ещё (${d.people.length - usersShown})</button>` : ""}`;
  const box = $("#users-list");
  box.querySelectorAll("[data-ub]").forEach((b) => (b.onclick = () => {
    const p = d.people.find((x) => String(x.id) === b.dataset.ub);
    if (!p.blocked && !b.dataset.sure) { b.dataset.sure = "1"; b.textContent = "Точно отключить?"; return; }
    haptic(); loadUsers({ uid: p.id, blocked: !p.blocked }).then(() => toast(p.blocked ? "Доступ включён" : "Отключён"));
  }));
  box.querySelectorAll("[data-ua]").forEach((b) => (b.onclick = () => {
    const p = d.people.find((x) => String(x.id) === b.dataset.ua);
    haptic(); loadUsers({ uid: p.id, ai: !p.ai });
  }));
  box.querySelectorAll("[data-ul]").forEach((b) => (b.onclick = () => {
    const f = box.querySelector(`[data-ulf="${b.dataset.ul}"]`);
    f.hidden = !f.hidden;
    if (!f.hidden) f.querySelector("input").focus();
  }));
  box.querySelectorAll("[data-ulf]").forEach((f) => (f.onsubmit = (e) => {
    e.preventDefault();
    haptic(); loadUsers({ uid: +f.dataset.ulf, limit: +f.querySelector("input").value || 0 }).then(() => toast("Лимит сохранён"));
  }));
  box.querySelectorAll("[data-ulr]").forEach((b) => (b.onclick = () => { haptic(); loadUsers({ uid: +b.dataset.ulr, limit: null }); }));
  $("#us-def").onsubmit = (e) => {
    e.preventDefault();
    haptic(); loadUsers({ default_limit: +$("#us-def input").value || 0 }).then(() => toast("Лимит по умолчанию сохранён"));
  };
  const m = $("#users-more");
  if (m) m.onclick = () => { usersShown += 20; loadUsers(); };
}

/* ---------------------------------------------------------------- after a résumé upload: fill the feed? */

async function offerPreset(cv) {
  try { if (!cv) cv = (await api("/api/cv/from-resume", { vid: rs.data.active.id })).cv; } catch (e) { return; }
  const roles = (cv && cv.roles || []).filter((r) => r.primary);
  if (!roles.length) return;
  const words = [...new Set(roles.flatMap((r) => r.words).concat(cv.skill_words || []))];
  const hh = [...new Set(roles.map((r) => r.hh).filter(Boolean))].join(" OR ");
  const chans = (cv.channels || []).filter((c) => c.suggested);
  st.sheet = { preset: true };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>📥 Заполнить ленту по резюме?</h3>
    <p class="hint" style="font-size:14px">Похоже, вы ищете: <b>${esc(roles.map((r) => r.label).join(", "))}</b>. Предлагаю такие настройки ленты:</p>
    <div class="cv"><div class="preview"><b>Слова для поиска</b>${esc(words.join(", ") || "—")}</div>
      <div class="preview"><b>Запрос на hh.ru</b>${esc(hh || "—")}</div>
      ${chans.length ? `<div class="preview"><b>Каналы</b>${esc(chans.map((c) => "@" + c.key).join(", "))}</div>` : ""}</div>
    <p class="hint">Ничего не поменяется, пока не нажмёте «Да». Потом всё можно поправить в «Поиск» → «Что попадает в ленту».</p>
    <div class="btns"><button class="yes" id="pr-yes">Да, заполнить</button><button id="pr-no">Не сейчас</button></div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  $("#pr-no").onclick = closeSheet;
  $("#pr-yes").onclick = async () => {
    try {
      const mine = st.sources ? st.sources.words.role.length : 0;
      const d = await api("/api/cv/apply", { roles: roles.map((r) => r.id), channels: chans.map((c) => c.key), mode: mine ? "merge" : "replace", skill_words: true });
      haptic("ok"); closeSheet();
      toast(`Готово: ${d.words} ${plural(d.words, "слово", "слова", "слов")} для поиска, hh.ru и каналы настроены`);
      loadSources().catch(() => {}); loadMe().catch(() => {});
    } catch (e) { fail(e); }
  };
  try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
}

/* ---------------------------------------------------------------- 📎 the uploaded résumé file */

async function loadOriginal() {
  const box = $("#rs-orig");
  if (!box) return;
  let d;
  try { d = await api("/api/resume/original"); } catch (e) { return; }
  if (!d.original) { box.innerHTML = ""; return; }
  const o = d.original;
  box.innerHTML = `<div class="group"><div class="tg" style="border-top:0"><span>📎 Оригинал: ${esc(o.file)}<small>загружен ${esc(when(o.at))}</small></span>
    <span class="ctl"><button class="ghost sm" id="orig-view">Текст</button><button class="ghost sm" id="orig-send">В чат</button></span></div></div>`;
  $("#orig-view").onclick = () => {
    st.sheet = { orig: true };
    $("#sheet").innerHTML = `<div class="grip"></div><h3>📎 ${esc(o.file)}</h3><p class="hint">Так файл прочитался при загрузке, без правок. Сам файл можно прислать в чат с ботом.</p>
      <div class="body orig-text">${esc(d.text || "Текст не извлёкся.")}</div>
      <div class="btns"><button class="yes" id="orig-send2">Прислать файл в чат</button><button id="orig-close">Закрыть</button></div>`;
    $("#sheet").hidden = $("#sheet-bg").hidden = false;
    $("#orig-close").onclick = closeSheet;
    $("#orig-send2").onclick = sendOriginal;
    try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
  };
  $("#orig-send").onclick = sendOriginal;
}
async function sendOriginal() {
  try { await api("/api/resume/original/send", {}); haptic("ok"); toast("Файл отправлен в чат с ботом"); } catch (e) { fail(e); }
}

/* ---------------------------------------------------------------- start over */

function showReset() {
  st.sheet = { reset: true };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>♻️ Начать заново?</h3>
    <p class="hint" style="font-size:14px">Purrfect Job станет как у нового пользователя:</p>
    <ul class="reset-list">
      <li>слова для поиска, стоп-слова, каналы, сайты и hh.ru очистятся;</li>
      <li>загруженное резюме удалится;</li>
      <li>стиль сбросится, обучение покажется снова.</li>
    </ul>
    <p class="hint" style="font-size:14px">Найденные вакансии, избранное и отклики останутся, если не отметить ниже.</p>
    <label class="check"><input type="checkbox" id="reset-jobs"> Удалить и все мои вакансии: ленту, избранное, отклики, скрытые</label>
    <div class="btns"><button class="bad" id="reset-yes">Да, начать заново</button><button id="reset-no">Отмена</button></div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  $("#reset-no").onclick = closeSheet;
  $("#reset-yes").onclick = async () => {
    const b = $("#reset-yes");
    if (b.disabled) return;
    b.disabled = true;
    b.textContent = "Сбрасываю…";
    try {
      await api("/api/reset", { confirm: "reset", wipe_jobs: $("#reset-jobs").checked });
      haptic("ok");
      try { localStorage.removeItem("jr-skin"); } catch (e) { /* blocked */ }
      location.reload();
    } catch (e) { b.disabled = false; b.textContent = "Да, начать заново"; fail(e); }
  };
  try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
}

/* ---------------------------------------------------------------- memo */

const FAQ = [
  ["Что такое Purrfect Job?", "Личный помощник по поиску удалённой работы. Он сам читает каналы с вакансиями, сайты удалёнки и hh.ru и собирает подходящее в одну ленту. Он только <b>читает</b>: никому не пишет и никуда не откликается, откликаетесь вы сами."],
  ["С чего начать? Лента пустая", "Сначала Purrfect Job не знает, что вы ищете. Проще всего загрузить резюме во вкладке «▤ Резюме»: я найду в нём роли и навыки и предложу слова, запрос для hh.ru и каналы. Заполню, только когда вы нажмёте «Да». Или настройте вручную: «Поиск» → блоки «Что попадает в ленту» под поиском."],
  ["Как разбирать ленту?", "Свайп <b>вправо</b> или ★ — в избранное, <b>влево</b> или ✕ — скрыть, <b>вверх</b> — оценка ИИ, <b>вниз</b> — подготовить отклик (ничего не отправится без вашего нажатия), тап — полный текст и ссылка. Кнопка ☰ вверху показывает ленту списком, ↻ — проверить источники прямо сейчас."],
  ["Что за цифры в шапке?", "Ваша воронка: сколько вакансий найдено (одинаковые из разных источников считаются один раз), сколько откликов, собеседований и офферов. Отклики и этапы считаются по «📋 Мои отклики»."],
  ["Как отмечать отклики?", "Откройте вакансию и выберите этап: «Откликнулся», «Тестовое», «Собеседование», «Оффер» или «Отказ». Все отклики — во «Ещё» → «📋 Мои отклики», там же можно добавить отклик вручную. Если по отклику неделю тишина, бот напомнит в чате."],
  ["Как откликнуться через приложение?", "Пока это есть только у владельца бота. Кнопка «✉️ Откликнуться» в вакансии (или свайп вниз) готовит письмо по вашему резюме и находит контакт: почту, @телеграм или ссылку. Вы читаете письмо и сами решаете, отправлять ли. Данные для писем (ФИО, город, зарплата, о себе) — во «Ещё» → «🧾 Профиль для откликов», поля на русском и английском (RUS / ENG)."],
  ["Как работает оценка?", "На каждой карточке сразу есть быстрая оценка 1–5 <b>без ИИ</b>: сколько ваших навыков из резюме названо в вакансии, совпадает ли название с тем, что вы ищете, и нет ли подвохов (офис, одна страна, старая). «✨ Оценить ИИ» — внимательная оценка по резюме с причинами; «✨ Оценить ИИ (N)» над лентой оценивает все новые. У ИИ дневной лимит."],
  ["Как сортировать и фильтровать?", "Над лентой: сначала новые, по зарплате, по совпадению слов, по оценке ИИ, избранное сначала, по алфавиту; грейд (Junior…Lead) и дата публикации. Чипы: #ru_company (русскоязычные компании), каналы, сайты, hh.ru, part-time. «Исключить источник…» убирает из ленты один канал или сайт."],
  ["Куда деваются офисные вакансии?", "Если в тексте вакансии (или в оригинале по ссылке) сказано «офис» или «гибрид», либо берут только из другой страны, она сразу уходит в «Ещё» → «🗄 Архив», без уведомления. Оттуда её можно вернуть. На остальных карточках бывают пометки «⚠️ в оригинале офис» или «🌍 только США»."],
  ["Что за пометки «перевыложена» и «висит больше 30 дней»?", "Так помечаются вакансии, которые, похоже, никого не ищут по-настоящему: висят на сайте месяц и больше, перевыкладываются снова и снова или пропали с сайта. Такие опускаются вниз ленты, а у владельца уходят в архив."],
  ["Что за строки 🛠 🏠 📌 на карточке?", "Короткое саммари из текста вакансии, без ИИ: стек, формат работы и первые задачи. 💰 — зарплата (примерно в долларах в месяц), 🕒 — часовой пояс, если они есть в тексте. «＋ ещё в…» — та же вакансия из других источников, склеена в одну."],
  ["Что такое «Просмотрено»?", "Вакансия, которую вы открыли (тап по карточке или «Подробнее»), но не добавили в избранное и не скрыли. Она остаётся в ленте, пока вы её не разберёте: со следующего входа она стоит в конце ленты с пометкой 👁, а новые идут первыми. Все открытые лежат в «Мои» → «👁 Просмотрено» с датой и временем, оттуда любую можно вернуть в ленту."],
  ["Что такое «Избранные компании»?", "Компания каждой вакансии, которую вы добавили в избранное, попадает в «Мои» → «⭐ Избранные компании». Туда можно добавить компанию и вручную. Когда у неё появляется новая вакансия, над лентой загорается полоска, а в чат приходит одно сообщение."],
  ["Можно скрыть вакансии не под мой пояс или страну?", "Да: «Поиск» → «🕒 Часовой пояс» (свой пояс и допустимая разница) и «🌏 Откуда берут» (ваша страна: скроются вакансии «только США», «только Европа» и т. п.). Где пояс или страна не указаны, вакансия остаётся."],
  ["Чем отличаются «Точно» и «Похожие роли» в поиске?", "«Точно» ищет фразу в названиях вакансий, слова в любом порядке. «Похожие роли» добавляет синонимы и смежные профессии по графу; лишнюю роль можно выключить нажатием на неё. Если название незнакомое, ИИ может определить роль по описанию — такие отмечены 🤖."],
  ["Что умеет вкладка «Резюме»?", "Загрузить файл (PDF, DOCX, выгрузка hh.ru или LinkedIn) или собрать с нуля; 4 шаблона, 6 цветов и фото; проверка с замечаниями — у каждого кнопка «Поправить вручную»; сравнение с вакансией; ✨ ИИ: улучшить формулировки, «О себе», подогнать под вакансию, перевести, сопроводительное; 🧭 карьерный план; скачать PDF. Новых фактов ИИ не придумывает."],
  ["Откуда берутся вакансии?", "Telegram-каналы, hh.ru и больше 70 сайтов: удалёнка (Remote.com, Jobgether, Himalayas и др.), крупные компании (Яндекс, Т-Банк, Сбер, Ozon, Авито…), Mastodon, Bluesky и LinkedIn. Включаются галочками в «Поиск» → «🌐 Сайты». Кто давно молчит — видно во «Ещё» → «📊 Источники»."],
  ["Как часто Purrfect Job ищет?", "Это видно и меняется во «Ещё» → «📡 Поиск вакансий». Там же можно выключить сообщения о новых вакансиях в чате Telegram."],
  ["Кто видит мои вакансии?", "Только вы. Каналы и сайты общие на всех (до 60 каналов), а отметки, избранное и отклики у каждого свои. Канал можно добавить свой (до 3 в день), сайт — предложить владельцу. Пожелания и ошибки — во «Ещё» → «💬 Обратная связь»."],
  ["Приложение не открывается", "Purrfect Job живёт на компьютере владельца. Если компьютер выключен или спит, загляните позже: новые вакансии он догонит."],
  ["Запутался в настройках", "«Ещё» → «♻️ Начать заново» очистит настройки, и можно настроить с нуля. Перед сбросом приложение переспросит."],
];
const MEMO = () => `<div class="grip"></div><h3>❓ Вопросы и ответы</h3><div class="memo faq">
${FAQ.map(([q, a]) => `<details><summary>${q}</summary><p>${a}</p></details>`).join("")}
<button class="link block" id="faq-onb">🎓 Показать обучение</button></div>
<div class="btns"><button class="yes wide" id="memo-ok">Понятно</button></div>`;
function showMemo() {
  st.sheet = { memo: true };
  $("#sheet").innerHTML = MEMO();
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  $("#memo-ok").onclick = closeSheet;
  $("#faq-onb").onclick = () => { closeSheet(); showOnboarding(); };
  try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
}

/* ---------------------------------------------------------------- onboarding */

const SLIDES = [
  () => `<div class="pic"><div class="big">📡</div></div><h2>Привет, ${esc(st.me.name)}! Это Purrfect Job</h2>
    <p>Я собираю удалённые вакансии из Telegram-каналов, с сайтов и с hh.ru в одну ленту. Проверяю сам, по расписанию, пока включён компьютер. Офисные и гибридные вакансии сразу убираю в архив.</p>`,
  () => `<div class="pic"><div class="demo-card back"></div><div class="demo-card"><b>Data Analyst</b><span class="co">Uzum</span><i></i><i style="width:70%"></i></div></div>
    <h2>Свайпай карточки</h2><p><b>Вправо</b> или <b>★</b> — в избранное, <b>влево</b> — скрыть, <b>вверх</b> — оценка ИИ. Тап по карточке открывает полный текст и ссылку.</p>`,
  () => `<div class="pic"><div class="big">🎯 🕸</div></div><h2>Ищи точно или похоже</h2>
    <p><b>Точно</b> — фраза один в один. <b>Похожие роли</b> — граф: синонимы и смежные профессии, например «бизнес-аналитик → системный, продуктовый».</p>`,
  () => `<div class="pic" style="height:auto"><div class="skins" id="onb-skins">${skinsHtml()}</div></div><h2>Выбери стиль</h2>
    <p>Каналы, сайты и слова для ленты — во вкладке «Поиск», в блоке «Что попадает в ленту». Стиль меняется во вкладке «Ещё» в любой момент.</p>
    <button class="cta" id="onb-cv">📄 Настроить поиск по моему резюме</button>
    <button class="link" id="onb-memo">❓ Вопросы и ответы: как всё устроено</button>`,
];
let slide = 0;
function showOnboarding() {
  slide = 0;
  $("#onb").hidden = false;
  renderSlide();
}
function renderSlide() {
  $("#onb-slides").innerHTML = `<div class="slide">${SLIDES[slide]()}</div>`;
  $("#onb-dots").innerHTML = SLIDES.map((_, k) => `<i class="${k === slide ? "on" : ""}"></i>`).join("");
  $("#onb-next").textContent = slide === SLIDES.length - 1 ? "Поехали" : "Дальше";
  $("#onb-skip").style.visibility = slide === SLIDES.length - 1 ? "hidden" : "visible";
  if ($("#onb-skins")) bindSkins($("#onb-skins"));
  if ($("#onb-memo")) $("#onb-memo").onclick = () => { finishOnboarding(); showMemo(); };
  if ($("#onb-cv")) $("#onb-cv").onclick = () => { finishOnboarding(); show("resume"); };
}
function finishOnboarding() {
  $("#onb").hidden = true;
  api("/api/settings", { onboarded: true, skin: document.documentElement.dataset.skin }).catch(() => {});
}
$("#onb-next").onclick = () => { haptic(); if (slide < SLIDES.length - 1) { slide++; renderSlide(); } else finishOnboarding(); };
$("#onb-skip").onclick = finishOnboarding;
(() => { // swipe between slides
  let x0 = null;
  $("#onb-slides").addEventListener("pointerdown", (e) => (x0 = e.clientX));
  $("#onb-slides").addEventListener("pointerup", (e) => {
    if (x0 === null) return;
    const dx = e.clientX - x0; x0 = null;
    if (dx < -50 && slide < SLIDES.length - 1) { slide++; renderSlide(); }
    if (dx > 50 && slide > 0) { slide--; renderSlide(); }
  });
})();

/* ---------------------------------------------------------------- navigation and start */

function show(name) {
  $$(".nav button").forEach((b) => (b.dataset.s === name ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  $$(".screen").forEach((s) => (s.hidden = s.id !== "s-" + name));
  $("#screens").scrollTop = 0;
  if (name === "saved") loadSaved();
  if (name === "more" || name === "search") loadSources().catch(fail);
  if (name === "more") { loadArchive().catch(() => {}); loadTrack(); $("#digest").setAttribute("aria-pressed", !!(st.me && st.me.digest)); renderRadar(); loadSourceHealth(); loadRoles(); loadProfile(); loadUsers(); }
  if (name === "resume") loadOriginal();
  if (name === "resume" && typeof loadPlan === "function") loadPlan();
  if (name === "resume") loadResume(true);
  if (name === "search" && !st.results.length) setTimeout(() => $("#q").focus(), 150);
}
$$(".nav button").forEach((b) => (b.onclick = () => { haptic(); show(b.dataset.s); }));
$$("#filters .chip").forEach((c) => (c.onclick = () => { haptic(); setFilter(c.dataset.f); }));
$("#view-toggle").onclick = () => {
  st.view = st.view === "list" ? "cards" : "list";
  try { localStorage.setItem("jr-feed", st.view); } catch (e) { /* blocked */ }
  haptic();
  toast(st.view === "list" ? "Лента списком" : "Лента карточками");
  renderStack();
};
$("#watchbar").onclick = () => { $("#watchbox").open = true; show("saved"); };
$$("#s-saved .seg button").forEach((b) => (b.onclick = () => { st.saved = b.dataset.v; loadSaved(); }));
$("#b-yes").onclick = () => topCard() && decide(topCard(), true);
$("#b-no").onclick = () => topCard() && decide(topCard(), false);
/* «Сбросить» in «Что ищу» / «Стоп-слова»: the first tap asks, the second (within 4 s) empties the list */
$$(".reset-words").forEach((b) => (b.onclick = () => {
  if (b.dataset.ask) { clearTimeout(+b.dataset.ask); delete b.dataset.ask; b.textContent = "Сбросить"; return change({ type: "words", group: b.dataset.g, clear: true }, "Список очищен"); }
  b.textContent = "Точно очистить всё? Нажмите ещё раз";
  b.dataset.ask = setTimeout(() => { delete b.dataset.ask; b.textContent = "Сбросить"; }, 4000);
}));
$("#sort").onchange = () => { st.sort = $("#sort").value; haptic(); renderFilterSum(); loadFeed().catch(fail); };
$("#grade").onchange = () => { st.grade = $("#grade").value; haptic(); renderFilterSum(); loadFeed().catch(fail); };
$("#fb-form").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("#fb-text").value.trim();
  if (text.length < 3) return toast("Напишите хотя бы пару слов");
  try { haptic(); await api("/api/feedback", { text }); $("#fb-text").value = ""; toast("Спасибо, отправлено"); } catch (err) { fail(err); }
};
$("#age").onchange = () => { st.age = $("#age").value; haptic(); renderFilterSum(); loadFeed().catch(fail); };
$("#score-new").onclick = scoreNew;
$("#ex-pick").onchange = () => { const v = $("#ex-pick").value; if (v) excludeSource(v, true); };
$("#ex-rvc").onclick = () => { const b = $("#ex-rvc"); if (b.dataset.name) excludeSource(b.dataset.name, b.getAttribute("aria-pressed") !== "true"); };
$("#refresh").onclick = refresh;
$("#search-form").onsubmit = (e) => { e.preventDefault(); st.off.clear(); runSearch(); };
$("#m-exact").onclick = () => { st.mode = "exact"; renderMode(); runSearch(); };
$("#m-similar").onclick = () => { st.mode = "similar"; renderMode(); runSearch(); };
$("#sheet-bg").onclick = closeSheet;
$$("form.add[data-group]").forEach((f) => (f.onsubmit = (e) => {
  e.preventDefault();
  const input = $("input", f);
  if (input.value.trim()) change({ type: "words", group: f.dataset.group, add: input.value }, "Добавил");
  input.value = "";
}));
$("#ch-add").onsubmit = (e) => {
  e.preventDefault();
  const input = $("input", e.target);
  if (input.value.trim()) change({ type: "channel_add", text: input.value });
  input.value = "";
};
$("#hh-form").onsubmit = (e) => { e.preventDefault(); change({ type: "hh", query: $("#hhq").value }, "Запрос для hh.ru сохранён"); };
$("#hh-remote").onclick = () => change({ type: "hh", remote: $("#hh-remote").getAttribute("aria-pressed") !== "true" });
$$("[data-bulk]").forEach((b) => (b.onclick = () => change({ type: b.dataset.bulk, on: b.dataset.on === "1" }, b.dataset.on === "1" ? "Включил все" : "Выключил все")));
const tzChange = (body) => change(body).then(() => Promise.all([loadFeed(), loadMe()])).catch(() => {});
$("#tz-on").onclick = () => tzChange({ type: "tz", on: $("#tz-on").getAttribute("aria-pressed") !== "true" });
$("#tz-mine").onchange = () => tzChange({ type: "tz", mine: +$("#tz-mine").value });
$("#tz-spread").onchange = () => tzChange({ type: "tz", spread: +$("#tz-spread").value });
$("#country-on").onclick = () => tzChange({ type: "country", on: $("#country-on").getAttribute("aria-pressed") !== "true" });
$("#country-key").onchange = () => tzChange({ type: "country", key: $("#country-key").value });
$("#track-add").onclick = addTrackSheet;
$("#digest").onclick = async () => {
  const on = $("#digest").getAttribute("aria-pressed") !== "true";
  try {
    await api("/api/settings", { digest: on });
    st.me.digest = on;
    $("#digest").setAttribute("aria-pressed", on);
    haptic();
    toast(on ? "Сводка по утрам включена" : "Сводка выключена");
  } catch (e) { fail(e); }
};
$("#again").onclick = showOnboarding;
$("#alerts").onclick = async () => {
  const on = $("#alerts").getAttribute("aria-pressed") !== "true";
  try { await api("/api/settings", { alerts: on }); st.me.alerts = on; renderRadar(); haptic(); toast(on ? "Сообщения о новых вакансиях включены" : "Сообщения выключены: новые вакансии будут только в ленте"); } catch (e) { fail(e); }
};
$("#interval").onchange = async () => {
  try { await api("/api/settings", { interval: +$("#interval").value }); st.me.interval = +$("#interval").value; haptic(); toast("Сохранил"); renderRadar(); } catch (e) { fail(e); }
};
$("#memo").onclick = showMemo;
$("#reset").onclick = showReset;

/* 🧩 «Подработки» (owner, in the feed): gigs with a low barrier from every source, picked by words (gigs.py, no AI).
   Folded by default; 10 shown, the rest on a tap; a tap opens the usual card (★, ✕, ✉️ Откликнуться). */
const gigs = { all: false, sort: "new", fl: false, more: false, d: null, poll: null, drafts: {}, busy: false, fresh: null };
async function loadGigs() {
  const g = $("#g-gigs");
  if (!st.me || !st.me.owner) { g.hidden = true; return; }
  g.hidden = false;
  try {
    gigs.d = await api("/api/gigs?all=1&sort=new");
  } catch (e) { $("#sum-gigs").textContent = "не загрузилось"; return; }
  renderGigs();
  clearTimeout(gigs.poll);
  if (gigs.d.refreshing) gigs.poll = setTimeout(loadGigs, 20000);  // the exchanges are being read: look again soon
}
const hoursAgo = (h) => (h < 1 ? `${Math.max(1, Math.round(h * 60))} мин назад` : `${String(Math.round(h * 10) / 10).replace(".", ",")} ч назад`);
const usdOf = (v) => (v ? "$" + Math.round(v).toLocaleString("ru-RU") : "");
/* the chips work on the list already in the page: no request, no wait */
const GIG_SORT = {
  new: (a, b) => (!!a.undated - !!b.undated) || (b.date > a.date ? 1 : b.date < a.date ? -1 : 0),
  ease: (a, b) => ((a.gig.level !== "easy") - (b.gig.level !== "easy")) || (b.gig.ease - a.gig.ease) || GIG_SORT.new(a, b),
  pay: (a, b) => payKey(b) - payKey(a) || GIG_SORT.new(a, b),
};
const payKey = (i) => (i.pay ? i.pay.hi * (i.pay.per === "/час" ? 160 : 1) : -1);
function gigView() {
  const all = gigs.d.items, mine = all.filter((i) => (i.gig.skills || []).length);
  let list = gigs.fl ? all.filter((i) => i.fl) : gigs.all ? all : mine;
  list = [...list].sort(GIG_SORT[gigs.sort] || GIG_SORT.new);
  return { list, all: all.length, mine: mine.length, easy: list.filter((i) => i.gig.level === "easy").length };
}
function renderGigs() {
  const d = gigs.d, box = $("#gigs"), v = gigView();
  $("#sum-gigs").textContent = v.list.length ? `🟢 ${v.easy} · всего ${v.list.length}` : d.refreshing ? "читаю биржи…" : "пока нет";
  const shown = gigs.more ? v.list : v.list.slice(0, 10);
  const src = Object.entries(d.sources || {}).map(([n, v]) => `${n} ${v.err && !v.n ? "—" : v.n ?? 0}`).join(" · ");
  const gl = (i) => i.gig || {};
  const sortChip = (k, label) => `<button class="chip" data-gs="${k}" aria-pressed="${gigs.sort === k}">${label}</button>`;
  const line = (i) => (i.fl
    ? [`⏱ ${hoursAgo(i.fl.age_h)}`, `${i.fl.bids} ${plural(i.fl.bids, "заявка", "заявки", "заявок")}`, ...(gl(i).skills || []).slice(0, 2)]
    : [originOf(i), ...(gl(i).why || []).slice(0, 2), ...(gl(i).skills || []).slice(0, 3)]);
  const busy = gigs.busy || d.refreshing;
  box.innerHTML = `<button class="gig-refresh" id="gigs-refresh" ${busy ? "disabled" : ""}>${busy
      ? '<span class="spin-dot"></span> Читаю Freelancer и FL.ru…' : "↻ Обновить подработки"}</button>`
    + (gigs.fresh != null && !busy ? `<div class="gig-fresh">${gigs.fresh ? `🆕 Новых: ${gigs.fresh}, они сверху` : "Новых заказов пока нет"} · ${esc(ago(d.checked_at))}</div>` : "")
    + '<p class="hint" style="margin:0 0 8px">Задачи, фриланс и неполная занятость с низким порогом входа. Отбор по словам в тексте, без ИИ: '
    + "🟢 легко зайти, 🟡 можно попробовать. Видите только вы.</p>"
    + `<div class="chips wrap" role="group" aria-label="Какие подработки"><button class="chip" id="gigs-fl" aria-pressed="${gigs.fl}">⚡ Freelancer</button>`
    + `<button class="chip" data-ga="0" aria-pressed="${!gigs.all && !gigs.fl}">Под мои навыки${v.mine ? " · " + v.mine : ""}</button>`
    + `<button class="chip" data-ga="1" aria-pressed="${gigs.all && !gigs.fl}">Все${v.all ? " · " + v.all : ""}</button></div>`
    + `<div class="chips wrap" role="group" aria-label="Порядок">${sortChip("new", "🆕 Свежие")}${sortChip("ease", "🟢 Лёгкие")}${sortChip("pay", "💵 По оплате")}</div>`
    + (shown.length ? shown.map((i, n) => `<button class="row" data-gn="${n}"><b>${gl(i).level === "easy" ? "🟢" : "🟡"} ${esc(i.title)}</b>`
      + `<span class="m${gl(i).level === "easy" ? " easy" : ""}">${esc(i.fl ? hoursAgo(i.fl.age_h) : i.undated ? "дата ?" : when(i.date))}</span>`
      + `<span class="s">${i.salary ? `<b class="pay">💵 ${esc(i.salary)}</b> · ` : ""}${esc(line(i).filter(Boolean).join(" · "))}</span></button>`).join("")
      : `<div class="hint" style="margin:0">${d.refreshing ? "Читаю биржи и hh.ru, это минута-другая…" : "Сейчас ничего подходящего."}</div>`)
    + (v.list.length > 10 ? `<button class="link block" id="gigs-more">${gigs.more ? "Свернуть" : `Показать все (${v.list.length})`}</button>` : "")
    + `<p class="gig-src">${d.checked_at ? "Биржи проверены " + esc(ago(d.checked_at)) : "Биржи ещё не проверялись"}${src ? ": " + esc(src) : ""}. `
    + "Freelancer обновляется сам каждые 15 минут, кнопка «↻» наверху — сразу. Ставку вы делаете сами на сайте: в карточке заказа есть готовая заявка.</p>";
  $$("#gigs [data-gn]").forEach((b) => (b.onclick = () => openSheet(shown[+b.dataset.gn], true)));
  $$("#gigs [data-ga]").forEach((b) => (b.onclick = () => { haptic(); gigs.all = b.dataset.ga === "1"; gigs.fl = false; gigs.more = false; renderGigs(); }));
  $$("#gigs [data-gs]").forEach((b) => (b.onclick = () => { haptic(); gigs.sort = b.dataset.gs; gigs.more = false; renderGigs(); }));
  $("#gigs-fl").onclick = () => { haptic(); gigs.fl = !gigs.fl; gigs.more = false; renderGigs(); };
  if ($("#gigs-more")) $("#gigs-more").onclick = () => { gigs.more = !gigs.more; renderGigs(); };
  $("#gigs-refresh").onclick = refreshGigs;
}
/* ↻ only the gig exchanges (Freelancer API + FL.ru, ~10–30 s), the main feed is not touched; then «Новых: N» */
async function refreshGigs() {
  if (gigs.busy) return;
  haptic();
  const before = new Set((gigs.d ? gigs.d.items : []).map((i) => i.key));
  gigs.busy = true; gigs.fresh = null; renderGigs();
  try {
    await api("/api/gigs/refresh", {});
    for (let n = 0; n < 40; n++) {
      await new Promise((r) => setTimeout(r, 3000));
      const d = await api("/api/gigs?all=1&sort=new");
      gigs.d = d;
      if (!d.refreshing) break;
    }
    gigs.fresh = gigs.d.items.filter((i) => !before.has(i.key)).length;
    if (gigs.sort === "new") gigs.more = false;
    haptic("ok");
  } catch (e) { fail(e); }
  gigs.busy = false;
  renderGigs();
}
/* ⚡ a Freelancer order in the card: everything to decide in a glance, then a ready bid. The bid itself is placed by
   Eduard on the site: «Скопировать и открыть» copies the text and opens the order page. */
function flBox(i) {
  const f = i.fl;
  if (!f) return "";
  const dr = gigs.drafts[i.key];
  const bid = f.bid ? `${f.bid.toLocaleString("ru-RU")} ${f.cur}${f.cur !== "USD" && f.bid_usd ? ` (≈ ${usdOf(f.bid_usd)})` : ""}` : "по бюджету клиента";
  const rows = [
    ["⏱ Опубликован", hoursAgo(f.age_h)],
    ["👥 Заявок", `${f.bids}${f.bid_avg_usd ? `, в среднем ${usdOf(f.bid_avg_usd)}` : ""}`],
    ["💵 Бюджет", i.salary || "не указан"],
    ["🛠 Что сделать", f.todo || "см. описание ниже"],
    ["🤖 С Claude", `примерно ${f.hours[0]}–${f.hours[1]} ч работы`],
    ["🎯 Ставка", `${bid}, срок ${f.days} ${plural(f.days, "день", "дня", "дней")} · ${f.fee_note}`],
  ];
  return `<div class="fl">${rows.map(([k, v]) => `<div class="fl-r"><span>${k}</span><b>${esc(v)}</b></div>`).join("")}
    <div class="fl-risk">⚠️ ${f.risks.map(esc).join("<br>⚠️ ")}</div>
    ${dr ? `<div class="fl-draft"><div class="lbl">✍️ Заявка (${esc(dr.how)})${dr.warning ? ` · <span class="warn">${esc(dr.warning)}</span>` : ""}</div>
        <textarea class="inp" id="fl-text" rows="10">${esc(dr.text)}</textarea>
        <div class="btns" style="margin-top:8px"><button class="yes wide" id="fl-copy">📋 Скопировать и открыть заказ</button>
        <button class="wide" id="fl-done">✅ Я сделал ставку</button></div></div>`
      : `<div class="btns" style="margin-top:8px"><button class="yes wide" id="fl-ai">✍️ Подготовить заявку (ИИ)</button>
        <button class="wide" id="fl-tpl">📝 Шаблон без ИИ</button></div>`}
  </div>`;
}
function wireFl(i) {
  if (!i.fl) return;
  const draft = async (ai) => {
    const b = $(ai ? "#fl-ai" : "#fl-tpl");
    if (b) { b.disabled = true; b.textContent = ai ? "ИИ пишет заявку… ~30 с" : "Готовлю…"; }
    try {
      const r = await api("/api/gigs/proposal", { key: i.key, ai });
      gigs.drafts[i.key] = r.job ? await waitJob(r.job) : r.result;
      haptic("ok");
    } catch (e) { fail(e); }
    if (st.sheet && st.sheet.item.key === i.key) renderSheet();
  };
  if ($("#fl-ai")) $("#fl-ai").onclick = () => draft(true);
  if ($("#fl-tpl")) $("#fl-tpl").onclick = () => draft(false);
  if ($("#fl-text")) $("#fl-text").oninput = (e) => { gigs.drafts[i.key].text = e.target.value; };
  if ($("#fl-copy")) $("#fl-copy").onclick = async () => {
    try { await navigator.clipboard.writeText($("#fl-text").value); toast("Заявка скопирована"); } catch (e) { toast("Выделите текст и скопируйте вручную"); }
    openLink(i.link);
  };
  if ($("#fl-done")) $("#fl-done").onclick = async () => {
    try {
      const f = i.fl;
      await api("/api/gigs/applied", { key: i.key, bid: f.bid ? `${f.bid} ${f.cur}` : "" });
      haptic("ok"); toast("Записал в «Мои отклики»"); closeSheet(); loadGigs(); loadMe().catch(() => {});
    } catch (e) { fail(e); }
  };
}
/* Drop-down lists: every <select> opens our own list from the bottom instead of the phone's system dialog
   (grey radio buttons on Android). The <select> stays in the page, hidden: its value, options and «change»
   events keep working for all the code that already uses it; the button next to it only shows and sets it. */
const picker = (() => {
  const proto = HTMLSelectElement.prototype;
  for (const k of ["value", "selectedIndex"]) {  // code that sets sel.value refreshes the visible button too
    const d = Object.getOwnPropertyDescriptor(proto, k);
    Object.defineProperty(proto, k, { configurable: true, enumerable: d.enumerable, get() { return d.get.call(this); },
      set(v) { d.set.call(this, v); if (this._pk) label(this); } });
  }
  const label = (sel) => {
    const o = sel.options[sel.selectedIndex];
    sel._pk.firstChild.textContent = o ? o.textContent : "";
    sel._pk.disabled = sel.disabled;
  };
  const titleOf = (sel) => {
    if (sel.getAttribute("aria-label")) return sel.getAttribute("aria-label");
    const lb = sel.closest("label, .fld, .tg");
    const t = lb && [...lb.childNodes].find((n) => n !== sel && (n.nodeType === 3 ? n.textContent.trim() : n.tagName === "SPAN"));
    return t ? t.textContent.trim() : "";
  };
  function wrap(sel) {
    if (sel._pk || sel.multiple) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pk " + sel.className;
    b.setAttribute("aria-haspopup", "listbox");
    b.innerHTML = '<span></span><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    sel._pk = b;
    sel.classList.add("pk-src");
    sel.after(b);
    label(sel);
    b.addEventListener("click", (e) => { e.preventDefault(); open(sel); });
    new MutationObserver(() => label(sel)).observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "selected"] });
  }
  let cur = null;
  function open(sel) {
    close(true);
    haptic("light");
    const bg = document.createElement("div"), box = document.createElement("div");
    bg.className = "pick-bg";
    box.className = "pick";
    box.setAttribute("role", "listbox");
    const title = titleOf(sel);
    box.innerHTML = `<div class="grip"></div>${title ? `<h4>${esc(title)}</h4>` : ""}<div class="pick-list"></div>`;
    const list = box.lastChild;
    [...sel.options].forEach((o, i) => {
      if (o.hidden) return;
      const r = document.createElement("button");
      r.type = "button";
      r.className = "pick-opt";
      r.setAttribute("role", "option");
      r.setAttribute("aria-selected", String(i === sel.selectedIndex));
      r.style.setProperty("--i", Math.min(list.children.length, 12));
      r.disabled = o.disabled;
      r.innerHTML = '<span></span><i aria-hidden="true">✓</i>';
      r.firstChild.textContent = o.textContent;
      r.onclick = () => {
        if (i !== sel.selectedIndex) {
          sel.selectedIndex = i;
          sel.dispatchEvent(new Event("input", { bubbles: true }));
          sel.dispatchEvent(new Event("change", { bubbles: true }));
        }
        list.querySelectorAll(".pick-opt").forEach((x) => x.setAttribute("aria-selected", String(x === r)));
        haptic("light");
        setTimeout(close, 120);
      };
      list.append(r);
    });
    bg.onclick = () => close();
    document.body.append(bg, box);
    cur = { bg, box };
    const on = list.querySelector('[aria-selected="true"]');
    if (on && list.scrollHeight > list.clientHeight) list.scrollTop = on.offsetTop - list.clientHeight / 2;
  }
  function close(now) {
    if (!cur) return false;
    const { bg, box } = cur;
    cur = null;
    if (now === true || matchMedia("(prefers-reduced-motion: reduce)").matches) { bg.remove(); box.remove(); return true; }
    bg.classList.add("closing"); box.classList.add("closing");
    setTimeout(() => { bg.remove(); box.remove(); }, 200);
    return true;
  }
  const scan = (root) => root.querySelectorAll("select").forEach(wrap);
  scan(document);
  new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => {
    if (n.nodeType !== 1) return;
    if (n.tagName === "SELECT") wrap(n); else scan(n);
  }))).observe(document.body, { childList: true, subtree: true });
  return { close };
})();

async function start() {
  // in full screen, content scrolled up slides under a band of the page colour, not under Telegram's buttons
  $("main").addEventListener("scroll", (e) => document.documentElement.classList.toggle("scrolled", e.target.scrollTop > 6), { passive: true });
  try { setSkin(localStorage.getItem("jr-skin") || "hybrid"); } catch (e) { setSkin("hybrid"); }
  if (!INIT) { $("#gate").hidden = false; return; }
  tg.ready();
  tg.expand();
  // phones: full screen, Telegram keeps only its two floating buttons and our header runs up behind them
  const fs = () => document.documentElement.classList.toggle("fs", !!tg.isFullscreen);
  try { tg.onEvent("fullscreenChanged", fs); fs(); if (["android", "ios"].includes(tg.platform)) tg.requestFullscreen(); } catch (e) { /* old client */ }
  try { tg.disableVerticalSwipes(); } catch (e) { /* old client: swiping down may close the app */ }
  try { tg.BackButton.onClick(() => { if (!picker.close()) closeSheet(); }); } catch (e) { /* old client */ }
  renderMode();
  try {
    await loadMe();
  } catch (e) {
    $("#gate-text").textContent = e.message;
    $("#gate").hidden = false;
    return;
  }
  if (st.me.skin) setSkin(st.me.skin);
  if (!st.me.onboarded) showOnboarding();
  await loadFeed().catch(feedFailed);
  loadGigs();
  setInterval(() => { // keep the counters fresh while the app is open
    if (document.hidden) return;
    const before = st.me && st.me.stats.new;
    // a feed that failed to load (the bot was restarting) comes back by itself once the counter says there is something
    loadMe().then(() => { if ((st.me.stats.new !== before || (st.me.stats.new && !(st.feed || []).length)) && !$("#s-feed").hidden) loadFeed(true).catch(() => {}); }).catch(() => {});
  }, 60000);
}
start();
