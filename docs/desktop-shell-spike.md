# Desktop Shell Spike: Tauri v2 vs Electron vs native

> **Status:** spike / decision record (draft for review)
> **Epic:** #303 — AgentOS Desktop App
> **Scope of this document:** choose the shell framework for the first-party
> desktop app. Everything downstream (gateway supervision, onboarding,
> packaging, CI) depends on this call, so it lands first.

## Decision

**Adopt Tauri v2** as the desktop shell, loading the existing `frontend/`
build from the supervised local gateway over `http://127.0.0.1:<port>/control`.

Electron is the runner-up and the fallback if a hard blocker surfaces during
the gateway-supervision prototype. A fully native shell (SwiftUI / WinUI /
GTK per platform) is rejected for v1.

## Constraints from the existing codebase

These are findings from reading the gateway source, not assumptions:

1. **The desktop app is a client, not a fork.** Every existing surface (CLI,
   TUI, Web UI, channels, MCP bridge) speaks WebSocket JSON-RPC at `/ws` or
   REST at `/api/*`. The desktop app must do the same
   (`src/agentos/gateway/websocket.py`, `docs/http-api.md`).

2. **CSWSH origin gate** (`is_allowed_ws_origin` in
   `src/agentos/gateway/websocket.py`). On a loopback bind the WS handshake is
   rejected (close 1008) unless:
   - no `Origin` header (non-browser clients), or
   - the origin host is loopback, or
   - the origin matches `control_ui.allowed_origins`.

   Consequences per shell:
   - **Tauri webview** (`tauri://localhost` on macOS/Linux, `http://tauri.localhost`
     on Windows): the Windows origin parses as non-loopback → **needs either
     an `allowed_origins` entry or, better, the frontend loaded over
     `http://127.0.0.1`** (which the supervised gateway already serves via
     `control_ui.py`'s static mount). Loading from the gateway URL sidesteps
     the gate entirely on every platform and keeps one code path with the
     browser UI. This is the recommended wiring.
   - **Electron `file://`**: sends `Origin: null` → rejected. Same fix: load
     over `http://127.0.0.1`. Works, but then Electron's "bundled app" story
     still depends on the gateway being up — which it always must be anyway,
     since the gateway *is* the product.
   - **Native client**: sends no `Origin` → allowed unconditionally.

3. **Auth is binary** (`src/agentos/gateway/auth.py`): `mode:none`
   (loopback-only) or `mode:token` (single shared token, no scopes or
   revocation). A supervising desktop app should generate its own token at
   first run and store it in the OS keychain. Both Tauri (Rust keyring crate)
   and Electron (safeStorage / keytar) can do this; neither has an advantage
   that changes the decision.

4. **`ClientInfo` handshake** (`src/agentos/gateway/protocol.py`) already
   carries `platform`, `device_family`, `mode`, `instance_id` — the desktop
   app just populates it. No protocol change needed for either shell.

5. **Frontend reuse**: the Web UI is a standard Vite/React build emitted to
   `src/agentos/gateway/static/dist` with a runtime-supplied `<base>`
   (`frontend/vite.config.ts`, `control_ui.py`). Because it is served over
   HTTP by the gateway itself, **both** shells can load the exact same
   artifact — no second Vite target strictly required for v1 (a desktop-only
   target can come later for tray-only windows, hotkey overlays, etc.).

## Comparison

| Criterion | Tauri v2 | Electron | Native (Swift/Win/GTK) |
|---|---|---|---|
| **Origin gate** | ✅ load via `http://127.0.0.1` → loopback, allowed | ⚠️ `file://` sends `Origin: null` → rejected; must load via `http://127.0.0.1` too | ✅ no `Origin` header |
| **Installer size** | ~8 MB (uses OS webview) | ~90+ MB (bundles Chromium) | smallest, but per-platform |
| **Idle RAM** | ~45–95 MB | ~140–280 MB | lowest |
| **Frontend reuse** | ✅ same `dist` artifact, WebKit/WebView2 quirks to test | ✅ same artifact, Chromium parity with dev | ❌ re-implement the UI per platform |
| **Gateway supervision** (spawn/health-check/restart/log capture) | ✅ Rust `std::process` + sidecar pattern; strong fit | ✅ Node `child_process`; equally capable | ✅ but written 3× |
| **OS integration** (tray, global hotkey, notifications, deep links) | ✅ official plugins (`tauri-plugin-global-shortcut`, notification, deep-link, single-instance) | ✅ mature APIs | ✅ but written 3× |
| **Auto-update** | ✅ built-in updater, tiny delta payloads, signed manifest | ✅ electron-updater, bigger payloads | ❌ custom per platform |
| **Signing/notarization** | same cost both ways (Apple $99/yr, Windows EV cert) | same | same ×3 |
| **Team cost** | Rust needed for shell glue (thin: process mgmt + plugin wiring) | Node only | 3 codebases |
| **Security defaults** | IPC allowlist, no implicit Node in webview | context isolation good, but preload discipline required | n/a |

### Notes on the trade-offs

- **Webview quirks (Tauri's real cost).** The UI renders in WebKit (macOS) and
  WebView2 (Windows), not Chromium. The Web UI targets modern CSS; expect a
  test pass on all three platforms. This is a QA cost, not an architecture
  cost — the DOM/WS/RPC layers are unaffected.
- **Rust ramp-up is bounded.** The desktop shell's Rust surface is small:
  spawn/supervise the gateway, open the window at the gateway URL, wire four
  plugins (tray, hotkey, notifications, deep links), keychain token storage.
  No business logic moves to Rust.
- **Electron's killer feature — Node in the main process — buys little here**
  because the heavy lifting already lives in the Python gateway, which the
  app supervises either way. Bundling Chromium to render a UI the OS webview
  renders fine is ~200 MB of dead weight for this product's shape.
- **Native is rejected** for v1: triplicating chat/sessions/onboarding against
  a moving `frontend/` is the slowest path to the epic's actual goal
  ("install the app, never touch a terminal").

## Risks / open items for the next child issue

1. **Prototype gate (time-boxed, ~2 days):** Tauri shell + supervised gateway
   + WS handshake over `http://127.0.0.1` on macOS and Windows. If the
   WebView2/WebKit rendering of the Web UI shows a blocking defect, fall back
   to Electron (same supervision design, heavier bundle).
2. **Windows origin check:** confirm the loaded URL (`http://127.0.0.1:<port>`)
   keeps `Origin` loopback inside the Tauri window on Windows (it should —
   the webview navigates to an http URL, not `tauri.localhost`).
3. **Port collisions:** supervision must allocate a free loopback port when
   18791 is taken (the CLI already documents `AGENTOS_GATEWAY` overrides; the
   desktop app should probe, then pass the chosen port to the window).
4. **Updates of the gateway itself:** the Tauri updater updates the shell;
   the bundled/supervised gateway payload needs its own versioning story
   (ship gateway inside the installer per release is simplest for v1).

## Alternatives considered

- **Electron:** viable fallback; rejected as the primary because the bundle/
  RAM cost buys nothing this architecture needs.
- **Native per-platform:** rejected; triples UI work for marginal gains.
- **Attach-only thin client (no supervision):** rejected by the epic itself —
  it leaves users needing a terminal, which is the problem being solved.
