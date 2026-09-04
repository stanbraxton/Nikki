# Mymation Desktop — local-only Tauri app

Mymation Desktop is a local-only adaptive AI assistant (formerly Youmation/Adaptive Assistant) designed for users seeking private, offline-first AI interactions and local memory management. The application runs locally utilizing sidecar binaries, maintaining strict architectural separation between the Tauri shell and core logic. It is distinct from Mymation cloud SaaS and Mymation Studio/GraphWorks.

## Business & pricing
- **Product Name & Bundle ID**: Mymation (`com.greencollar.mymation`), renamed from Youmation for trademark reasons [2026-08-14].
- **Pricing Strategy**: $99/year perpetual-fallback license only; no lifetime or monthly subscription options [2026-08-14].
- **License Enforcement**: Offline Ed25519 verification combined with an update build-date gate [2026-08-14].

## Architecture
- **Tech Stack**: Tauri desktop application with a Rust backend core (`crates/assistant-core`) and a thin Tauri shell glue (`src-tauri/`) [2026-08-14].
- **Core Separation**: `crates/assistant-core` remains strictly Tauri-free, housing sidecar lifecycle management, SSE chat client logic, memory/reflection/extraction pipelines, and tests.
- **Webview Security**: The webview never accesses the sidecar port or API keys; capabilities are strictly minimized (`core:default` + dialog only).
- **Model Runtime (llama.cpp)**: 
  - Pinned llama.cpp release: **b10430** (bump only after passing smoke tests) [2026-08-14].
  - `--flash-attn` requires an explicit argument (`on|off|auto`); a bare flag causes startup failure.
  - Release binaries are dynamically linked; child process execution environments must include the bundled library directory in `LD_LIBRARY_PATH`, `DYLD_FALLBACK_LIBRARY_PATH`, and `PATH`.
  - Release archive formats: `.tar.gz` for macOS/Linux, `.zip` for Windows (`bin-win-vulkan-x64` for Windows).
  - Distribution zips exclude fetched llama binaries, dynamic libraries, and GGUFs; release preflight scripts dictate required fetch scripts.
  - Distribution requirements: Apple Developer ID signing, notarization, and a production license keypair are mandatory for customer distribution (local builds bypass quarantine/Gatekeeper) [2026-08-24].

## Data model
- **Database & Vectors**: SQLite relational database utilizing the `sqlite-vec` extension for vector similarity search.
- **Vector Queries & Tables**: `sqlite-vec` KNN distance evaluates to NULL for zero query vectors and should be handled as `Option<f64>`. `vec0` virtual tables are keyed by `rowid`, joining directly to `memory_chunk.rowid`, with embeddings stored as f32-LE binary blobs.
- **Schema Variance**: Local environments may feature a `memory_event` table lacking a `kind` column; queries must not assume its presence [2026-08-14].

## Key logic
- **Behavior Rules**: Prompt rules must be enforced in the system-prompt layer rather than user chat, as small models tend to execute pasted rules instead of retaining them.
- **Embeddings Pipeline**: `nomic-embed` requires explicit task prefixes; use `EmbedTask` (`search_document:` / `search_query:`) rather than raw strings.
- **Verification & Smoke Test Commands**:
  - Core chat smoke: `LLAMA_SERVER_BIN=… MODEL=… LLAMA_LIB_DIR=… cargo run --example smoke`
  - Memory smoke: `LLAMA_SERVER_BIN=… EMBED_MODEL=… cargo run --example memory_smoke`
  - Reflection smoke: `CHAT_MODEL=… EMBED_MODEL=… cargo run --example reflection_smoke`
  - Extraction evaluation gate (hard gate: 50/50 constrained-JSON parses): `cargo run --example extraction_eval`
  - Download resume proof: `cargo run --release --example download_smoke`
  - License cross-check: `cargo run --example license_check <pubkey.bin> <key>`
- **Sandbox Model Configurations**:
  - SmolLM2-135M Q4_K_M: Used for chat plumbing (insufficient quality for extraction tasks).
  - Qwen2.5-1.5B-Instruct Q4_K_M: Used for extraction-quality testing.
  - nomic-embed-text-v1.5 Q8_0: Used for vector embeddings.

## Integrations & APIs
- **Llama.cpp Server**: Integrated via local SSE chat client interface.
- **Licensing API**: Offline Ed25519 signature validation.

## Status
- **Current Milestone**: M7 / app version 1.1.0 featuring a coaching-style setting [2026-08-24].
- **End-to-End Verification**: M1-M3 workflow successfully verified on MacBook Pro (chat → extraction → promote-at-2 → contradiction card → update → recall in new chat) [2026-08-14].

## Gotchas & lessons
- **Rust Dependencies**: `tracing-subscriber` requires explicit feature flags (`features = ["env-filter"]`) to support `with_env_filter`/`EnvFilter` [2026-08-14].
- **Cargo Configuration**: Shipped release zips previously omitted `src-tauri` Cargo.lock; root workspace Cargo.lock management requires diligence before releases.
- **Schema Assumptions**: Avoid assuming column presence across environments (e.g., `memory_event.kind` column absence on specific local machine instances) [2026-08-14].

## Open items
- Commit root workspace Cargo.lock before the next release build.
- Finalize Apple Developer ID signing, notarization setup, and production license keypair generation for general customer distribution.
