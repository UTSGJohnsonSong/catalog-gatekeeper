# Rules

Rules are pure functions in `src/gatekeeper/rules/`. Thresholds live in `rules/spec.py`, and a test asserts they equal `eval/spec/rules_spec.yaml`, which the eval generator reads.

## Audit rules: defects in a product

| Id | Name | Fires when | Severity | Agent may fix |
|---|---|---|---|---|
| A01 | DESC_MISSING | Visible description text is empty | high | yes |
| A02 | DESC_THIN | Visible length is 1 to 59 characters. Gift cards are exempt | medium | yes |
| A03 | DESC_PLACEHOLDER | Contains "lorem ipsum", or the whole text is a known placeholder | high | yes |
| A04 | DESC_HTML_MALFORMED | Unclosed or mismatched tags. Implied end tags for p, li, td, tr are allowed | medium | yes |
| A05 | SEO_TITLE_MISSING | Blank | low | yes |
| A06 | SEO_TITLE_TOO_LONG | Over 70 characters | low | yes |
| A07 | SEO_DESC_MISSING | Blank | medium | yes |
| A08 | SEO_DESC_TOO_LONG | Over 160 characters | low | yes |
| A09 | TITLE_SHOUTING | At least 80% capitals, ignoring acronyms and tokens with digits | low | no |
| A10 | IMAGE_ALT_MISSING | An image has blank alt text | medium | no |
| A11 | DUPLICATE_TITLE | Two or more active products share a normalized title | medium | no |
| A12 | PRICE_NONPOSITIVE | An active product has a variant at or below zero | critical | no |
| A13 | INJECTION_PHRASE | Matches a known injection phrasing, or hides bidi or tag characters | high | no |

## Validators: checks a proposed fix must pass

Every validator always runs, so the reviewer sees all evidence. "New" means in the proposed text and not in the current product.

| Id | Name | Checks | Severity |
|---|---|---|---|
| V01 | FIELD_ALLOWLIST | Only description_html, seo_title, seo_description change, and each really changes | block |
| V02 | BASE_HASH_CURRENT | The product has not changed since the run started | block |
| V03 | HTML_SAFE | Allowed tags only, no attributes, balanced; no markup in SEO fields | block |
| V04 | NO_NEW_LINKS_OR_CONTACTS | No new URL, bare domain, email or phone number | block |
| V05 | NUMBER_UNIT_FIDELITY | Every number and unit is in the product or a verified quote. `2.50 kg` and `2.5 kilograms` are the same fact | block |
| V06 | HARD_CLAIM_LEXICON | No new medical, comparative, absolute or price-promise claim | block |
| V07 | POLICY_CLAIM_NEEDS_CITATION | A new policy word (return, refund, shipping, warranty...) needs a verified quote containing it | block |
| V08 | CITATION_VALID | Each citation passes the mechanical check | block |
| V09 | FACT_PRESERVATION | Numbers and model tokens in a healthy description survive a rewrite | block |
| V10 | EDIT_RATIO | A rewrite of a healthy description stays close to the original | warn |
| V11 | INJECTION_RESIDUE | No new injection phrasing, role markers or tool syntax | block |
| V12 | RESOLVES_WITHOUT_REGRESSION | Resolves at least one finding and adds none | block |

## Judge: escalate only

J01 unsupported claim, J02 meaning drift, J03 tone. The judge can add a warning and raise risk, and it can do nothing else. It must return a quote, and code discards any quote that is not really in the text. It never runs on a blocked proposal. If it fails, risk goes up by 25 and the proposal says so. The database backs this with `CHECK (kind <> 'judge' OR severity = 'warn')`.

## Known limits, stated plainly

- **A citation can be verbatim and still irrelevant.** The check proves the words exist in a passage retrieved during the run. It does not prove the passage applies to this product. In development the template model cited a bottle warranty on a sleeping bag, and every check passed. The template was fixed, but the gap remains for a real model. The judge (J01) and the human reviewer are the layers meant to catch it.
- **The lexicons are English word lists.** V06 and V07 can miss a paraphrased claim, and they can false-block plain English such as "the long return trip". V05 does not check spelled-out numbers.
- **V07 matches substrings on purpose.** "return" also matches "returned", at the cost of the false blocks above.
- **Injection patterns catch known phrasing only.** They are one layer. The structural defense is the narrow tool surface, the validators, and a person approving an exact diff.
