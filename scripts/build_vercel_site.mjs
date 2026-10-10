#!/usr/bin/env node
// Stage already generated content for Vercel; never refetch or recalculate market data.
import { cpSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const PUBLIC_FILES = ['index.html', 'strategy_backtest.html', 'payload.json'];
export const PUBLIC_DIRS = ['assets', 'data', 'reports'];
export const MARKER = '<!-- ETF RPS: Vercel Web Analytics -->';
export const SNIPPET = `${MARKER}
<script>
  window.va = window.va || function () { (window.vaq = window.vaq || []).push(arguments); };
</script>
<script defer src="/_vercel/insights/script.js"></script>
`;

export function instrumentHtml(html) {
  if (html.includes(MARKER)) return html;
  if (/<script\b[^>]*\bsrc\s*=\s*["'][^"']*\/_vercel\/insights\/script\.js/i.test(html)) return html;
  const match = /<\/head\s*>/i.exec(html);
  if (!match) throw new Error('HTML has no closing head tag; refusing silent omission');
  return html.slice(0, match.index) + SNIPPET + html.slice(match.index);
}

function listFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const absolute = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Symlinks are not published: ${absolute}`);
    if (entry.name.startsWith('.')) throw new Error(`Hidden files are not published: ${absolute}`);
    return entry.isDirectory() ? listFiles(absolute) : [absolute];
  });
}

export function buildSite(root) {
  root = path.resolve(root);
  const output = path.join(root, 'public');
  if (existsSync(output) && lstatSync(output).isSymbolicLink()) throw new Error('Output must not be a symlink');
  const inputs = [...PUBLIC_FILES, ...PUBLIC_DIRS];
  for (const relative of inputs) {
    const input = path.join(root, relative);
    if (!existsSync(input) || lstatSync(input).isSymbolicLink()) throw new Error(`Missing or unsafe public input: ${relative}`);
    if (PUBLIC_DIRS.includes(relative)) listFiles(input);
  }
  // Only this build's known output is removed, never source files or repo internals.
  rmSync(output, { recursive: true, force: true });
  mkdirSync(output);
  for (const relative of inputs) cpSync(path.join(root, relative), path.join(output, relative), { recursive: true });
  const files = listFiles(output);
  let htmlCount = 0;
  for (const file of files.filter(file => file.endsWith('.html'))) {
    const original = readFileSync(file, 'utf8');
    writeFileSync(file, instrumentHtml(original));
    htmlCount++;
  }
  return { output, files: files.length, htmlCount };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = process.argv[2] || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  console.log(JSON.stringify(buildSite(root)));
}
