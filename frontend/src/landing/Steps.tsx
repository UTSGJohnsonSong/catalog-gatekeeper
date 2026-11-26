import type { ReactNode } from "react";
import { AUDIT_RULES, auditRule } from "./content";
import { FixText } from "./FixText";
import { Lights, Verdict, lightsOf, type Check, type Light } from "./GateRun";
import store from "./product.json";
import sample from "./sample.json";
import { useTabs } from "./tabs";

// One real product walks the seven steps, one step on screen at a time. The values come from sample.json (a demo
// run) or product.json (the simulated store's seed data), except step 5's sentence, which is written here. The run
// was captured while the fix waited at step 6, so step 7 lists the possible endings instead of a result.

const [passed] = sample.fixes;
const product = store.product;
const citation = passed.citations[0];

// The name the review screen shows for this status (frontend/src/components/Page.tsx).
const STATUS_NAMES: Record<string, string> = { pending_review: "Needs review" };

// Where a snapshot's values come from, shown on the snapshot itself: the store's seed data, or the captured run.
const FROM_STORE = "Simulated store";
const FROM_RUN = "Demo run";

function variantLine(): string {
  const names = product.variants.map((variant) => variant.options.join(" "));
  const prices = new Set(product.variants.map((variant) => variant.price));
  if (prices.size === 1) return `${names.join(" and ")}, ${[...prices][0]} each`;
  return product.variants.map((variant) => `${variant.options.join(" ")} ${variant.price}`).join(", ");
}

// One bar for each audit rule, flagged where the rule fired on this product.
function auditLights(): Light[] {
  const fired = new Set(passed.findings.map((finding) => finding.rule_id));
  return AUDIT_RULES.map((rule) => ({ id: rule.id, state: fired.has(rule.id) ? "flagged" : "idle", title: rule.name }));
}

// `caption` stays under 25 words. `more` is the rest of the explanation, one tap away.
type Step = { name: string; caption: string; more?: string; source: string; snapshot: ReactNode };

const STEPS: Step[] = [
  {
    name: "Sync",
    caption: "A signed webhook only says that something changed, so the product is fetched again from the store.",
    more: "The app checks the signature on the raw bytes and handles each delivery id once. Tests cover replayed, forged and out-of-order deliveries.",
    source: FROM_STORE,
    snapshot: (
      <>
        <p className="snap-title">{product.title}</p>
        <p className="snap-sub">
          {product.vendor}, {product.product_type}
        </p>
        <dl className="snap-fields">
          <div>
            <dt>Description</dt>
            <dd className="catalog">{passed.before.join(" ")}</dd>
          </div>
          <div>
            <dt>Search description</dt>
            <dd className="catalog">{product.seo_description}</dd>
          </div>
          <div>
            <dt>Variants</dt>
            <dd>{variantLine()}</dd>
          </div>
        </dl>
      </>
    ),
  },
  {
    name: "Audit",
    caption:
      "Thirteen rules flag problems. Here the visible text is under 60 characters. No other rule fired, and the agent may fix this one.",
    source: FROM_RUN,
    snapshot: (
      <>
        <Lights items={auditLights()} />
        <ul className="snap-findings" role="list">
          {passed.findings.map((finding) => (
            <li key={finding.rule_id}>
              <span className="snap-id">{finding.rule_id}</span> {auditRule(finding.rule_id)?.name ?? finding.name}
              <span className="snap-severity">{auditRule(finding.rule_id)?.severity} severity</span>
            </li>
          ))}
        </ul>
      </>
    ),
  },
  {
    name: "Agent",
    caption:
      "A hosted LLM drafts a fix for one product at a time. In the demo, a template script drafts the fixes instead.",
    more: "Its tools are bound to that product, and a statement about store policy has to quote the policy.",
    source: FROM_RUN,
    snapshot: (
      <>
        <FixText fix={passed} />
        <p className="snap-note">
          {citation && `Cites ${citation.chunk_id}, quoted word for word. `}
          Drafted by {passed.model}, a template script.
        </p>
      </>
    ),
  },
  {
    name: "Twelve checks",
    caption:
      "Deterministic validators decide. Eleven can stop a fix, and a stopped fix cannot be approved by anyone, admins included.",
    source: FROM_RUN,
    snapshot: (
      <>
        <Lights items={lightsOf(passed.checks as Check[])} />
        <Verdict checks={passed.checks as Check[]} />
        <p className="snap-note">
          <a href="#gate">Try to get a bad fix past them</a>, in the gate at the top.
        </p>
      </>
    ),
  },
  {
    name: "Judge",
    caption:
      "A second model call reads the fix. It can raise the risk score, but it cannot clear a check. The demo has no judge.",
    more: "With a hosted LLM it looks for unsupported claims, meaning drift and tone, as J01 to J03. It never runs on a stopped fix.",
    source: FROM_RUN,
    snapshot: <p className="snap-state">The LLM judge did not run.</p>,
  },
  {
    name: "A person",
    caption: "The reviewer approves the exact change on screen. The approval is bound to a hash of that change.",
    more: "Approve sends back a hash of what the screen showed. If the product changed in the meantime, the fix goes out of date instead.",
    source: FROM_RUN,
    snapshot: (
      <>
        <p className="snap-state">
          <span className="stamp stamp-hold">{STATUS_NAMES[passed.status] ?? passed.status}</span>
        </p>
        <p className="snap-note">The demo run was captured at this point, with the fix waiting for a person.</p>
      </>
    ),
  },
  {
    name: "Write-back",
    caption: "The worker writes, reads the product back and compares hashes before marking it written.",
    more: "If the merchant edited the product first, the fix is marked out of date instead of overwriting the newer text. On a live Shopify store a gap of about one round trip remains, because its update call takes no precondition. Retrying is safe: in a test, four parallel applies on real Postgres sent one write.",
    source: FROM_RUN,
    snapshot: (
      <>
        <ul className="snap-endings" role="list">
          <li>
            <span className="stamp stamp-cleared">Written</span> The product, read back, matches the approved text.
          </li>
          <li>
            <span className="stamp stamp-neutral">Out of date</span> The merchant edited the product first. Nothing is
            written.
          </li>
          <li>
            <span className="stamp stamp-stopped">Write failed</span> The store refused the write, or reads back
            different text.
          </li>
        </ul>
        <p className="snap-note">
          This fix was still waiting for review when the run was captured, so these are its possible endings, not its
          result.
        </p>
      </>
    ),
  },
];

const LAST = STEPS.length - 1;

export function Steps() {
  const tabs = useTabs("step", STEPS.length);
  return (
    <>
      {/* The lane and its two buttons stay above the panel, so they keep their place while the panel under them
          changes height from step to step. */}
      <div className="steps-bar">
        <div className="steps-lane" role="tablist" aria-label="The seven steps" onKeyDown={tabs.onKeyDown}>
          {STEPS.map((step, index) => (
            <button
              key={step.name}
              type="button"
              className={index <= tabs.active ? "stop stop-reached" : "stop"}
              {...tabs.tab(index)}
            >
              <span className="stop-name">{step.name}</span>
            </button>
          ))}
        </div>
        {/* At either end the button stays focusable and says it is unavailable, so focus is never lost. */}
        <div className="steps-nav">
          <button
            type="button"
            className="button"
            aria-label="Previous step"
            aria-disabled={tabs.active === 0}
            onClick={() => tabs.select(Math.max(0, tabs.active - 1))}
          >
            Previous
          </button>
          <button
            type="button"
            className="button"
            aria-label="Next step"
            aria-disabled={tabs.active === LAST}
            onClick={() => tabs.select(Math.min(LAST, tabs.active + 1))}
          >
            Next
          </button>
        </div>
      </div>

      {STEPS.map((step, index) => (
        <div key={step.name} className="steps-panel" {...tabs.panel(index)}>
          <div>
            {/* On a phone the lane shows numbers only, so the panel names its step. */}
            <h3 className="step-title">{step.name}</h3>
            <p className="step-caption">{step.caption}</p>
            {step.more && (
              <details className="more">
                <summary>More detail</summary>
                <p>{step.more}</p>
              </details>
            )}
          </div>
          <figure className="snap">
            <figcaption className="snap-source">
              <span className="tag">{step.source}</span>
            </figcaption>
            {step.snapshot}
          </figure>
        </div>
      ))}
    </>
  );
}
