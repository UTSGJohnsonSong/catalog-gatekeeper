# ADR-0001: Python and FastAPI over Rails

**Status:** accepted, 2026-10-31. Supersedes `docs/ARCHITECTURE_DRAFT.md`.

The first draft was a Rails 8 app, chosen to match one job description at Shopify. Across 87 saved job descriptions, Rails appears in 3, while agents, LLMs, TypeScript, AWS, evaluation and RAG each appear in 15 or more. The owner delegated the choice. The ideas in the draft that do not depend on the framework carried over unchanged: rules block while the judge only escalates, verifiable quotes, content-hash compare-and-set, webhook as signal, and a synthetic eval labelled by construction.

**Cost:** the project no longer closes the Rails gap for Shopify roles specifically. The draft stays in the repo as a record.
