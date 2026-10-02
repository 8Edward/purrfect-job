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
const st = { me: null, feed: [], filter: "all", saved: "fav", mode: "exact", off: new Set(), results: [], sources: null, sheet: null };

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
const realComp = (c) => c && !/^не указан/i.test(c);
const metaHtml = (i) => [i.location && "📍 " + i.location, realComp(i.comp) && "💰 " + i.comp].filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join("");
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
  st.feed = (await api("/api/jobs?view=new")).items;
  renderStack();
}
function keep(i) {
  const f = st.filter;
  return f === "all" || (f === "ru" && (i.tags || []).includes("ru-company"))
    || (f === "pt" && (i.tags || []).includes("part-time")) || kindOf(i) === f;
}
function renderStack() {
  const items = st.feed.filter(keep), box = $("#stack");
  if (!items.length) {
    const filtered = st.feed.length && st.filter !== "all";
    box.innerHTML = `<div class="empty"><b>${filtered ? "Тут пусто" : "Всё разобрано"}</b>${filtered
      ? "С этим фильтром новых нет."
      : "Новые вакансии появятся после следующей проверки."}<br><button class="link" id="empty-act">${filtered ? "Показать все" : "Проверить сейчас"}</button></div>`;
    $("#empty-act").onclick = filtered ? () => setFilter("all") : refresh;
    $("#left").textContent = "";
    $("#acts").hidden = true;
    return;
  }
  $("#acts").hidden = false;
  $("#left").textContent = `Ещё ${items.length} · вправо — в работу, влево — скрыть, тап — подробно`;
  box.innerHTML = items.slice(0, 3).reverse().map((i, n, arr) => {
    const depth = arr.length - 1 - n;
    return `<article class="card" data-key="${esc(i.key)}" style="transform:translateY(${depth * 10}px) scale(${1 - depth * 0.04});opacity:${1 - depth * 0.25};z-index:${10 - depth}">
      <span class="verdict yes">В РАБОТУ</span><span class="verdict no">СКРЫТЬ</span>
      <div class="src"><span>${esc(originOf(i))}</span><span>${esc(when(i.date))}${i.fav ? " · ★" : ""}</span></div>
      <h3>${esc(i.title)}</h3>${i.company ? `<div class="co">${esc(i.company)}</div>` : ""}
      <div class="meta">${metaHtml(i)}</div><div class="tags">${tagsHtml(i.tags)}</div>
      <p>${esc(i.text || "Полное описание на сайте.")}</p><span class="more">Подробнее →</span></article>`;
  }).join("");
  const top = box.querySelector(".card:last-child");
  if (top) drag(top);
}
const byKey = (key) => st.feed.find((i) => i.key === key);
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
  let x0 = null, y0 = 0, dx = 0;
  card.addEventListener("pointerdown", (e) => { x0 = e.clientX; y0 = e.clientY; dx = 0; card.classList.add("drag"); card.setPointerCapture(e.pointerId); });
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
    if (tap && e.type === "pointerup") openSheet(byKey(card.dataset.key), false);
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
    <div class="body">${esc(i.text || "Полное описание на сайте: кнопка ниже.")}</div>
    <div class="btns">
      ${fresh ? `<button class="yes" data-a="take">✓ В работу</button><button data-a="hide">✕ Скрыть</button>` : ""}
      ${i.status === "hidden" ? `<button data-a="restore" class="wide">↩ Вернуть в новые</button>` : ""}
      <button data-a="fav" class="${fresh ? "" : "wide"}">${i.fav ? "★ В избранном" : "☆ В избранное"}</button>
      ${i.link ? `<button data-a="open" class="${fresh ? "" : "wide"}">${i.source === "tg" ? "🔗 Пост" : "🔗 Вакансия"}</button>` : ""}
    </div>`;
  $$("#sheet [data-a]").forEach((b) => (b.onclick = () => sheetAction(b.dataset.a)));
}
async function sheetAction(action) {
  const { item, viaSearch } = st.sheet;
  if (action === "open") return openLink(item.link);
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
      <span>${c.removable ? `<button class="x" data-rm="${esc(c.key)}" aria-label="Убрать из общего списка">🗑</button>` : ""}<button class="sw" data-ch="${esc(c.key)}" aria-pressed="${c.on}" aria-label="${esc(c.title)}"></button></span></div>`).join("");
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
<p>Это <b>пример</b>. Если вы ищете другое, поменяйте всё во вкладке «Ещё»: слова, каналы, сайты, запрос для hh.ru.</p>
<h4>Как пользоваться</h4>
<ul>
<li><b>Лента:</b> свайп вправо — в работу, влево — скрыть, ★ — в избранное, тап — полный текст и ссылка.</li>
<li><b>Поиск 🎯 Точно:</b> ищет фразу один в один, например «бизнес аналитик».</li>
<li><b>Поиск 🕸 Похожие:</b> добавляет синонимы и смежные роли по графу. Лишнюю роль можно выключить нажатием.</li>
<li><b>Мои:</b> избранное, взятое в работу и скрытое (его можно вернуть).</li>
<li><b>↻</b> на главной: проверить каналы прямо сейчас.</li>
</ul>
<h4>Важно знать</h4>
<ul>
<li>Радар только <b>читает</b> открытые каналы и сайты. Он никому не пишет и никуда не откликается: откликаетесь вы сами.</li>
<li>Ваш список видите только вы. Каналы и сайты общие на всех (до 30 каналов), а отметки у каждого свои.</li>
<li>Радар живёт на компьютере владельца. Если приложение не открывается, компьютер выключен или спит: загляните позже, новые вакансии он догонит.</li>
<li>Канал можно добавить свой (до 3 в день), сайт можно предложить владельцу.</li>
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
    <p>Каналы, сайты и слова для поиска — во вкладке «Ещё». Там же можно сменить стиль в любой момент.</p>
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
  window.scrollTo(0, 0);
  if (name === "saved") loadSaved();
  if (name === "more") loadSources().catch(fail);
  if (name === "search" && !st.results.length) setTimeout(() => $("#q").focus(), 150);
}
$$(".nav button").forEach((b) => (b.onclick = () => { haptic(); show(b.dataset.s); }));
$$("#filters .chip").forEach((c) => (c.onclick = () => { haptic(); setFilter(c.dataset.f); }));
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
$("#again").onclick = showOnboarding;
$("#memo").onclick = showMemo;

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
