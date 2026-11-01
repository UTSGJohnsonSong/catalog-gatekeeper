# Rules

Rules are pure functions in `src/gatekeeper/rules/`. Thresholds live in `rules/spec.py`. A test asserts that the description and SEO limits and the rule ids equal `eval/spec/rules_spec.yaml`, which the eval generator reads. The other constants are not mirrored.

## Audit rules: defects in a product

| Id | Name | Fires when | Severity | Agent may fix |
|---|---|---|---|---|
| A01 | DESC_MISSING | Visible description text is empty | high | yes |
| A02 | DESC_THIN | Visible length is 1 to 59 characters. Gift cards are exempt | medium | yes |
| A03 | DESC_PLACEHOLDER | Contains "lorem ipsum", is a known placeholder, or is 8 words or fewer and matches a placeholder pattern | high | yes |
| A04 | DESC_HTML_MALFORMED | Unclosed or mismatched tags. Implied end tags for p, li, td, tr are allowed | medium | yes |
| A05 | SEO_TITLE_MISSING | Blank | low | yes |
| A06 | SEO_TITLE_TOO_LONG | Over 70 characters | low | yes |
| A07 | SEO_DESC_MISSING | Blank | medium | yes |
| A08 | SEO_DESC_TOO_LONG | Over 160 characters | low | yes |
| A09 | TITLE_SHOUTING | At least 80% capitals, ignoring acronyms and tokens with digits | low | no |
| A10 | IMAGE_ALT_MISSING | An image has blank alt text | medium | no |
| A11 | DUPLICATE_TITLE | Two or more active products share a normalized title | medium | no |
| A12 | PRICE_NONPOSITIVE | An active product has a variant at or below zero | critical | no |
| A13 | INJECTION_PHRASE | Matches a known injection phrasing, or hides bidi, tag or invisible characters | high | no |

## Validators: checks a proposed fix must pass

Every validator always runs, so the reviewer sees all evidence. "New" means in the proposed text and not in the current product.

| Id | Name | Checks | Severity |
|---|---|---|---|
| V01 | FIELD_ALLOWLIST | Only description_html, seo_title, seo_description change, and each really changes | block |
| V02 | BASE_HASH_CURRENT | The product has not changed since the run started | block |
| V03 | HTML_SAFE | Allowed tags only, no attributes, balanced; no comments, CDATA or unterminated tags, which browsers can read differently from the parser; no markup in SEO fields | block |
| V04 | NO_NEW_LINKS_OR_CONTACTS | No new URL, bare domain with any letters-only TLD, email or phone number, including entity-encoded forms and forms split by invisible characters | block |
| V05 | NUMBER_UNIT_FIDELITY | Every number and recognized unit is in the product or a verified quote. `2.50 kg` and `2.5 kilograms` are the same fact, and `$20` needs a price of 20 | block |
| V06 | HARD_CLAIM_LEXICON | No new medical, comparative, absolute or price-promise claim | block |
| V07 | POLICY_CLAIM_NEEDS_CITATION | A new policy word (return, refund, shipping, warranty...) needs a verified quote containing it | block |
| V08 | CITATION_VALID | Each citation passes the mechanical check | block |
| V09 | FACT_PRESERVATION | Numbers with a recognized unit and model tokens in an existing description survive a rewrite | block |
| V10 | EDIT_RATIO | A rewrite of a healthy description stays close to the original | warn |
| V11 | INJECTION_RESIDUE | No new injection phrasing, role markers or tool syntax | block |
| V12 | RESOLVES_WITHOUT_REGRESSION | Resolves at least one finding and adds none | block |

## Judge: escalate only

J01 unsupported claim, J02 meaning drift, J03 tone. The judge can add a warning and raise risk, and it can do nothing else. It must return a quote, and code discards any quote that is not really in the text. It never runs on a blocked proposal. If it fails, risk goes up by 25 and the proposal says so. The database backs this with `CHECK (kind <> 'judge' OR severity = 'warn')`.

## Known limits, stated plainly

- **A citation can be verbatim and still irrelevant.** The check proves the words exist in a passage retrieved during the run. It does not prove the passage applies to this product. In development the template model cited a bottle warranty on a sleeping bag, and every check passed. The template was fixed, but the gap remains for a real model. The judge never sees citations, so only the human reviewer covers this.
- **Phrase lists do not generalize.** On wording it had never seen, A13 caught 10 of 30 injection cases and A03 caught 15 of 30 placeholders (`eval/reports/`). A missed placeholder is usually still caught by A02, because it is short. Injection is why the real defense is structural.
- **The lexicons are English word lists.** V06 and V07 can miss a paraphrased claim, and they can false-block plain English such as "the long return trip". V05 does not check spelled-out numbers, or numbers followed by a word it does not know as a unit. V09 does not protect counts like "4 cups" or "set of 3".
- **V07 matches substrings on purpose.** "return" also matches "returned", at the cost of the false blocks above.
