#!/usr/bin/env node
// webshot -- drive a real GPU-accelerated Chrome, screenshot it, and diff the result.
//
// Generic on purpose: it knows about browsers, waiting, settling, screenshots and image
// comparison, and nothing about any particular application. Per-project knowledge (where the
// camera goes, when a scene counts as loaded) is supplied with --wait-fn / --eval.
//
//   webshot doctor
//   webshot shot http://localhost:3000 --out out.png --wait '#app'
//   webshot check http://localhost:3000 --baseline base.png
//   webshot watch http://localhost:3000 --seconds 300
//   webshot compare a.png b.png --diff d.png
//
// Exit codes: 0 ok, 1 failure/regression, 2 software renderer, 3 missing baseline, 64 usage.

import { promises as fs, realpathSync } from 'node:fs';
import path from 'node:path';
import { execFile, spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

// Browsers are baked into the image at build time; honour an override for local checkouts.
process.env.PLAYWRIGHT_BROWSERS_PATH ??= '/opt/playwright';
const { chromium } = await import('playwright');

// ---------------------------------------------------------------------------------------------
// Environment repair.
//
// sbclaude's entrypoint fixes all of this at container start. These remain as fallbacks so
// webshot also works in a plain container, or one built before those fixes landed. Each is a
// no-op when the environment is already correct.
// ---------------------------------------------------------------------------------------------

const STATE_DIR =
  process.env.WEBSHOT_STATE_DIR ??
  path.join(process.env.XDG_CACHE_HOME || `${process.env.HOME}/.cache`, 'webshot');

async function exists(p) {
  return fs.access(p).then(
    () => true,
    () => false,
  );
}

async function stateDir(name) {
  const dir = path.join(STATE_DIR, name);
  await fs.mkdir(dir, { recursive: true });
  return dir;
}

/**
 * libglvnd finds EGL drivers only through vendor manifests. The NVIDIA container toolkit injects
 * libEGL_nvidia.so.0 but not always its manifest; without one EGL silently falls back to Mesa and
 * ANGLE dies with SIGTRAP and no diagnostic.
 */
async function fixEglIcd() {
  const dir = '/usr/share/glvnd/egl_vendor.d';
  const have = await fs.readdir(dir).then(
    (f) => f.some((x) => /nvidia/i.test(x)),
    () => false,
  );
  if (have) return {};
  // Array.find cannot take an async predicate (a Promise is always truthy), so check in order.
  let lib = null;
  for (const p of [
    '/usr/lib/x86_64-linux-gnu/libEGL_nvidia.so.0',
    '/usr/lib64/libEGL_nvidia.so.0',
  ]) {
    if (await exists(p)) {
      lib = p;
      break;
    }
  }
  if (!lib) return {};
  const out = path.join(await stateDir('icd'), '10_nvidia.json');
  await fs.writeFile(
    out,
    JSON.stringify({
      file_format_version: '1.0.0',
      ICD: { library_path: 'libEGL_nvidia.so.0' },
    }),
  );
  const mesa = `${dir}/50_mesa.json`;
  return { __EGL_VENDOR_LIBRARY_FILENAMES: (await exists(mesa)) ? `${out}:${mesa}` : out };
}

/**
 * Give the browser a config directory of our own, always.
 *
 * Chrome derives its crashpad database path from XDG_CONFIG_HOME, and in a container ~/.config is
 * often root-owned (Docker creates it as the parent of a bind mount). Chrome then cannot create
 * the directory, passes an EMPTY --database to chrome_crashpad_handler, and aborts at startup
 * with SIGTRAP and no usable diagnostic.
 *
 * Deliberately unconditional rather than "use ~/.config when it happens to be writable":
 *   - hermetic -- a screenshot tool should not depend on, or write to, the user's home config;
 *   - reproducible -- Chrome persists rendering-relevant settings there, so an ambient profile
 *     could make two runs of the same baseline differ for reasons unrelated to any change;
 *   - no permission surprises on a box where that directory is not ours.
 */
async function fixConfigHome() {
  return { XDG_CONFIG_HOME: await stateDir('config') };
}

/**
 * Mesa's libgbm looks for gbm/nvidia-drm_gbm.so; the toolkit injects libnvidia-allocator.so.1
 * under its own name only. Headless does not care, but a headed window cannot get hardware
 * buffers without it -- chrome://gpu reports "Software only" and no WebGL context is created.
 */
async function fixGbmBackend() {
  const alloc = '/usr/lib/x86_64-linux-gnu/libnvidia-allocator.so.1';
  if (await exists('/usr/lib/x86_64-linux-gnu/gbm/nvidia-drm_gbm.so')) return {};
  if (!(await exists(alloc))) return {};
  const dir = await stateDir('gbm');
  const link = path.join(dir, 'nvidia-drm_gbm.so');
  if (!(await exists(link))) await fs.symlink(alloc, link).catch(() => {});
  return { GBM_BACKENDS_PATH: dir, GBM_BACKEND: 'nvidia-drm', __GLX_VENDOR_LIBRARY_NAME: 'nvidia' };
}

async function browserEnv() {
  return {
    ...process.env,
    ...(await fixEglIcd()),
    ...(await fixConfigHome()),
    ...(await fixGbmBackend()),
  };
}

// ---------------------------------------------------------------------------------------------
// Launching.
// ---------------------------------------------------------------------------------------------

// Measured on NVIDIA + headless: only the Vulkan ANGLE backend reaches the GPU. gl-egl, egl and
// the default all silently produce SwiftShader because they want a display server.
const GPU_ARGS = [
  '--use-gl=angle',
  '--use-angle=vulkan',
  '--ignore-gpu-blocklist',
  '--enable-gpu-rasterization',
  '--enable-zero-copy',
  '--disable-gpu-vsync',
  '--disable-frame-rate-limit',
  '--disable-backgrounding-occluded-windows',
  '--disable-renderer-backgrounding',
  '--disable-background-timer-throttling',
];

// Chrome honours only the LAST --enable-features flag, so every feature must go in one list.
// Emitting two (say, SkiaGraphite here and WaylandFractionalScaleV1 for headed) would silently
// drop the first.
const BASE_FEATURES = ['SkiaGraphite'];
const featureArg = (extra = []) => `--enable-features=${[...BASE_FEATURES, ...extra].join(',')}`;
// /dev/shm is 64 MB by default under Docker, which is not enough for Chrome's renderer shared
// memory: it dies with SIGTRAP ("Aw, Snap!") partway through loading a real page.
const COMMON_ARGS = ['--hide-scrollbars', '--mute-audio', '--disable-dev-shm-usage'];
const HEADED_ARGS = [
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-sync',
  '--disable-features=ChromeWhatsNewUI,SigninIntercept,PrivacySandboxSettings4',
];

/**
 * Headless unless a window is explicitly asked for.
 *
 * Deliberately NOT "use a window if a display exists": this runs unattended during other work,
 * and a tool that pops windows over what you are doing is a bad neighbour. `watch`/`serve` opt
 * in, and `--mode auto` is available for anyone who wants display-detection.
 */
function resolveMode(mode, { preferHeaded = false } = {}) {
  if (mode && mode !== 'auto') return mode;
  if (mode === 'auto' || preferHeaded)
    return process.env.WAYLAND_DISPLAY ? 'wayland' : process.env.DISPLAY ? 'x11' : 'gpu';
  return 'gpu';
}

async function launch({ mode, cdp, width, height, scale = 1, port = null }) {
  const env = await browserEnv();
  if (mode === 'cdp') {
    const browser = await chromium.connectOverCDP(cdp);
    const context = browser.contexts()[0] ?? (await browser.newContext());
    // ownsPage: we opened this tab in someone else's browser, so we must close it again --
    // otherwise every capture against a `serve` session leaves a tab behind.
    return {
      handle: browser,
      page: await context.newPage(),
      persistent: false,
      attached: true,
      ownsPage: true,
    };
  }
  if (mode === 'wayland' || mode === 'x11') {
    const args = [
      ...GPU_ARGS,
      ...COMMON_ARGS,
      ...HEADED_ARGS,
      `--ozone-platform=${mode}`,
      `--window-size=${width},${height}`,
      '--window-position=60,60',
      // Match the compositor's scale. Without this the page renders at 1x and the
      // compositor upscales it, which is what "blocky on a 150% display" looks like.
      // ALWAYS pin the device scale factor, even at 1. Without it the compositor's scale
      // leaks in: on a 150% display a 1920-CSS-pixel window yields a 2832-pixel backing
      // store, so a headed capture is not 1:1 with --width/--height and cannot be compared
      // against a headless baseline. Pass --scale 1.5 to deliberately render at 150%.
      `--force-device-scale-factor=${scale}`,
      // Let Chrome negotiate fractional scaling rather than let the compositor upscale a
      // 1x surface, which is what makes a 150% display look blocky.
      featureArg(mode === 'wayland' ? ['WaylandFractionalScaleV1'] : []),
      ...(port ? [`--remote-debugging-port=${port}`] : []),
    ];
    // launchPersistentContext, NOT launch(): launch() starts Chrome with --no-startup-window
    // and creates the page as a CDP target, which some compositors map but never present --
    // you get a taskbar entry that will not raise.
    const context = await chromium.launchPersistentContext(
      await stateDir(`profile-${mode}`),
      // viewport null: let the real window drive the page size. Exact capture dimensions
      // come from --force-device-scale-factor plus --window-size above.
      { headless: false, channel: 'chromium', args, env, viewport: null },
    );
    return {
      handle: context,
      page: context.pages()[0] ?? (await context.newPage()),
      persistent: true,
      attached: false,
    };
  }
  // Headless. chromium-headless-shell (the default for headless:true) starts reliably in
  // containers and reaches the GPU via Vulkan; the full build is not needed here.
  const browser = await chromium.launch({
    headless: true,
    args: [...GPU_ARGS, ...COMMON_ARGS, featureArg()],
    env,
  });
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: scale,
  });
  return { handle: browser, page: await context.newPage(), persistent: false, attached: false };
}

/** Track in-flight fetches so "the app said it is ready" can be backed by "and nothing is loading". */
async function installProbes(page, { stubMidi = true } = {}) {
  await page.addInitScript(
    ({ stubMidi }) => {
      const state = { pending: 0, completed: 0, failed: [] };
      globalThis.__webshot = state;
      const original = globalThis.fetch;
      globalThis.fetch = function (...args) {
        state.pending++;
        return original.apply(this, args).then(
          (r) => {
            state.pending--;
            state.completed++;
            return r;
          },
          (e) => {
            state.pending--;
            state.failed.push(String(e));
            throw e;
          },
        );
      };
      // Web MIDI fails in a container either way (denied, or "Platform dependent
      // initialization failed" with no ALSA) and pollutes the page-error list. An empty
      // MIDIAccess is the truthful answer: there are no devices.
      if (stubMidi && globalThis.Navigator && 'requestMIDIAccess' in Navigator.prototype) {
        Object.defineProperty(Navigator.prototype, 'requestMIDIAccess', {
          configurable: true,
          writable: true,
          value: () =>
            Promise.resolve({
              inputs: new Map(),
              outputs: new Map(),
              sysexEnabled: false,
              onstatechange: null,
              addEventListener() {},
              removeEventListener() {},
            }),
        });
      }
    },
    { stubMidi },
  );
}

/**
 * Force the page's viewport and device scale factor, so a capture is exactly width x height.
 *
 * Sizing the WINDOW is not enough for a headed browser: the tab strip and borders eat into it
 * (a 1920x1080 window yields a ~1888x951 viewport), and on a scaled desktop the compositor's
 * devicePixelRatio multiplies the backing store on top of that -- 1888x951 at 1.5 becomes a
 * 2832x1426 screenshot. --force-device-scale-factor is ignored under Wayland fractional scaling.
 * Overriding the metrics through CDP pins both, so headed and headless captures are comparable.
 *
 * Only used for captures. `watch`/`serve` deliberately skip it: an interactive window should be
 * a normal window.
 */
async function forceMetrics(page, context, { width, height, scale }) {
  try {
    const s = await context.newCDPSession(page);
    await s.send('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: scale,
      mobile: false,
    });
    await s.detach().catch(() => {});
    return true;
  } catch {
    return false;
  }
}

/** Force real bounds and raise the window; harmless when headless. */
async function raiseWindow(page, context, { width, height }) {
  try {
    const s = await context.newCDPSession(page);
    const { windowId } = await s.send('Browser.getWindowForTarget');
    // A window still in its initial state ignores a bounds change, so set state first.
    await s.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal' } });
    await s.send('Browser.setWindowBounds', {
      windowId,
      bounds: { left: 60, top: 60, width, height, windowState: 'normal' },
    });
    await page.bringToFront();
    await s.detach().catch(() => {});
  } catch {
    /* headless has no window; capture does not depend on visibility */
  }
}

// ---------------------------------------------------------------------------------------------
// GPU verification.
// ---------------------------------------------------------------------------------------------

const SOFTWARE_RE = /swiftshader|llvmpipe|softpipe|software|microsoft basic/i;

async function gpuInfo(page) {
  return page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return { webgl2: false };
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      webgl2: true,
      vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
    };
  });
}

/**
 * Refuse to render on a CPU rasteriser. A software screenshot does not represent what anyone
 * sees, so comparing against it is worse than not comparing: it looks like evidence and is not.
 */
async function assertHardware(page) {
  const info = await gpuInfo(page);
  if (!info.webgl2)
    throw new Error('no WebGL2 context; container needs GPU passthrough (see: webshot doctor)');
  // Headed Chrome can return an EMPTY renderer string. Never read "no information" as
  // "not software" -- that would pass this gate vacuously.
  const identity = `${info.vendor ?? ''} ${info.renderer ?? ''}`.trim();
  if (!identity)
    throw new Error('could not identify the WebGL renderer; refusing to assume hardware');
  if (SOFTWARE_RE.test(identity))
    throw new Error(`refusing to render on a software rasteriser (${identity})`);
  return info;
}

// ---------------------------------------------------------------------------------------------
// Load / wait / settle / capture.
// ---------------------------------------------------------------------------------------------

async function loadPage(page, url, opts) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  const timeout = Number(opts.timeout ?? 120000);

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout });

  // Each wait is named, so a timeout says WHICH signal never arrived.
  const stage = async (label, fn, arg) => {
    try {
      await page.waitForFunction(fn, arg, { timeout });
    } catch {
      throw new Error(
        `stalled at '${label}' after ${timeout}ms` +
          (errors.length ? `; page errors: ${errors.slice(0, 3).join(' | ')}` : '; no page errors'),
      );
    }
  };
  if (opts.wait) await page.waitForSelector(opts.wait, { timeout, state: 'attached' });
  // Pass the expression through as a STRING: Playwright evaluates it in the page. Building a
  // function from it here would run it in Node, where `window` does not exist.
  if (opts.waitFn) await stage('--wait-fn', opts.waitFn);
  if (!opts.noNetworkIdle) await stage('network-idle', () => globalThis.__webshot.pending === 0);
  return errors;
}

/**
 * Screenshot repeatedly until two consecutive captures are byte-identical.
 *
 * Pixel equality between frames is a general "it has settled" signal: it catches async texture
 * uploads and progressive rendering that no fixed sleep reliably would. Capture goes through CDP
 * rather than canvas.toDataURL() because WebGL contexts are usually created with
 * preserveDrawingBuffer:false, where the drawing buffer is gone by the time script runs.
 */
async function capture(page, opts) {
  const target = opts.selector ? page.locator(opts.selector).first() : page;
  if (opts.selector)
    await target.waitFor({ state: 'attached', timeout: Number(opts.timeout ?? 120000) });
  const shoot = () =>
    opts.selector
      ? target.screenshot({ type: 'png' })
      : page.screenshot({ type: 'png', fullPage: !!opts.fullPage });

  if (opts.noSettle) return { buffer: await shoot(), attempts: 1, settled: true };

  const maxAttempts = Number(opts.maxAttempts ?? 20);
  let previous = null,
    stable = 0,
    shot = null;
  for (let i = 0; i < maxAttempts; i++) {
    await page.evaluate(
      () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
    );
    shot = await shoot();
    if (previous && shot.equals(previous)) {
      if (++stable >= Number(opts.settleFrames ?? 2) - 1)
        return { buffer: shot, attempts: i + 1, settled: true };
    } else stable = 0;
    previous = shot;
    await page.waitForTimeout(Number(opts.frameDelay ?? 120));
  }
  return { buffer: shot, attempts: maxAttempts, settled: false };
}

/** Read a PNG's pixel dimensions straight from its IHDR chunk (bytes 16..24). */
async function pngSize(buffer) {
  if (buffer.length < 24) return null;
  return [buffer.readUInt32BE(16), buffer.readUInt32BE(20)];
}

/**
 * Say so when the capture is not the size that was asked for.
 *
 * On a scaled desktop (e.g. 150%) a headed capture comes out at width*dpr by height*dpr: the
 * logical viewport is correct, but Chrome ignores deviceScaleFactor overrides under Wayland
 * fractional scaling, so the backing store is supersampled. That is fine to look at and fine to
 * compare headed-against-headed, but it is NOT byte-comparable with a headless baseline -- and
 * silently emitting a differently-sized image would make a later diff meaningless.
 */
function warnScale(size, opts, mode) {
  if (!size) return;
  const want = [Number(opts.width ?? 1920), Number(opts.height ?? 1080)];
  const scale = Number(opts.scale ?? 1);
  if (size[0] === want[0] * scale && size[1] === want[1] * scale) return;
  console.error(
    `NOTE: captured ${size[0]}x${size[1]}, requested ${want[0]}x${want[1]}` +
      ` (${(size[0] / want[0]).toFixed(2)}x). Your display scale is applied in '${mode}' mode;` +
      ' use headless (the default) for baselines that must match exactly.',
  );
}

/** ImageMagick RMSE normalised to 0..1. `compare` exits non-zero whenever images differ at all. */
async function compareImages(a, b, diffPath = null) {
  let stderr = '';
  try {
    ({ stderr } = await execFileAsync('compare', ['-metric', 'RMSE', a, b, diffPath ?? 'null:']));
  } catch (e) {
    stderr = e.stderr ?? '';
    if (!/\(([0-9.eE+-]+)\)/.test(stderr))
      throw new Error(`compare failed: ${String(stderr).trim() || e.message}`);
  }
  const m = stderr.match(/\(([0-9.eE+-]+)\)/);
  if (!m) throw new Error(`could not parse RMSE from: ${stderr.trim()}`);
  return Number(m[1]);
}

// ---------------------------------------------------------------------------------------------
// CLI.
// ---------------------------------------------------------------------------------------------

function parseArgs(argv) {
  const out = { _: [] };
  const camel = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) {
      out._.push(a);
      continue;
    }
    const [k, inline] = a.slice(2).split('=');
    const next = argv[i + 1];
    if (inline !== undefined) out[camel(k)] = inline;
    else if (next === undefined || next.startsWith('--')) out[camel(k)] = true;
    else {
      out[camel(k)] = next;
      i++;
    }
  }
  return out;
}

async function withBrowser(opts, fn) {
  const width = Number(opts.width ?? 1920);
  const height = Number(opts.height ?? 1080);
  const scale = Number(opts.scale ?? 1);
  const mode = resolveMode(opts.mode, { preferHeaded: !!opts._preferHeaded });
  const { handle, page, persistent, attached, ownsPage } = await launch({
    mode,
    cdp: opts.cdp,
    width,
    height,
    scale,
    port: opts.port ? Number(opts.port) : null,
  });
  try {
    await installProbes(page, { stubMidi: !opts.noMidiStub });
    const context = persistent ? handle : page.context();
    if (!attached && (mode === 'wayland' || mode === 'x11')) {
      await raiseWindow(page, context, { width, height });
      // Captures need exact pixels; interactive sessions want a natural window.
      if (opts._exactMetrics) await forceMetrics(page, context, { width, height, scale });
    }
    return await fn(page, { mode, width, height, handle, persistent });
  } finally {
    // Close the tab we opened before dropping the connection. For a CDP-attached browser
    // handle.close() only disconnects, so without this the tab would outlive us.
    if (ownsPage) await page.close().catch(() => {});
    if (!opts._keepOpen) await handle.close().catch(() => {});
  }
}

const DEFAULT_PORT = 9222;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Is a CDP endpoint accepting connections? */
async function cdpAlive(port) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/json/version`, {
      signal: AbortSignal.timeout(1000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function setWindowStateOn(context, page, windowState) {
  const s = await context.newCDPSession(page);
  const { windowId } = await s.send('Browser.getWindowForTarget');
  await s.send('Browser.setWindowBounds', { windowId, bounds: { windowState } });
  if (windowState !== 'minimized') await page.bringToFront().catch(() => {});
  await s.detach().catch(() => {});
}

/**
 * Set the window state of every window in a running browser reached over CDP.
 *
 * Note it never calls browser.close(): over a CDP connection that would terminate the browser we
 * are trying to keep alive. Letting the process exit closes the socket cleanly instead.
 */
async function setWindowState(port, windowState) {
  if (!(await cdpAlive(port)))
    throw new Error(`no browser listening on ${port}; start one with: webshot serve <url>`);
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  const context = browser.contexts()[0];
  const pages = context?.pages() ?? [];
  if (!pages.length) throw new Error('browser has no open pages');
  for (const page of pages) await setWindowStateOn(context, page, windowState);
  return pages.length;
}

/** Load, optionally run project-specific setup, then capture. */
async function runCapture(page, url, opts) {
  if (!opts.allowSoftware) {
    await page.goto('about:blank');
    await assertHardware(page);
  }
  const errors = await loadPage(page, url, opts);
  const script = opts.evalFile ? await fs.readFile(opts.evalFile, 'utf8') : opts.eval;
  let evalResult;
  if (script) {
    // Run the project's own setup: position a camera, hide UI, seed state. Awaited, so it can
    // be async.
    evalResult = await page.evaluate(`(async () => { ${script} })()`);
  }
  if (opts.delay) await page.waitForTimeout(Number(opts.delay));
  return { ...(await capture(page, opts)), errors, evalResult };
}

const COMMANDS = {
  async doctor(opts) {
    return withBrowser(opts, async (page, { mode }) => {
      await page.goto('about:blank');
      const info = await gpuInfo(page);
      const identity = `${info.vendor ?? ''} ${info.renderer ?? ''}`.trim();
      const software = SOFTWARE_RE.test(identity);
      console.log(JSON.stringify({ mode, ...info, software }, null, 2));
      if (!info.webgl2) {
        console.error('\nFAIL: no WebGL2 context.');
        process.exitCode = 1;
      } else if (software) {
        console.error(`\nWARNING: software renderer (${identity}).`);
        process.exitCode = 2;
      } else console.error(`\nOK: hardware renderer -- ${identity || '(unnamed)'}`);
    });
  },

  async shot(opts) {
    const url = opts._[0];
    if (!url) throw new Error('usage: webshot shot <url> [--out FILE]');
    const out = opts.out ?? 'webshot.png';
    return withBrowser({ ...opts, _exactMetrics: true }, async (page, { mode }) => {
      const r = await runCapture(page, url, opts);
      await fs.mkdir(path.dirname(path.resolve(out)), { recursive: true });
      await fs.writeFile(out, r.buffer);
      const size = await pngSize(r.buffer);
      console.log(
        JSON.stringify(
          {
            url,
            file: out,
            mode,
            size,
            requested: [Number(opts.width ?? 1920), Number(opts.height ?? 1080)],
            settled: r.settled,
            attempts: r.attempts,
            evalResult: r.evalResult,
            pageErrors: r.errors.slice(0, 10),
          },
          null,
          2,
        ),
      );
      if (!r.settled) console.error('WARNING: frames never stabilised; capture may be mid-load.');
      warnScale(size, opts, mode);
    });
  },

  async check(opts) {
    const url = opts._[0];
    const baseline = opts.baseline;
    if (!url || !baseline) throw new Error('usage: webshot check <url> --baseline FILE');
    const threshold = Number(opts.threshold ?? 0.02);
    const actual = opts.out ?? 'webshot-actual.png';
    return withBrowser({ ...opts, _exactMetrics: true }, async (page, { mode }) => {
      const r = await runCapture(page, url, opts);
      await fs.mkdir(path.dirname(path.resolve(actual)), { recursive: true });
      await fs.writeFile(actual, r.buffer);
      if (!(await exists(baseline))) {
        console.log(
          JSON.stringify(
            { status: 'NO_BASELINE', actual, hint: `cp ${actual} ${baseline}` },
            null,
            2,
          ),
        );
        process.exitCode = 3;
        return;
      }
      const diff = opts.diff ?? `${actual.replace(/\.png$/, '')}.diff.png`;
      const rmse = await compareImages(baseline, actual, diff);
      const pass = rmse <= threshold;
      console.log(
        JSON.stringify(
          { status: pass ? 'PASS' : 'FAIL', rmse, threshold, baseline, actual, diff, mode },
          null,
          2,
        ),
      );
      if (!pass) process.exitCode = 1;
    });
  },

  async compare(opts) {
    const [a, b] = opts._;
    if (!a || !b) throw new Error('usage: webshot compare <a.png> <b.png> [--diff FILE]');
    const diff = opts.diff ?? null;
    if (diff) await fs.mkdir(path.dirname(path.resolve(diff)), { recursive: true });
    console.log(JSON.stringify({ a, b, rmse: await compareImages(a, b, diff), diff }, null, 2));
  },

  /**
   * Long-lived browser with a debug port, left running after this process exits.
   * Pair with `webshot hide` / `webshot show` to put it away and bring it back without
   * reloading the page or losing state.
   */
  async serve(opts) {
    const url = opts._[0];
    if (!url) throw new Error('usage: webshot serve <url> [--port 9222] [--hidden]');
    const port = Number(opts.port ?? DEFAULT_PORT);
    const width = Number(opts.width ?? 1920);
    const height = Number(opts.height ?? 1080);
    const scale = Number(opts.scale ?? 1);
    const mode = resolveMode(opts.mode, { preferHeaded: true });

    if (await cdpAlive(port))
      throw new Error(
        `something is already listening on ${port}; use --port, or 'webshot stop --port ${port}'`,
      );

    // Spawn Chrome ourselves, detached. Playwright terminates browsers it launched when the
    // owning process exits, which is the opposite of what a long-lived session needs.
    const args = [
      ...GPU_ARGS,
      ...COMMON_ARGS,
      ...HEADED_ARGS,
      // Playwright passes this for the browsers IT launches; serve spawns Chrome directly,
      // so it must pass it too. Without it the zygote aborts with "No usable sandbox!"
      // before the debug port is ever opened. (We are already inside a container.)
      '--no-sandbox',
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${await stateDir(`serve-${port}`)}`,
      `--window-size=${width},${height}`,
      '--window-position=60,60',
      ...(mode === 'gpu' ? ['--headless=new'] : [`--ozone-platform=${mode}`]),
      featureArg(mode === 'wayland' ? ['WaylandFractionalScaleV1'] : []),
      ...(scale !== 1 ? [`--force-device-scale-factor=${scale}`] : []),
      url,
    ];
    // Keep the browser's own stderr: when it dies before opening the port, that log is the
    // only explanation there is.
    const logPath = path.join(await stateDir('logs'), `serve-${port}.log`);
    const log = await fs.open(logPath, 'w');
    const child = spawn(chromium.executablePath(), args, {
      detached: true,
      stdio: ['ignore', log.fd, log.fd],
      env: await browserEnv(),
    });
    child.unref();
    await log.close();

    for (let i = 0; i < 100 && !(await cdpAlive(port)); i++) await sleep(200);
    if (!(await cdpAlive(port))) {
      const tail = await fs.readFile(logPath, 'utf8').then(
        (t) => t.trim().split('\n').slice(-3).join('\n'),
        () => '(no log)',
      );
      throw new Error(
        `browser did not open a debug port on ${port}\n${tail}\n  full log: ${logPath}`,
      );
    }

    const script = opts.evalFile ? await fs.readFile(opts.evalFile, 'utf8') : opts.eval;
    if (script || opts.hidden) {
      const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
      const context = browser.contexts()[0];
      const page = context.pages()[0] ?? (await context.newPage());
      if (script) {
        await page.waitForLoadState('domcontentloaded').catch(() => {});
        await page.evaluate(`(async () => { ${script} })()`).catch((e) => {
          console.error(`warning: --eval failed: ${e.message}`);
        });
      }
      if (opts.hidden) await setWindowStateOn(context, page, 'minimized');
      // Deliberately no browser.close(): over a CDP connection that would terminate the
      // browser we are trying to leave running. Exiting the process drops the socket, and
      // Chrome does not care when a DevTools client goes away.
    }
    console.log(
      JSON.stringify(
        {
          url,
          mode,
          port,
          pid: child.pid,
          hidden: !!opts.hidden,
          show: `webshot show --port ${port}`,
          hide: `webshot hide --port ${port}`,
          capture: `webshot shot ${url} --mode cdp --cdp http://127.0.0.1:${port}`,
          stop: `webshot stop --port ${port}`,
        },
        null,
        2,
      ),
    );
    process.exit(0); // Leave the detached browser running.
  },

  async stop(opts) {
    const port = Number(opts.port ?? DEFAULT_PORT);
    if (!(await cdpAlive(port))) throw new Error(`nothing listening on ${port}`);
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    await browser.close(); // Here we DO want the browser gone.
    console.log(JSON.stringify({ port, stopped: true }, null, 2));
  },

  async show(opts) {
    const port = Number(opts.port ?? DEFAULT_PORT);
    console.log(
      JSON.stringify(
        { port, windows: await setWindowState(port, 'normal'), state: 'shown' },
        null,
        2,
      ),
    );
  },

  async hide(opts) {
    const port = Number(opts.port ?? DEFAULT_PORT);
    console.log(
      JSON.stringify(
        { port, windows: await setWindowState(port, 'minimized'), state: 'hidden' },
        null,
        2,
      ),
    );
  },

  async watch(opts) {
    const url = opts._[0];
    if (!url) throw new Error('usage: webshot watch <url> [--seconds N]');
    const seconds = Number(opts.seconds ?? 300);
    return withBrowser({ ...opts, _preferHeaded: true }, async (page, { mode }) => {
      await loadPage(page, url, opts);
      const script = opts.evalFile ? await fs.readFile(opts.evalFile, 'utf8') : opts.eval;
      if (script) await page.evaluate(`(async () => { ${script} })()`);
      console.log(JSON.stringify({ url, mode, window: 'open', seconds }, null, 2));
      console.error(`\nWindow open for ${seconds}s. Ctrl-C to close early.`);
      await page.waitForTimeout(seconds * 1000);
    });
  },
};

// Exported for tests. Everything above is side-effect free; only the block below acts.
export { compareImages, parseArgs, pngSize, warnScale, resolveMode, SOFTWARE_RE };

// Run the CLI only when executed directly, so a test can import the helpers without the
// process exiting on a usage error.
// argv[1] must be resolved through symlinks first: `webshot` is installed as a symlink
// (/usr/local/bin/webshot -> /opt/webshot/webshot.mjs), and Node sets import.meta.url to the
// REAL path while argv[1] keeps the link path. Comparing them unresolved makes this false for
// every symlinked invocation, so the CLI silently does nothing and exits 0 -- which looks
// exactly like a passing check.
const invokedDirectly =
  process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href;
if (!invokedDirectly) {
  // Imported as a module: nothing else to do.
} else {
  const argv = process.argv.slice(2);
  const cmd = argv[0];
  if (!cmd || !COMMANDS[cmd]) {
    console.error(`webshot -- GPU browser screenshots and visual diffs

  webshot doctor                       what GPU are we actually on?
  webshot shot <url> --out FILE        capture (headless)
  webshot check <url> --baseline FILE  capture and diff (exit 1 on regression)
  webshot compare <a> <b> --diff FILE  one-off image diff
  webshot watch <url> --seconds N      open a window for a while, then close it

long-lived session -- put it away and bring it back without losing page state
  webshot serve <url> [--hidden]       start a detached browser with a debug port
  webshot hide                         minimise it (stops it disturbing you)
  webshot show                         restore and raise it
  webshot stop                         close it

Everything is HEADLESS unless you ask for a window (serve/watch, or --mode wayland|x11),
so this never pops something over what you are doing.

options
  --mode gpu|wayland|x11|cdp|auto   default gpu (headless); auto picks a display if present
  --cdp URL                         attach to an existing browser
  --width N --height N              viewport (default 1920x1080)
  --scale N                         device scale factor; use 1.5 to match a 150% display
  --port N                          debug port for serve/show/hide/stop (default 9222)
  --wait SELECTOR                   wait for a CSS selector
  --wait-fn 'JS'                    wait until an expression returns truthy
  --no-network-idle                 skip waiting for in-flight fetches to finish
  --eval 'JS' | --eval-file FILE    run setup in the page before capturing (awaited)
  --selector CSS                    screenshot one element instead of the viewport
  --full-page                       capture the full scrollable page
  --no-settle                       capture immediately instead of waiting for stable frames
  --settle-frames N --max-attempts N --frame-delay MS
  --delay MS                        extra pause before capturing
  --threshold N                     max RMSE for check to pass (default 0.02)
  --timeout MS                      per-stage timeout (default 120000)
  --allow-software                  do NOT refuse a software renderer (off by default)`);
    process.exit(64);
  }
  COMMANDS[cmd](parseArgs(argv.slice(1))).catch((e) => {
    console.error(`error: ${e.message}`);
    process.exit(1);
  });
}
