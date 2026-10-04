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
const PER_BOARD_TIMEOUT_MS = 120_000;
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
const PAGED = {
  himalayas: { pages: 15, first: 'https://himalayas.app/jobs/api?limit=20', parse: parseHimalayasResponse },
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
const UA = { 'User-Agent': 'Mozilla/5.0 (JobRadar personal job alerts)' };
const xmlText = (block, tag) => {
  const m = block.match(new RegExp(String.raw`<${tag}[^>]*>\s*(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?\s*</${tag}>`));
  return m ? m[1].replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, ' ').trim() : '';
};
async function getJson(url) {
  const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(25_000) });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
async function getText(url) {
  const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(25_000) });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.text();
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
    const j = await getJson(`https://api.lever.co/v0/postings/${c.slug}?mode=json`);
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
  async agilefluent() {  // job board for jobs abroad: open JSON API, remote only
    const out = [];
    for (let page = 1; page <= 8; page++) {
      const r = await fetch('https://jobboard.agilefluent.ru/api/jobs/search', {
        method: 'POST', headers: { ...UA, 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: { formats: ['remote'] }, pagination: { limit: 50, page } }), signal: AbortSignal.timeout(25_000),
      });
      if (!r.ok) throw new Error(`agilefluent: HTTP ${r.status}`);
      const j = await r.json();
      for (const x of j.data || []) {
        out.push({ title: x.title, url: `https://jobboard.agilefluent.ru/jobs/${x.id}`, company: x.companyName, location: ['Удалённо', x.country, x.salaryLabel && x.salaryLabel !== 'Не указана' ? x.salaryLabel : ''].filter(Boolean).join(' · '), posted: x.createdAtIso || '' });
      }
      if (!j.hasMore) break;
      await new Promise((r) => setTimeout(r, 300));
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
        if (j.title && /^https?:\/\//.test(j.url || '')) jobs.push({ ...j, company: j.company || c.name, ru: !!c.ru });
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
for (const id of ids) {
  const provider = id.startsWith('co-') ? {} : providers.get(id);
  if (!provider) {
    out[id] = { error: 'unknown provider' };
    continue;
  }
  try {
    const run = () => withTimeout(id.startsWith('co-') ? fetchCompanies(id) : PAGED[id] ? fetchPaged(PAGED[id]) : provider.fetch({ name: id, provider: id }, ctx),
      PER_BOARD_TIMEOUT_MS);
    // one more try: most failures seen are one-off «aborted» / «fetch failed» timeouts
    const jobs = await run().catch(async () => { await new Promise((r) => setTimeout(r, 3000)); return run(); });
    if (!Array.isArray(jobs)) throw new Error('fetch() did not return an array');
    out[id] = {
      jobs: jobs
        .filter((j) => j && typeof j.title === 'string' && /^https?:\/\//i.test(j.url || ''))
        .slice(0, MAX_JOBS_PER_BOARD)
        .map((j) => ({
          title: clean(j.title, 200),
          url: clean(j.url, 500),
          company: clean(j.company, 120),
          location: clean(j.location, 120),
          posted: clean(j.postedAt || j.posted || j.date || '', 40),
          ...(j.ru ? { ru: true } : {}),
        })),
    };
  } catch (err) {
    out[id] = { error: clean(String(err?.message || err), 200) };
  }
}
process.stdout.write(JSON.stringify(out) + '\n');
