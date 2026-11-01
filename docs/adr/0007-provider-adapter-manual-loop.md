# ADR-0007: The provider SDK with a manual loop, no agent framework

**Status:** accepted.

The options were LangChain or LangGraph, the SDK's beta tool runner, or a manual loop on the provider's Python SDK. The loop here needs budget checks before every agent model call and tool call, a database row per model call, and a run state machine that maps each stop reason to an outcome. In a framework those would be callbacks around someone else's loop. The loop is about 100 lines, and every line is explainable in an interview.

A small `LLMProvider` port keeps the provider swappable. `ScriptedProvider` drives tests, including a hijacked model. `HeuristicProvider` is the template demo model. `AnthropicProvider` is the real one.

Request details follow the current model's rules: no sampling parameters, no forced `tool_choice`, no disabled thinking, `strict: true` tools, effort in `output_config`, and assistant content replayed unchanged so thinking blocks survive. The server-side refusal fallback is on. The API key comes only from an env var, passed explicitly, so a local CLI profile is never picked up by accident.

**Cost:** no framework experience comes from this project, and some job descriptions ask for it. The real provider has never been called, because no key was available during the build. Its tests pin the request shape and error mapping against a fake client only.
