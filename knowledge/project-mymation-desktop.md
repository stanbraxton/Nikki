# Mymation Desktop — local-only Tauri app

Mymation Desktop is Stan's local-only adaptive AI assistant (formerly named Youmation/Adaptive Assistant), built as a Tauri v2 app with llama.cpp for local inference. It is designed for private, offline-first AI interaction with local memory management. It is **not** related to your own hosting/platform — this is a separate desktop product Stan builds. Distinguish it clearly from Mymation cloud SaaS and from Mymation Studio/GraphWorks, which are different products.

Source of truth for specs: `projects/adaptive-memory-desktop/SPEC.md`. Code lives at `projects/adaptive-memory-desktop/app/`. If you're asked to build, package, test, or debug this app, read the full spec first.

## Business & pricing
- Product name: **Mymation**; bundle id `com.greencollar.mymation`. Renamed from Youmation for trademark reasons. [Stan/app, 2026-08-14]
- Pricing: $99/year perpetual-fallback license only — no lifetime tier and no monthly tier. [SPEC, 2026-08-14]
- License enforcement: offline Ed25519 verification plus an update build-date gate. [SPEC, 2026-08-14]

## Architecture
- Tech stack: Tauri desktop shell + Rust core crate + bundled llama.cpp sidecar binaries.
- `crates/assistant-core` must stay Tauri-free: sidecar lifecycle, SSE chat client, and all memory/reflection/extraction logic and tests belong there.
- Keep `src-tauri/` (the Tauri shell glue) thin. Note: this sandbox environment lacks webkit/gtk dev libraries, so `src-tauri/` cannot be compiled here — only `assistant-core` can be built/tested locally in a sandbox.
- Webview never sees the sidecar port or the API key; Tauri capabilities are kept minimal (`core:default` + dialog only).
- Behavior rules belong in the system-prompt layer, not in user chat — small local models tend to execute pasted rules instead of storing them as instructions.
- `nomic-embed` requires task prefixes; always use the `EmbedTask` enum (`search_document:` / `search_query:`) rather than raw strings.

### llama.cpp and model notes
- Pinned llama.cpp release: **b10430**; only bump after passing smoke tests. [smoke-tested, 2026-08-14]
- `--flash-attn` requires an explicit value (`on|off|auto`); passing a bare flag with no value kills startup.
- Release binaries are dynamically linked, so the child process environment must include the bundled lib dir in `LD_LIBRARY_PATH`, `DYLD_FALLBACK_LIBRARY_PATH`, and `PATH`.
- Release archive formats: `.tar.gz` for mac/linux, `.zip` for Windows; use the `bin-win-vulkan-x64` asset for Windows.
- Sandbox model set for testing: SmolLM2-135M Q4_K_M for chat plumbing, Qwen2.5-1.5B-Instruct Q4_K_M for extraction-quality tests, nomic-embed-text-v1.5 Q8_0 for embeddings. Note: SmolLM2 is too weak for extraction-quality testing — use Qwen2.5-1.5B for that.

## Data model
- Storage: SQLite with the `sqlite-vec` extension for vector similarity search.
- `sqlite-vec` KNN distance is NULL for zero query vectors — read the value as `Option<f64>`, not a bare float.
- `vec0` virtual-table rows are keyed by `rowid`, joined to `memory_chunk.rowid`; embeddings are stored as f32-LE binary blobs.
- Gotcha: on Stan's real Mac, the `memory_event` table has no `kind` column — do not assume it exists when writing support queries or diagnosing issues there. [Stan Mac, 2026-08-14]

## Verification / smoke-test commands
Run these when building, packaging, or changing model/prompt behavior:
- Core chat smoke: `LLAMA_SERVER_BIN=… MODEL=… LLAMA_LIB_DIR=… cargo run --example smoke`
- Memory smoke: `LLAMA_SERVER_BIN=… EMBED_MODEL=… cargo run --example memory_smoke`
- Reflection smoke: `CHAT_MODEL=… EMBED_MODEL=… cargo run --example reflection_smoke`
- Extraction gate — run before any model/prompt change, this is a hard gate requiring 50/50 constrained-JSON parses: `cargo run --example extraction_eval`
- Download resume proof: `cargo run --release --example download_smoke`
- License cross-check: `cargo run --example license_check <pubkey.bin> <key>`

## Release / packaging notes
- Current packaged milestone: M7 / app version 1.1.0, with a coaching-style setting. [build, 2026-08-24]
- Distribution zips exclude fetched llama binaries, dynamic libraries, and GGUFs; the release preflight step tells Stan which fetch script to run to obtain them.
- Local builds avoid quarantine/Gatekeeper restrictions, but customer distribution still requires: Apple Developer ID signing, notarization, and a production license keypair (these are not yet finalized). [release, 2026-08-24]
- Stan's zips have shipped without a `src-tauri` Cargo.lock in the past — consider committing a root workspace Cargo.lock before the next release to avoid this.

## Status
- M1–M3 end-to-end path was verified on Stan's MacBook Pro: chat → extraction → promote-at-2 → contradiction card → update → recall in a new chat. [app thread, 2026-08-14]

## Gotchas & lessons learned
- On Stan's Mac (used as shell CI for this project), `tracing-subscriber` needed `features = ["env-filter"]` enabled to support `with_env_filter`/`EnvFilter` — a past shell-only build issue. [Stan Mac, 2026-08-14]
- `sqlite-vec` KNN distance is NULL for zero query vectors — always read it as `Option<f64>`.
- `memory_event` table schema differs across environments — Stan's Mac lacks a `kind` column; don't assume it's present. [Stan Mac, 2026-08-14]
- Small local models will literally execute behavior rules pasted into chat rather than treat them as configuration — keep such rules in the system-prompt layer only.

## Open items
- Commit a root workspace Cargo.lock before the next release build (fixes missing `src-tauri` Cargo.lock in shipped zips).
- Finalize Apple Developer ID signing, notarization, and generate a production license keypair before customer distribution can proceed.
