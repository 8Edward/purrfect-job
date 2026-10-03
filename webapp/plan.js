"use strict";
/* 🧭 Career plan (the «Резюме» tab, its own block under the résumé): from the active résumé
   version and a goal — grade up, or another role or field — Claude drafts a step-by-step
   plan. Today's position is described only from the résumé; the server drops invented facts. */

const pl = { data: null, goal: "grade", target: "", busy: false };
const PLAN_GOALS = { grade: "⬆️ Поднять грейд", role: "🔀 Сменить роль или сферу" };
const OLD_GOALS = { field: "🔀 Сменить роль или сферу" };  // plans saved before the two buttons were merged

async function loadPlan() {
  try {
    const d = await api("/api/plan");
    pl.data = d.plan;
    if (d.plan && !pl.busy) { pl.goal = PLAN_GOALS[d.plan.goal] ? d.plan.goal : "role"; pl.target = d.plan.goal_text || pl.target; }
  } catch (e) { /* the tab works without it */ }
  renderPlan();
}
function renderPlan() {
  const box = $("#rs-plan");
  if (!box) return;
  const p = pl.data, list = (xs) => (xs || []).map((x) => `<li>${esc(x)}</li>`).join("");
  const form = `<div class="goals">${Object.entries(PLAN_GOALS).map(([k, v]) => `<button class="stage" data-goal="${k}" aria-pressed="${pl.goal === k}">${v}</button>`).join("")}</div>
    <input class="inp" id="plan-target" maxlength="120" value="${esc(pl.target)}" placeholder="${pl.goal === "grade" ? "Например: Senior Data Analyst (можно пусто)" : "Куда: например, Product Analyst или аналитик в финтехе"}">
    <button class="cta" id="plan-go" ${pl.busy ? "disabled" : ""}>${pl.busy ? "Составляю план… (до пары минут)" : p ? "✨ Составить заново" : "✨ Составить план"}</button>`;
  const body = p ? `<div class="lbl">${esc(PLAN_GOALS[p.goal] || OLD_GOALS[p.goal] || "")}${p.target ? " → " + esc(p.target) : ""}</div>
    ${p.now ? `<div><b>Сейчас.</b> ${esc(p.now)}</div>` : ""}
    ${p.strengths && p.strengths.length ? `<div><b>Уже есть</b><ul>${list(p.strengths)}</ul></div>` : ""}
    ${p.gaps && p.gaps.length ? `<div><b>Чего не хватает</b><ul>${list(p.gaps)}</ul></div>` : ""}
    <div><b>Шаги</b><ol>${p.steps.map((s) => `<li><b>${esc(s.title)}</b>${s.weeks ? ` · ${esc(s.weeks)}` : ""}<small>${esc(s.what)}</small></li>`).join("")}</ol></div>
    ${p.resume && p.resume.length ? `<div><b>Что поменять в резюме</b><ul>${list(p.resume)}</ul></div>` : ""}
    ${p.questions && p.questions.length ? `<div><b>Уточните, чтобы план стал точнее</b><ul>${list(p.questions)}</ul></div>` : ""}
    <span class="muted">По версии «${esc(p.version || "")}». ИИ описывает ваш опыт только по резюме${p.dropped ? `; ${p.dropped} ${plural(p.dropped, "фразу", "фразы", "фраз")} с непроверенными фактами я убрал` : ""}. Новые навыки в шагах — то, чему учиться.</span>` : "";
  box.innerHTML = `<div class="group"><h4>🧭 Карьерный план</h4><div class="plan">
    <p class="hint" style="margin:0">Выберите цель, и ИИ по вашему резюме составит пошаговый план перехода: что уже есть, чего не хватает, что делать и сколько это займёт.</p>
    ${form}${body}</div></div>`;
  $$("[data-goal]", box).forEach((b) => (b.onclick = () => { pl.goal = b.dataset.goal; pl.target = $("#plan-target").value; haptic(); renderPlan(); }));
  $("#plan-target").oninput = (e) => (pl.target = e.target.value);
  $("#plan-go").onclick = makePlan;
}
async function makePlan() {
  if (pl.busy) return;
  pl.busy = true;
  renderPlan();
  try {
    const d = await api("/api/plan", { goal: pl.goal, target: pl.target, vid: typeof rs !== "undefined" && rs.data && rs.data.active ? rs.data.active.id : undefined });
    pl.data = await waitJob(d.job);
    haptic("ok");
    toast("План готов");
  } catch (e) { fail(e); }
  pl.busy = false;
  renderPlan();
}
