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
    const jobs = await withTimeout(id.startsWith('co-') ? fetchCompanies(id) : PAGED[id] ? fetchPaged(PAGED[id]) : provider.fetch({ name: id, provider: id }, ctx),
      PER_BOARD_TIMEOUT_MS);
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
