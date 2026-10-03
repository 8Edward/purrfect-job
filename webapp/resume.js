"use strict";
/* ▤ Résumé tab: versions, check (score + remarks), line-by-line editing on a page that looks
   exactly like the PDF, the step-by-step builder, vacancy match, AI proposals, export to chat.
   Uses helpers from app.js: $, $$, esc, api, toast, fail, haptic, plural, closeSheet, setCv. */

const rs = { data: null, view: "start", blocks: [], draft: null, step: 0, skills: [], review: null, job: null, match: null, myJobs: null, showAll: false };
const LEVEL = { must: "Важно", should: "Стоит поправить", nice: "Мелочь" };
const TYPES = [["h1", "Имя"], ["h2", "Раздел"], ["h3", "Должность / место"], ["li", "Пункт"], ["p", "Текст"]];
const WEAK_RE = /^(занимал|занимаюсь|отвечал|участвовал|принимал участие|работал (с|над|в)|выполнял|помогал|делал|обязанности|responsible for|worked (on|with)|helped|participated|involved in|duties|assisted)/i;
const OP_LABEL = { improve: "Улучшаю формулировки", summary: "Пишу «О себе»", tailor: "Подгоняю под вакансию", translate: "Перевожу", cover: "Пишу сопроводительное" };

/* ---------------------------------------------------------------- load and frame */

async function loadResume(keepView) {
  try { rs.data = await api("/api/resume"); } catch (e) { return fail(e); }
  rs.blocks = rs.data.blocks || [];
  if (!rs.data.active) rs.view = ["wizard", "import"].includes(rs.view) ? rs.view : "start";
  else if (!keepView || rs.view === "start") rs.view = "main";
  renderResume();
}
function renderResume() {
  const body = $("#rs-body");
  body.oninput = body.onchange = null;  // the builder sets these; other views don't want them
  renderVersions();
  const v = rs.view;
  if (v === "start") renderStart();
  else if (v === "main") renderMain();
  else if (v === "raw") renderRaw();
  else if (v === "wizard") renderWizard();
  else if (v === "import") renderImport();
  else if (v === "match") renderMatch();
  else if (v === "review") renderReview();
  $("#screens").scrollTop = v === "main" ? $("#screens").scrollTop : 0;
}
function go(view) { rs.view = view; haptic(); renderResume(); $("#screens").scrollTop = 0; }
const backBtn = (to = "main", text = "← Назад") => `<button class="link rs-back" data-go="${to}">${text}</button>`;
function bindCommon(root = $("#rs-body")) {
  $$("[data-go]", root).forEach((b) => (b.onclick = () => go(b.dataset.go)));
  $$("[data-ai]", root).forEach((b) => (b.onclick = () => askAI(b.dataset.ai, b.dataset.focus ? { focus: b.dataset.focus } : {})));
}

function renderVersions() {
  const box = $("#rs-versions"), d = rs.data;
  if (!d || !d.versions.length) { box.innerHTML = ""; return; }
  box.innerHTML = d.versions.map((v) => `<button class="chip" data-v="${esc(v.id)}" aria-pressed="${d.active && v.id === d.active.id}">${v.lang === "en" ? "🇬🇧 " : ""}${esc(v.name)}</button>`).join("")
    + `<button class="chip" id="rs-more" aria-label="Версии">＋ ⋯</button>`;
  $$("[data-v]", box).forEach((b) => (b.onclick = async () => {
    try { rs.data = await api("/api/resume/version", { action: "select", vid: b.dataset.v }); rs.blocks = rs.data.blocks || []; rs.view = "main"; renderResume(); } catch (e) { fail(e); }
  }));
  $("#rs-more").onclick = versionSheet;
}

/* ---------------------------------------------------------------- paper (looks like the PDF) */

function inl(s) {
  let h = esc(s);
  h = h.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|mailto:[^)\s]+)\)/g, '<a href="$2">$1</a>');
  h = h.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  h = h.replace(/(^|[^*\w])\*(?!\s)(.+?)\*(?![*\w])/g, "$1<i>$2</i>");
  return h;
}
const PAGE_W = 794;  // A4 in CSS px, same as resume.py: the page is drawn full size and scaled to the phone
const EDIT_CSS = ".cv [data-i], .cv [data-photo] { cursor: pointer; border-radius: 3px; transition: background .15s; } .cv [data-i].hl { background: #fff1c2 !important; color: #1a1d24; }";
function mountPaper() {
  // The page comes from the server (resume.render) — the very same HTML and CSS the PDF is made of.
  // A shadow root keeps the template's styles and the app's styles apart.
  const host = $("#paper-host");
  if (!host) return;
  let root = host.shadowRoot;
  if (!root) {
    root = host.attachShadow({ mode: "open" });
    root.addEventListener("click", (e) => {  // a shadow root has no onclick property
      const el = e.target.closest("[data-i], [data-photo]");
      if (!el) return;
      e.preventDefault();
      if (el.dataset.photo) photoSheet(); else editBlock(+el.dataset.i);
    });
  }
  root.innerHTML = `<style>${rs.data.paper.css}${EDIT_CSS}</style>${rs.data.paper.html}`;
  const fit = () => { if (host.isConnected) host.style.zoom = Math.min(1, host.parentElement.clientWidth / PAGE_W); };
  fit();
  window.onresize = fit;
}
const paperEl = (i) => { const h = $("#paper-host"); return h && h.shadowRoot ? h.shadowRoot.querySelector(`[data-i="${i}"]`) : null; };
function designHtml() {
  const d = rs.data.design;
  return `<div class="group pad rs-design"><h4>🎨 Оформление</h4>
    <div class="tpls">${rs.data.templates.map((t) => `<button class="tpl" data-tpl="${t.id}" aria-pressed="${d.tpl === t.id}"><i class="tp-${t.id}"></i><b>${esc(t.name)}</b><span>${esc(t.about)}</span></button>`).join("")}</div>
    <div class="dots">${Object.entries(rs.data.colors).map(([k, c]) => `<button class="dot" data-color="${k}" style="--c:${c}" aria-pressed="${d.color === k}" aria-label="Цвет"></button>`).join("")}</div>
    <button class="rs-act wide" id="rs-photo">📷 ${d.has_photo ? (d.photo ? "Фото: изменить или убрать" : "Фото скрыто — показать или изменить") : "Добавить фото (по желанию)"}</button>
    <div class="twobtn"><button class="ghost" id="rs-undo" ${rs.prevDesign && rs.prevDesign.vid === rs.data.active.id ? "" : "disabled"}>↶ Отменить</button>
      <button class="ghost" id="rs-orig" ${isOriginal(d) ? "disabled" : ""}>↺ Исходный вид</button></div></div>`;
}
const ORIGINAL = { tpl: "classic", color: "blue", photo: false };  // how every résumé looks before any design change
const isOriginal = (d) => Object.entries(ORIGINAL).every(([k, v]) => d[k] === v);
async function setDesign(change, undo) {
  const d = rs.data.design, vid = rs.data.active.id;
  try {
    rs.data = await api("/api/resume/design", { vid, ...change });
    rs.prevDesign = undo ? null : { vid, tpl: d.tpl, color: d.color, photo: d.photo };
    rs.blocks = rs.data.blocks; haptic(); renderResume();
  } catch (e) { fail(e); }
}

/* ---------------------------------------------------------------- start: no résumé yet */

function renderStart() {
  $("#rs-body").innerHTML = `<p class="hint" style="font-size:14px">Соберём сильное резюме. Выберите, с чего начать:</p>
    <div class="rs-start">
      <label class="rs-card"><b>📄 Загрузить файл</b><span>PDF, DOCX или TXT. Разберу, оценю и подскажу, что поправить.</span>
        <input type="file" id="rs-file" accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"></label>
      <button class="rs-card" data-go="import"><b>📥 Из hh.ru или LinkedIn</b><span>Нет файла? Скачайте резюме с сайта — покажу, где нажать, — и я заполню всё сам.</span></button>
      <button class="rs-card" data-go="wizard"><b>✍️ Собрать с нуля</b><span>По шагам с подсказками: контакты, опыт, навыки. Минут 10–15.</span></button>
    </div>
    <p class="muted">Резюме хранится на компьютере владельца бота. Удалить можно в любой момент.</p>`;
  bindCommon();
  $("#rs-file").onchange = (e) => e.target.files[0] && uploadFile(e.target.files[0]);
}
/* ---------------------------------------------------------------- import: hh.ru / LinkedIn exports */

const IMPORT_HOW = {
  hh: { name: "hh.ru", steps: [
    "Откройте hh.ru → «Мои резюме» и нажмите на нужное резюме.",
    "Справа (на телефоне — внизу или в меню ⋯) нажмите «Скачать».",
    "Выберите <b>PDF</b> (или «Word» — тоже подойдёт).",
    "Вернитесь сюда и нажмите «Выбрать файл» ниже.",
  ], note: "Лучше всего читается PDF. После загрузки проверьте места работы: на hh даты стоят перед компанией, иногда их надо поправить." },
  li: { name: "LinkedIn (PDF)", steps: [
    "Откройте свой профиль LinkedIn в браузере (в приложении этой кнопки нет).",
    "Под фото нажмите «Ещё» (More) → «Сохранить в PDF» (Save to PDF).",
    "Вернитесь сюда и нажмите «Выбрать файл» ниже.",
  ], note: "Быстро, но PDF у LinkedIn в две колонки — что-то может встать не туда. Самый точный способ — архив данных." },
  zip: { name: "LinkedIn (архив)", steps: [
    "LinkedIn → «Я» (Me) → «Настройки» → «Конфиденциальность данных» (Data privacy).",
    "«Получить копию данных» (Get a copy of your data) → отметьте «Профиль», «Опыт», «Образование», «Навыки» или всё сразу.",
    "Нажмите «Запросить архив». Письмо со ссылкой придёт на почту — от 10 минут до суток.",
    "Скачайте ZIP по ссылке из письма и выберите его здесь, распаковывать не нужно.",
  ], note: "Самый точный способ: должности, даты, описания, навыки и языки берутся из таблиц как есть." },
};
function renderImport() {
  const k = rs.importTab || "hh", how = IMPORT_HOW[k];
  $("#rs-body").innerHTML = `${backBtn(rs.data && rs.data.active ? "main" : "start")}
    <div class="group pad"><h4>📥 Заполнить из hh.ru или LinkedIn</h4>
    <p class="muted">По ссылке на профиль данные забрать нельзя: hh.ru закрыл доступ для соискателей, LinkedIn без входа ничего не отдаёт. Но оба сайта дают скачать своё резюме — а его я разберу.</p>
    <div class="seg three" role="group">${Object.entries(IMPORT_HOW).map(([id, x]) => `<button data-itab="${id}" aria-pressed="${id === k}">${x.name}</button>`).join("")}</div>
    <ol class="steps">${how.steps.map((s) => `<li>${s}</li>`).join("")}</ol>
    <p class="muted">${how.note}</p>
    <label class="rs-card"><b>📎 Выбрать файл</b><span>${k === "zip" ? "ZIP из письма LinkedIn" : "PDF, RTF/DOC (Word), DOCX или TXT"}</span>
      <input type="file" id="imp-file" accept="${k === "zip" ? ".zip,application/zip" : ".pdf,.rtf,.doc,.docx,.txt,application/pdf"}"></label>
    <p class="muted">Ничего не придумываю: в резюме попадёт только то, что есть в файле. Потом покажу оценку и подскажу, что поправить.</p></div>`;
  bindCommon();
  $$("[data-itab]").forEach((b) => (b.onclick = () => { rs.importTab = b.dataset.itab; haptic(); renderImport(); }));
  $("#imp-file").onchange = (e) => e.target.files[0] && importFile(e.target.files[0]);
}
async function importFile(file) {
  if (file.size > 5 * 1024 * 1024) return fail(new Error("Файл больше 5 МБ."));
  $("#rs-body").innerHTML = '<div class="empty"><b>Разбираю файл…</b>Раскладываю по разделам и проверяю.</div>';
  try {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch("/api/resume/import", { method: "POST", headers: { Authorization: "tma " + INIT }, body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Ошибка " + res.status);
    rs.data = data; rs.blocks = data.blocks; haptic("ok");
    toast(`Готово: заполнил из ${data.source}. Проверьте строки — нажмите на любую, чтобы поправить`);
    go("main");
  } catch (e) { fail(e); renderImport(); }
}

async function uploadFile(file) {
  $("#rs-body").innerHTML = '<div class="empty"><b>Читаю резюме…</b>Разбираю текст и проверяю.</div>';
  await uploadCv(file);  // app.js: extracts text, builds the search-preset analysis and a new version
  await loadResume();
}

/* ---------------------------------------------------------------- main: check + paper */

function aiNote() {
  const ai = rs.data.ai;
  if (!ai.allowed) return `<p class="muted">✨ ИИ-помощь выключена. Её включает владелец бота в «👥 Близкие».</p>`;
  if (!ai.ready) return `<p class="muted">✨ ${esc(ai.why || "ИИ не подключён.")}${ai.owner && ai.status === "login" ? " На ноуте дважды кликните <code>telegram\\claude_login.bat</code> и войдите тем же аккаунтом, что в приложении Claude." : ""}</p>`;
  return `<p class="muted">✨ ИИ готов · осталось на сегодня: ${ai.left}. Всё, что он предлагает, вы принимаете сами.</p>`;
}
function aiBtn(op, label, extra = "") {
  const ai = rs.data.ai, off = !ai.allowed || !ai.ready;
  return `<button class="rs-act${off ? " off" : ""}" data-ai="${op}" ${extra}>${label}</button>`;
}
function renderMain() {
  const d = rs.data, doc = d.doctor, lang = d.active.lang;
  const remarks = rs.showAll ? doc.remarks : doc.remarks.slice(0, 3);
  $("#rs-body").innerHTML = `
    <div class="rs-score">
      <div class="ring" style="--p:${doc.score}"><b>${doc.score}</b></div>
      <div><b class="lvl">${esc(doc.level)}</b>
        <span class="muted">${doc.stats.words} слов · пунктов в опыте ${doc.stats.bullets}, с цифрами ${doc.stats.with_numbers}</span>
        ${doc.good.length ? `<div class="oks">${doc.good.map((g) => `<span>✓ ${esc(g)}</span>`).join("")}</div>` : ""}</div>
    </div>
    ${doc.remarks.length ? `<div class="rs-remarks">${remarks.map((r, ri) => `<div class="remark ${r.level}">
        <div class="rh"><i></i><b>${esc(r.title)}</b><small>${LEVEL[r.level]}</small></div>
        <p>${esc(r.detail)}</p>
        ${r.examples.length ? `<ul>${r.examples.map((x, n) => `<li data-ex="${ri}:${n}" title="Поправить эту строку">${esc(x.length > 110 ? x.slice(0, 110) + "…" : x)} <span class="go">✏️</span></li>`).join("")}</ul>` : ""}
        <div class="rfix">${r.ai ? aiBtn(r.ai, r.ai === "summary" ? "✨ Написать с ИИ" : "✨ Исправить с ИИ", `data-focus="${esc(r.title)}"`) : ""}<button class="rs-act" data-hand="${ri}">✏️ Поправить вручную</button></div>
      </div>`).join("")}
      ${doc.remarks.length > 3 ? `<button class="link" id="rs-all">${rs.showAll ? "Свернуть" : `Показать все замечания (${doc.remarks.length})`}</button>` : ""}</div>`
      : `<p class="hint">Замечаний нет — резюме в хорошей форме. 🎉</p>`}
    <div class="rs-grid">
      <button class="rs-act" data-go="match">🎯 Сравнить с вакансией</button>
      ${aiBtn("improve", "✨ Улучшить формулировки")}
      ${aiBtn("summary", "📝 «О себе» с ИИ")}
      ${aiBtn("translate", lang === "en" ? "🌐 Перевести на русский" : "🌐 Перевести на английский")}
      <button class="rs-act" data-go="match" data-cover="1">✉️ Сопроводительное</button>
      <button class="rs-act" id="rs-export">⬇️ Скачать PDF / HTML</button>
    </div>
    ${aiNote()}
    ${designHtml()}
    <div class="rs-paperhead"><b>Ваше резюме</b><span>Нажмите на строку или фото, чтобы поправить · <button class="link" data-go="raw">весь текст</button></span></div>
    <div class="paper-wrap"><div id="paper-host"></div></div>
    <div class="twobtn"><button class="ghost" id="rs-addline">＋ Строка в конец</button><button class="ghost" id="rs-addsec">＋ Раздел</button></div>
    <div class="group" style="margin-top:14px"><h4>🔎 Настроить поиск вакансий по этому резюме</h4><div id="cv-box"></div></div>`;
  bindCommon();
  if ($("#rs-all")) $("#rs-all").onclick = () => { rs.showAll = !rs.showAll; renderMain(); };
  $$("[data-hand]").forEach((b) => (b.onclick = () => fixByHand(remarks[+b.dataset.hand], 0)));
  $$("[data-ex]").forEach((li) => (li.onclick = () => { const [ri, n] = li.dataset.ex.split(":").map(Number); fixByHand(remarks[ri], n); }));
  $$("[data-cover]").forEach((b) => (b.onclick = () => { rs.coverMode = true; go("match"); }));
  $("[data-go=match]").onclick = () => { rs.coverMode = false; go("match"); };
  $("#rs-export").onclick = exportSheet;
  mountPaper();
  $$("[data-tpl]").forEach((b) => (b.onclick = () => setDesign({ tpl: b.dataset.tpl })));
  $$("[data-color]").forEach((b) => (b.onclick = () => setDesign({ color: b.dataset.color })));
  $("#rs-photo").onclick = photoSheet;
  $("#rs-orig").onclick = () => { setDesign(ORIGINAL); toast("Вернул исходный вид: Классика, синий, без фото"); };
  $("#rs-undo").onclick = () => { const p = rs.prevDesign; if (p) setDesign({ tpl: p.tpl, color: p.color, photo: p.photo }, true); };
  $("#rs-addline").onclick = () => { rs.blocks.push({ t: "li", x: "" }); editBlock(rs.blocks.length - 1, true); };
  $("#rs-addsec").onclick = sectionSheet;
  mountCvBox();
}
/* ✏️ «Поправить вручную» on a remark: jump to the line it is about on the page and open its editor;
   for something missing (contacts, «О себе», a section) add an empty line in the right place. */
const plainLine = (s) => String(s || "").replace(/[*_`#\[\]]/g, "").replace(/\(https?:[^)]*\)/g, "").replace(/\s+/g, " ").trim().toLowerCase();
function showLine(i, isNew) {
  const el = paperEl(i);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  haptic();
  setTimeout(() => editBlock(i, isNew), el ? 350 : 0);
}
function headEnd() {  // the line after the name and the contact lines under it
  let e = rs.blocks.findIndex((b) => b.t === "h1") + 1;
  while (e > 0 && e < rs.blocks.length && rs.blocks[e].t === "p") e++;
  return e || 0;
}
function fixByHand(r, n) {
  if (!r) return;
  const ex = r.examples[n] || r.examples[0];
  if (ex) {
    const want = plainLine(ex), word = want.length < 30;
    const i = rs.blocks.findIndex((b) => (word ? plainLine(b.x).includes(want) : plainLine(b.x) === want));
    const j = i >= 0 ? i : rs.blocks.findIndex((b) => plainLine(b.x).startsWith(want.slice(0, 40)));
    if (j >= 0) return showLine(j);
  }
  const en = rs.data.active.lang === "en";
  if (r.id === "name") return rs.blocks[0] && rs.blocks[0].t === "h1" ? showLine(0) : (rs.blocks.unshift({ t: "h1", x: "" }), editBlock(0, true));
  if (r.id === "contacts" || r.id === "where") { const at = headEnd(); rs.blocks.splice(at, 0, { t: "p", x: "" }); return editBlock(at, true); }
  if (r.id === "summary") {
    const at = headEnd();
    rs.blocks.splice(at, 0, { t: "h2", x: en ? "Summary" : "О себе" }, { t: "p", x: "" });
    return editBlock(at + 1, true);
  }
  if (["experience", "skills", "education", "no-bullets"].includes(r.id)) return sectionSheet();
  if (r.id === "dates") { const i = rs.blocks.findIndex((b) => b.t === "h3"); if (i >= 0) return showLine(i); }
  if (r.id.startsWith("summary")) { const i = rs.blocks.findIndex((b, k) => k > 0 && rs.blocks[k - 1].t === "h2" && /о себе|summary|about/i.test(rs.blocks[k - 1].x)); if (i >= 0) return showLine(i); }
  const host = $("#paper-host");  // everything else: the whole text is the place to fix it
  if (host) host.scrollIntoView({ behavior: "smooth", block: "start" });
  toast("Нажмите на строку в резюме, чтобы её поправить");
}
async function mountCvBox() {
  if (rs.data.cv) return setCv(rs.data.cv);
  $("#cv-box").innerHTML = `<div class="cv"><p>Найду в резюме ваши роли и навыки и предложу слова для поиска, запрос на hh.ru и каналы. Ничего не поменяется без вашего «Применить».</p>
    <button class="cta" id="cv-analyze">Подобрать настройки поиска</button></div>`;
  $("#cv-analyze").onclick = async () => {
    try { setCv((await api("/api/cv/from-resume", { vid: rs.data.active.id })).cv); haptic("ok"); } catch (e) { fail(e); }
  };
}

/* ---------------------------------------------------------------- line editor */

function editBlock(i, isNew) {
  const b = rs.blocks[i];
  if (!b) return;
  const host = $("#paper-host");
  if (host && host.shadowRoot) host.shadowRoot.querySelectorAll(".hl").forEach((x) => x.classList.remove("hl"));
  const el = paperEl(i);
  if (el) el.classList.add("hl");
  st.sheet = { block: i };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>Правка строки</h3>
    <div class="chips wrap" id="be-type">${TYPES.map(([t, n]) => `<button class="chip" data-t="${t}" aria-pressed="${b.t === t}">${n}</button>`).join("")}</div>
    <textarea id="be-text" rows="4" placeholder="Текст строки">${esc(b.x)}</textarea>
    <div class="be-tools">
      <button data-fx="b" title="Жирный"><b>Ж</b></button><button data-fx="i" title="Курсив"><i>К</i></button>
      <button data-fx="link" title="Ссылка">🔗</button><button data-fx="up" title="Выше">↑</button><button data-fx="down" title="Ниже">↓</button>
      <button data-fx="add" title="Новая строка ниже">＋</button><button data-fx="del" title="Удалить" class="del">🗑</button>
    </div>
    <div class="be-preview paper" id="be-preview"></div>
    <p class="muted" id="be-hint"></p>${b.t === "h2" ? '<p class="muted">↑ ↓ двигают раздел целиком, со всеми строками.</p>' : ""}
    <div class="btns"><button class="yes" id="be-save">Сохранить</button><button id="be-cancel">Отмена</button></div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  try { tg.BackButton.show(); } catch (e) { /* not in Telegram */ }
  const ta = $("#be-text");
  let type = b.t;
  const preview = () => {
    $("#be-preview").innerHTML = `<${type}>${inl(ta.value) || "&nbsp;"}</${type}>`;
    $("#be-hint").textContent = type === "li" && ta.value && WEAK_RE.test(ta.value.trim())
      ? "Подсказка: начните с действия — Разработал, Построил, Автоматизировал…" : type === "li" && ta.value && !/\d/.test(ta.value) ? "Подсказка: если есть цифра результата — добавьте её." : "";
  };
  preview();
  ta.oninput = preview;
  $$("#be-type [data-t]").forEach((c) => (c.onclick = () => { type = c.dataset.t; $$("#be-type [data-t]").forEach((x) => x.setAttribute("aria-pressed", x === c)); preview(); }));
  const wrap = (l, r, ph) => {
    const s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e) || ph;
    ta.value = ta.value.slice(0, s) + l + sel + r + ta.value.slice(e);
    ta.focus(); ta.selectionStart = s + l.length; ta.selectionEnd = s + l.length + sel.length; preview();
  };
  $$(".be-tools [data-fx]").forEach((t) => (t.onclick = async () => {
    const fx = t.dataset.fx;
    if (fx === "b") return wrap("**", "**", "текст");
    if (fx === "i") return wrap("*", "*", "текст");
    if (fx === "link") return wrap("[", "](https://)", "текст ссылки");
    commit(false);
    if ((fx === "up" || fx === "down") && rs.blocks[i].t === "h2") {
      const to = moveSection(i, fx === "up" ? -1 : 1);
      if (to === null) return toast(fx === "up" ? "Это и так первый раздел" : "Это и так последний раздел");
      await saveBlocks(); return editBlock(to);
    }
    if (fx === "up" && i > 0) { [rs.blocks[i - 1], rs.blocks[i]] = [rs.blocks[i], rs.blocks[i - 1]]; await saveBlocks(); return editBlock(i - 1); }
    if (fx === "down" && i < rs.blocks.length - 1) { [rs.blocks[i + 1], rs.blocks[i]] = [rs.blocks[i], rs.blocks[i + 1]]; await saveBlocks(); return editBlock(i + 1); }
    if (fx === "add") { rs.blocks.splice(i + 1, 0, { t: rs.blocks[i].t === "li" ? "li" : "p", x: "" }); return editBlock(i + 1, true); }
    if (fx === "del") {
      if (!t.dataset.sure) { t.dataset.sure = "1"; t.textContent = "Удалить?"; return; }
      rs.blocks.splice(i, 1); closeSheet(); await saveBlocks(); toast("Строку удалил");
    }
  }));
  const commit = (close) => {
    rs.blocks[i] = { t: type, x: ta.value.replace(/\s+/g, " ").trim() };
    if (close) closeSheet();
  };
  $("#be-save").onclick = async () => { commit(true); rs.blocks = rs.blocks.filter((x) => x.x); await saveBlocks(); haptic("ok"); };
  $("#be-cancel").onclick = () => { if (isNew && !rs.blocks[i].x) rs.blocks.splice(i, 1); closeSheet(); renderMain(); };
  if (isNew) setTimeout(() => ta.focus(), 200);
}
function sectionEnd(i) {
  let e = i + 1;
  while (e < rs.blocks.length && !["h1", "h2"].includes(rs.blocks[e].t)) e++;
  return e;
}
function moveSection(i, dir) {
  // [i, end) is this section; swap it with the whole previous or next section. Returns its new start.
  const b = rs.blocks, end = sectionEnd(i);
  if (dir < 0) {
    let p = i - 1;
    while (p >= 0 && b[p].t !== "h2") p--;
    if (p < 0) return null;
    rs.blocks = [...b.slice(0, p), ...b.slice(i, end), ...b.slice(p, i), ...b.slice(end)];
    return p;
  }
  if (end >= b.length || b[end].t !== "h2") return null;
  const nEnd = sectionEnd(end);
  rs.blocks = [...b.slice(0, i), ...b.slice(end, nEnd), ...b.slice(i, end), ...b.slice(nEnd)];
  return i + (nEnd - end);
}
const SECTIONS = {
  ru: ["О себе", "Опыт работы", "Образование", "Навыки", "Языки", "Проекты", "Курсы и сертификаты", "Достижения"],
  en: ["Summary", "Work Experience", "Education", "Skills", "Languages", "Projects", "Courses & Certifications", "Achievements"],
};
function sectionSheet() {
  const have = new Set(rs.blocks.filter((b) => b.t === "h2").map((b) => b.x.toLowerCase()));
  const list = SECTIONS[rs.data.active.lang === "en" ? "en" : "ru"].filter((x) => !have.has(x.toLowerCase()));
  st.sheet = { section: true };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>＋ Новый раздел</h3>
    <div class="chips wrap">${list.map((x) => `<button class="chip" data-sec="${esc(x)}">${esc(x)}</button>`).join("")}</div>
    <form class="add" id="sec-own" style="padding:0"><input placeholder="Или свой: Волонтёрство, Публикации…" maxlength="60"><button>+</button></form>
    <p class="muted">Раздел встанет в конец — потом его можно передвинуть: нажмите на заголовок и стрелки ↑ ↓.</p>
    <div class="btns"><button class="wide" id="sec-close">Закрыть</button></div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  const add = async (name) => {
    name = name.trim();
    if (!name) return;
    closeSheet();
    rs.blocks.push({ t: "h2", x: name });
    await saveBlocks();
    rs.blocks.push({ t: /навык|skill|язык|language/i.test(name) ? "p" : "li", x: "" });
    editBlock(rs.blocks.length - 1, true);
  };
  $$("[data-sec]").forEach((b) => (b.onclick = () => add(b.dataset.sec)));
  $("#sec-own").onsubmit = (e) => { e.preventDefault(); add($("input", e.target).value); };
  $("#sec-close").onclick = closeSheet;
}

/* ---------------------------------------------------------------- photo: pick, crop, shape */

function photoSheet() {
  const d = rs.data.design;
  st.sheet = { photo: true };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>📷 Фото в резюме</h3>
    <p class="muted">По желанию. В США и Великобритании фото обычно не ставят, в СНГ и Азии — часто. Роботы-фильтры (ATS) фото не читают.</p>
    <div class="crop" id="crop" hidden><img id="crop-img" alt=""></div>
    <input type="range" id="crop-zoom" min="1" max="3" step="0.01" value="1" hidden aria-label="Масштаб">
    <p class="muted" id="crop-hint" hidden>Двигайте фото пальцем, ползунком — приблизить.</p>
    <label class="rs-card"><b>${d.has_photo ? "🖼 Выбрать другое фото" : "🖼 Выбрать фото"}</b><span>JPG или PNG из галереи</span><input type="file" id="ph-file" accept="image/*"></label>
    <div class="seg" role="group" aria-label="Форма"><button data-shape="round" aria-pressed="${d.shape !== "square"}">● Круг</button><button data-shape="square" aria-pressed="${d.shape === "square"}">■ Квадрат</button></div>
    ${d.has_photo ? `<div class="tg"><span>Показывать фото в этой версии</span><button class="sw" id="ph-show" aria-pressed="${d.photo}" aria-label="Показывать фото"></button></div>` : ""}
    <div class="btns"><button class="yes" id="ph-save" hidden>Сохранить фото</button><button id="ph-close">Закрыть</button></div>
    ${d.has_photo ? '<button class="danger-btn" id="ph-del">Удалить фото совсем</button>' : ""}`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  $("#ph-close").onclick = closeSheet;
  $$("[data-shape]").forEach((b) => (b.onclick = () => {
    $$("[data-shape]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    $("#crop").classList.toggle("square", b.dataset.shape === "square");
    if ($("#ph-save").hidden && d.has_photo) setDesign({ shape: b.dataset.shape });
  }));
  if ($("#ph-show")) $("#ph-show").onclick = () => { closeSheet(); setDesign({ photo: !d.photo }); };
  if ($("#ph-del")) $("#ph-del").onclick = async (e) => {
    if (!e.target.dataset.sure) { e.target.dataset.sure = "1"; e.target.textContent = "Точно удалить? Нажмите ещё раз"; return; }
    try { rs.data = await api("/api/resume/photo", { remove: true }); rs.blocks = rs.data.blocks; closeSheet(); renderResume(); toast("Фото удалил"); } catch (err) { fail(err); }
  };
  $("#ph-file").onchange = (e) => e.target.files[0] && startCrop(e.target.files[0]);
}
function startCrop(file) {
  const V = 260, img = $("#crop-img"), box = $("#crop"), zoom = $("#crop-zoom");
  const c = { x: 0, y: 0, s: 1, base: 1 };
  const url = URL.createObjectURL(file);
  const clamp = () => {
    const w = img.naturalWidth * c.s, h = img.naturalHeight * c.s;
    c.x = Math.min(0, Math.max(V - w, c.x)); c.y = Math.min(0, Math.max(V - h, c.y));
    img.style.transform = `translate(${c.x}px, ${c.y}px) scale(${c.s})`;
  };
  img.onerror = () => toast("Не получилось открыть картинку. Нужен JPG или PNG.");
  img.onload = () => {
    box.hidden = zoom.hidden = $("#crop-hint").hidden = $("#ph-save").hidden = false;
    box.classList.toggle("square", $('[data-shape="square"]').getAttribute("aria-pressed") === "true");
    c.base = c.s = V / Math.min(img.naturalWidth, img.naturalHeight);
    c.x = (V - img.naturalWidth * c.s) / 2; c.y = (V - img.naturalHeight * c.s) / 2;
    zoom.value = 1;
    clamp();
  };
  img.src = url;
  zoom.oninput = () => {
    const ns = c.base * +zoom.value, k = ns / c.s;
    c.x = V / 2 - (V / 2 - c.x) * k; c.y = V / 2 - (V / 2 - c.y) * k; c.s = ns; clamp();
  };
  let drag = null;
  box.onpointerdown = (e) => { drag = { x: e.clientX, y: e.clientY, cx: c.x, cy: c.y }; box.setPointerCapture(e.pointerId); };
  box.onpointermove = (e) => { if (!drag) return; c.x = drag.cx + e.clientX - drag.x; c.y = drag.cy + e.clientY - drag.y; clamp(); };
  box.onpointerup = box.onpointercancel = () => (drag = null);
  $("#ph-save").onclick = async () => {
    const cv = document.createElement("canvas");
    cv.width = cv.height = 400;
    cv.getContext("2d").drawImage(img, -c.x / c.s, -c.y / c.s, V / c.s, V / c.s, 0, 0, 400, 400);
    const shape = $('[data-shape="square"]').getAttribute("aria-pressed") === "true" ? "square" : "round";
    try {
      $("#ph-save").disabled = true;
      await api("/api/resume/photo", { vid: rs.data.active.id, data: cv.toDataURL("image/jpeg", 0.88) });
      URL.revokeObjectURL(url);
      closeSheet(); toast("Фото на месте");
      await setDesign({ shape, photo: true });
    } catch (err) { $("#ph-save").disabled = false; fail(err); }
  };
}
async function saveBlocks() {
  try {
    rs.data = await api("/api/resume/save", { vid: rs.data.active.id, blocks: rs.blocks.filter((x) => x.x) });
    rs.blocks = rs.data.blocks;
    renderResume();
  } catch (e) { fail(e); }
}

/* ---------------------------------------------------------------- whole text */

function renderRaw() {
  $("#rs-body").innerHTML = `${backBtn()}<div class="group pad"><h4>Весь текст резюме</h4>
    <p class="muted">Разметка: <code># Имя</code>, <code>## Раздел</code>, <code>### Должность — Компания</code>, <code>- пункт</code>, <code>**жирный**</code>.</p>
    <textarea id="raw" class="big">${esc(rs.data.markdown)}</textarea>
    <div class="twobtn"><button class="cta" id="raw-save">Сохранить</button><button class="ghost" data-go="main">Отмена</button></div></div>`;
  bindCommon();
  $("#raw-save").onclick = async () => {
    try { rs.data = await api("/api/resume/save", { vid: rs.data.active.id, markdown: $("#raw").value }); rs.blocks = rs.data.blocks; haptic("ok"); toast("Сохранил"); go("main"); } catch (e) { fail(e); }
  };
}

/* ---------------------------------------------------------------- builder from scratch */

const STEPS = ["Контакты", "Опыт", "Образование", "Навыки", "Языки", "О себе", "Готово"];
function blankDraft() {
  return { lang: "ru", name: "", title: "", location: "", email: "", telegram: "", phone: "", linkedin: "", github: "",
    jobs: [{ role: "", company: "", place: "", from: "", to: "", bullets: [{ action: "", how: "", result: "" }] }],
    education: [{ school: "", degree: "", year: "" }], skills: [], languages: [{ name: "Русский", level: "Родной" }, { name: "Английский", level: "" }],
    wants: [], summary: "" };
}
async function startWizard() {
  try {
    const d = await api("/api/resume/draft");
    rs.draft = d.draft && Object.keys(d.draft).length ? d.draft : blankDraft();
    rs.skills = d.skills;
  } catch (e) { rs.draft = blankDraft(); }
  rs.step = rs.draft._step || 0;
}
function composeBullet(b, lang) {
  const a = (b.action || "").trim().replace(/[.;,]+$/, ""), how = (b.how || "").trim().replace(/[.;,]+$/, ""), res = (b.result || "").trim().replace(/[.;,]+$/, "");
  if (!a) return "";
  let line = a[0].toUpperCase() + a.slice(1) + (how ? " " + how : "");
  if (res) line += (/^[а-яё]+(в|вши)\b|^(saving|cutting|reducing|increasing)\b/i.test(res) ? ", " : lang === "ru" ? ": " : ", ") + res;
  return line;
}
const fld = (k, label, ph = "", extra = "") => `<label class="fld"><span>${label}</span><input class="inp" data-k="${k}" value="${esc(rs.draft[k] || "")}" placeholder="${esc(ph)}" ${extra}></label>`;
function renderWizard() {
  if (!rs.draft) { startWizard().then(renderWizard); $("#rs-body").innerHTML = '<div class="skeleton"></div>'; return; }
  const d = rs.draft, s = rs.step, en = d.lang === "en";
  let html = `<div class="wz-head">${backBtn(rs.data && rs.data.active ? "main" : "start", "✕ Закрыть")}<span>Шаг ${s + 1} из ${STEPS.length} · ${STEPS[s]}</span></div>
    <div class="wz-bar"><i style="width:${Math.round(((s + 1) / STEPS.length) * 100)}%"></i></div><div class="group pad">`;
  if (s === 0) {
    html += `<div class="seg" role="group"><button data-lang="ru" aria-pressed="${!en}">Резюме на русском</button><button data-lang="en" aria-pressed="${en}">In English</button></div>
      ${fld("name", "Имя и фамилия", en ? "Eduard Dunaev" : "Эдуард Дунаев")}
      ${fld("title", "Кем хотите работать", en ? "Data Analyst" : "Аналитик данных")}
      ${fld("location", "Где вы и формат", en ? "Thailand (UTC+7) · Remote, any time zone" : "Таиланд (UTC+7) · удалённо, любой часовой пояс")}
      ${fld("email", "Email", "name@gmail.com", 'type="email"')}${fld("telegram", "Telegram", "@username")}
      ${fld("phone", "Телефон (необязательно)", "+66 …")}${fld("linkedin", "LinkedIn / GitHub / портфолио (необязательно)", "linkedin.com/in/…")}
      <p class="muted">Для удалёнки важно указать часовой пояс — это спрашивают первым.</p>`;
  } else if (s === 1) {
    html += `<p class="muted">Начните с последнего места. На каждый пункт: <b>что сделали</b> → <b>чем</b> → <b>какой результат</b> (цифры, если есть).</p>`;
    d.jobs.forEach((j, ji) => {
      html += `<div class="wz-job"><div class="rh"><b>Место работы ${ji + 1}</b>${d.jobs.length > 1 ? `<button class="link" data-deljob="${ji}">Убрать</button>` : ""}</div>
        <input class="inp" data-j="${ji}" data-k="role" value="${esc(j.role)}" placeholder="${en ? "Data Analyst" : "Должность: Аналитик данных"}">
        <input class="inp" data-j="${ji}" data-k="company" value="${esc(j.company)}" placeholder="${en ? "Company" : "Компания"}">
        <input class="inp" data-j="${ji}" data-k="place" value="${esc(j.place)}" placeholder="${en ? "City / Remote" : "Город или «удалённо»"}">
        <div class="two"><input class="inp" data-j="${ji}" data-k="from" value="${esc(j.from)}" placeholder="${en ? "Mar 2024" : "Март 2024"}">
          <input class="inp" data-j="${ji}" data-k="to" value="${esc(j.to)}" placeholder="${en ? "Present" : "наст. время"}"></div>
        ${j.bullets.map((b, bi) => `<div class="wz-bullet">
          <input class="inp" data-j="${ji}" data-b="${bi}" data-k="action" value="${esc(b.action)}" placeholder="${en ? "What you did: Automated weekly reports" : "Что сделали: Автоматизировал выгрузку отчётов"}">
          <input class="inp" data-j="${ji}" data-b="${bi}" data-k="how" value="${esc(b.how)}" placeholder="${en ? "With what: in Python (Pandas)" : "Чем: на Python (Pandas)"}">
          <input class="inp" data-j="${ji}" data-b="${bi}" data-k="result" value="${esc(b.result)}" placeholder="${en ? "Result: cutting time from 1 h to 15 min" : "Результат: время сократилось с 1 часа до 15 минут"}">
          <div class="line" id="bl-${ji}-${bi}"></div></div>`).join("")}
        <button class="link" data-addb="${ji}">＋ ещё пункт</button></div>`;
    });
    html += `<button class="ghost wide" id="wz-addjob">＋ Ещё место работы</button>`;
  } else if (s === 2) {
    d.education.forEach((e, ei) => {
      html += `<div class="wz-job"><input class="inp" data-e="${ei}" data-k="school" value="${esc(e.school)}" placeholder="${en ? "Bauman Moscow State Technical University" : "Вуз: МГТУ им. Баумана"}">
        <input class="inp" data-e="${ei}" data-k="degree" value="${esc(e.degree)}" placeholder="${en ? "Bachelor's, Control Systems" : "Степень и специальность"}">
        <input class="inp" data-e="${ei}" data-k="year" value="${esc(e.year)}" placeholder="${en ? "2023" : "Год окончания"}"></div>`;
    });
    html += `<button class="link" id="wz-addedu">＋ Ещё учёба (курсы, второе образование)</button>`;
  } else if (s === 3) {
    const pool = [...new Set([...(rs.skills || []), ...d.skills])];
    html += `<p class="muted">Отметьте, с чем реально работаете, и добавьте своё. По этим словам резюме находят фильтры.</p>
      <div class="chips wrap">${pool.map((k) => `<button class="chip" data-skill="${esc(k)}" aria-pressed="${d.skills.includes(k)}">${esc(k)}</button>`).join("")}</div>
      <form class="add" id="wz-skill" style="padding:0"><input placeholder="Своё через запятую: Airflow, Greenplum"><button>+</button></form>`;
  } else if (s === 4) {
    const levels = en ? ["", "Native", "C2", "C1", "B2", "B1", "A2"] : ["", "Родной", "C2", "C1", "B2", "B1", "A2"];
    d.languages.forEach((l, li) => {
      html += `<div class="two"><input class="inp" data-l="${li}" data-k="name" value="${esc(l.name)}" placeholder="${en ? "English" : "Язык"}">
        <select class="inp" data-l="${li}" data-k="level">${levels.map((x) => `<option ${x === l.level ? "selected" : ""} value="${x}">${x || "уровень"}</option>`).join("")}</select></div>`;
    });
    html += `<button class="link" id="wz-addlang">＋ Ещё язык</button>`;
  } else if (s === 5) {
    const wants = en ? ["full-time", "part-time", "contract"] : ["полная занятость", "частичная занятость", "контракт"];
    html += `<p class="muted">2–3 предложения: кто вы, сколько опыта, с чем работаете, что ищете. Без «ответственный и коммуникабельный».</p>
      <div class="chips wrap">${wants.map((w) => `<button class="chip" data-want="${w}" aria-pressed="${d.wants.includes(w)}">${w}</button>`).join("")}</div>
      <textarea id="wz-summary" class="inp" rows="5" placeholder="Нажмите «Подставить заготовку» или напишите сами">${esc(d.summary)}</textarea>
      <button class="link" id="wz-suggest">Подставить заготовку из ответов</button>
      ${rs.data && rs.data.ai && rs.data.ai.ready && rs.data.ai.allowed ? '<p class="muted">После сборки можно попросить ИИ сделать «О себе» сильнее.</p>' : ""}`;
  } else {
    const jobs = d.jobs.filter((j) => j.role || j.company).length;
    html += `<p>Готово к сборке: ${esc(d.name || "без имени")}, ${jobs} ${plural(jobs, "место", "места", "мест")} работы, навыков: ${d.skills.length}.</p>
      <label class="fld"><span>Название версии</span><input class="inp" id="wz-vname" value="${rs.data && rs.data.versions.length ? "Новая версия" : "Основное"}" maxlength="40"></label>
      <button class="cta wide" id="wz-build">Собрать резюме</button>
      <p class="muted">После сборки покажу оценку и замечания. Любую строку можно будет поправить.</p>`;
  }
  html += `</div><div class="twobtn">${s > 0 ? '<button class="ghost" id="wz-prev">← Назад</button>' : "<span></span>"}${s < STEPS.length - 1 ? '<button class="cta" id="wz-next">Дальше →</button>' : ""}</div>`;
  $("#rs-body").innerHTML = html;
  bindCommon();
  bindWizard();
}
function bindWizard() {
  const d = rs.draft, body = $("#rs-body");
  const lines = () => d.jobs.forEach((j, ji) => j.bullets.forEach((b, bi) => {
    const el = $(`#bl-${ji}-${bi}`);
    if (!el) return;
    const line = composeBullet(b, d.lang);
    const hint = line && WEAK_RE.test(line) ? " ⚠️ начните с действия" : line && !/\d/.test(line) ? " · цифра усилит пункт" : "";
    el.innerHTML = line ? `→ ${esc(line)}<small>${hint}</small>` : "";
  }));
  lines();
  body.oninput = (e) => {
    const t = e.target, k = t.dataset.k;
    if (!k) return;
    if (t.dataset.b !== undefined) d.jobs[+t.dataset.j].bullets[+t.dataset.b][k] = t.value;
    else if (t.dataset.j !== undefined) d.jobs[+t.dataset.j][k] = t.value;
    else if (t.dataset.e !== undefined) d.education[+t.dataset.e][k] = t.value;
    else if (t.dataset.l !== undefined) d.languages[+t.dataset.l][k] = t.value;
    else d[k] = t.value;
    lines();
  };
  body.onchange = body.oninput;
  $$("[data-lang]").forEach((b) => (b.onclick = () => { d.lang = b.dataset.lang; renderWizard(); }));
  $$("[data-addb]").forEach((b) => (b.onclick = () => { d.jobs[+b.dataset.addb].bullets.push({ action: "", how: "", result: "" }); renderWizard(); }));
  $$("[data-deljob]").forEach((b) => (b.onclick = () => { d.jobs.splice(+b.dataset.deljob, 1); renderWizard(); }));
  if ($("#wz-addjob")) $("#wz-addjob").onclick = () => { d.jobs.push({ role: "", company: "", place: "", from: "", to: "", bullets: [{ action: "", how: "", result: "" }] }); renderWizard(); };
  if ($("#wz-addedu")) $("#wz-addedu").onclick = () => { d.education.push({ school: "", degree: "", year: "" }); renderWizard(); };
  if ($("#wz-addlang")) $("#wz-addlang").onclick = () => { d.languages.push({ name: "", level: "" }); renderWizard(); };
  $$("[data-skill]").forEach((b) => (b.onclick = () => { const k = b.dataset.skill; d.skills = d.skills.includes(k) ? d.skills.filter((x) => x !== k) : [...d.skills, k]; b.setAttribute("aria-pressed", d.skills.includes(k)); }));
  if ($("#wz-skill")) $("#wz-skill").onsubmit = (e) => { e.preventDefault(); const v = $("input", e.target).value; d.skills = [...new Set([...d.skills, ...v.split(",").map((x) => x.trim()).filter(Boolean)])]; renderWizard(); };
  $$("[data-want]").forEach((b) => (b.onclick = () => { const w = b.dataset.want; d.wants = d.wants.includes(w) ? d.wants.filter((x) => x !== w) : [...d.wants, w]; b.setAttribute("aria-pressed", d.wants.includes(w)); }));
  if ($("#wz-summary")) $("#wz-summary").oninput = (e) => (d.summary = e.target.value);
  if ($("#wz-suggest")) $("#wz-suggest").onclick = async () => { const r = await saveDraft(); if (r) { d.summary = r.summary; renderWizard(); } };
  if ($("#wz-prev")) $("#wz-prev").onclick = () => { rs.step--; saveDraft(); renderWizard(); $("#screens").scrollTop = 0; };
  if ($("#wz-next")) $("#wz-next").onclick = async () => {
    if (rs.step === 0 && !d.name.trim()) return toast("Напишите имя — с него начинается резюме");
    rs.step++;
    const r = await saveDraft();
    if (r && rs.step === 3) rs.skills = r.skills;
    renderWizard();
    $("#screens").scrollTop = 0;
  };
  if ($("#wz-build")) $("#wz-build").onclick = async () => {
    try {
      d.jobs.forEach((j) => (j.bullets = j.bullets.filter((b) => b.action)));
      rs.data = await api("/api/resume/build", { draft: d, name: $("#wz-vname").value });
      rs.blocks = rs.data.blocks; rs.draft = null; haptic("ok"); toast("Резюме собрано! Вот оценка и подсказки"); go("main");
    } catch (e) { fail(e); }
  };
}
async function saveDraft() {
  try { rs.draft._step = rs.step; return await api("/api/resume/draft", { draft: rs.draft }); } catch (e) { fail(e); return null; }
}

/* ---------------------------------------------------------------- vacancy match */

async function renderMatch() {
  if (!rs.myJobs) {
    try { rs.myJobs = (await api("/api/jobs?view=all")).items.slice(0, 40); } catch (e) { rs.myJobs = []; }
  }
  const m = rs.match;
  $("#rs-body").innerHTML = `${backBtn()}<div class="group pad"><h4>${rs.coverMode ? "✉️ Сопроводительное письмо" : "🎯 Сравнить с вакансией"}</h4>
    <select class="inp" id="mt-job"><option value="">— выбрать из моих вакансий —</option>${rs.myJobs.map((j) => `<option value="${esc(j.key)}">${esc((j.title || "").slice(0, 60))} · ${esc((j.company || j.origin || "").slice(0, 25))}</option>`).join("")}</select>
    <textarea id="mt-text" class="inp" rows="6" placeholder="…или вставьте сюда текст вакансии">${esc(rs.matchText || "")}</textarea>
    ${rs.coverMode ? aiBtn("cover", "✉️ Написать сопроводительное (ИИ)") : `<button class="cta wide" id="mt-go">Сравнить</button>`}</div>
    ${m ? `<div class="group pad"><div class="rs-score small"><div class="ring" style="--p:${m.score}"><b>${m.score}%</b></div>
      <div><b>Совпадение по ключевым словам</b><span class="muted">${m.role ? "Роль в вакансии: " + esc(m.role) + ". " : ""}Есть: ${m.present.length}, нет: ${m.missing.length}.</span></div></div>
      ${m.missing.length ? `<p class="lbl">Нет в резюме</p><div class="chips wrap">${m.missing.map((x) => `<span class="chip miss">${esc(x.term)}</span>`).join("")}</div>
        <p class="muted">Добавляйте только то, с чем правда работали: в опыт, навыки или «О себе». Роботы-фильтры ищут именно эти слова.</p>` : ""}
      ${m.present.length ? `<p class="lbl">Уже есть</p><div class="chips wrap">${m.present.map((x) => `<span class="chip hit">✓ ${esc(x.term)}</span>`).join("")}</div>` : ""}
      <div class="rs-grid">${aiBtn("tailor", "✨ Подогнать резюме (ИИ)")}${aiBtn("cover", "✉️ Сопроводительное (ИИ)")}</div></div>` : ""}`;
  bindCommon();
  $("#mt-job").onchange = (e) => (rs.matchKey = e.target.value);
  $("#mt-text").oninput = (e) => (rs.matchText = e.target.value);
  if ($("#mt-go")) $("#mt-go").onclick = async () => {
    try {
      rs.match = await api("/api/resume/match", { vid: rs.data.active.id, vacancy: rs.matchText || "", job_key: rs.matchKey || "" });
      rs.matchText = rs.match.vacancy; haptic(); renderMatch();
    } catch (e) { fail(e); }
  };
}

/* ---------------------------------------------------------------- AI: start, wait, review */

function askAI(op, extra = {}) {
  const ai = rs.data.ai;
  if (!ai.allowed) return toast("ИИ для вас выключен. Его включает владелец бота в «👥 Близкие».");
  if (!ai.ready) return toast(ai.why || "ИИ не подключён");
  if (op === "translate") extra.lang = rs.data.active.lang === "en" ? "ru" : "en";
  if (op === "tailor" || op === "cover") {
    extra.vacancy = rs.matchText || ($("#mt-text") && $("#mt-text").value) || "";
    extra.job_key = rs.matchKey || "";
    if (extra.vacancy.length < 80 && !extra.job_key) return toast("Сначала выберите вакансию или вставьте её текст");
  }
  runAI(op, extra);
}
async function runAI(op, extra) {
  if (rs.job) return toast("ИИ ещё работает над прошлым запросом");
  let started;
  try { started = await api("/api/resume/ai", { op, vid: rs.data.active.id, ...extra }); } catch (e) { return fail(e); }
  rs.job = { id: started.job, op, vid: rs.data.active.id };
  st.sheet = { ai: true };
  $("#sheet").innerHTML = `<div class="grip"></div><div class="ai-wait"><div class="spinner"></div><h3>✨ ${OP_LABEL[op]}…</h3>
    <p class="muted">${started.queued ? "Сначала закончу чужой запрос, потом ваш. " : ""}Обычно 20–90 секунд. Можно свернуть — я сообщу, когда будет готово.</p>
    <div class="btns"><button class="wide" id="ai-hide">Свернуть</button></div></div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  $("#ai-hide").onclick = closeSheet;
  pollAI();
}
async function pollAI() {
  if (!rs.job) return;
  let j;
  try { j = await api(`/api/resume/ai/${rs.job.id}`); } catch (e) { rs.job = null; closeAiSheet(); return fail(e); }
  if (j.status === "running") return setTimeout(pollAI, 2500);
  const job = rs.job;
  rs.job = null;
  closeAiSheet();
  if (j.status === "error") { haptic("error"); return toast(j.error); }
  haptic("ok");
  rs.review = { op: job.op, vid: job.vid, result: j.result };
  if (job.op === "translate") await loadResume(true);
  toast("✨ Готово — посмотрите предложения");
  if ($("#s-resume").hidden) show("resume");
  go("review");
}
function closeAiSheet() { if (st.sheet && st.sheet.ai) closeSheet(); }

function renderReview() {
  const r = rs.review;
  if (!r) return go("main");
  const res = r.result;
  let html = backBtn("main", "← К резюме");
  if (r.op === "improve" || r.op === "tailor") {
    html += `<div class="group pad"><h4>${r.op === "tailor" ? "✨ Под вакансию" : "✨ Предложения по формулировкам"}</h4>
      <p class="muted">Отметьте, что принять. Текст «после» можно поправить прямо здесь.${res.dropped ? ` ${res.dropped} ${plural(res.dropped, "предложение отбросил", "предложения отбросил", "предложений отбросил")}: в них были факты или цифры, которых нет в резюме.` : ""}</p>`;
    if (res.summary) html += `<div class="chg"><label class="check"><input type="checkbox" id="rv-sum" checked> Новое «О себе»</label><textarea class="inp" id="rv-sumtext" rows="4">${esc(res.summary)}</textarea></div>`;
    html += res.changes.length ? res.changes.map((c, n) => `<div class="chg"><label class="check"><input type="checkbox" data-n="${n}" checked> ${esc(c.why || "Улучшение")}</label>
        <div class="before">${esc(c.before)}</div><textarea class="inp" data-after="${n}" rows="3">${esc(c.after)}</textarea></div>`).join("")
      : '<p class="hint">ИИ не нашёл, что улучшить без выдумок. Значит, формулировки уже крепкие.</p>';
    if (res.questions.length) html += `<p class="lbl">ИИ хочет уточнить</p><ul class="reset-list">${res.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>
      <p class="muted">Если знаете ответ — впишите цифру в нужную строку резюме сами.</p>`;
    html += `<button class="cta wide" id="rv-apply">Применить выбранное</button></div>`;
  } else if (r.op === "summary") {
    html += `<div class="group pad"><h4>📝 Варианты «О себе»</h4><p class="muted">Выберите один — он заменит текущий блок. Можно поправить перед заменой.</p>
      ${res.variants.map((v, n) => `<div class="chg"><textarea class="inp" data-var="${n}" rows="4">${esc(v)}</textarea><button class="ghost" data-take="${n}">Взять этот</button></div>`).join("") || '<p class="hint">Не получилось без выдумок. Попробуйте ещё раз.</p>'}</div>`;
  } else if (r.op === "translate") {
    html += `<div class="group pad"><h4>🌐 Перевод готов: «${esc(res.name)}»</h4>
      ${res.problems.length ? `<p class="lbl">Проверка нашла, что посмотреть</p><ul class="reset-list">${res.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
        <p class="muted">Откройте перевод и поправьте эти места — нажмите на строку.</p>` : '<p class="hint">✓ Проверка: все числа, даты, контакты и названия на месте, разделы и пункты совпадают.</p>'}
      <button class="cta wide" id="rv-open">Открыть перевод</button></div>`;
  } else if (r.op === "cover") {
    html += `<div class="group pad"><h4>✉️ Сопроводительное письмо</h4>${res.warning ? `<p class="muted">⚠️ ${esc(res.warning)}</p>` : ""}
      <textarea class="inp big" id="rv-cover">${esc(res.text)}</textarea><button class="cta wide" id="rv-copy">Скопировать</button></div>`;
  }
  $("#rs-body").innerHTML = html;
  bindCommon();
  if ($("#rv-apply")) $("#rv-apply").onclick = async () => {
    const changes = res.changes.map((c, n) => ({ ...c, after: $(`[data-after="${n}"]`).value })).filter((c, n) => $(`[data-n="${n}"]`).checked);
    const summary = $("#rv-sum") && $("#rv-sum").checked ? $("#rv-sumtext").value : "";
    if (!changes.length && !summary) return toast("Ничего не отмечено");
    try { rs.data = await api("/api/resume/apply", { vid: r.vid, changes, summary }); rs.blocks = rs.data.blocks; haptic("ok"); toast(`Применил: ${rs.data.applied}`); rs.review = null; go("main"); } catch (e) { fail(e); }
  };
  $$("[data-take]").forEach((b) => (b.onclick = async () => {
    try { rs.data = await api("/api/resume/apply", { vid: r.vid, summary: $(`[data-var="${b.dataset.take}"]`).value }); rs.blocks = rs.data.blocks; haptic("ok"); toast("«О себе» обновлено"); rs.review = null; go("main"); } catch (e) { fail(e); }
  }));
  if ($("#rv-open")) $("#rv-open").onclick = async () => {
    try { rs.data = await api("/api/resume/version", { action: "select", vid: res.vid }); rs.blocks = rs.data.blocks; rs.review = null; go("main"); } catch (e) { fail(e); }
  };
  if ($("#rv-copy")) $("#rv-copy").onclick = async () => {
    const ta = $("#rv-cover");
    try { await navigator.clipboard.writeText(ta.value); toast("Скопировал"); } catch (e) { ta.select(); toast("Выделил текст — скопируйте"); }
  };
}

/* ---------------------------------------------------------------- versions and export */

function versionSheet() {
  const a = rs.data.active;
  st.sheet = { versions: true };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>Версии резюме</h3>
    ${a ? `<label class="fld"><span>Название этой версии</span><input class="inp" id="vs-name" value="${esc(a.name)}" maxlength="40"></label>
      <div class="twobtn"><button class="ghost" id="vs-rename">Переименовать</button><button class="ghost" id="vs-dup">Сделать копию</button></div>` : ""}
    <p class="lbl">Новая версия</p>
    <div class="rs-start">
      <label class="rs-card"><b>📄 Из файла</b><input type="file" id="vs-file" accept=".pdf,.docx,.txt,.md"></label>
      <button class="rs-card" id="vs-import"><b>📥 Из hh.ru или LinkedIn</b></button>
      <button class="rs-card" id="vs-wizard"><b>✍️ Собрать с нуля</b></button>
    </div>
    ${a ? `<button class="danger-btn" id="vs-del">Удалить «${esc(a.name)}»</button>` : ""}
    <div class="btns"><button class="wide" id="vs-close">Закрыть</button></div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  const act = async (body, note) => {
    try { rs.data = await api("/api/resume/version", body); rs.blocks = rs.data.blocks || []; closeSheet(); rs.view = rs.data.active ? "main" : "start"; renderResume(); toast(note); } catch (e) { fail(e); }
  };
  $("#vs-close").onclick = closeSheet;
  if ($("#vs-rename")) $("#vs-rename").onclick = () => act({ action: "rename", vid: a.id, name: $("#vs-name").value }, "Переименовал");
  if ($("#vs-dup")) $("#vs-dup").onclick = () => act({ action: "duplicate", vid: a.id }, "Сделал копию — правьте её смело");
  if ($("#vs-del")) $("#vs-del").onclick = (e) => {
    if (!e.target.dataset.sure) { e.target.dataset.sure = "1"; e.target.textContent = "Точно удалить? Нажмите ещё раз"; return; }
    act({ action: "delete", vid: a.id }, "Версию удалил");
  };
  $("#vs-file").onchange = (e) => { closeSheet(); if (e.target.files[0]) uploadFile(e.target.files[0]); };
  $("#vs-import").onclick = () => { closeSheet(); go("import"); };
  $("#vs-wizard").onclick =() => { closeSheet(); rs.draft = null; go("wizard"); };
}
function exportSheet() {
  st.sheet = { export: true };
  $("#sheet").innerHTML = `<div class="grip"></div><h3>⬇️ Скачать «${esc(rs.data.active.name)}»</h3>
    <p class="muted">Файл придёт в чат с ботом — оттуда его можно переслать или сохранить.</p>
    <div class="rs-start">
      <button class="rs-card" data-fmt="pdf"><b>📄 PDF</b><span>Для отправки работодателю.</span></button>
      <button class="rs-card" data-fmt="html"><b>🌐 HTML</b><span>Открыть в браузере, поправить, распечатать.</span></button>
      <button class="rs-card" data-fmt="md"><b>📝 Текст</b><span>Markdown — для копирования в формы.</span></button>
    </div><div class="btns"><button class="wide" id="ex-close">Закрыть</button></div>`;
  $("#sheet").hidden = $("#sheet-bg").hidden = false;
  $("#ex-close").onclick = closeSheet;
  $$("[data-fmt]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    b.querySelector("span").textContent = "Готовлю файл…";
    try { await api("/api/resume/export", { vid: rs.data.active.id, format: b.dataset.fmt }); haptic("ok"); closeSheet(); toast("📩 Отправил в чат с ботом"); } catch (e) { b.disabled = false; fail(e); }
  }));
}
