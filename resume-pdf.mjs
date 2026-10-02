// A résumé HTML page -> A4 PDF with the Chromium that career-ops already uses.
// Usage: node telegram/resume-pdf.mjs <input.html> <output.pdf>
// (career-ops' generate-pdf.mjs is for cv.md and runs its fact checks; app résumés are the person's own.)
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  console.error('usage: node resume-pdf.mjs <input.html> <output.pdf>');
  process.exit(2);
}
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.setContent(readFileSync(input, 'utf8'), { waitUntil: 'load' });
  await page.pdf({ path: output, format: 'A4', printBackground: true, preferCSSPageSize: true });
} finally {
  await browser.close();
}
