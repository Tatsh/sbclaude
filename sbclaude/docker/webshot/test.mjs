// Tests for webshot's pure helpers -- the parts that need no browser.
// Run: node --test test.mjs
//
// The browser-driven half is covered end to end by `webshot check` against a blessed baseline;
// what is worth unit-testing here is the logic that silently corrupts results when wrong:
// argument parsing, image comparison, and the size/scale reporting that tells a user their
// capture is not the size they asked for.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import {
  SOFTWARE_RE,
  compareImages,
  parseArgs,
  pngSize,
  resolveMode,
  warnScale,
} from './webshot.mjs';

const run = promisify(execFile);

test('parseArgs: flags, values, inline =, kebab to camel, positionals', () => {
  const a = parseArgs([
    'shot',
    'http://x',
    '--out',
    'f.png',
    '--width=800',
    '--no-settle',
    '--wait-fn',
    'window.ok',
  ]);
  assert.deepEqual(a._, ['shot', 'http://x']);
  assert.equal(a.out, 'f.png');
  assert.equal(a.width, '800');
  assert.equal(a.noSettle, true); // bare flag
  assert.equal(a.waitFn, 'window.ok'); // --wait-fn -> waitFn
});

test('parseArgs: a flag followed by another flag stays boolean', () => {
  // Otherwise `--hidden --port 9222` would eat "--port" as the value of --hidden.
  const a = parseArgs(['--hidden', '--port', '9222']);
  assert.equal(a.hidden, true);
  assert.equal(a.port, '9222');
});

test('resolveMode: headless unless a window is explicitly requested', () => {
  const saved = { w: process.env.WAYLAND_DISPLAY, d: process.env.DISPLAY };
  process.env.WAYLAND_DISPLAY = 'wayland-0';
  try {
    // A display being present must NOT be enough to open a window: this runs unattended.
    assert.equal(resolveMode(undefined), 'gpu');
    assert.equal(resolveMode('auto'), 'wayland');
    assert.equal(resolveMode(undefined, { preferHeaded: true }), 'wayland');
    assert.equal(resolveMode('gpu', { preferHeaded: true }), 'gpu'); // explicit wins
  } finally {
    if (saved.w === undefined) delete process.env.WAYLAND_DISPLAY;
    else process.env.WAYLAND_DISPLAY = saved.w;
    if (saved.d === undefined) delete process.env.DISPLAY;
    else process.env.DISPLAY = saved.d;
  }
});

test('SOFTWARE_RE: catches the CPU rasterisers, not real hardware', () => {
  for (const s of [
    'ANGLE (Google, SwiftShader driver)',
    'llvmpipe (LLVM 15)',
    'Software Rasterizer',
  ])
    assert.ok(SOFTWARE_RE.test(s), s);
  assert.ok(!SOFTWARE_RE.test('ANGLE (NVIDIA, Vulkan 1.4.341 (NVIDIA GeForce RTX 4090), NVIDIA)'));
});

test('pngSize: reads dimensions from the IHDR chunk', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'webshot-'));
  const f = path.join(tmp, 'a.png');
  await run('convert', ['-size', '321x123', 'xc:black', f]);
  assert.deepEqual(await pngSize(await fs.readFile(f)), [321, 123]);
  assert.equal(await pngSize(Buffer.alloc(4)), null); // too short to contain a header
  await fs.rm(tmp, { recursive: true, force: true });
});

test('warnScale: silent when exact, warns when the display scale leaked in', () => {
  const messages = [];
  const original = console.error;
  console.error = (m) => messages.push(m);
  try {
    warnScale([1920, 1080], { width: 1920, height: 1080 }, 'gpu');
    assert.equal(messages.length, 0, 'exact capture must not warn');
    warnScale([2880, 1620], { width: 1920, height: 1080 }, 'wayland');
    assert.match(messages.at(-1), /2880x1620.*1920x1080.*1\.50x/);
    // An intentional --scale is not a surprise, so it must stay quiet.
    warnScale([2880, 1620], { width: 1920, height: 1080, scale: 1.5 }, 'wayland');
    assert.equal(messages.length, 1, 'explicit --scale must not warn');
  } finally {
    console.error = original;
  }
});

test('compareImages: 0 for identical, ~1 for black vs white, and writes a diff', async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'webshot-'));
  const black = path.join(tmp, 'b.png');
  const white = path.join(tmp, 'w.png');
  const copy = path.join(tmp, 'b2.png');
  await run('convert', ['-size', '32x32', 'xc:black', black]);
  await run('convert', ['-size', '32x32', 'xc:white', white]);
  await fs.copyFile(black, copy);

  assert.equal(await compareImages(black, copy), 0);
  assert.ok((await compareImages(black, white)) > 0.9);

  const diff = path.join(tmp, 'd.png');
  await compareImages(black, white, diff);
  assert.ok((await fs.stat(diff)).size > 0, 'the diff image is the artifact a human looks at');
  await fs.rm(tmp, { recursive: true, force: true });
});

test('compareImages: a missing file raises, never a silent 0', async () => {
  // A silent 0 here would read as "identical" and mark a broken run as passing.
  await assert.rejects(compareImages('/nonexistent-a.png', '/nonexistent-b.png'), /compare failed/);
});

test('CLI runs when invoked through a symlink, not just by its real path', async () => {
  // webshot is installed as /usr/local/bin/webshot -> /opt/webshot/webshot.mjs. Node sets
  // import.meta.url to the RESOLVED path while process.argv[1] keeps the symlink path, so an
  // unresolved comparison makes the "am I the entry point?" check false and the CLI silently
  // does nothing -- exiting 0, which is indistinguishable from a passing check. This is the
  // worst possible failure for a regression gate, and it is invisible without a test.
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'webshot-symlink-'));
  const link = path.join(dir, 'webshot');
  await fs.symlink(path.resolve('webshot.mjs'), link);
  // `--help`-less usage: no command exits 64 and prints usage. Any output at all proves the
  // CLI body ran; the bug produced zero bytes and exit 0.
  const result = await run(process.execPath, [link]).catch((e) => e);
  assert.equal(result.code, 64, 'expected the usage exit code, got a silent success');
  assert.match(
    result.stderr ?? '',
    /webshot doctor/,
    'CLI produced no usage text through a symlink',
  );
  await fs.rm(dir, { recursive: true, force: true });
});
