// Raw job-board feed for Job Radar's family mode.
//
// scan.mjs filters every board through YOUR portals.yml (titles, locations) and
// writes to your pipeline, so it can't serve other people. This script reuses the
// same career-ops provider modules but only fetches: it prints every posting from
// the given boards as JSON, and the bot then filters per person by their words.
//
// Usage: node telegram/board-feed.mjs remotive,himalayas
// Prints one JSON object: {"<provider>": {"jobs": [{title, url, company, location, posted}]} | {"error": "..."}}

import fs from 'fs';
import path from 'path';
import * as yaml from 'js-yaml';
import { fileURLToPath } from 'url';

import { makeHttpCtx } from '../providers/_http.mjs';
import { loadProviders } from '../providers/_registry.mjs';
import { parseHimalayasResponse } from '../providers/himalayas.mjs';
import { parseJobicyResponse } from '../providers/jobicy.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PER_BOARD_TIMEOUT_MS = 240_000;
const MAX_JOBS_PER_BOARD = 3000;

const ids = (process.argv[2] || '').split(',').map((s) => s.trim()).filter((s) => /^[a-z0-9-]{2,40}$/.test(s));
const providers = await loadProviders(path.join(ROOT, 'providers'));
const ctx = { ...makeHttpCtx(), includeUndated: true };

function clean(value, max) {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim().slice(0, max) : '';
}

function withTimeout(promise, ms) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`timeout after ${ms / 1000}s`)), ms); }),
  ]).finally(() => clearTimeout(timer));
}

// Some boards give only their first page through the career-ops provider (Himalayas: the
// 20 newest of ~100k). For those we walk the board's own cursor pages here.
// Himalayas also says which countries a job hires from (locationRestrictions, [] = anyone): kept as `countries`
// so the bot can show only jobs open to the person's country.
function parseHimalayasWithCountries(json) {
  const byUrl = new Map();
  for (const x of json?.jobs || []) {
    const list = Array.isArray(x?.locationRestrictions) ? x.locationRestrictions.filter((c) => typeof c === 'string').map((c) => c.trim()).slice(0, 300) : [];
    for (const u of [x?.applicationLink, x?.guid]) { try { byUrl.set(new URL(u).href, list); } catch { /* not a link */ } }
  }
  return parseHimalayasResponse(json).map((j) => {
    const countries = byUrl.get(j.url) || [];
    const location = countries.length ? countries.slice(0, 4).join(', ') + (countries.length > 4 ? ` +${countries.length - 4}` : '') : 'Worldwide';
    return { ...j, countries, location };
  });
}

const PAGED = {
  himalayas: { pages: 15, first: 'https://himalayas.app/jobs/api?limit=20', parse: parseHimalayasWithCountries },
  jobicy: { pages: 5, first: 'https://jobicy.com/api/v2/remote-jobs?count=100', parse: (j) => parseJobicyResponse(j) },
};

async function fetchPaged(spec) {
  const jobs = [];
  let url = spec.first;
  for (let i = 0; i < spec.pages && url; i++) {
    const json = await ctx.fetchJson(url, { redirect: 'error' });
    jobs.push(...spec.parse(json));
    const next = json && typeof json.nextCursor === 'string' && json.nextCursor !== 'None' ? json.nextCursor : '';
    url = next ? `${spec.first}&cursor=${encodeURIComponent(next)}` : '';
  }
  return jobs;
}


// «co-<name>»: one company's career page of chosen companies (telegram/companies.yml): free public ATS feeds
// and hh.ru employer RSS. Every job carries ru:true for Russian-speaking companies.
const UA = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; JobRadar personal job alerts) AppleWebKit/537.36 Chrome/124 Safari/537.36' };
const xmlText = (block, tag) => {
  const m = block.match(new RegExp(String.raw`<${tag}[^>]*>\s*(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?\s*</${tag}>`));
  return m ? m[1].replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, ' ').trim() : '';
};
async function getJson(url, ms = 25_000) {
  const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(ms) });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
async function getText(url) {
  const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(25_000) });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.text();
}
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const decode = (s) => String(s || '').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ').trim();
async function postJson(url, body, headers = {}) {
  const r = await fetch(url, {
    method: 'POST', headers: { ...UA, 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body), signal: AbortSignal.timeout(25_000),
  });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
// «Удалённо · Москва» when the site's own work-format words say remote, else just the place.
function remoteLoc(formats, place) {
  const words = [].concat(formats || []).join(' ');
  return (/удал|remote|дистанц/i.test(words) ? 'Удалённо · ' : '') + (place || '');
}
// Nuxt «devalue» payload: a flat array where objects hold indexes into it (Ported from freehire, MIT).
function unflatten(raw) {
  const memo = new Map();
  const ref = (v) => (typeof v === 'number' ? hydrate(v) : v);
  function hydrate(i) {
    if (i < 0 || i >= raw.length) return null;
    if (memo.has(i)) return memo.get(i);
    const tok = raw[i];
    let out = tok;
    if (Array.isArray(tok)) {
      out = typeof tok[0] === 'string' ? (tok.length > 1 ? ref(tok[1]) : null) : [];
      memo.set(i, out);
      if (typeof tok[0] !== 'string') for (const r of tok) out.push(ref(r));
    } else if (tok && typeof tok === 'object') {
      out = {};
      memo.set(i, out);
      for (const [k, v] of Object.entries(tok)) out[k] = ref(v);
    }
    memo.set(i, out);
    return out;
  }
  return hydrate(0);
}

// Social posts (Mastodon, Bluesky): a post counts as a vacancy only when it says so (hiring words) and names one of
// the wanted roles (c.roles, regex words); the title is the post's first line, so the person's role words can match it.
const JOB_SIGNAL = /\b(we'?re hiring|we are hiring|now hiring|is hiring|hiring[:!]|open (?:role|position)|job opening|vacancy|apply (?:now|here|at|via)|join (?:us|our team)|looking for (?:a|an) )|#hiring|#jobs?\b|ищем|вакансия/i;
function socialLooksLikeJob(text, c) {
  if (text.length < 40 || !/https?:\/\//.test(text) || !JOB_SIGNAL.test(text)) return false;
  const roles = new RegExp(c.roles || 'analy|business intelligence|\bBI\b|аналитик', 'i');
  return roles.test(text);
}
function socialTitle(text) {
  const pos = text.match(/Position:\s*(.+?)\s*(?:🏢|Company:|$)/u)?.[1];  // job-alert bots: «💼 Position: Data Analyst 🏢 Company: …»
  const t = (pos || text).replace(/https?:\/\/\S+/g, '').replace(/#\s*\w+/g, '').replace(/\s+/g, ' ').trim();
  return t.length > 140 ? t.slice(0, 137).replace(/\s\S*$/, '') + '…' : t;
}

const FETCHERS = {
  async greenhouse(c) {
    const j = await getJson(`https://boards-api.greenhouse.io/v1/boards/${c.slug}/jobs`);
    return (j.jobs || []).map((x) => ({ title: x.title, url: x.absolute_url, location: x.location?.name || '', posted: x.updated_at || '' }));
  },
  async ashby(c) {
    const j = await getJson(`https://api.ashbyhq.com/posting-api/job-board/${c.slug}`);
    return (j.jobs || []).map((x) => ({ title: x.title, url: x.jobUrl, location: x.location || '', posted: x.publishedAt || '' }));
  },
  async lever(c) {
    const j = await getJson(`https://api.lever.co/v0/postings/${c.slug}?mode=json`, c.timeout || 25_000);  // Binance's answer is huge: companies.yml gives it more time
    return j.map((x) => ({ title: x.text, url: x.hostedUrl, location: x.categories?.location || '', posted: x.createdAt ? new Date(x.createdAt).toISOString() : '' }));
  },
  async workable(c) {
    const j = await getJson(`https://apply.workable.com/api/v1/widget/accounts/${c.slug}`);
    return (j.jobs || []).map((x) => ({ title: x.title, url: x.url, location: [x.city, x.country].filter(Boolean).join(', '), posted: x.published_on || '' }));
  },
  async personio(c) {
    const xml = await getText(`https://${c.slug}.jobs.personio.de/xml`);
    return xml.split('<position>').slice(1).map((b) => {
      const id = xmlText(b, 'id');
      return { title: xmlText(b, 'name'), url: `https://${c.slug}.jobs.personio.de/job/${id}`, location: xmlText(b, 'office'), posted: xmlText(b, 'createdAt') };
    });
  },
  async teamtailor(c) {
    const xml = await getText(`https://${c.slug}.teamtailor.com/jobs.rss`);
    return xml.split('<item>').slice(1).map((b) => ({ title: xmlText(b, 'title'), url: xmlText(b, 'link'), location: xmlText(b, 'remoteStatus') || '', posted: xmlText(b, 'pubDate') }));
  },
  async workday(c) {  // public Workday job-board API: host (semrush.wd5), tenant, site
    const base = `https://${c.host}.myworkdayjobs.com`;
    const out = [];
    for (let offset = 0; offset < 400; offset += 20) {
      const r = await fetch(`${base}/wday/cxs/${c.tenant}/${c.site}/jobs`, {
        method: 'POST', headers: { ...UA, 'Content-Type': 'application/json' },
        body: JSON.stringify({ appliedFacets: {}, limit: 20, offset, searchText: '' }), signal: AbortSignal.timeout(25_000),
      });
      if (!r.ok) throw new Error(`workday ${c.tenant}: HTTP ${r.status}`);
      const j = await r.json();
      for (const x of j.jobPostings || []) {
        out.push({ title: x.title, url: `${base}/${c.site}${x.externalPath}`, location: x.locationsText || '', posted: x.postedOn || '' });
      }
      if (!(j.jobPostings || []).length || offset + 20 >= (j.total || 0)) break;
    }
    return out;
  },
  async comeet(c) {  // the careers page embeds COMPANY_POSITIONS_DATA = [...]
    const html = await getText(c.url);
    const i = html.indexOf('COMPANY_POSITIONS_DATA = ');
    if (i < 0) throw new Error('comeet: no positions data');
    let depth = 0, start = html.indexOf('[', i), end = start;
    for (; end < html.length; end++) {
      const ch = html[end];
      if (ch === '"') { for (end++; html[end] !== '"'; end++) if (html[end] === '\\') end++; }
      else if (ch === '[') depth++;
      else if (ch === ']' && --depth === 0) break;
    }
    return JSON.parse(html.slice(start, end + 1)).map((x) => ({
      title: x.name, url: x.url_active_page || x.url_comeet_hosted_page || c.url,
      location: [x.location?.city, x.location?.country].filter(Boolean).join(', ') + (x.workplace_type ? ` · ${x.workplace_type}` : ''),
      posted: x.time_updated || '',
    }));
  },
  async hcareers(c) {  // h.careers company page: cards with aria-label "Открыть вакансию <title>" + /job/<uuid>
    const html = await getText(`https://h.careers/jobs/company/${c.slug}`);
    const out = [];
    for (const m of html.matchAll(/aria-label="Открыть вакансию ([^"]+)"[^>]*href="\/job\/([0-9a-f-]{36})"/g)) {
      out.push({ title: m[1].replace(/&amp;/g, '&').replace(/&quot;/g, '"'), url: `https://h.careers/job/${m[2]}`, location: '', posted: '' });
    }
    return out;
  },
  async pinpoint(c) {  // https://<slug>.pinpointhq.com/postings.json
    const j = await getJson(`https://${c.slug}.pinpointhq.com/postings.json`);
    return (j.data || []).map((x) => ({
      title: x.title, url: x.url,
      location: [x.location?.city, x.location?.name].filter(Boolean).join(', ') + (x.workplace_type_text ? ` · ${x.workplace_type_text}` : ''),
      posted: '',
    }));
  },
  async himalayas_country(c) {  // Himalayas jobs open to one country (c.country, e.g. TH), per search word, a few pages each
    const seen = new Map();
    for (const q of c.queries || ['analyst']) {
      for (let page = 1; page <= (c.pages || 4); page++) {
        const url = `https://himalayas.app/jobs/api/search?country=${encodeURIComponent(c.country)}&q=${encodeURIComponent(q)}&page=${page}`;
        const j = await getJson(url);
        const got = parseHimalayasWithCountries(j);
        for (const x of got) if (!seen.has(x.url)) seen.set(x.url, x);
        if ((j.jobs || []).length < 20) break;
        await pause(250);
      }
    }
    return [...seen.values()];
  },
  async tierb() {  // «tier B» (telegram/tierb.yml): remote companies hiring worldwide / APAC, their own boards, 6 at a time
    const list = (yaml.load(fs.readFileSync(path.join(ROOT, 'telegram', 'tierb.yml'), 'utf8'))?.companies || [])
      .filter((c) => FETCHERS[c.ats] && ['greenhouse', 'lever', 'ashby', 'workable'].includes(c.ats) && c.slug);
    const out = [];
    const failed = [];
    let next = 0;
    await Promise.all(Array.from({ length: 6 }, async () => {
      while (next < list.length) {
        const c = list[next++];
        try {
          for (const j of await withTimeout(FETCHERS[c.ats]({ slug: c.slug }), 30_000)) {
            // big companies list offices too: only remote / anywhere / Asia ones (or no place said) are kept
            if (!j.location || /remote|anywhere|worldwide|global|distributed|asia|apac|thailand|emea|home/i.test(j.location)) out.push({ ...j, company: c.name });
          }
        } catch { failed.push(c.name); }
      }
    }));
    if (!out.length && failed.length) throw new Error(`tier B: no board answered (${failed.length})`);
    if (failed.length) process.stderr.write(`tier B: ${failed.length} boards failed: ${failed.slice(0, 10).join(', ')}\n`);
    return out;
  },
  async aviasales() {
    const j = await getJson('https://vacancies-app.aviasales.ru/api/vacancies?language=ru');
    return j.map((x) => ({ title: String(x.position).trim(), url: `https://www.aviasales.ru/about/vacancies/${x.id}`, location: (x.workPlace || '') + (x.team?.name ? ` · ${x.team.name}` : ''), posted: '' }));
  },
  async nexters() {
    const j = await getJson('https://www.nexters.com/back/api/vacancies');
    return (j.data || []).map((x) => ({ title: x.attributes.title, url: `https://www.nexters.com/en/about/alljobs/?vacancy=${x.attributes.slug}`, location: x.attributes.category_name || '', posted: '' }));
  },
  async playrix() {
    const r = await fetch('https://playrix.com/api/v1/index.php?action=job%2FgetList', {
      method: 'POST', headers: { ...UA, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ action: 'job/getList', options: { lang: 'en' } }), signal: AbortSignal.timeout(25_000),
    });
    if (!r.ok) throw new Error(`playrix: HTTP ${r.status}`);
    const j = await r.json();
    return (j.items || []).filter((x) => !x.isHidden).map((x) => ({ title: x.titleMsg || x.name, url: `https://playrix.com/job/open/${x.code}`, location: x.previewText || '', posted: x.dateUpdate ? x.dateUpdate.replace(' ', 'T') : '' }));
  },
  async avito() {  // server-rendered lists per department; data-vacancy-remote says «Да» for remote
    const base = 'https://career.avito.com';
    const home = await getText(`${base}/vacancies/`);
    const depts = [...new Set([...home.matchAll(/href="\/vacancies\/([a-z0-9-]+)\/"/g)].map((m) => m[1]))];
    const out = new Map();
    for (const d of depts) {
      const html = await getText(`${base}/vacancies/${d}/`);
      for (const b of html.split('data-vacancy-geo=').slice(1)) {
        const geo = b.match(/^"([^"]*)"/)?.[1] || '';
        const remote = b.match(/data-vacancy-remote="([^"]*)"/)?.[1] === 'Да';
        const href = b.match(/href="(\/vacancies\/[a-z0-9-]+\/[0-9]+\/)"/)?.[1];
        const name = b.match(/vacancies-section__item-name">([^<]+)</)?.[1];
        if (href && name && !out.has(href)) out.set(href, { title: name.trim(), url: base + href, location: (remote ? 'Удалённо' : geo) + (remote && geo ? ` · ${geo}` : ''), posted: '' });
      }
      await new Promise((r) => setTimeout(r, 400));
    }
    return [...out.values()];
  },
  async ostrovok() {
    const html = await getText('https://career.ostrovok.ru/vacancy');
    return [...html.matchAll(/data-tm-name="([^"]+)"\s+href="(\/vacancy\/[0-9]+)"/g)].map((m) => ({ title: m[1].replace(/&amp;/g, '&'), url: `https://career.ostrovok.ru${m[2]}`, location: '', posted: '' }));
  },
  async twogis() {  // job.2gis.ru: ?page=N returns the first N pages at once, so one big page gets everything
    const html = await getText('https://job.2gis.ru/vacancies?page=80');
    const out = [];
    for (const b of html.split('<li><a href="/vacancies/').slice(1)) {
      const path = b.match(/^([a-z_]+\/[0-9]+)"/)?.[1];
      const title = b.match(/<h4[^>]*>([^<]+)</)?.[1];
      if (!path || !title) continue;
      const chips = [...b.slice(0, 1500).matchAll(/chipLabel[^>]*>([^<]+)</g)].map((m) => m[1]);
      out.push({ title: title.replace(/&amp;/g, '&').trim(), url: `https://job.2gis.ru/vacancies/${path}`, location: chips.includes('Удалённо') ? 'Удалённо' : '', posted: '' });
    }
    return out;
  },
  async kontur() {
    const html = await getText('https://kontur.ru/career/vacancies');
    const out = new Map();
    for (const b of html.split('<a class="vacancy" href="').slice(1)) {
      const path = b.match(/^(\/career\/vacancies\/[0-9]+)"/)?.[1];
      const title = b.match(/vacancy__title">([^<]+)</)?.[1];
      if (!path || !title || out.has(path)) continue;
      const spans = [...b.slice(0, 900).matchAll(/<span>([^<]+)<\/span>/g)].map((m) => m[1].replace(/&nbsp;|&#xA0;/g, ' '));
      out.set(path, { title: title.replace(/&nbsp;|&#xA0;/g, ' ').replace(/&#x2B;/g, '+').replace(/&amp;/g, '&').trim(), url: `https://kontur.ru${path}`, location: spans.join(' · '), posted: '' });
    }
    return [...out.values()];
  },
  async habr(c) {  // Habr Career's open JSON list: every remote vacancy, newest first
    const out = [];
    for (let page = 1; page <= 12; page++) {
      const j = await getJson(`https://career.habr.com/api/frontend/vacancies?type=all&sort=date&per_page=50&remote=true&page=${page}`);
      for (const v of j.list || []) {
        const sal = v.salary?.formatted ? ` · ${v.salary.formatted}` : '';
        out.push({ title: v.title, url: `https://career.habr.com${v.href}`, company: v.company?.title || c.name, location: `Удалённо${v.location?.title ? ' · ' + v.location.title : ''}${sal}`, posted: v.publishedDate?.date || '' });
      }
      if (page >= (j.meta?.totalPages || 1)) break;
      await new Promise((r) => setTimeout(r, 300));
    }
    return out;
  },
  async uzum() {  // people.uzum.com (Next.js, server-rendered): ?page=N, 15 cards each
    const base = 'https://people.uzum.com';
    const out = new Map();
    for (let page = 1; page <= 10; page++) {
      const html = await getText(`${base}/career/ru/vacancies${page > 1 ? `?page=${page}` : ''}`);
      let added = 0;
      for (const b of html.split('<h3 class="vacancy-card_title').slice(1)) {
        const title = b.match(/^[^>]*>([^<]+)</)?.[1];
        const before = html.slice(0, html.indexOf(b));
        const href = [...before.matchAll(/href="(\/career\/ru\/vacancies\/[0-9]+)"/g)].pop()?.[1];
        const descr = b.match(/vacancy-card_description[^>]*>([^<]+)</)?.[1] || '';
        if (title && href && !out.has(href)) { out.set(href, { title: title.replace(/&quot;/g, '"').replace(/&amp;/g, '&'), url: base + href, location: descr, posted: '' }); added++; }
      }
      if (!added) break;
      await new Promise((r) => setTimeout(r, 400));
    }
    return [...out.values()];
  },
  async twinby() {  // vacancies are the titles in the page's carousel (no per-vacancy pages)
    const html = await getText('https://twinby.ru/career');
    const titles = [...new Set([...html.matchAll(/images-carousel-item"[^>]*>.*?<span[^>]*>([^<]+)<\/span>/g)].map((m) => m[1].trim()))];
    return titles.map((t) => ({ title: t, url: `https://twinby.ru/career?vacancy=${encodeURIComponent(t.toLowerCase().replace(/[^a-zа-я0-9]+/g, '-'))}`, location: '', posted: '' }));
  },
  async yasno() {  // work.yasno.live: Tilda cards, link = /<slug>, description = «удаленно» / «гибрид»
    const html = await getText('https://work.yasno.live/');
    const out = new Map();
    for (const b of html.split('<a class="js-product-link" href="').slice(1)) {
      const slug = b.match(/^(\/[a-z0-9_-]+)"/)?.[1];
      const title = b.match(/js-product-name"[^>]*>\s*([^<]+?)\s*</)?.[1];
      const descr = b.match(/t-descr[^>]*>\s*([^<]*?)\s*</)?.[1] || '';
      if (slug && title && !out.has(slug)) out.set(slug, { title, url: `https://work.yasno.live${slug}`, location: descr, posted: '' });
    }
    return [...out.values()];
  },
  async agilefluent() {  // job board for jobs abroad: open JSON API. Its «remote» filter also returns office and hybrid
    // jobs (checked 2026-10: of 3000 only 937 are remote), so every job's own `formats` is checked, and a remote job
    // there belongs to its `countries` (the list the person must live in; «ww» = anywhere).
    const ISO = { tha: 'Thailand', rus: 'Russian Federation', kaz: 'Kazakhstan', uzb: 'Uzbekistan', kgz: 'Kyrgyzstan', blr: 'Belarus',
      ukr: 'Ukraine', geo: 'Georgia', arm: 'Armenia', aze: 'Azerbaijan', srb: 'Serbia', mne: 'Montenegro', tur: 'Turkey', cyp: 'Cyprus',
      pol: 'Poland', deu: 'Germany', esp: 'Spain', prt: 'Portugal', are: 'United Arab Emirates', idn: 'Indonesia', vnm: 'Vietnam',
      mys: 'Malaysia', phl: 'Philippines', ind: 'India', usa: 'United States', gbr: 'United Kingdom', nld: 'Netherlands',
      bel: 'Belgium', fra: 'France', che: 'Switzerland', can: 'Canada', ita: 'Italy', bra: 'Brazil', egy: 'Egypt', cze: 'Czechia',
      sau: 'Saudi Arabia', sgp: 'Singapore' };
    const out = [];
    for (let page = 1; page <= 70; page++) {
      const r = await fetch('https://jobboard.agilefluent.ru/api/jobs/search', {
        method: 'POST', headers: { ...UA, 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: { formats: ['remote'] }, pagination: { limit: 50, page } }), signal: AbortSignal.timeout(25_000),
      });
      if (!r.ok) throw new Error(`agilefluent: HTTP ${r.status}`);
      const j = await r.json();
      for (const x of j.data || []) {
        if (!(x.formats || []).includes('remote') || (x.formats || []).some((f) => f !== 'remote')) continue;  // office / hybrid
        const codes = (x.countries && x.countries.length ? x.countries : [x.country]).filter(Boolean).map((c) => String(c).toLowerCase());
        const anywhere = !codes.length || codes.includes('ww');
        const names = anywhere ? [] : codes.map((c) => ISO[c] || c.toUpperCase());
        out.push({
          title: x.title, url: `https://jobboard.agilefluent.ru/jobs/${x.id}`, company: x.companyName, countries: names,
          location: (anywhere ? 'Удалённо · весь мир' : 'Удалённо · только ' + names.slice(0, 3).join(', ')) + (x.salaryLabel && x.salaryLabel !== 'Не указана' ? ` · ${x.salaryLabel}` : ''),
          posted: x.createdAtIso || '',
        });
      }
      if (!j.hasMore) break;
      await new Promise((r) => setTimeout(r, 250));
    }
    return out;
  },
  async finder() {  // finder.work remote list: schema.org ItemList in every page, 25 per page
    const out = new Map();
    for (let page = 1; page <= 10; page++) {
      const html = await getText(`https://finder.work/vacancies/remote${page > 1 ? `?page=${page}` : ''}`);
      let added = 0;
      for (const m of html.matchAll(/<script type="application\/ld\+json">(.*?)<\/script>/gs)) {
        let j; try { j = JSON.parse(m[1]); } catch { continue; }
        for (const it of j.itemListElement || []) {
          if (it.url && it.name && !out.has(it.url)) { out.set(it.url, { title: it.name, url: it.url, location: 'Удалённо', posted: '' }); added++; }
        }
      }
      if (!added) break;
      await new Promise((r) => setTimeout(r, 400));
    }
    return [...out.values()];
  },
  async tochka() {  // hr.tochka.com loads 20 at a time behind «Показать ещё»: a real browser clicks through
    const { chromium } = await import('playwright');
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage({ userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36' });
      await page.goto('https://hr.tochka.com/vacancies/', { waitUntil: 'domcontentloaded', timeout: 40_000 });
      await page.waitForTimeout(4000);
      for (let i = 0; i < 12; i++) {
        const more = page.getByText('Показать ещё', { exact: true }).first();
        if (!(await more.count())) break;
        await more.click();
        await page.waitForTimeout(1500);
      }
      const items = await page.$$eval('a[href*="/vacancies/catalog/"]', (as) => [...new Map(as.map((a) => [a.href, a.innerText.trim().replace(/\s+/g, ' ')])).entries()]);
      return items.map(([url, text]) => ({ title: text.replace(/\s*(Удалённо|Офис|Гибрид)$/i, ''), url, location: /Удалённо$/i.test(text) ? 'Удалённо' : '', posted: '' }));
    } finally {
      await browser.close();
    }
  },
  // ---- Ported from freehire (MIT), Copyright (c) 2026 freehire contributors
  // (internal/ingest/sources/*.go): list endpoints only, no per-vacancy detail requests.
  async ozon() {  // only «external_vacancy» (the rest are internal moves); the link is the hh.ru copy
    const out = [];
    for (let page = 1; page <= 200; page++) {
      const j = await getJson(`https://job-api.ozon.ru/v2/vacancy?page=${page}&limit=50`);
      for (const x of j.items || []) {
        if (x.vacancyType !== 'external_vacancy' || !x.hhId) continue;
        out.push({ title: x.title, url: `https://hh.ru/vacancy/${x.hhId}`, location: remoteLoc(x.workFormat, x.city), posted: '' });
      }
      if (page >= (j.meta?.totalPages || 1)) break;
      await pause(150);
    }
    return out;
  },
  async tbank() {  // the site's own request (2026-10): generatedGraphQL filter, 10 per page, all three categories
    const g = {
      type: 'T_CAREER', status: 'ACTIVE', includeSeoAndPcPublications: false, includeInternshipPublications: true,
      userGroup: { groups: ['Control'], type: 'SPECIFIC' }, collapsePredstavitelPublications: true,
      or: ['tcareer_it', 'tcareer_back_office', 'tcareer_work_with_clients'].map((category) => ({ category })),
    };
    const out = [];
    for (let offset = 0, i = 0; i < 80; i++) {
      const j = await postJson('https://www.tbank.ru/pfpjobs/papi/getVacancies', { filters: { generatedGraphQL: g }, pagination: { offset } });
      if (j.resultCode !== 'OK') throw new Error(`tbank: ${j.errorMessage || j.resultCode}`);
      for (const x of j.payload?.vacancies || []) {
        if (!x.urlSlug || x.redirectUrl) continue;
        out.push({ title: x.title, url: `https://www.tbank.ru/career/it/vacancy/all/${x.seoSlug}/${x.urlSlug}/`, location: remoteLoc(x.tags, x.subtitle), posted: '' });
      }
      const next = j.payload?.nextPagination || {};
      if (next.isFinished || !(next.offset > offset)) break;
      offset = next.offset;
      await pause(250);
    }
    return out;
  },
  async sber() {
    const out = [];
    for (let skip = 0, i = 0; i < 100; i++) {  // the API gives fewer than asked: step by what came back
      const j = await getJson(`https://rabota.sber.ru/public/app-candidate-public-api-gateway/api/v1/publications?skip=${skip}&take=200`);
      const vs = j.data?.vacancies || [];
      skip += vs.length;
      for (const x of vs) {
        const remote = /удал[её]нн|дистанцион/i.test(`${x.city || ''} ${x.conditions || ''}`);
        out.push({ title: x.title, url: `https://rabota.sber.ru/search/${x.internalId}`, company: x.company || 'Сбер', location: (remote ? 'Удалённо · ' : '') + (x.city || ''), posted: x.publicationDate || '' });
      }
      if (!vs.length || skip >= (j.data?.total || 0)) break;
      await pause(200);
    }
    return out;
  },
  async getmatch() {  // an aggregator: every offer carries its own company; location_items[].format = remote/hybrid/office
    const out = [];
    for (let offset = 0; offset < 20000; offset += 100) {
      const j = await getJson(`https://getmatch.ru/api/offers?offset=${offset}&limit=100`);
      for (const o of j.offers || []) {
        const items = o.location_items || [];
        const remote = items.some((l) => l.format === 'remote' && !l.exclude);
        const where = [...new Set(items.filter((l) => !l.exclude).map((l) => l.label))].join(', ');
        const sal = o.salary_display_from || o.salary_display_to ? ` · ${[o.salary_display_from, o.salary_display_to].filter(Boolean).join('–')} ${o.salary_currency || ''}`.trimEnd() : '';
        out.push({ title: o.position, url: `https://getmatch.ru${o.url}`, company: o.company?.name || '', location: (remote ? 'Удалённо · ' : '') + where + sal, posted: o.published_at || '' });
      }
      if (!(j.offers || []).length || offset + 100 >= (j.meta?.total || 0)) break;
      await pause(250);
    }
    return out;
  },
  async huntflow(c) {  // <slug>.huntflow.io/_payload.json: a Nuxt «devalue» flat array, unflattened here
    const node = unflatten(await getJson(`https://${c.slug}.huntflow.io/_payload.json`));
    return (node?.data?.vacancies?.items || []).filter((x) => !x.archived_at).map((x) => ({
      title: x.position, url: `https://${c.slug}.huntflow.io/vacancy/${x.slug}`, location: x.city || '', posted: '',
    }));
  },
  async yandex() {  // cursor pages; skip publications that only redirect elsewhere or are «fast track» events
    const base = 'https://yandex.ru/jobs/api/publications';
    const out = [];
    const seen = new Set();
    let url = `${base}?page_size=100`;
    for (let i = 0; url && i < 100; i++) {
      const j = await getJson(url);
      for (const x of j.results || []) {
        if (x.redirect_url || x.fast_track) continue;
        const remote = (x.vacancy?.work_modes || []).some((m) => /remote/.test(m.slug || '') || /удал/i.test(m.name || ''));
        const cities = (x.vacancy?.cities || []).map((c) => c.name).join(', ');
        out.push({ title: x.title, url: `https://yandex.ru/jobs/vacancies/${x.publication_slug_url}`, location: (remote ? 'Удалённо · ' : '') + cities, posted: x.modified || '' });
      }
      const cursor = j.next ? new URL(j.next).searchParams.get('cursor') : '';
      if (!cursor || seen.has(cursor)) break;
      seen.add(cursor);
      url = `${base}?page_size=100&cursor=${encodeURIComponent(cursor)}`;
      await pause(200);
    }
    return out;
  },
  async mts() {  // the public API key sits in the site's own config (apiKey:"...")
    const key = (await getText('https://job.mts.ru/')).match(/apiKey:"([^"]+)"/)?.[1];
    if (!key) throw new Error('mts: apiKey not found');
    const out = [];
    for (let offset = 0; offset < 10000; offset += 200) {
      const j = await postJson('https://api.job.mts.ru/v1/vacancies/filtered/career', { offset, limit: 200 }, { 'x-api-key': key });
      for (const x of j.data?.vacancies || []) {
        out.push({ title: x.name, url: `https://job.mts.ru/vacancy/${x.id}`, company: x.info?.brand || 'МТС', location: remoteLoc(x.info?.worktype, x.info?.city), posted: '' });
      }
      if (offset + 200 >= (j.data?.pageInfo?.total || 0)) break;
      await pause(250);
    }
    return out;
  },
  async vk() {
    const out = [];
    let url = 'https://team.vk.company/career/api/v2/vacancies/?limit=50&offset=0';
    for (let i = 0; url && i < 60; i++) {
      const j = await getJson(url.replace(/^http:/, 'https:'));
      for (const x of j.results || []) {
        out.push({ title: x.title, url: `https://team.vk.company/vacancy/${x.id}/`, company: x.group?.name ? `VK · ${x.group.name}` : 'VK', location: (x.remote ? 'Удалённо · ' : '') + (x.town?.name || ''), posted: '' });
      }
      url = j.next || '';
      await pause(250);
    }
    return out;
  },
  async alfabank() {
    const cities = Object.fromEntries(((await getJson('https://job.alfabank.ru/api/vacancies/options?listId=cities')).optionLists?.cities || []).map((c) => [c.id, c.text]));
    const out = [];
    for (let skip = 0; skip < 10000; skip += 100) {
      const j = await getJson(`https://job.alfabank.ru/api/vacancies?skip=${skip}&take=100`);
      for (const x of j.items || []) {
        if (x.isHidden || x.isInternal) continue;
        const city = cities[x.cityId] || '';
        const remote = String(x.slug || '').includes('/remote-job/') || /удал/i.test(city);
        out.push({ title: x.name, url: `https://job.alfabank.ru${x.slug}`, location: (remote ? 'Удалённо · ' : '') + city, posted: x.createdAt || '' });
      }
      if (!(j.items || []).length || skip + 100 >= (j.total || 0)) break;
      await pause(200);
    }
    return out;
  },
  async rwb() {  // Wildberries & Russ; the API gives at most 50 per page whatever the limit
    const out = [];
    for (let offset = 0, i = 0; i < 100; i++) {
      const j = await getJson(`https://career.rwb.ru/crm-api/api/v1/pub/vacancies?limit=50&offset=${offset}`);
      const items = j.data?.items || [];
      for (const x of items) {
        out.push({ title: x.name, url: `https://career.rwb.ru/vacancies/${x.id}`, location: remoteLoc((x.employment_types || []).map((e) => e.title), x.city_title), posted: '' });
      }
      offset += items.length;
      if (!items.length || offset >= (j.data?.range?.count || 0)) break;
      await pause(200);
    }
    return out;
  },
  async lamoda() {
    const out = [];
    for (let start = 0, i = 0; i < 50; i++) {
      const j = await getJson(`https://job.lamoda.ru/api/hr/vacancies/compact?pagination[limit]=100&pagination[start]=${start}`);
      for (const x of j.data || []) {
        out.push({ title: x.name, url: `https://job.lamoda.ru/vacancies/${x.slug}`, location: x.location?.name || '', posted: x.externalPublicationDate || '' });
      }
      start += (j.data || []).length;
      if (!(j.data || []).length || start >= (j.meta?.total || 0)) break;
      await pause(200);
    }
    return out;
  },
  async kuper() {
    const out = [];
    for (let page = 1, pages = 1; page <= pages && page <= 50; page++) {
      const j = await getJson(`https://vacancies-api.sbermarket.ru/api/vacancy_pagination/?page=${page}`);
      for (const blk of j.result || []) {
        if (blk.category === 'pagination') pages = blk.data?.pages || 1;
        if (blk.category !== 'vacancies') continue;
        for (const x of blk.data || []) {
          out.push({ title: x.title, url: `https://kuper.ru/rabota/${x.friendlyUrl}-${x.idForUrl}`, location: remoteLoc(x.wf, x.city), posted: '' });
        }
      }
      await pause(200);
    }
    return out;
  },
  async dodo() {
    const j = await getJson('https://career-api.dodoteam.ru/api/v1/vacancies');
    return (j.data || []).flatMap((g) => g.items || []).map((x) => ({
      title: x.position, url: `https://dodo.team/vacancy/${x.id}`, company: x.brand || 'Dodo Brands', location: remoteLoc(x.work_format, x.vacancy_location), posted: '',
    }));
  },
  async domclick() {
    const j = await getJson('https://career.domclick.ru/api/v1/vacancy/');
    return (j.result || []).map((x) => ({
      title: x.title, url: `https://career.domclick.ru/vacancy/${x.slug}`,
      location: ((x.vacancycontent?.work_format || []).includes('REMOTE') ? 'Удалённо · ' : '') + (x.area?.name || ''), posted: '',
    }));
  },
  async geekjob() {  // aggregator; list pages /vacancies/N, 20 cards each
    const out = new Map();
    for (let page = 1; page <= 30; page++) {
      const html = await getText(`https://geekjob.ru/vacancies/${page}`);
      let added = 0;
      for (const b of html.split('<p class="truncate vacancy-name">').slice(1)) {
        const m = b.match(/href="(\/vacancy\/[0-9a-f]{24})"[^>]*>([^<]+)</);
        if (!m || out.has(m[1])) continue;
        const company = b.match(/company-name">\s*<a[^>]*>([^<]+)</)?.[1] || '';
        out.set(m[1], { title: decode(m[2]), url: `https://geekjob.ru${m[1]}`, company: decode(company), location: /remote-label|удал[её]нн/i.test(b.slice(0, 1500)) ? 'Удалённо' : '', posted: '' });
        added++;
      }
      if (!added) break;
      await pause(400);
    }
    return [...out.values()];
  },
  async trudvsem() {  // «Работа России»: only distance-work vacancies («Дистанционная (удаленная) работа»)
    const out = [];
    const since = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 19) + 'Z';
    for (let page = 0; page < 12; page++) {  // ~12 s per page: only what changed in the last week
      const j = await getJson(`https://opendata.trudvsem.ru/api/v1/vacancies?limit=100&offset=${page}&modifiedFrom=${since}&text=${encodeURIComponent('удаленная')}`, 60_000);
      const vs = j.results?.vacancies || [];
      for (const { vacancy: v } of vs) {
        if (!/удал|дистанц/i.test(v.employment || '') || !v['job-name'] || !v.vac_url) continue;
        const sal = v.salary ? ` · ${v.salary} ₽` : '';
        out.push({ title: v['job-name'], url: v.vac_url, company: v.company?.name || '', location: `Удалённо · ${v.region?.name || ''}${sal}`, posted: v['creation-date'] || '' });
      }
      if (vs.length < 100 || (page + 1) * 100 >= (j.meta?.total || 0)) break;
      await pause(300);
    }
    return out;
  },
  async djinni() {  // aggregator (Ukraine / remote); JSON-LD JobPosting list in each page
    const out = [];
    for (let page = 1; page <= 30; page++) {
      const r = await fetch(`https://djinni.co/jobs/?page=${page}`, { headers: UA, signal: AbortSignal.timeout(25_000) });
      if (!r.ok) { if (out.length) break; throw new Error(`djinni: HTTP ${r.status}`); }
      if (!r.url.includes(`page=${page}`)) break;  // past the end it redirects to /jobs/
      const html = await r.text();
      let added = 0;
      for (const m of html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)) {
        let j; try { j = JSON.parse(m[1]); } catch { continue; }
        for (const p of [].concat(j)) {
          if (p?.['@type'] !== 'JobPosting' || !p.url) continue;
          const remote = String(p.jobLocationType || '').toUpperCase() === 'TELECOMMUTE';
          const a = p.applicantLocationRequirements?.address || {};
          out.push({ title: p.title, url: p.url, company: p.hiringOrganization?.name || '', location: (remote ? 'Удалённо · ' : '') + (a.addressCountry || a.addressRegion || ''), posted: p.datePosted || '' });
          added++;
        }
      }
      if (!added) break;
      await pause(600);
    }
    return out;
  },
  // ---- 2026-10-05: more sources (all off until ticked): Remote.com, Jobgether, Mastodon, Bluesky, LinkedIn guest pages
  async remotecom(c) {  // remote.com/jobs: server-rendered list, ?query=…&page=N; each card = title link + company + a few facts
    const out = new Map();
    for (const q of c.queries || ['data analyst']) {
      for (let page = 1; page <= (c.pages || 2); page++) {
        const html = await getText(`https://remote.com/jobs/all?query=${encodeURIComponent(q)}&page=${page}`);
        const cards = [...html.matchAll(/href="(\/jobs\/[a-z0-9-]+-c[a-z0-9]+\/[^"]+)"[^>]*><span[^>]*>([^<]+)<\/span><\/a><span[^>]*>([^<]*)<\/span>/g)];
        if (!cards.length) break;
        cards.forEach((m, n) => {
          const url = `https://remote.com${m[1]}`;
          if (out.has(url)) return;
          const tail = html.slice(m.index + m[0].length, cards[n + 1] ? cards[n + 1].index : m.index + m[0].length + 4000).slice(0, 3000);
          const facts = [...tail.replace(/<svg[\s\S]*?<\/svg>/g, '').matchAll(/<li[^>]*>([\s\S]*?)<\/li>/g)].map((x) => decode(x[1].replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ')).filter(Boolean);
          out.set(url, { title: decode(m[2]), url, company: decode(m[3]), location: facts.slice(0, 3).join(' · '), posted: '' });
        });
        await pause(500);
      }
    }
    return [...out.values()];
  },
  async jobgether(c) {  // official open API: jobgether.com/api/v1/jobs (every job is remote-friendly), 25 per page
    const out = new Map();
    for (const q of c.queries || ['data analyst']) {
      for (let page = 1; page <= (c.pages || 2); page++) {
        const j = await getJson(`https://jobgether.com/api/v1/jobs?keyword=${encodeURIComponent(q)}&limit=25&page=${page}`);
        for (const x of j.jobs || []) {
          if (out.has(x.url)) continue;
          const sal = x.salaryRange ? (() => { const m = String(x.salaryRange).match(/^(\d+)-(\d+)\s*(\w+)?/); return m ? `${m[1] === m[2] ? m[1] : m[1] + '–' + m[2]} ${m[3] || ''}/год`.trim() : ''; })() : '';
          out.set(x.url, { title: x.title, url: x.url, company: x.company || '', location: [x.remote, x.location, x.contractType].filter(Boolean).join(' · '), comp: sal, posted: x.postedAt || '' });
        }
        if (!j.pagination?.hasMore) break;
        await pause(400);
      }
    }
    return [...out.values()];
  },
  async mastodon(c) {  // public tag timelines (no login): #hiring #dataanalyst …; only posts that look like a vacancy
    const out = new Map();
    for (const host of c.hosts || ['mastodon.social']) {
      for (const tag of c.tags || ['hiring']) {
        let list;
        try { list = await getJson(`https://${host}/api/v1/timelines/tag/${encodeURIComponent(tag)}?limit=40`); } catch { continue; }
        for (const p of list) {
          const text = decode(String(p.content || '').replace(/<br\s*\/?>|<\/p>/g, ' \n').replace(/<[^>]+>/g, ' ')).replace(/[ \t]+/g, ' ').trim();
          if (!p.url || p.reblog || p.in_reply_to_id || !socialLooksLikeJob(text, c)) continue;
          out.set(p.url, { title: socialTitle(text), url: p.url, company: '@' + (p.account?.acct || ''), location: host, posted: p.created_at || '' });
        }
        await pause(500);
      }
    }
    return [...out.values()];
  },
  async bluesky(c) {  // public search API of api.bsky.app (the bsky.social host wants a login)
    const out = new Map();
    for (const q of c.queries || ['hiring data analyst remote']) {
      let j;
      try { j = await getJson(`https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=${encodeURIComponent(q)}&sort=latest&limit=50`); } catch { continue; }
      for (const p of j.posts || []) {
        const text = String(p.record?.text || '').replace(/\s+/g, ' ').trim();
        const rkey = String(p.uri || '').split('/').pop();
        if (!rkey || p.record?.reply || !socialLooksLikeJob(text, c)) continue;
        const url = `https://bsky.app/profile/${p.author?.handle}/post/${rkey}`;
        out.set(url, { title: socialTitle(text), url, company: '@' + (p.author?.handle || ''), location: 'Bluesky', posted: p.record?.createdAt || p.indexedAt || '' });
      }
      await pause(500);
    }
    return [...out.values()];
  },
  async linkedin(c) {  // LinkedIn's public guest search (no login): remote filter, a few pages per query, slow and polite
    const out = new Map();
    for (const q of c.queries || ['data analyst']) {
      for (let page = 0; page < (c.pages || 2); page++) {
        const html = await getText(`https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=${encodeURIComponent(q)}&location=${encodeURIComponent(c.location || 'Worldwide')}&f_WT=2&f_TPR=r604800&start=${page * 25}`);
        const cards = html.split('<li>').slice(1);
        if (!cards.length) break;
        for (const b of cards) {
          const url = b.match(/href="(https:\/\/[a-z.]*linkedin\.com\/jobs\/view\/[^"?]+)/)?.[1];
          const title = b.match(/base-search-card__title[^>]*>\s*([^<]+?)\s*</)?.[1];
          if (!url || !title || out.has(url)) continue;
          const company = b.match(/base-search-card__subtitle[^>]*>[\s\S]*?(?:<a[^>]*>\s*)?([^<>]+?)\s*(?:<\/a>|<\/h4>)/)?.[1] || '';
          const loc = b.match(/job-search-card__location[^>]*>\s*([^<]+?)\s*</)?.[1] || '';
          out.set(url, { title: decode(title), url, company: decode(company), location: 'Удалённо · ' + decode(loc), posted: b.match(/datetime="([^"]+)"/)?.[1] || '' });
        }
        await pause(1500);
      }
    }
    return [...out.values()];
  },
  // ---- end of the freehire port
  async hh(c) {
    const out = [];
    const seen = new Set();
    const queries = c.q && c.q.length ? c.q : [''];
    for (const id of c.ids || []) {
      for (const q of queries) {
        const extra = q ? `&text=${encodeURIComponent(q)}&search_field=name` : '';
        const xml = await getText(`https://hh.ru/search/vacancy/rss?employer_id=${id}&schedule=remote&order_by=publication_time${extra}`);
        await new Promise((r) => setTimeout(r, 600));
        for (const b of xml.split('<item>').slice(1)) {
          const link = xmlText(b, 'link').split('?')[0];
          if (!/^https:\/\/([a-z0-9-]+\.)?hh\.ru\/vacancy\/\d+/.test(link) || seen.has(link)) continue;
          seen.add(link);
          const desc = b.match(/<description>([\s\S]*?)<\/description>/)?.[1] || '';
          const field = (l) => (desc.match(new RegExp(String.raw`${l}:\s*([^<]+)`))?.[1] || '').trim();
          out.push({ title: xmlText(b, 'title'), url: link, location: field('Регион') + ' · удалённо', posted: xmlText(b, 'pubDate'), company: field('Вакансия компании') });
        }
      }
    }
    return out;
  },
};
async function fetchCompanies(id) {
  const cfg = yaml.load(fs.readFileSync(path.join(ROOT, 'telegram', 'companies.yml'), 'utf8')) || {};
  const jobs = [];
  const errors = [];
  for (const c of (cfg.companies || []).filter((x) => x.id === id)) {
    try {
      const got = await FETCHERS[c.kind](c);
      for (const j of got) {
        if (c.only_loc && !new RegExp(c.only_loc, 'i').test(j.location || '')) continue;  // e.g. one city of a company with many offices
        if (j.title && /^https?:\/\//.test(j.url || '')) jobs.push({ ...j, company: j.company || c.name, ru: !!c.ru, ...(c.tags ? { tags: c.tags } : {}) });
      }
    } catch (err) {
      errors.push(`${c.name}: ${String(err?.message || err).slice(0, 80)}`);
    }
  }
  if (!jobs.length && errors.length) throw new Error(errors.join('; '));
  if (errors.length) process.stderr.write(`companies: ${errors.join('; ')}\n`);
  return jobs;
}

const out = {};
const PARALLEL = 4;  // boards fetched at once (each board itself stays polite: one request at a time)
const queue = [...ids];
await Promise.all(Array.from({ length: PARALLEL }, async () => {
  while (queue.length) await fetchBoard(queue.shift());
}));
// exit right away: a board that hit its time limit may still be paging in the background and
// would otherwise keep this process (and the bot's wait) alive long after the answer is ready
process.stdout.write(JSON.stringify(Object.fromEntries(ids.filter((id) => out[id]).map((id) => [id, out[id]]))) + '\n',
  () => process.exit(0));

async function fetchBoard(id) {
  const provider = id.startsWith('co-') ? {} : providers.get(id);
  if (!provider) {
    out[id] = { error: 'unknown provider' };
    return;
  }
  try {
    const run = () => withTimeout(id.startsWith('co-') ? fetchCompanies(id) : PAGED[id] ? fetchPaged(PAGED[id]) : provider.fetch({ name: id, provider: id }, ctx),
      PER_BOARD_TIMEOUT_MS);
    // one more try: most failures seen are one-off «aborted» / «fetch failed» timeouts
    // (not after a timeout: that board is just slow, a second try would only double the wait)
    const jobs = await run().catch(async (e) => {
      if (/^timeout after/.test(String(e?.message))) throw e;
      await new Promise((r) => setTimeout(r, 3000));
      return run();
    });
    if (!Array.isArray(jobs)) throw new Error('fetch() did not return an array');
    out[id] = {
      jobs: jobs
        .filter((j) => j && typeof j.title === 'string' && /^https?:\/\//i.test(j.url || ''))
        // big Russian boards (Sber, Alfa…) are mostly office jobs: remote ones go first so the cap keeps them
        .sort((a, b) => Number(/^Удалённо/.test(b.location || '')) - Number(/^Удалённо/.test(a.location || '')))
        .slice(0, MAX_JOBS_PER_BOARD)
        .map((j) => ({
          title: clean(j.title, 200),
          url: clean(j.url, 500),
          company: clean(j.company, 120),
          location: clean(j.location, 120),
          ...(j.comp ? { comp: clean(j.comp, 60) } : {}),
          posted: clean(j.postedAt || j.posted || j.date || '', 40),
          ...(j.ru ? { ru: true } : {}),
          ...(Array.isArray(j.countries) ? { countries: j.countries.map((c) => clean(c, 60)) } : {}),
          ...(Array.isArray(j.tags) ? { tags: j.tags.map((t) => clean(t, 30)) } : {}),
        })),
    };
  } catch (err) {
    out[id] = { error: clean(String(err?.message || err), 200) };
  }
}
