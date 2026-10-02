// Raw job-board feed for Job Radar's family mode.
//
// scan.mjs filters every board through YOUR portals.yml (titles, locations) and
// writes to your pipeline, so it can't serve other people. This script reuses the
// same career-ops provider modules but only fetches: it prints every posting from
// the given boards as JSON, and the bot then filters per person by their words.
//
// Usage: node telegram/board-feed.mjs remotive,himalayas
// Prints one JSON object: {"<provider>": {"jobs": [{title, url, company, location, posted}]} | {"error": "..."}}

import path from 'path';
import { fileURLToPath } from 'url';

import { makeHttpCtx } from '../providers/_http.mjs';
import { loadProviders } from '../providers/_registry.mjs';

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

const out = {};
for (const id of ids) {
  const provider = providers.get(id);
  if (!provider) {
    out[id] = { error: 'unknown provider' };
    continue;
  }
  try {
    const jobs = await withTimeout(provider.fetch({ name: id, provider: id }, ctx), PER_BOARD_TIMEOUT_MS);
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
        })),
    };
  } catch (err) {
    out[id] = { error: clean(String(err?.message || err), 200) };
  }
}
process.stdout.write(JSON.stringify(out) + '\n');
