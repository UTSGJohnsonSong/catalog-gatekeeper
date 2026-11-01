# ADR-0009: A synthetic eval, labelled by construction

**Status:** accepted.

No labelled catalog-defect dataset exists with a clear licence, and scraping stores is out. So a generator builds clean products from templates and injects one defect at a time, which makes each label true by construction.

To limit circularity: the generator may not import `gatekeeper.rules` (an AST test enforces it), thresholds come from a hand-written spec file, base products cannot cross splits, wording pools differ by split, the test pool includes phrasings the rule lists do not contain, and hard negatives exist. The test split is frozen by hash, and failing cases are hidden on it.

**What it does not give:** the same engineer wrote the rules and both pools, so independence is procedural, not absolute. Synthetic defect rates are not real prevalence. These numbers measure rules against their specification, not against real merchants or a real LLM.
