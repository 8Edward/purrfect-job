# Purrfect Job

A Telegram bot with a Mini App for finding remote work. It collects vacancies from Telegram channels,
hh.ru, job boards and company career pages, drops office-only and "remote, but US/RU only" postings,
matches jobs by the meaning of the role, and helps with the résumé and applications.

One owner (the bot reads channels through their own Telegram account) and any number of guests, each with
their own keywords, channels, sites and résumé. The user interface is in Russian: it was built for
Russian-speaking job seekers.

## What it does

- **Feed**: vacancy cards from 100+ sources with filters by date, grade, source and salary; duplicates from
  different sources are merged; "evergreen" and vanished postings sink to the bottom.
- **Search by role**: an exact phrase or "similar roles" over a role graph (`rolegraph.yml`, titles from
  O*NET); unknown titles are labelled by Claude in the background and the answers are remembered.
- **Résumé**: versions, an ATS check, a rule-based Junior/Middle/Senior estimate, a match against a vacancy,
  a builder with templates, PDF export; AI rewrites go through a fact check (new numbers and names are dropped).
- **Applications**: a cover letter in the vacancy's language, a stage tracker, a batch of letters from
  favourites. Nothing is sent without a human tap.
- **Gigs**: freelance and part-time work from hh.ru, FL.ru, Freelancer and Remotive with an "how easy to get
  in" score.
- **Reliability**: a watchdog restarts the bot after a crash or a hang; daily copies of the feed.

## Stack

Python 3.12 (Telethon, aiohttp, Pillow); plain JavaScript for the Mini App; Node.js for the site scanners and
PDF (Playwright); a Cloudflare quick tunnel for the app's address; Claude Code on a subscription for the AI
features.

## How it was built

Built with an AI coding assistant (Claude Code). I set the product requirements, made the design decisions
and tested every feature on real data and real users; the AI wrote most of the code. The commit history is
the log of that work.

## Relation to career-ops

The repository is checked out inside a clone of [career-ops](https://github.com/santifer/career-ops)
(as `purrfect-job/` in its root) and uses its job-board scanner `scan.mjs`, `portals.yml`, the `providers/`
modules and `cv.md`. Another location can be given in the `CAREER_OPS_DIR` environment variable.

## Running it (Windows)

1. `setup.bat`: packages and `config.yml` (api_id and api_hash from my.telegram.org, a bot token from
   @BotFather).
2. `login.bat`: the owner's Telegram login.
3. `autostart.ps1`: the bot as a Windows scheduled task that starts at logon; `watchdog-install.ps1`: the
   watchdog.

Tests: `py -3 test_webapp.py` and the other `test_*.py` files print `ok`.

Private data (sessions, `config.yml`, `users/`, `data/`) never enters the repository.
