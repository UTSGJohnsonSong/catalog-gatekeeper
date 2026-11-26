// The page's copy for the rules, limits, moves, figures and status, kept apart from the layout. The step captions
// are in Steps.tsx and the drawing's texts in Architecture.tsx, beside what they describe. Every statement has a
// source:
// docs/RULES.md for the rules, and docs/ARCHITECTURE.md, docs/DEMO_HONESTY.md and README.md for the limits and the
// status. Change the copy when those documents change.
//
// The page shows little text at rest and keeps the rest one interaction away, so several entries come in two parts:
// a short `lead` that is always on screen and a `rest` that opens.

export type Severity = "low" | "medium" | "high" | "critical";

// `when` finishes the sentence "Fires when ...".
export type AuditRule = { id: string; name: string; when: string; severity: Severity; agentMayFix: boolean };

// docs/RULES.md, "Audit rules". The agent may fix A01 to A08; A09 to A13 need a person.
export const AUDIT_RULES: AuditRule[] = [
  {
    id: "A01",
    name: "No description",
    when: "the visible description is empty.",
    severity: "high",
    agentMayFix: true,
  },
  {
    id: "A02",
    name: "Thin description",
    when: "the visible description is 1 to 59 characters long. Gift cards are exempt.",
    severity: "medium",
    agentMayFix: true,
  },
  {
    id: "A03",
    name: "Placeholder description",
    when: 'the description contains "lorem ipsum", is a known placeholder, or is 8 words or fewer and matches a placeholder pattern.',
    severity: "high",
    agentMayFix: true,
  },
  {
    id: "A04",
    name: "Broken HTML",
    when: "a tag in the description is unclosed or mismatched. Implied end tags for p, li, td and tr are allowed.",
    severity: "medium",
    agentMayFix: true,
  },
  { id: "A05", name: "No SEO title", when: "the SEO title is blank.", severity: "low", agentMayFix: true },
  {
    id: "A06",
    name: "Long SEO title",
    when: "the SEO title is over 70 characters.",
    severity: "low",
    agentMayFix: true,
  },
  {
    id: "A07",
    name: "No SEO description",
    when: "the SEO description is blank.",
    severity: "medium",
    agentMayFix: true,
  },
  {
    id: "A08",
    name: "Long SEO description",
    when: "the SEO description is over 160 characters.",
    severity: "low",
    agentMayFix: true,
  },
  {
    id: "A09",
    name: "Title in capitals",
    when: "at least 80% of the title is capital letters, not counting acronyms and words with digits.",
    severity: "low",
    agentMayFix: false,
  },
  {
    id: "A10",
    name: "No alt text",
    when: "an image has blank alt text.",
    severity: "medium",
    agentMayFix: false,
  },
  {
    id: "A11",
    name: "Duplicate title",
    when: "two or more active products share a title once it is normalized.",
    severity: "medium",
    agentMayFix: false,
  },
  {
    id: "A12",
    name: "Zero or negative price",
    when: "an active product has a variant priced at or below zero.",
    severity: "critical",
    agentMayFix: false,
  },
  {
    id: "A13",
    name: "Instruction to a model",
    when: "text matches a known injection phrasing, or hides bidi, tag or invisible characters.",
    severity: "high",
    agentMayFix: false,
  },
];

export function auditRule(id: string): AuditRule | undefined {
  return AUDIT_RULES.find((rule) => rule.id === id);
}

// Each limit is stated only as far as docs/ARCHITECTURE.md, docs/DEMO_HONESTY.md, README.md and the step copy
// support it.
export const HARD_LIMITS: { lead: string; detail: string }[] = [
  {
    lead: "The agent cannot touch another product.",
    detail: "Its tools are bound to the one product it was given.",
  },
  {
    lead: "Nobody can approve a stopped fix.",
    detail: "When a check stops a fix, no reviewer can approve it, admins included.",
  },
  {
    lead: "The judge cannot clear a check or block a fix.",
    detail:
      "It can only add a warning and raise the risk score. A database constraint means a judge result can never be a block.",
  },
  {
    lead: "An approval covers only the exact change on screen.",
    detail:
      "The review screen sends back a hash of what it showed, and a database trigger makes the proposal's content immutable.",
  },
  {
    lead: "Write-back will not overwrite a newer edit by the merchant.",
    detail:
      "The worker reads the product back and compares hashes before marking a fix written. If the merchant edited the product first, the fix goes out of date instead. On a live Shopify store a gap of about one round trip remains.",
  },
  {
    lead: "No new write starts while write-back is switched off.",
    detail:
      "An admin can switch it off at any time. Approved fixes then wait, and a fix that has not been sent yet goes back to approved. A write already on its way to the store is not called back.",
  },
  {
    lead: "The template script cannot write to a merchant's store.",
    detail:
      "Live writes need a model key, with one exception: a development store, which the app confirms with Shopify before every write. With the live adapter, write-back on and only the template script, the app refuses to start unless that exception is switched on.",
  },
  {
    lead: "The app cannot rewrite its audit log.",
    detail:
      "Every move of a fix adds one audit row, and the log is append-only, by trigger and by grant. The public demo's reset empties it, and the activity page says when.",
  },
  {
    lead: "The MCP server cannot change anything.",
    detail: "Its tools only read.",
  },
];

// docs/ARCHITECTURE.md, "Proposal states", in the names the review screen uses. `next` lists the states a fix can
// move to, for the drawing. `to` says the same in words, with the two conditions a list cannot carry. A state that
// is not a `from` here has no move out: the database's table of moves has no row for it.
export const MOVES: { from: string; next: string[]; to: string }[] = [
  { from: "New", next: ["Blocked", "Needs review"], to: "Blocked, which is final, or Needs review" },
  { from: "Needs review", next: ["Approved", "Rejected", "Out of date"], to: "Approved, Rejected or Out of date" },
  { from: "Approved", next: ["Writing", "Rejected", "Out of date"], to: "Writing, Rejected or Out of date" },
  {
    from: "Writing",
    next: ["Written", "Out of date", "Write failed", "Approved"],
    to: "Written, Out of date or Write failed, or back to Approved if write-back is switched off before the write is sent",
  },
];

// Each figure is copied from a report in eval/reports/; Landing.tsx names the reports and Landing.test.tsx pins the
// copies. `note` stays on screen under the name. `more` is the longer explanation behind the row.
export type Meter = {
  id: string;
  name: string;
  part: number;
  whole: number;
  note?: string;
  // The gate's floor for this measure, as a share of the whole. Only a measure that fell below its floor has one.
  floor?: number;
  more: string;
};

export const METERS: Meter[] = [
  {
    id: "recall",
    name: "Known defects the block rules caught",
    part: 323,
    whole: 330,
    more: "All seven misses were V06.",
  },
  {
    id: "precision",
    name: "Block rules, precision",
    part: 323,
    whole: 329,
    more: "Precision counts each rule on each fix: all six wrong stops were V07, two of them on fixes that should have passed and four on fixes that had a different defect.",
  },
  {
    id: "false-blocks",
    name: "Clean fixes wrongly blocked",
    part: 2,
    whole: 102,
    note: "Lower is better",
    more: "Two of 102 fixes that should have passed were blocked. All six wrong stops were V07, two of them on these fixes.",
  },
  {
    id: "citations-rejected",
    name: "Invalid citations rejected",
    part: 57,
    whole: 57,
    more: "This measures the citation checker, not a model.",
  },
  {
    id: "citations-wrongly-rejected",
    name: "Valid citations wrongly rejected",
    part: 0,
    whole: 25,
    note: "Lower is better",
    more: "This measures the citation checker, not a model.",
  },
  {
    id: "injection",
    name: "Injection phrases rule A13 found",
    part: 10,
    whole: 30,
    note: "Below its floor of 0.50. The report says PASS_WITH_KNOWN_FAILURES, not PASS.",
    floor: 0.5,
    more: "A phrase list cannot recognise wording it has not seen; the structural limits around the agent are the real defence.",
  },
  {
    id: "retrieval",
    name: "Reworded questions found in the top three",
    part: 2,
    whole: 8,
    more: "This is the policy search. The embeddings are lexical, not semantic.",
  },
  {
    id: "agent-run",
    name: "Fixes reaching human review, one hosted-LLM run",
    part: 40,
    whole: 40,
    note: "Synthetic products, at about 0.025 USD each. None was blocked, so this run did not test the checks against a bad fix.",
    more: "Repair quality has no automatic score yet.",
  },
];

export const STACK: string[] = [
  "FastAPI",
  "Postgres with pgvector",
  "Postgres job queue",
  "Polaris web components",
  "React",
];

// A statement with a short lead that stays on screen. `rest`, when there is one, opens under it.
export type Item = { lead: string; rest?: string[] };

// What is true today. This section is meant to be rewritten when that changes, so it holds no install steps.
// README.md, "What is real and what is not" and "Limits", and docs/DEMO_HONESTY.md.
export const STATUS_DONE: Item[] = [
  {
    lead: "API, job queue, write-back and webhooks run",
    rest: ["In Docker, and in tests against a real Postgres database."],
  },
  {
    lead: "Rules and citation checker are measured",
    rest: ["On a frozen synthetic test split, with Wilson intervals."],
  },
  { lead: "A hosted LLM ran the agent once, on 40 synthetic products" },
  { lead: "MCP server ran over streamable HTTP" },
  {
    lead: "Embedded mode is built and tested",
    rest: [
      "The review screen is built to open inside a store's Shopify admin, sign people in with Shopify's session tokens, exchange them for expiring store tokens and answer the mandatory privacy webhooks. It is tested against a mocked Shopify server and a real Postgres database.",
    ],
  },
];

export const STATUS_NOT_DONE: Item[] = [
  {
    lead: "Not in the Shopify App Store",
    rest: ["One deployment serves one store, and there is no billing."],
  },
  {
    lead: "Embedded mode has not yet run against Shopify itself",
    rest: ["Its tests use a mocked Shopify server."],
  },
  {
    lead: "Live adapter has never called a store",
    rest: ["It has been tested only against a mock."],
  },
  {
    lead: "Rules are English word lists",
    rest: ["So they miss paraphrases and can block plain English by mistake."],
  },
  { lead: "Eval data is synthetic; its author wrote the rules" },
  { lead: "Repair quality has no automatic score" },
  { lead: "Reviewer tokens never expire" },
  { lead: "Rate limits only in the public demo" },
];

export const STORE_DATA: Item[] = [
  {
    lead: "Public demo uses a simulated store and synthetic products",
    rest: ["No merchant's store is connected to it, and it uses no hosted LLM."],
  },
  {
    lead: "Live adapter reads product fields only, no customers or orders",
    rest: [
      "It serves one store and needs two permissions: read_products and write_products. The fields are titles, descriptions, search text, tags, vendor, type, status, variants with their prices, and image alt text. So far it has run only against a mocked Shopify server.",
    ],
  },
  {
    lead: "App keeps its own records in Postgres",
    rest: [
      "The products it read, their findings, the proposed fixes with their checks and reviews, the audit log, each webhook delivery as received, each agent run's transcript, and a copy of a product's fields before each write.",
      "When a hosted LLM drafts the fixes, it is sent the text of the product it works on and the policy passages it searches.",
      "Run from a shell, the store's credentials live in the server's environment: one token set by hand, or the app's client ID and secret, from which the server gets a 24-hour token and keeps it in memory.",
      "In embedded mode the store's token comes from Shopify's token exchange. It expires after an hour, is refreshed before then, and is stored in Postgres encrypted with AES-256-GCM. Uninstalling the app deletes it at once, and Shopify's erasure request 48 hours later deletes the store's catalog.",
    ],
  },
];
