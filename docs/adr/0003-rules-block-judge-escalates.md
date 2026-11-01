# ADR-0003: Deterministic rules block; the LLM judge only escalates

**Status:** accepted.

A rule that can block must be auditable and repeatable, so only code blocks. The judge can raise risk with a quote that code verifies, and it can do nothing else. A database check constraint means a judge row can never be a block, whatever the application does. If the judge is down the proposal says so and risk rises, because silence must not look like a pass.

**Cost:** rules are lexical, so they miss paraphrases and sometimes false-block plain English. The eval measures that instead of hiding it.
