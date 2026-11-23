// The page's longer copy, kept apart from the layout so it can be edited in one place. Every statement has a source:
// docs/RULES.md for the rules, and docs/ARCHITECTURE.md, docs/DEMO_HONESTY.md and README.md for the limits and the
// status. Change the copy when those documents change.

export type Severity = "low" | "medium" | "high" | "critical";

export type AuditRule = { id: string; name: string; when: string; severity: Severity };

// docs/RULES.md, "Audit rules". The agent may fix A01 to A08; A09 to A13 need a person.
export const AUDIT_RULES: AuditRule[] = [
  { id: "A01", name: "Description is missing", when: "The visible description is empty.", severity: "high" },
  {
    id: "A02",
    name: "Description is thin",
    when: "The visible description is 1 to 59 characters long. Gift cards are exempt.",
    severity: "medium",
  },
  {
    id: "A03",
    name: "Description is a placeholder",
    when: 'It contains "lorem ipsum", is a known placeholder, or is 8 words or fewer and matches a placeholder pattern.',
    severity: "high",
  },
  {
    id: "A04",
    name: "Description HTML is broken",
    when: "A tag is unclosed or mismatched. Implied end tags for p, li, td and tr are allowed.",
    severity: "medium",
  },
  { id: "A05", name: "Search title is missing", when: "The SEO title is blank.", severity: "low" },
  { id: "A06", name: "Search title is too long", when: "The SEO title is over 70 characters.", severity: "low" },
  { id: "A07", name: "Search description is missing", when: "The SEO description is blank.", severity: "medium" },
  { id: "A08", name: "Search description is too long", when: "The SEO description is over 160 characters.", severity: "low" },
  {
    id: "A09",
    name: "Title is in capitals",
    when: "At least 80% of the title is capital letters, not counting acronyms and words with digits.",
    severity: "low",
  },
  { id: "A10", name: "Image has no alt text", when: "An image has blank alt text.", severity: "medium" },
  {
    id: "A11",
    name: "Title is a duplicate",
    when: "Two or more active products share a title once it is normalized.",
    severity: "medium",
  },
  {
    id: "A12",
    name: "Price is zero or below",
    when: "An active product has a variant priced at or below zero.",
    severity: "critical",
  },
  {
    id: "A13",
    name: "Text looks like an instruction to a model",
    when: "It matches a known injection phrasing, or hides bidi, tag or invisible characters.",
    severity: "high",
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
    lead: "The template script cannot write to a live store.",
    detail:
      "Live writes need a model key. With the live adapter, write-back on and only the template script, the app refuses to start.",
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

// docs/ARCHITECTURE.md, "Proposal states", in the names the review screen uses.
export const MOVES: { from: string; to: string }[] = [
  { from: "New", to: "Blocked, which is final, or Needs review" },
  { from: "Needs review", to: "Approved, Rejected or Out of date" },
  { from: "Approved", to: "Writing, Rejected or Out of date" },
  {
    from: "Writing",
    to: "Written, Out of date or Write failed, or back to Approved if write-back is switched off before the write is sent",
  },
];

export const STACK: string[] = [
  "FastAPI",
  "Postgres with pgvector",
  "A Postgres job queue, with no Redis or Celery",
  "The review screen on Shopify's Polaris web components",
  "This page in plain React",
];

// What is true today. This section is meant to be rewritten when that changes, so it holds no install steps.
export const STORE_DATA: string[] = [
  "The public demo uses a simulated store and synthetic products. No merchant's store is connected to it.",
  "The live adapter is written for a single store and needs two permissions: read_products and write_products. It reads product fields only, such as titles, descriptions, search text, variants, prices and image alt text. It reads no customer data and no orders. So far it has run only against a mocked Shopify server.",
  "The app keeps its own records in Postgres: the products it read, their findings, the proposed fixes with their checks and reviews, the audit log, each webhook delivery as received, each agent run's transcript, and a copy of a product's fields before each write.",
  "When a hosted LLM drafts the fixes, it is sent the text of the product it works on and the policy passages it searches. The public demo uses no hosted LLM.",
  "The store's credentials live in the server's environment, never in the database. They are either one token set by hand, or the app's client ID and secret, from which the server gets a 24-hour token and keeps it in memory.",
];

// README.md, "What is real and what is not" and "Limits", and docs/DEMO_HONESTY.md.
export const STATUS_DONE: string[] = [
  "The API, the job queue, write-back and webhooks run in Docker and in tests against a real Postgres database.",
  "The rules and the citation checker are measured on a frozen synthetic test split, with Wilson intervals.",
  "A hosted LLM has run the agent once, on 40 synthetic products.",
  "The MCP server has run over streamable HTTP.",
];

export const STATUS_NOT_DONE: string[] = [
  "It is not a public Shopify app: there is no OAuth install, no compliance webhooks and no session tokens.",
  "The live adapter has been tested only against a mock. It has never called a store.",
  "The rules are English word lists, so they miss paraphrases and can block plain English by mistake.",
  "The eval data is synthetic, and its author also wrote the rules.",
  "Repair quality has no automatic score.",
  "Reviewer tokens never expire, and rate limits exist only in the public demo.",
];
