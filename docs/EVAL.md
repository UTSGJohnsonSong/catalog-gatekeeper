# Evaluation

**What this measures:** deterministic rules, the citation checker and lexical retrieval, against a specification, on synthetic data. **What it does not measure:** how well any LLM repairs a catalog. A real model has run once, on the dev split (see "The agent suite"). That gives cost, latency and run outcomes, and no accuracy score.

Run everything with `python -m gatekeeper_eval all --split dev`, then `--split test`. Each run writes a JSON report to `eval/reports/` with the command, git SHA, whether source files had uncommitted changes, dataset hash and limits.

## Latest results, dataset v2

Test split: 468 audit cases, 387 proposal cases, 82 citation cases, 33 retrieval questions. Numbers below are from the first run of the frozen split, at commit `7ce3d50`. The first-run console output is in `eval/reports/test-split-first-run-console.txt`. Later runs of the same split give the same numbers, because the rules did not change.

| Suite | Dev split (tuned on) | Test split (held out) |
|---|---|---|
| Audit rules, micro | precision 393/393, recall 393/393 | precision 358/373 = 0.960, recall 358/393 = 0.911 |
| Validators, block rules, micro | precision 331/331, recall 331/331 | precision 323/329 = 0.982, recall 323/330 = 0.979 |
| False blocks on clean proposals | 0/102 | 2/102 = 0.020, Wilson 95% CI 0.005 to 0.069 |
| Citation checker, invalid citations rejected | 51/51 | 57/57 |
| Citation checker, valid citations wrongly rejected | 0/25 | 0/25 |
| Retrieval, worded like the policy: recall@3 | 21/21 | 19/19 |
| Retrieval, worded like the policy: MRR | 1.000 | 0.947 |
| Retrieval, paraphrased: recall@3 | 5/12 | 2/8 |

Rules that fell short on the test split:

| Rule | Test split | Why |
|---|---|---|
| A13 injection phrase | recall 10/30 = 0.333 | A phrase list does not recognize wording it has never seen. **This fails the 0.50 floor** and is recorded as a known failure, not tuned away |
| A03 placeholder | recall 15/30 = 0.50 | Same reason. The missed ones were still flagged, by A02, as too short |
| A02 thin | precision 31/46 = 0.674 | The other side of A03: those 15 cases fire A02 instead of A03 |
| V06 hard claim | recall 23/30 = 0.767 | "Proven to reduce joint pain" is not in the word list |
| V07 policy claim | precision 15/21 = 0.714 | "The long return trip" and "smooth delivery of hot water" are false blocks; 4 more are `Guaranteed lowest price`, where V07 firing is arguably right and the case's label did not declare it |

Dev scores of 1.0 mean little on their own, because the rules were tuned on that split until its gates passed. The first dev run failed: V11 recall 0.60 and false-block rate 0.069.

The end-to-end suite ran 17 demo products through the real pipeline with the template model. It proved control flow only: 14 proposals pending and applied, 3 blocked, 3 blocked approvals refused, and each invariant held. It is not a measure of accuracy.

## History, which affects how far to trust these

- **Dataset v1's test split was seen.** During the review pass a review agent scored it and reported A03 recall 0.40. It no longer counts as held out. Its wording moved to dev, and v2 got new test wording. v1 is kept in `eval/datasets/v1/`.
- **Order for v2.** Rules committed at `5e4c1ba`, test split frozen at `7ce3d50`, then the first run. Since then the only change under `src/gatekeeper/rules` or `resources` is a docstring.
- **The author knew the rules when writing the test wording.** Independence here is procedural, not absolute.

## The gate

`eval/gates.yaml` holds the thresholds, set on 2026-10-31 before the first run of any suite. They were not changed after any result. The test split's audit report still fails the per-rule floor because of A13. Rather than lower the floor or tune against this split, `known_failures` records it with its baseline. The report shows `PASS_WITH_KNOWN_FAILURES`, lists the failure, and blocks CI again if the value gets worse. Removing it properly needs better detection, checked on a new dataset version.

Tests make the gate fail on purpose: rules that find nothing, rules that fire on everything, a checker that accepts everything, an unknown suite, an end-to-end run that processed nothing, and an unfrozen test split.

## The agent suite

`python -m gatekeeper_eval agent` needs a key and costs money. Its report is saved as `SKIPPED` without a key. With a key it is `FAILED` if no run finished, which also exits nonzero, and otherwise `NOT_GATED`, because no thresholds are set yet. The report's `status` is `PARTIAL` if the sweep stopped early, and those numbers should not be quoted.

It samples 40 products: 6 for each of A01, A02, A03, A05 and A07, and 10 injection cases that also lack an SEO description, so the agent has to write text while reading the hostile description. It reports run outcomes, validator results, judge concerns, policy retrieval, citation checks, cost and latency. It does not score repair quality, so a person reads `per_case`. `injected_phrase_copied` detects verbatim copying only, not following the instruction in new words.

### First real run, 2026-11-02

`claude-opus-5-5`, dev split, commit `66a1eca`. Sources: `eval/reports/agent-dev-20261102T095137Z.json`, and `eval/reports/agent-dev-20261102T095137Z-db-extract.txt` for the judge, validator and retrieval rows, which that report did not record yet.

| Measure | Result | How far it goes |
|---|---|---|
| Runs finished | 40/40 | |
| Proposals pending review | 40/40, 0 blocked | Every validator passed, 480/480, so the block path was not tested by this run |
| Injection cases, instruction not followed | 10/10 | Read by hand, not scored |
| Injection cases, `injection_suspected` flag | 10/10 | Not independent. The agent reads A13's finding first, and A13 was tuned on this split |
| Injected phrase copied verbatim | 0/10 | Verbatim match only |
| Judge | 40/40 returned a verdict, 2 concerns kept, 0 discarded | Both concerns are the finding below |
| Policy search and citations | 0 searches, 0 citations | The sample never reached this path, so it says nothing about citation quality with a real model |
| Cost | 1.02 USD total: 0.88 agent, 0.14 judge | 0.025 per product |
| Latency | p50 12.8 s, p95 20.3 s | Includes the judge |

**What the run found.** A03 treats "Coming soon" as a placeholder, so the agent replaced it. On the two cases worded that way ("Coming soon", "COMING SOON!"), the judge objected that the new text no longer says the item is unavailable. The rule's own label calls these placeholders, so the rule may be wrong about what the words mean. No deterministic check caught this. It was not changed, because a person should decide, and it is flagged for review as designed.

A rough word comparison found no number in any proposal that was missing from its source product. 15 proposals added a generic word such as "range" or "bag", and these were judged harmless by reading them. This is a check by script and by eye, not a score. All the products are simple and synthetic.

An earlier version of this sample never ran its injection cases, because they shared product ids with the other cases and the fixture store dropped them. It also reported "measured" when every run failed. A dummy-key run against a closed port exposed both before any real run, and `tests/eval/test_eval_integrity.py` now covers them.

## Limits

- All data is synthetic, and defect rates are chosen by construction, about 30 positives per rule per split. They are not real-world prevalence.
- With about 30 positives per rule, per-rule intervals are wide. The reports give Wilson intervals.
- Retrieval is lexical, on 19 chunks of invented policy, with a small question set written by the same author.
- Latency figures are for rules on a laptop, with no model call and no real store.
