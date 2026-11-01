# Datasheet: eval dataset v2

**What it is.** Synthetic products and proposals for measuring deterministic rules. Built by `python -m gatekeeper_eval build` from hand-written templates (`src/gatekeeper_eval/generator/`). No scraped, marketplace, customer or personal data. Store policies are written for a fictional store.

**How labels are made.** A clean base product gets one mutation, and the operator declares which rules should fire. Labels come from the specification, not from rule output. The generator may not import `gatekeeper.rules`, and a test enforces that.

**Splits.** Split by a hash of the base product id, so no base crosses splits. Wording pools differ by split. The test pool deliberately includes phrasing the rule lists do not contain. Files matching `*_test.jsonl` are frozen by hash in `FROZEN_TEST_SHA256`. Changing one means creating `datasets/v2`.

**Retrieval labels.** Questions were written by hand. Each answerable question names a phrase that must identify exactly one gold chunk, and the build fails otherwise. 40 are worded like the policy text, 20 are paraphrases, 10 are unanswerable.

**Known biases.**
- The same engineer wrote the rules, the generator and both wording pools. Independence is procedural, not absolute.
- Defect rates here are chosen by construction, about 30 positives per rule per split. They are not real-world prevalence.
- Product text is templated, and real listings are messier.
- All text is English.
- The test pool was written just before its first run, by an author who knew how the rules work. v1 test wording was seen during review and moved into dev.

**What not to conclude.** These numbers say how well the rules match their written specification on constructed cases. They do not say how often a real catalog is fixed correctly, and they say nothing about an LLM.
