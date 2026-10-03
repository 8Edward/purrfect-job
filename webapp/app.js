"use strict";
/* Job Radar mini app: talks to the bot's API (webapp.py). Every request carries
   Telegram's signed launch data, so each person sees only their own vacancies. */

const tg = window.Telegram && window.Telegram.WebApp;
const INIT = tg ? tg.initData : "";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const SKINS = {
  radar: { name: "Радар", bg: "#0d1524", colors: ["#0d1524", "#1c2c47", "#f2a93b"] },
  dossier: { name: "Досье", bg: "#e9edf2", colors: ["#e9edf2", "#ffffff", "#2440d0"] },
  tropic: { name: "Тропики", bg: "#e6f4ef", colors: ["#e6f4ef", "#0f3b37", "#ff6650"] },
};
const MATCH = { exact: "точно" };
const st = { me: null, feed: [], filter: "all", saved: "fav", mode: "exact", off: new Set(), results: [], sources: null, sheet: null, scoring: new Set(), tzHidden: 0, view: "cards" };
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
const metaHtml = (i) => [i.location && "📍 " + i.location, i.salary && "💰 " + i.salary, i.tz && "🕒 " + i.tz].filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join("");
const alsoText = (i) => (i.also && i.also.length ? `＋ ещё в: ${i.also.map((a) => (a.source === "tg" ? "💬 " : "🌐 ") + a.origin).join(", ")}` : "");
function aiLine(i) {
  if (i.ai) return `<div class="ai"><b>✨ ${i.ai.score}/5</b> ${esc(i.ai.verdict)}</div>`;
  if (st.scoring.has(i.key)) return '<div class="ai busy">✨ ИИ оценивает…</div>';
  return st.me && st.me.ai ? '<button class="ai-btn" data-score>✨ Оценить ИИ: подходит ли мне</button>' : "";
}
function openLink(url) {
  if (!url) return;
  if (tg && /^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
  else if (tg) tg.openLink(url);
  else window.open(url, "_blank");
}

/* ---------------------------------------------------------------- skins */

function setSkin(skin, save) {
  if (!SKINS[skin]) skin = "radar";
  document.documentElement.dataset.skin = skin;
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
  st.me = await api("/api/me");
  const m = st.me, s = m.stats;
  $("#hello").innerHTML = s.new
    ? `${esc(m.name)}, <em>${s.new}</em> ${plural(s.new, "новая", "новые", "новых")}`
    : `${esc(m.name)}, всё разобрано`;
  $("#checked").textContent = (m.checked_at ? "Проверено " + ago(m.checked_at) : "Первая проверка скоро")
    + (m.next_check ? ` · следующая через ${m.next_check} мин` : "");
  $("#st-ch").textContent = s.channels;
  $("#st-web").textContent = s.sites;
  $("#st-taken").textContent = s.taken;
  $("#badge").hidden = !s.new;
  $("#badge").textContent = s.new > 99 ? "99+" : s.new;
}
async function loadFeed() {
  const d = await api("/api/jobs?view=new");
  st.feed = d.items;
  st.tzHidden = d.tz_hidden || 0;
  renderStack();
}
function keep(i) {
  const f = st.filter;
  return f === "all" || (f === "ru" && (i.tags || []).includes("ru-company"))
    || (f === "pt" && (i.tags || []).includes("part-time")) || kindOf(i) === f;
}
function renderStack() {
  const items = st.feed.filter(keep), box = $("#stack");
  const list = st.view === "list";
  $("#view-toggle").textContent = list ? "❏" : "☰";
  $("#view-toggle").setAttribute("aria-label", list ? "Показать карточками" : "Показать списком");
  box.hidden = list;
  $("#feed-list").hidden = !list;
  if (list) {
    $("#acts").hidden = true;
    $("#left").textContent = items.length ? `Всего ${items.length} · тап — подробно` + (st.tzHidden ? ` · не под ваш пояс скрыто: ${st.tzHidden}` : "") : "";
    renderList(items);
    if (items.length) return;
    box.hidden = false;
  }
  if (!items.length) {
    const filtered = st.feed.length && st.filter !== "all";
    box.innerHTML = `<div class="empty"><b>${filtered ? "Тут пусто" : "Всё разобрано"}</b>${filtered
      ? "С этим фильтром новых нет."
      : "Новые вакансии появятся после следующей проверки."}${st.tzHidden ? `<br>Не под ваш часовой пояс скрыто: ${st.tzHidden}.` : ""}<br><button class="link" id="empty-act">${filtered ? "Показать все" : "Проверить сейчас"}</button></div>`;
    $("#empty-act").onclick = filtered ? () => setFilter("all") : refresh;
    $("#left").textContent = "";
    $("#acts").hidden = true;
    return;
  }
  $("#acts").hidden = false;
  $("#left").textContent = `Ещё ${items.length} · вправо — в работу, влево — скрыть, тап — подробно` + (st.tzHidden ? ` · не под ваш пояс скрыто: ${st.tzHidden}` : "");
  box.innerHTML = items.slice(0, 3).reverse().map((i, n, arr) => {
    const depth = arr.length - 1 - n;
    return `<article class="card" data-key="${esc(i.key)}" style="transform:translateY(${depth * 10}px) scale(${1 - depth * 0.04});opacity:${1 - depth * 0.25};z-index:${10 - depth}">
      <span class="verdict yes">В РАБОТУ</span><span class="verdict no">СКРЫТЬ</span>
      <div class="src"><span>${esc(originOf(i))}</span><span>${esc(when(i.date))}${i.fav ? " · ★" : ""}</span></div>
      <h3>${esc(i.title)}</h3>${i.company ? `<div class="co">${esc(i.company)}</div>` : ""}
      <div class="meta">${metaHtml(i)}</div><div class="tags">${tagsHtml(i.tags)}</div>${aiLine(i)}
      ${i.also ? `<div class="also">${esc(alsoText(i))}</div>` : ""}
      <p>${esc(i.text || "Полное описание на сайте.")}</p><span class="more">Подробнее →</span></article>`;
  }).join("");
  const top = box.querySelector(".card:last-child");
  if (top) drag(top);
}
const byKey = (key) => st.feed.find((i) => i.key === key);

/* the whole feed as a scrolling list (the other view of the same vacancies) */
function renderList(items) {
  const box = $("#feed-list");
  box.innerHTML = items.map((i, n) => {
    const facts = [i.company, originOf(i), when(i.date)].filter(Boolean).join(" · ");
    const extra = [i.salary && "💰 " + i.salary, i.tz && "🕒 " + i.tz, i.also && "＋" + i.also.length].filter(Boolean).join(" · ");
    return `<div class="lrow" data-n="${n}"><button class="lmain" data-open="${n}"><b>${esc(i.title)}${i.fav ? " ★" : ""}</b>
      <span class="s">${esc(facts)}</span>${extra ? `<span class="s x">${esc(extra)}</span>` : ""}
      ${i.ai ? `<span class="sc">✨ ${i.ai.score}</span>` : ""}</button>
      <span class="lacts"><button class="no" data-no="${n}" aria-label="Скрыть">✕</button><button class="yes" data-yes="${n}" aria-label="В работу">✓</button></span></div>`;
  }).join("");
  $$("[data-open]", box).forEach((b) => (b.onclick = () => openSheet(items[+b.dataset.open], false)));
  $$("[data-yes]", box).forEach((b) => (b.onclick = () => quick(items[+b.dataset.yes], true, b.closest(".lrow"))));
  $$("[data-no]", box).forEach((b) => (b.onclick = () => quick(items[+b.dataset.no], false, b.closest(".lrow"))));
}
async function quick(item, yes, row) {
  haptic(yes ? "ok" : "medium");
  if (row) row.classList.add(yes ? "gone-yes" : "gone-no");
  st.feed = st.feed.filter((i) => i !== item);
  setTimeout(renderStack, 180);
  try {
    const d = await api(`/api/jobs/${encodeURIComponent(item.key)}/${yes ? "take" : "hide"}`, {});
    toast(d.note);
    loadMe().catch(() => {});
  } catch (e) { fail(e); loadFeed().catch(() => {}); }
}
function topCard() { return $("#stack .card:last-child"); }

async function decide(card, yes) {
  const item = byKey(card.dataset.key);
  card.style.transform = `translateX(${yes ? 480 : -480}px) rotate(${yes ? 18 : -18}deg)`;
  card.style.opacity = 0;
  haptic(yes ? "ok" : "medium");
  st.feed = st.feed.filter((i) => i !== item);
  setTimeout(renderStack, 200);
  try {
    const d = await api(`/api/jobs/${encodeURIComponent(item.key)}/${yes ? "take" : "hide"}`, {});
    toast(d.note);
    loadMe().catch(() => {});
  } catch (e) { fail(e); loadFeed().catch(() => {}); }
}
function drag(card) {
  let x0 = null, y0 = 0, dx = 0, down = null;
  card.addEventListener("pointerdown", (e) => { x0 = e.clientX; y0 = e.clientY; dx = 0; down = e.target; card.classList.add("drag"); card.setPointerCapture(e.pointerId); });
  card.addEventListener("pointermove", (e) => {
    if (x0 === null) return;
    dx = e.clientX - x0;
    card.style.transform = `translateX(${dx}px) rotate(${dx / 18}deg)`;
    $(".verdict.yes", card).style.opacity = Math.max(0, dx / 90);
    $(".verdict.no", card).style.opacity = Math.max(0, -dx / 90);
  });
  const end = (e) => {
    if (x0 === null) return;
    const tap = Math.abs(dx) < 6 && Math.abs((e.clientY || y0) - y0) < 6;
    x0 = null;
    card.classList.remove("drag");
    if (Math.abs(dx) > 90) return decide(card, dx > 0);
    card.style.transform = "";
    $$(".verdict", card).forEach((v) => (v.style.opacity = 0));
    if (tap && e.type === "pointerup") {
      if (down && down.closest && down.closest("[data-score]")) scoreJob(byKey(card.dataset.key));
      else openSheet(byKey(card.dataset.key), false);
    }
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}
function setFilter(f) {
  st.filter = f;
  $$("#filters .chip").forEach((c) => c.setAttribute("aria-pressed", c.dataset.f === f));
  renderStack();
}
async function refresh() {
  const b = $("#refresh");
  if (b.classList.contains("spin")) return;
  b.classList.add("spin");
  haptic();
  try {
    const d = await api("/api/refresh", {});
    toast(d.busy ? "Проверка уже идёт, загляни через минуту" : d.new ? `Нашёл ${d.new} ${plural(d.new, "новую", "новые", "новых")}` : "Новых пока нет");
    await Promise.all([loadMe(), loadFeed()]);
  } catch (e) { fail(e); }
  b.classList.remove("spin");
}

/* ---------------------------------------------------------------- detail sheet */

async function openSheet(item, viaSearch) {
  if (!item) return;
  st.sheet = { item, viaSearch };
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
  const status = { taken: "✓ В работе", hidden: "✕ Скрыта" }[i.status] || "";
  $("#sheet").innerHTML = `<div class="grip"></div>
    <div class="src">${esc(originOf(i))} · ${esc(when(i.date))}</div>
    <h3>${esc(i.title)}</h3>${i.company ? `<div class="co">${esc(i.company)}</div>` : ""}
    <div class="meta">${metaHtml(i)}</div><div class="tags" style="margin-top:8px">${tagsHtml(i.tags)}</div>
    ${status ? `<div class="status">${status}</div>` : ""}
    ${aiBox(i)}
    ${i.also ? `<div class="also">Эта же вакансия ещё в: ${i.also.map((a, n) => a.link ? `<button class="link" data-also="${n}">${esc(a.origin)}</button>` : esc(a.origin)).join(", ")}</div>` : ""}
    <div class="track"><div class="lbl">📋 Мой отклик${i.silent >= 7 ? ` · <span class="warn">⏰ тишина ${i.silent} дн.</span>` : ""}</div><div class="chiprow">${[["", "—"], ...Object.entries(STAGES)].map(([k, v]) =>
      `<button class="stage" data-stage="${k}" aria-pressed="${((i.track && i.track.stage) || "") === k}">${v}</button>`).join("")}</div></div>
    <div class="body">${esc(i.text || "Полное описание на сайте: кнопка ниже.")}</div>
    <div class="btns">
      ${fresh ? `<button class="yes" data-a="take">✓ В работу</button><button data-a="hide">✕ Скрыть</button>` : ""}
      ${i.status === "hidden" ? `<button data-a="restore" class="wide">↩ Вернуть в новые</button>` : ""}
      <button data-a="fav" class="${fresh ? "" : "wide"}">${i.fav ? "★ В избранном" : "☆ В избранное"}</button>
      ${i.link ? `<button data-a="open" class="${fresh ? "" : "wide"}">${i.source === "tg" ? "🔗 Пост" : "🔗 Вакансия"}</button>` : ""}
    </div>`;
  $$("#sheet [data-a]").forEach((b) => (b.onclick = () => sheetAction(b.dataset.a)));
  $$("#sheet [data-stage]").forEach((b) => (b.onclick = () => setStage(st.sheet.item, b.dataset.stage)));
  $$("#sheet [data-also]").forEach((b) => (b.onclick = () => openLink(i.also[+b.dataset.also].link)));
}
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
    box.innerHTML = (counts.length ? `<div class="hint">${counts.map(([k, n]) => `${STAGES[k]}: <b>${n}</b>`).join(" · ")}</div>` : "")
      + (items.length ? items.map((i, n) => row(i, n, "t")).join("")
        : '<div class="hint">Пока пусто. Откройте вакансию и отметьте этап внизу карточки: «Откликнулся», «Тестовое»… Если неделю нет новостей, бот напомнит.</div>')
      + (d.taken.length ? `<div class="lbl">В работе, без отметки</div>${d.taken.map((i, n) => row(i, n, "k")).join("")}` : "");
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
  try {
    const path = viaSearch ? `/api/search/${encodeURIComponent(item.key)}/${action}` : `/api/jobs/${encodeURIComponent(item.key)}/${action}`;
    const d = await api(path, {});
    haptic(action === "take" ? "ok" : "light");
    toast(d.note);
    st.sheet.item = { ...item, ...d.item, text: item.text, match: item.match };
    st.feed = st.feed.map((i) => (i.key === item.key ? { ...i, ...d.item, text: i.text } : i)).filter((i) => i.status === "new");
    st.results = st.results.map((i) => (i.key === item.key ? { ...i, status: d.item.status, fav: d.item.fav } : i));
    renderSheet();
    renderStack();
    renderResults();
    if (!$("#s-saved").hidden) loadSaved();
    loadMe().catch(() => {});
  } catch (e) { fail(e); }
}
function closeSheet() {
  st.sheet = null;
  $("#sheet").hidden = $("#sheet-bg").hidden = true;
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
  $("#res-head").textContent = "Ищу в ленте, каналах за неделю, на сайтах и hh.ru…";
  $("#results").innerHTML = '<div class="skeleton"></div>'.repeat(4);
  try {
    const d = await api(`/api/search?q=${encodeURIComponent(q)}&mode=${st.mode}&off=${encodeURIComponent([...st.off].join(","))}`);
    st.results = d.items;
    renderGraph(d.graph);
    const src = d.sources || {};
    const parts = [["inbox", "лента"], ["channels", "каналы"], ["sites", "сайты"], ["hh", "hh.ru"]]
      .filter(([k]) => src[k]).map(([k, n]) => `${n} ${src[k]}`);
    $("#res-head").innerHTML = (d.no_role ? "Этой роли пока нет в графе: показываю точные совпадения. Граф можно дополнить в rolegraph.yml.<br>" : "")
      + `Найдено: <b>${d.items.length}</b>` + (parts.length ? ` · просмотрел: ${esc(parts.join(", "))}` : "");
    renderResults();
    haptic();
  } catch (e) { $("#results").innerHTML = ""; $("#res-head").textContent = ""; fail(e); }
}
function renderResults() {
  $("#results").innerHTML = st.results.length ? st.results.map((i, n) => {
    const mark = i.status === "taken" ? " ✓" : i.status === "hidden" ? " ✕" : i.fav ? " ★" : "";
    return `<button class="row" data-n="${n}"><b>${esc(i.title)}${mark}</b><span class="m ${i.match.kind === "exact" ? "exact" : ""}">${esc(i.match.label)}</span>
      <span class="s">${esc([i.company, originOf(i), when(i.date)].filter(Boolean).join(" · "))}</span></button>`;
  }).join("") : ($("#q").value.trim() && $("#res-head").textContent.startsWith("Найдено") ? '<div class="empty"><b>Ничего</b>Попробуйте «Похожие роли» или другое слово.</div>' : "");
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
    runSearch();
  }));
}

/* ---------------------------------------------------------------- my vacancies */

async function loadSaved() {
  $$("#s-saved .seg button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === st.saved));
  const box = $("#saved");
  try {
    const items = (await api("/api/jobs?view=" + st.saved)).items;
    const empty = { fav: "Нажмите ★ на вакансии, и она окажется здесь.", taken: "Свайпните карточку вправо, и она окажется здесь.", hidden: "Скрытых нет." };
    box.innerHTML = items.length ? items.map((i, n) =>
      `<button class="row" data-n="${n}"><b>${esc(i.title)}</b><span class="m">${esc(when(i.date))}</span><span class="s">${esc([i.company, originOf(i)].filter(Boolean).join(" · "))}</span></button>`).join("")
      : `<div class="empty"><b>Пока пусто</b>${empty[st.saved]}</div>`;
    $$("#saved .row").forEach((r) => (r.onclick = () => openSheet(items[+r.dataset.n], false)));
  } catch (e) { fail(e); }
}

/* ---------------------------------------------------------------- settings */

async function loadSources(data) {
  const s = data || (await api("/api/sources"));
  st.sources = s;
  if (s.note) toast(s.note);
  $("#skins").innerHTML = skinsHtml();
  bindSkins($("#skins"));
  for (const g of ["role", "negative"]) {
    $("#w-" + g).innerHTML = s.words[g].length ? s.words[g].map((w, n) => `<button class="word" data-g="${g}" data-n="${n}">${esc(w)} ✕</button>`).join("")
      : `<span class="hint">${g === "role" ? "Пусто: добавьте профессию и навыки." : "Пусто."}</span>`;
  }
  $$(".word").forEach((b) => (b.onclick = () => change({ type: "words", group: b.dataset.g, remove: +b.dataset.n }, "Удалил")));
  const on = s.channels.filter((c) => c.on).length;
  $("#ch-head").textContent = `📡 Каналы · отмечено ${on} · в общем списке ${s.channels.length} из ${s.max_pool}`;
  $("#ch-list").innerHTML = s.channels.map((c) => `<div class="tg"><span>${esc(c.title)}<small>@${esc(c.key)}</small></span>
      <span class="ctl">${c.removable ? `<button class="x" data-rm="${esc(c.key)}" aria-label="Убрать из общего списка">🗑</button>` : ""}<button class="sw" data-ch="${esc(c.key)}" aria-pressed="${c.on}" aria-label="${esc(c.title)}"></button></span></div>`).join("");
  $$("[data-ch]").forEach((b) => (b.onclick = () => change({ type: "channel", key: b.dataset.ch, on: b.getAttribute("aria-pressed") !== "true" })));
  $$("[data-rm]").forEach((b) => (b.onclick = () => {
    if (b.dataset.sure) return change({ type: "channel_remove", key: b.dataset.rm }, "Убрал из общего списка");
    b.dataset.sure = "1"; b.textContent = "Убрать?"; setTimeout(() => { delete b.dataset.sure; b.textContent = "🗑"; }, 2500);
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
    box.innerHTML = `<div class="cv"><p>Загрузите резюме (PDF, DOCX или TXT). Я найду в нём ваши роли и навыки и предложу настройки поиска: слова, запрос для hh.ru и каналы. Ничего не поменяется, пока вы не нажмёте «Применить».</p>
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
      <div class="preview"><b>Слова для поиска</b>${esc(words.join(", ") || "—")}</div>
      <div class="preview"><b>Запрос на hh.ru</b>${esc(hh || "—")}</div>
      ${cv.channels.length ? `<div class="lbl">Каналы</div>${cv.channels.map((c) => `<div class="tg" style="padding:8px 0"><span>${esc(c.title)}<small>@${esc(c.key)}${c.suggested ? " · подходит" : ""}</small></span><button class="sw" data-cvch="${esc(c.key)}" aria-pressed="${cvState.channels.has(c.key)}" aria-label="${esc(c.title)}"></button></div>`).join("")}` : ""}
      <div class="twobtn"><button class="cta" id="cv-replace">Заменить мои слова</button><button class="ghost" id="cv-merge">Добавить к моим</button></div>
      ${uploadHtml(true)}
      <button class="link" id="cv-delete">Удалить резюме</button></div>`;
    $$("[data-role]", box).forEach((b) => (b.onclick = () => { const id = b.dataset.role; cvState.roles.has(id) ? cvState.roles.delete(id) : cvState.roles.add(id); haptic(); renderCv(); }));
    $$("[data-cvch]", box).forEach((b) => (b.onclick = () => { const k = b.dataset.cvch; cvState.channels.has(k) ? cvState.channels.delete(k) : cvState.channels.add(k); haptic(); renderCv(); }));
    $("#cv-skills").onchange = (e) => { cvState.skills = e.target.checked; renderCv(); };
    $("#cv-replace").onclick = () => applyCv("replace");
    $("#cv-merge").onclick = () => applyCv("merge");
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
    toast(data.cv.roles.some((r) => r.primary) ? "Готово! Проверьте роли и нажмите «Применить»" : "Резюме прочитал, но роли не распознал");
  } catch (e) { cvState.busy = false; renderCv(); fail(e); }
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

/* ---------------------------------------------------------------- start over */

function showReset() {
  st.sheet = { reset: true };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>♻️ Начать заново?</h3>
    <p class="hint" style="font-size:14px">Радар станет как у нового пользователя:</p>
    <ul class="reset-list">
      <li>слова для поиска, стоп-слова, каналы и hh.ru вернутся к примеру «Data Analyst»;</li>
      <li>загруженное резюме удалится;</li>
      <li>стиль сбросится, обучение покажется снова.</li>
    </ul>
    <p class="hint" style="font-size:14px">Найденные вакансии, избранное и «в работе» останутся, если не отметить ниже.</p>
    <label class="check"><input type="checkbox" id="reset-jobs"> Удалить и все мои вакансии: ленту, избранное, «в работе», скрытые</label>
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

const MEMO = () => `<div class="grip"></div><h3>📖 Памятка</h3><div class="memo">
<p><b>Что это.</b> Радар — личный помощник по поиску удалённой работы. Он сам читает каналы с вакансиями, сайты удалёнки и hh.ru и собирает подходящее в одну ленту. Не нужно листать десяток каналов: открыл, разобрал карточки, готово.</p>
<h4>Что уже настроено</h4>
<ul>
<li><b>Профессия:</b> Data Analyst и всё вокруг: аналитик данных, BI, продуктовый аналитик, SQL, ETL, Power BI, Tableau.</li>
<li><b>Формат:</b> удалёнка по всему миру, полный день, частичная занятость и контракт.</li>
<li><b>📡 10 каналов:</b> @foranalysts, @datajob, @datasciencejobs, @analytics_jobs, @remoteit, @relocateme, @geekjobs, @getitrussia, @habr_career, @careerspace.</li>
<li><b>🌐 Сайты:</b> Himalayas, Remote OK, Remotive, We Work Remotely, Working Nomads, Jobicy, Hacker News и hh.ru.</li>
<li><b>#ru_company:</b> метка на вакансиях русскоязычных компаний: Plata, Salmon, Uzum, Kaspi, Яндекс, Ozon, Avito и др.</li>
<li><b>Стоп-слова:</b> стажировки, «только офис», «только для США», резюме соискателей.</li>
</ul>
<p>Это <b>пример</b>. Если вы ищете другое, проще всего загрузить резюме во вкладке «▤ Резюме». Я найду в нём ваши роли и навыки и предложу слова, запрос для hh.ru и каналы, а вы подтвердите. Или поменяйте всё вручную во вкладке «Поиск», в блоке «Что попадает в ленту»: слова, каналы, сайты, запрос для hh.ru.</p>
<h4>Резюме</h4>
<ul>
<li><b>Загрузить</b> готовое (PDF, DOCX, TXT) или <b>собрать с нуля</b> по шагам с подсказками.</li>
<li><b>🎨 Оформление:</b> 4 шаблона (Классика ATS, Современный, Компактный, Минимал), 6 цветов и фото по желанию. Текст при смене шаблона не меняется.</li>
<li><b>Проверка:</b> оценка и конкретные замечания: нет цифр, слабые глаголы, нет контактов, пустые слова.</li>
<li><b>Точная правка:</b> резюме показано так, как будет в PDF. Нажмите на любую строку и поправьте её; ＋ Раздел добавит новый, стрелки ↑ ↓ на заголовке двигают раздел целиком.</li>
<li><b>Под вакансию:</b> вставьте текст вакансии и увидите, каких слов из неё нет в резюме.</li>
<li><b>✨ ИИ</b>: улучшить формулировки, написать «О себе», подогнать под вакансию, перевести на английский, написать сопроводительное. Все правки — предложения: вы принимаете каждую сами. Новые факты и цифры ИИ не придумывает.</li>
<li><b>Скачать:</b> PDF, HTML или текст — файл придёт в чат с ботом.</li>
</ul>
<h4>Как пользоваться</h4>
<ul>
<li><b>Лента:</b> свайп вправо — в работу, влево — скрыть, ★ — в избранное, тап — полный текст и ссылка.</li>
<li><b>Поиск 🎯 Точно:</b> ищет фразу один в один, например «бизнес аналитик».</li>
<li><b>Поиск 🕸 Похожие:</b> добавляет синонимы и смежные роли по графу. Лишнюю роль можно выключить нажатием.</li>
<li><b>Мои:</b> избранное, взятое в работу и скрытое (его можно вернуть).</li>
<li><b>↻</b> на главной: проверить каналы прямо сейчас. <b>☰</b> рядом: вся лента списком, а не карточками.</li>
<li><b>✨ Оценить ИИ</b> на карточке: насколько вакансия подходит именно вам (1–5) и почему. Сравнивает с вашим резюме.</li>
<li><b>💰 и 🕒</b> на карточке: зарплата и часовой пояс, если они есть в тексте. «＋ ещё в…» — та же вакансия из других источников, склеена в одну.</li>
<li><b>📋 Мои отклики</b> во «Ещё»: отмечайте этапы (откликнулся, тестовое, собеседование, оффер, отказ). Если неделю тишина, бот напомнит.</li>
<li><b>🕒 Часовой пояс</b> в «Поиск → Что попадает в ленту»: скрывать вакансии, которым нужен далёкий от вас пояс.</li>
<li><b>🧭 Карьерный план</b> в «Резюме»: цель (грейд, роль, направление), и ИИ по резюме распишет шаги.</li>
</ul>
<h4>Важно знать</h4>
<ul>
<li>Радар только <b>читает</b> открытые каналы и сайты. Он никому не пишет и никуда не откликается: откликаетесь вы сами.</li>
<li>Ваш список видите только вы. Каналы и сайты общие на всех (до 30 каналов), а отметки у каждого свои.</li>
<li>Радар живёт на компьютере владельца. Если приложение не открывается, компьютер выключен или спит: загляните позже, новые вакансии он догонит.</li>
<li>Канал можно добавить свой (до 3 в день), сайт можно предложить владельцу.</li>
<li>Запутались в настройках? «Ещё → ♻️ Начать заново» вернёт всё к примеру. Перед сбросом приложение переспросит.</li>
</ul></div>
<div class="btns"><button class="yes wide" id="memo-ok">Понятно</button></div>`;
function showMemo() {
  st.sheet = { memo: true };
  $("#sheet").innerHTML = MEMO();
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  $("#memo-ok").onclick = closeSheet;
  try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
}

/* ---------------------------------------------------------------- onboarding */

const SLIDES = [
  () => `<div class="pic"><div class="big">📡</div></div><h2>Привет, ${esc(st.me.name)}! Это Радар</h2>
    <p>Я собираю удалённые вакансии из Telegram-каналов, с сайтов и с hh.ru в одну ленту. Проверяю сам, каждые 15 минут, пока включён компьютер.</p>`,
  () => `<div class="pic"><div class="demo-card back"></div><div class="demo-card"><b>Data Analyst</b><span class="co">Uzum</span><i></i><i style="width:70%"></i></div></div>
    <h2>Свайпай карточки</h2><p><b>Вправо</b> — в работу, <b>влево</b> — скрыть, <b>★</b> — в избранное. Тап по карточке открывает полный текст и ссылку.</p>`,
  () => `<div class="pic"><div class="big">🎯 🕸</div></div><h2>Ищи точно или похоже</h2>
    <p><b>Точно</b> — фраза один в один. <b>Похожие роли</b> — граф: синонимы и смежные профессии, например «бизнес-аналитик → системный, продуктовый».</p>`,
  () => `<div class="pic" style="height:auto"><div class="skins" id="onb-skins">${skinsHtml()}</div></div><h2>Выбери стиль</h2>
    <p>Каналы, сайты и слова для ленты — во вкладке «Поиск», внизу. Стиль меняется во вкладке «Ещё» в любой момент.</p>
    <button class="cta" id="onb-cv">📄 Настроить поиск по моему резюме</button>
    <button class="link" id="onb-memo">📖 Памятка: что уже настроено и как всё устроено</button>`,
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
  if (name === "more") { loadTrack(); $("#digest").setAttribute("aria-pressed", !!(st.me && st.me.digest)); }
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
$$("#s-saved .seg button").forEach((b) => (b.onclick = () => { st.saved = b.dataset.v; loadSaved(); }));
$("#b-yes").onclick = () => topCard() && decide(topCard(), true);
$("#b-no").onclick = () => topCard() && decide(topCard(), false);
$("#b-fav").onclick = async () => {
  const c = topCard(); if (!c) return;
  const item = byKey(c.dataset.key);
  try {
    const d = await api(`/api/jobs/${encodeURIComponent(item.key)}/fav`, {});
    item.fav = d.item.fav; haptic(); toast(d.note); renderStack();
  } catch (e) { fail(e); }
};
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
const tzChange = (body) => change(body).then(() => Promise.all([loadFeed(), loadMe()])).catch(() => {});
$("#tz-on").onclick = () => tzChange({ type: "tz", on: $("#tz-on").getAttribute("aria-pressed") !== "true" });
$("#tz-mine").onchange = () => tzChange({ type: "tz", mine: +$("#tz-mine").value });
$("#tz-spread").onchange = () => tzChange({ type: "tz", spread: +$("#tz-spread").value });
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
$("#memo").onclick = showMemo;
$("#reset").onclick = showReset;

async function start() {
  try { setSkin(localStorage.getItem("jr-skin") || "radar"); } catch (e) { setSkin("radar"); }
  if (!INIT) { $("#gate").hidden = false; return; }
  tg.ready();
  tg.expand();
  try { tg.disableVerticalSwipes(); } catch (e) { /* old client: swiping down may close the app */ }
  try { tg.BackButton.onClick(closeSheet); } catch (e) { /* old client */ }
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
  await loadFeed().catch(fail);
  setInterval(() => { // keep the counters fresh while the app is open
    if (document.hidden) return;
    const before = st.me && st.me.stats.new;
    loadMe().then(() => { if (st.me.stats.new !== before && !$("#s-feed").hidden) loadFeed(); }).catch(() => {});
  }, 60000);
}
start();
