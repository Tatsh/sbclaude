#!/usr/bin/env node
// Thin noclip glue for the generic `webshot` tool.
//
// webshot owns everything general: launching a GPU browser, waiting, settling, screenshots,
// diffing, baselines, headed/headless. This file owns only what is specific to noclip -- how to
// tell that a scene has loaded, and how to put the camera somewhere reproducible.
//
//   ./nc.mjs shot  <view>
//   ./nc.mjs check <view>              capture and diff against baselines/<view>.png
//   ./nc.mjs watch <view>              open a window and leave it up
//   ./nc.mjs bless <view>              promote the last capture to the baseline
//   ./nc.mjs where                     print the current camera, to author a new view
//
// Any extra flags are passed straight through to webshot (--width, --scale, --port, ...).
//
// NOTHING HERE IS GAME-SPECIFIC. It talks only to noclip's own API (window.main: viewer, scene,
// ui, saveManager, camera). To use it for a different game, copy this file and write a new
// views.json -- that file holds the scene id and the camera placements, and is the only part
// that changes. The one optional coupling is `main.scene.worldBounds`, used only by
// `fitScene: true` views; scenes that do not publish it use `eye`+`target` or `saveState`
// instead, and get a clear error rather than a bad picture.

import { promises as fs, existsSync } from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

/**
 * Does this webshot actually run? Invoked with no arguments it must print usage and exit 64.
 *
 * Guards against a specific, nasty failure: an image whose webshot has the symlink entry-point
 * bug (import.meta.url compared to an unresolved argv[1]) does nothing at all and exits 0. A
 * `check` built on that reports success while never rendering or diffing anything -- a green
 * regression gate that is not testing the code. Fail loudly instead.
 */
function cliWorks(bin) {
  const r = spawnSync(bin, [], { encoding: 'utf8' });
  return r.status === 64 && /webshot/.test(`${r.stderr ?? ''}${r.stdout ?? ''}`);
}

/**
 * Find webshot, preferring the one installed in the container image.
 *
 * The vendored ./webshot.mjs is a fallback for boxes whose image predates it (or ships a broken
 * one). Once `webshot` on PATH works, the local copy (and .browsers / node_modules beside it)
 * can be deleted.
 */
function resolveWebshot() {
  if (process.env.WEBSHOT_BIN) return { cmd: process.env.WEBSHOT_BIN, pre: [], env: {} };
  for (const dir of (process.env.PATH ?? '').split(':')) {
    if (dir && existsSync(path.join(dir, 'webshot')) && cliWorks(path.join(dir, 'webshot')))
      return { cmd: 'webshot', pre: [], env: {} };
  }
  const local = path.join(HERE, 'webshot.mjs');
  if (!existsSync(local)) {
    // Distinguish the two ways this happens: a box with no webshot at all, versus one whose
    // webshot is present but broken (cliWorks rejected it). The second used to be silent.
    const onPath = (process.env.PATH ?? '')
      .split(':')
      .some((d) => d && existsSync(path.join(d, 'webshot')));
    throw new Error(
      onPath
        ? 'webshot is on PATH but does not run (it printed no usage); rebuild the container ' +
            'image, or set WEBSHOT_BIN to a working copy'
        : 'webshot not found on PATH and no vendored copy beside nc.mjs',
    );
  }
  // The vendored copy needs a browser; the image build normally supplies /opt/playwright.
  const browsers = path.join(HERE, '.browsers');
  return {
    cmd: process.execPath,
    pre: [local],
    env: existsSync(browsers) ? { PLAYWRIGHT_BROWSERS_PATH: browsers } : {},
  };
}

const WEBSHOT = resolveWebshot();

// `main.scene` is a getter that reads this.viewer.scene, so it THROWS while the viewer is still
// initialising -- optional chaining on `main` does not save you. Test a plain property instead.
const READY = '!!window.main?.viewer && window.main.scene !== null';

const config = JSON.parse(await fs.readFile(path.join(HERE, 'views.json'), 'utf8'));

/** JS run inside the page before capture: pin settings, place the camera, freeze the frame. */
function setupScript(view, { interactive = false }) {
  const fit = { margin: 0.42, azimuth: 0.9, elevation: 0.45, ...(view.fit ?? {}) };
  return `
const main = globalThis.main, v = main.viewer;
// Antialiasing lives in localStorage, not in the code, so a baseline captured on one profile
// would diff against another for no code reason. Pin it.
main.saveManager.saveSetting('AntialiasingMode', ${config.antialiasing ?? 1});
${
  interactive
    ? ''
    : `main.ui.toggleUI(false);
v.sceneTimeScale = 0;`
}
${
  view.fitScene
    ? `
const b = main.scene.worldBounds;
if (!b || !isFinite(b.min[0])) throw new Error('scene does not publish worldBounds');
const c = [0,1,2].map(i => (b.min[i] + b.max[i]) / 2);
const r = 0.5 * Math.hypot(b.max[0]-b.min[0], b.max[1]-b.min[1], b.max[2]-b.min[2]);
const d = (r * ${fit.margin}) / Math.sin(v.camera.fovY / 2);
const eye = [
  c[0] + d * Math.cos(${fit.elevation}) * Math.sin(${fit.azimuth}),
  c[1] + d * Math.sin(${fit.elevation}),
  c[2] + d * Math.cos(${fit.elevation}) * Math.cos(${fit.azimuth}),
];
const target = c;`
    : `
const eye = ${JSON.stringify(view.eye ?? null)}, target = ${JSON.stringify(view.target ?? null)};
if (!eye || !target) throw new Error('view needs fitScene, or eye+target');`
}
// noclip stores a camera-to-world matrix; writing it and calling worldMatrixUpdated() (which
// inverts it into the view matrix) is the whole teleport.
const sub = (a,b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const cross = (a,b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const norm = (u) => { const l = Math.hypot(u[0],u[1],u[2]);
  if (!l) throw new Error('degenerate camera: eye == target, or up parallel to view');
  return [u[0]/l, u[1]/l, u[2]/l]; };
const z = norm(sub(eye, target)), x = norm(cross([0,1,0], z)), y = cross(z, x);
const m = v.camera.worldMatrix;
m.set([x[0],x[1],x[2],0, y[0],y[1],y[2],0, z[0],z[1],z[2],0, eye[0],eye[1],eye[2],1]);
v.camera.worldMatrixUpdated();
// The controller integrates residual velocity; zero it so it cannot drift off the mark.
const cc = v.cameraController;
if (cc) { for (const k of ['linearVelocity','angularVelocity','vel','velocity']) {
  const q = cc[k]; if (q && q.length) for (let i = 0; i < q.length; i++) q[i] = 0; } cc.forceUpdate = true; }
return { eye, target };`;
}

function run(args) {
  return new Promise((resolve) => {
    spawn(WEBSHOT.cmd, [...WEBSHOT.pre, ...args], {
      stdio: 'inherit',
      env: { ...process.env, ...WEBSHOT.env },
    }).on('exit', (code) => resolve(code ?? 1));
  });
}

const [cmd, viewName, ...rest] = process.argv.slice(2);
const url = (view) =>
  `${config.base}/#${view?.scene ?? config.scene}` + (view?.saveState ? `;${view.saveState}` : '');

if (cmd === 'where') {
  process.exit(
    await run([
      'shot',
      url(null),
      '--out',
      '/dev/null',
      '--no-settle',
      '--wait-fn',
      READY,
      '--eval',
      'const m = main.viewer.camera.worldMatrix;' +
        ' const eye = [m[12],m[13],m[14]];' +
        ' return { eye, target: [eye[0]-m[8], eye[1]-m[9], eye[2]-m[10]], fovY: main.viewer.camera.fovY };',
      ...rest,
    ]),
  );
}

const view = config.views[viewName];
if (!['shot', 'check', 'watch', 'bless'].includes(cmd) || !view) {
  console.error(
    `usage: ./nc.mjs <shot|check|watch|bless> <view> [webshot flags...]\n` +
      `       ./nc.mjs where\n\nviews: ${Object.keys(config.views).join(', ')}`,
  );
  process.exit(64);
}

if (cmd === 'bless') {
  // Promote the last capture to the gating baseline. Deliberately separate from `shot`, so
  // approving a new reference image is always a decision, never a side effect.
  const from = path.join(HERE, 'out', `${viewName}.png`);
  const to = path.join(HERE, 'baselines', `${viewName}.png`);
  await fs.mkdir(path.dirname(to), { recursive: true });
  await fs.copyFile(from, to);
  console.log(JSON.stringify({ view: viewName, blessed: to }, null, 2));
  process.exit(0);
}

const common = [
  '--wait-fn',
  READY,
  '--selector',
  'canvas',
  '--eval',
  setupScript(view, { interactive: cmd === 'watch' }),
  ...rest,
];
const out = path.join(HERE, 'out', `${viewName}.png`);

if (cmd === 'watch') {
  process.exit(await run(['watch', url(view), '--mode', 'wayland', ...common]));
} else if (cmd === 'shot') {
  process.exit(await run(['shot', url(view), '--out', out, ...common]));
} else {
  process.exit(
    await run([
      'check',
      url(view),
      '--baseline',
      path.join(HERE, 'baselines', `${viewName}.png`),
      '--out',
      out,
      '--threshold',
      String(view.threshold ?? config.threshold ?? 0.02),
      ...common,
    ]),
  );
}
