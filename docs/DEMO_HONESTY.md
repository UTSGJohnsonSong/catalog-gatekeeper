# What is real and what is simulated

| Part | State | How it is labelled |
|---|---|---|
| Store | Simulated by `FixtureShopify` in a Postgres schema | Banner: "The store is simulated." |
| Model | `heuristic-v1`, a template script that speaks the tool protocol. It is not an LLM | Banner, proposal page, `agent_runs.mode = 'demo'`, `model` field in the API and MCP output |
| Judge | Not run in demo mode, because a template cannot assess anything | No judge rows, `judge_status: disabled` |
| Embeddings | Hashed TF-IDF. Lexical matching, no semantic understanding | `embedder` in every retrieval report |
| Sign-in | Demo tokens exist only with `DEMO_MODE=1` and no configured tokens. Production refuses to boot with demo mode on | Banner: "Demo sign-in is on." |
| Embedded app | One development store, opened from its Shopify admin, with the live adapter. The template model drafts the fixes; it may write only after Shopify reports the store as a development store. Not yet run against a real store | Banner and settings page name the template model; `agent_runs.mode = 'demo'` |
| Public demo | `PUBLIC_DEMO=1`: only the reviewer demo token, and a shared sandbox that resets on boot and every `DEMO_RESET_MINUTES`. The reset empties the activity log, which the app role itself can never change | Banner: "This is a shared sandbox". The activity page says when the log is cleared |

Rules that keep this honest:

1. `agent_runs.model` holds what actually served the run, including a fallback model. The demo never records the hosted LLM's model id.
2. Eval reports carry `agent_metrics: null` unless a real model ran. A test fails if a plumbing report carries agent metrics.
3. Without a key the agent suite prints and saves `SKIPPED`, and with a key it saves `NOT_GATED`. It never saves `PASS`, because no agent thresholds exist yet.
4. Tests marked `pipeline_plumbing` exercise control flow with scripted models, and say that they do not measure model accuracy.

## The fixture is stricter than Shopify in one way

`FixtureShopify.update_product` enforces `expected_content_hash` atomically. Shopify's `productUpdate` documents no such precondition, so `LiveShopify` ignores that argument. The race test proves the service handles a stale rejection. It does not prove the live adapter closes the race. See `docs/adr/0006-shopify-port.md`.
