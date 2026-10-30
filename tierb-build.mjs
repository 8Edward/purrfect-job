// Builds telegram/tierb.yml: «tier B» companies = remote companies from remoteintech/remote-jobs
// (github.com/remoteintech/remote-jobs, ISC) that hire worldwide or in Asia-Pacific, each with its public
// job board (Greenhouse / Lever / Ashby / Workable) when one could be found.
//
// Usage (run again from time to time, the list grows):
//   git clone --depth 1 https://github.com/remoteintech/remote-jobs.git D:\tools\tmp\remote-jobs
//   node telegram/tierb-build.mjs D:\tools\tmp\remote-jobs
//
// How a board is found: links in the company's own profile, then its website (home page, /careers, /jobs),
// then a guess from the website's domain name checked against the ATS APIs. Free public endpoints only.

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import * as yaml from 'js-yaml';

const HERE = path.dirname(fileURLToPath(import.meta.url));  // Purrfect Job
const ROOT = path.resolve(HERE, '..');  // career-ops: this repo is checked out inside it (providers/, node_modules)
const SRC = process.argv[2];
if (!SRC || !fs.existsSync(path.join(SRC, 'src', 'companies'))) {
  console.error('usage: node telegram/tierb-build.mjs <path to a remoteintech/remote-jobs clone>');
  process.exit(1);
}
const OUT = path.join(HERE, 'tierb.yml');
const UA = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36' };
const OPEN_TO_ASIA = /worldwide|anywhere|all (?:a)?round the world|all over the world|any(?:where in the)? (?:country|world)|asia|apac|thailand|south[- ]?east|doesn'?t matter where|no matter where|global/i;
const LOCKED = /\b(?:us|usa|u\.s\.|united states|uk|canada|europe|eu)[- ]based|\bonly\b/i;

function frontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---/);
  try { return m ? yaml.load(m[1]) || {} : {}; } catch { return {}; }
}
function section(text, name) {
  const m = text.match(new RegExp(`^## ${name}\\s*\\n([\\s\\S]*?)(?=^## |$(?![\\s\\S]))`, 'm'));
  return m ? m[1].trim() : '';
}

const ATS = [  // [kind, regex over html/markdown giving the board slug]
  ['greenhouse', /(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io\/(?:embed\/job_board\?for=)?([a-z0-9_-]+)/i],
  ['greenhouse', /greenhouse\.io\/embed\/job_board(?:\/js)?\?for=([a-z0-9_-]+)/i],
  ['lever', /jobs(?:\.eu)?\.lever\.co\/([a-z0-9_.-]+)/i],
  ['ashby', /jobs\.ashbyhq\.com\/([a-z0-9_.%-]+)/i],
  ['workable', /apply\.workable\.com\/([a-z0-9_-]+)/i],
];
const SKIP_SLUG = /^(embed|api|v1|js|jobs|careers|static|assets|j|o|share)$/i;
function findAts(text) {
  for (const [kind, re] of ATS) {
    const m = String(text || '').match(re);
    if (m && !SKIP_SLUG.test(m[1])) return { kind, slug: decodeURIComponent(m[1]).replace(/[/?#].*$/, '') };
  }
  return null;
}

async function get(url, json = false) {
  try {
    const r = await fetch(url, { headers: UA, redirect: 'follow', signal: AbortSignal.timeout(15_000) });
    if (!r.ok) return null;
    return json ? await r.json() : (await r.text()).slice(0, 1_500_000);
  } catch { return null; }
}

// open jobs on a board, or null when the board does not exist
async function boardJobs(kind, slug) {
  if (kind === 'greenhouse') { const j = await get(`https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`, true); return j && Array.isArray(j.jobs) ? j.jobs.length : null; }
  if (kind === 'lever') { const j = await get(`https://api.lever.co/v0/postings/${slug}?mode=json`, true); return Array.isArray(j) ? j.length : null; }
  if (kind === 'ashby') { const j = await get(`https://api.ashbyhq.com/posting-api/job-board/${slug}`, true); return j && Array.isArray(j.jobs) ? j.jobs.length : null; }
  if (kind === 'workable') { const j = await get(`https://apply.workable.com/api/v1/widget/accounts/${slug}`, true); return j && Array.isArray(j.jobs) ? j.jobs.length : null; }
  return null;
}

async function resolve(c) {
  const fromProfile = findAts(c.md);
  if (fromProfile && (await boardJobs(fromProfile.kind, fromProfile.slug)) !== null) return { ...fromProfile, how: 'profile' };
  if (c.site) {
    const base = c.site.replace(/\/+$/, '');
    for (const url of [base, `${base}/careers`, `${base}/jobs`, `${base}/careers/`, `${base}/company/careers`]) {
      const found = findAts(await get(url));
      if (found && (await boardJobs(found.kind, found.slug)) !== null) return { ...found, how: 'website' };
    }
  }
  // a guess: the domain name (1password.com -> 1password) must be a board on exactly one ATS
  let domain = '';
  try {
    const labels = new URL(c.site).hostname.replace(/^www\./, '').toLowerCase().split('.');
    if (labels.length === 2) domain = labels[0];  // build.gr.ps -> no guess: «build» is not the company
  } catch { /* no site */ }
  const name = c.name.toLowerCase().replace(/[^a-z0-9]+/g, '');
  const alike = name.includes(domain) || domain.includes(name) || (name.length >= 4 && domain.startsWith(name.slice(0, 4)));
  if (domain.length >= 4 && alike) {
    const hits = [];
    for (const kind of ['greenhouse', 'lever', 'ashby']) {
      const n = await boardJobs(kind, domain);
      if (n !== null) hits.push({ kind, slug: domain, how: 'guess' });
    }
    if (hits.length === 1) return hits[0];
  }
  return null;
}

const files = fs.readdirSync(path.join(SRC, 'src', 'companies')).filter((f) => f.endsWith('.md'));
const picked = [];
for (const f of files) {
  const md = fs.readFileSync(path.join(SRC, 'src', 'companies', f), 'utf8').replace(/\r\n/g, '\n');
  const fm = frontmatter(md);
  if (!fm.title || fm.remote_policy === 'hybrid') continue;
  const regionText = section(md, 'Region');
  const ok = fm.region === 'worldwide' || fm.region === 'asia-pacific'
    || (fm.region === 'other' && OPEN_TO_ASIA.test(regionText) && !LOCKED.test(regionText));
  if (!ok) continue;
  picked.push({ name: String(fm.title).trim(), ri: fm.slug || f.replace(/\.md$/, ''), site: fm.website || '', region: fm.region, policy: fm.remote_policy || '', md });
}
console.error(`${picked.length} of ${files.length} companies hire worldwide / in Asia-Pacific`);

const out = [];
let next = 0;
await Promise.all(Array.from({ length: 8 }, async () => {
  while (next < picked.length) {
    const c = picked[next++];
    const board = await resolve(c);
    out.push({ name: c.name, ri: c.ri, site: c.site, region: c.region, policy: c.policy, ...(board ? { ats: board.kind, slug: board.slug, how: board.how } : {}) });
    if (out.length % 50 === 0) console.error(`  ${out.length} / ${picked.length}`);
  }
}));
out.sort((a, b) => a.name.localeCompare(b.name));
const withBoard = out.filter((c) => c.ats).length;
const head = `# «Tier B»: remote companies from remoteintech/remote-jobs (ISC) that hire worldwide or in Asia-Pacific.
# Built by telegram/tierb-build.mjs on ${new Date().toISOString().slice(0, 10)}: ${out.length} companies, ${withBoard} with a public job board
# (ats + slug; how = where the board was found: profile / website / guess from the domain name).
# Read by board-feed.mjs (site «co-tierb») and by the bot (the «тир B» mark on a card).
`;
fs.writeFileSync(OUT, head + yaml.dump({ companies: out }, { lineWidth: 200, flowLevel: 2 }), 'utf8');
console.error(`wrote ${OUT}: ${out.length} companies, ${withBoard} with a board`);
