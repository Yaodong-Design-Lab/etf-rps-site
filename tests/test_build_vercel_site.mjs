import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { instrumentHtml, buildSite, SNIPPET, MARKER, PUBLIC_FILES, PUBLIC_DIRS } from '../scripts/build_vercel_site.mjs';

const original = '<!doctype html>\r\n<html><head><style>x{color:red}</style></head><body>中文<script>let state = 1;</script></body></html>';
const fixture = () => {
  const root = mkdtempSync(path.join(tmpdir(), 'rps-analytics-'));
  for (const file of PUBLIC_FILES) writeFileSync(path.join(root, file), file.endsWith('.html') ? original : '{"date":"2026-10-09"}');
  for (const dir of PUBLIC_DIRS) mkdirSync(path.join(root, dir));
  writeFileSync(path.join(root, 'reports', '2026-10-09.html'), original);
  writeFileSync(path.join(root, 'assets', 'app.js'), 'let state = 1;\r\n');
  writeFileSync(path.join(root, 'data', 'latest.js'), 'window.COCKPIT_DATA={};');
  return root;
};

test('injection is byte-preserving and idempotent', () => {
  const result = instrumentHtml(original);
  assert.equal(result.replace(SNIPPET, ''), original);
  assert.equal(instrumentHtml(result), result);
  assert.equal(result.split('/_vercel/insights/script.js').length - 1, 1);
});
test('preexisting script not duplicated', () => {
  const html = '<head><script defer src="/_vercel/insights/script.js"></script></head>';
  assert.equal(instrumentHtml(html), html);
});
test('uppercase closing head accepted; malformed HTML rejected', () => {
  assert.ok(instrumentHtml('<HEAD></HEAD>').includes(MARKER));
  assert.throws(() => instrumentHtml('<body>No head</body>'));
});
test('all pages and assets preserved; private inputs excluded; regeneration survives', () => {
  const root = fixture();
  try {
    writeFileSync(path.join(root, '.env'), 'secret');
    mkdirSync(path.join(root, '.git'));
    mkdirSync(path.join(root, 'automation'));
    writeFileSync(path.join(root, 'automation', 'internal.csv'), 'internal');
    const result = buildSite(root);
    assert.equal(result.files, 6);
    assert.equal(result.htmlCount, 3);
    for (const relative of ['index.html', 'strategy_backtest.html', 'reports/2026-10-09.html']) {
      assert.equal(readFileSync(path.join(root, relative), 'utf8'), original);
      assert.equal(readFileSync(path.join(result.output, relative), 'utf8').replace(SNIPPET, ''), original);
    }
    for (const relative of ['assets/app.js', 'data/latest.js', 'payload.json']) {
      assert.deepEqual(readFileSync(path.join(root, relative)), readFileSync(path.join(result.output, relative)));
    }
    for (const relative of ['.git', '.env', 'automation']) assert.equal(existsSync(path.join(result.output, relative)), false);
    writeFileSync(path.join(root, 'index.html'), original.replace('中文', '更新'));
    writeFileSync(path.join(result.output, 'stale.html'), 'stale');
    buildSite(root);
    assert.ok(readFileSync(path.join(result.output, 'index.html'), 'utf8').includes('更新'));
    assert.ok(readFileSync(path.join(result.output, 'index.html'), 'utf8').includes(MARKER));
    assert.equal(existsSync(path.join(result.output, 'stale.html')), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
test('symlink output is rejected', () => {
  const root = fixture();
  try {
    symlinkSync(path.join(root, 'assets'), path.join(root, 'public'));
    assert.throws(() => buildSite(root));
    assert.ok(existsSync(path.join(root, 'assets', 'app.js')));
  } finally { rmSync(root, { recursive: true, force: true }); }
});
test('hidden and symlinked assets rejected', () => {
  const root = fixture();
  try {
    writeFileSync(path.join(root, 'assets', '.env'), 'secret');
    assert.throws(() => buildSite(root));
    rmSync(path.join(root, 'assets', '.env'));
    symlinkSync(path.join(root, 'payload.json'), path.join(root, 'assets', 'link.json'));
    assert.throws(() => buildSite(root));
  } finally { rmSync(root, { recursive: true, force: true }); }
});
