# Starting a new noclip integration

How to go from "I have a game's data files" to "a scene in noclip with an automated visual
regression gate". Written from the THPS2 integration in this repo; the paths below are real.

There are three pieces, and only the first is real work:

1. **The scene** — parser + renderer + `SceneDesc`, in `noclip.website/src/<Game>/`
2. **The data** — converted assets under `noclip.website/data/<Game>/`
3. **The view tests** — `viewer-tests/`, about 15 lines of config

---

## 1. The scene

Start from `noclip.website/src/Example/Scenes.ts` (663 lines, heavily commented — it is a
tutorial, not just a stub). A simpler real-world reference is `src/CrazyTaxi/`.

You need, at minimum:

```ts
const pathBase = `MyGame`;                       // relative to data/

class MyScene implements SceneGfx {
    public render(device: GfxDevice, viewerInput: ViewerRenderInput) { ... }
    public destroy(device: GfxDevice) { ... }    // release every Gfx resource you created
}

class MySceneDesc implements SceneDesc {
    constructor(public id: string, public name: string) {}
    public async createScene(device: GfxDevice, context: SceneContext): Promise<SceneGfx> {
        const data = await context.dataFetcher.fetchData(`${pathBase}/level1.bin`);
        return new MyScene(/* ... */);
    }
}

export const sceneGroup: SceneGroup = {
    id: 'MyGame',                                 // URL id -- keep STABLE, people bookmark it
    name: 'My Game',
    sceneDescs: [ new MySceneDesc('Level1', 'Level 1') ],
};
```

Then register it in `src/main.ts` — two lines, and the only edit to an existing noclip file:

```ts
import * as Scenes_MyGame from './MyGame/scenes.js';
// ...and add `Scenes_MyGame.sceneGroup,` to the `sceneGroups` array
```

The scene is then at `#MyGame/Level1`.

### Data

Put converted assets in `noclip.website/data/<Game>/`. The dev server serves `data/` at `/data`
(see the `serveData` middleware in `rsbuild.config.ts`), including directory listings and range
requests, so you can browse `http://localhost:8124/data/MyGame/` while working.

Fetch with `context.dataFetcher.fetchData(path)`. Pass `{ allow404: true }` for optional files and
check `byteLength > 0` — that is how THPS2 handles levels that have no object or trigger file.

Prefer parsing the game's real format at runtime over pre-converting to a bespoke intermediate:
it keeps the reverse-engineering honest and the data directory small. THPS2 ships the original
`.PSX`/`.TRG` files and only pre-converts textures (BMP → PNG), because the browser cannot decode
the original texture container.

---

## 2. Running it

```bash
cd noclip.website
npx rsbuild dev --port 8124        # then open http://localhost:8124/#MyGame/Level1
```

`npm start` also works but goes through wireit and rebuilds the Rust/WASM bits first.

**A GPU is required.** noclip deliberately refuses to run on SwiftShader:
`initializeViewerWebGL2` returns `GARBAGE_WEBGL2_SWIFTSHADER`, falls through to WebGPU, finds no
adapter, and `init()` returns early — leaving `window.main.viewer` **undefined forever, with no
error in the console**. If the viewer never appears, check the renderer before debugging anything
else: `webshot doctor`.

---

## 3. The view tests

`viewer-tests/` is three layers, and only the last is specific to a game:

| Layer        | Where                                            | Changes per game?                              |
| ------------ | ------------------------------------------------ | ---------------------------------------------- |
| `webshot`    | `/usr/local/bin/webshot`, in the container image | never                                          |
| `nc.mjs`     | `viewer-tests/nc.mjs`                            | copy verbatim — it talks only to `window.main` |
| `views.json` | `viewer-tests/views.json`                        | **the only file you write**                    |

To set it up for a new game: copy `nc.mjs`, `mkdir baselines out`, and write:

```json
{
  "antialiasing": 2,
  "base": "http://127.0.0.1:8124",
  "scene": "MyGame/Level1",
  "threshold": 0.02,
  "views": {
    "level1-overview": { "saveState": "AbbJBAAAAAAAAAAAR@jEh=" }
  }
}
```

Then:

```bash
./nc.mjs shot  level1-overview     # capture to out/
./nc.mjs bless level1-overview     # approve it as the baseline (always a deliberate act)
./nc.mjs check level1-overview     # capture + diff; exit 1 on regression
./nc.mjs watch level1-overview     # open a window and fly around
./nc.mjs where                     # print the current camera, to author a new view
```

### Placing the camera

Three options; pick by how settled your importer is.

| Form                            | Needs               | Use when                                                                                                                          |
| ------------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `"saveState": "..."`            | nothing             | **Bootstrapping.** `watch`, fly to a good spot, copy the `;ShareData=...` from the URL bar. It is noclip's own share-link format. |
| `"eye": [...], "target": [...]` | nothing             | You want a specific, human-readable vantage point. `./nc.mjs where` prints it in the right shape.                                 |
| `"fitScene": true`              | `scene.worldBounds` | **Steady state.** Frames the level from its own geometry.                                                                         |

`fitScene` is the only one needing scene-side support — a `worldBounds` field accumulated as you
build vertex buffers (see `src/THPS2/scenes.ts`):

```ts
public worldBounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
```

It is worth adding early. Hardcoded coordinates silently reframe the moment you fix a scale or
origin bug, and the resulting diff screams about a camera move rather than the render change you
actually made. `fitScene` re-derives from geometry every run, so it survives that.

Tune framing with `"fit": { "margin": 0.42, "azimuth": 0.9, "elevation": 0.45 }`. A margin below
1 zooms in — useful when a few stray distant polygons inflate the bounding sphere.

---

## Gotchas worth knowing before you hit them

Each of these cost real time here, and none produces a useful error message.

- **The viewer initialises asynchronously, and `main.scene` is a getter that throws.** It reads
  `this.viewer.scene`, so optional chaining on `main` does not save you. The correct ready test is
  `!!window.main?.viewer && window.main.scene !== null`.
- **`canvas.toDataURL()` returns garbage.** noclip creates its WebGL2 context with
  `preserveDrawingBuffer: false`, so the drawing buffer is gone by the time script runs. Capture
  has to go through CDP, which reads the compositor's presented frame. `webshot` does this.
- **Settings live in `localStorage`, not in code.** Antialiasing especially: a baseline blessed on
  one browser profile will diff against another for reasons unrelated to your renderer. Pin
  anything your scene persists — `views.json` pins `AntialiasingMode`.
- **Freeze animation before comparing.** `viewer.sceneTimeScale = 0`, and hide the UI with
  `main.ui.toggleUI(false)`. `nc.mjs` does both.
- **"Settled" means pixel-stable, not "after a sleep."** `webshot` re-captures until two
  consecutive frames are byte-identical, which catches async texture uploads that no fixed delay
  reliably would.
- **Treat `baselines/*.png` as source, not output.** They are the accumulated record of what you
  have judged correct, and nothing can regenerate that judgement. A missing baseline is at least
  loud — `check` exits **3** with `NO_BASELINE`, distinct from pass (0) and regression (1) — but
  losing one silently resets the gate to "whatever it renders now is fine".
- **Real-game screenshots are not baselines.** A WebGL re-implementation is never pixel-equal to
  captured hardware footage — expect RMSE around 0.3–0.6. Reference images are reported and never
  gate; only your own blessed output gates.

---

## Checklist

- [ ] Formats reversed far enough to get geometry + UVs out
- [ ] `src/<Game>/` — parser, `SceneGfx` (with a real `destroy`), `SceneDesc`, `sceneGroup`
- [ ] Registered in `src/main.ts` (import + push to `sceneGroups`)
- [ ] Assets under `data/<Game>/`; optional files fetched with `allow404`
- [ ] Loads in a browser at `#<Game>/<Level>`; `webshot doctor` shows a hardware renderer
- [ ] `viewer-tests/`: `nc.mjs` copied, `views.json` written, `baselines/` and `out/` created
- [ ] One view captured, eyeballed, and blessed
- [ ] `./nc.mjs check <view>` passes — and **verify it fails** when you change something, or you
      have a gate that is not testing anything
- [ ] `worldBounds` added, and views moved to `fitScene`, once the importer settles
