import type { ReactNode } from "react";
import { auditRule } from "./content";
import { FixText } from "./FixText";
import { GateRun, type Check } from "./GateRun";
import store from "./product.json";
import sample from "./sample.json";

// The signature: one real product walks the seven steps, and beside each step hangs a snapshot of its state there.
// Every value comes from sample.json (a demo run) or product.json (the simulated store's seed data). Steps 6 and 7
// were not captured for this fix, so their snapshots say so instead of showing a result.

const [passed, stopped] = sample.fixes;
const product = store.product;

// The names the review screen shows for these statuses (frontend/src/components/Page.tsx).
const STATUS_NAMES: Record<string, string> = { pending_review: "Needs review", blocked: "Blocked" };

function variantLine(): string {
  const names = product.variants.map((variant) => variant.options.join(" "));
  const prices = new Set(product.variants.map((variant) => variant.price));
  if (prices.size === 1) return `${names.join(" and ")}, ${[...prices][0]} each`;
  return product.variants.map((variant) => `${variant.options.join(" ")} ${variant.price}`).join(", ");
}

function Snapshot({ label, children }: { label: string; children: ReactNode }) {
  return (
    <figure className="snap">
      <figcaption className="snap-label">{label}</figcaption>
      {children}
    </figure>
  );
}

type Step = { name: string; text: string; snapshot: ReactNode; aside?: ReactNode };

function steps(): Step[] {
  const citation = passed.citations[0];
  return [
    {
      name: "Sync",
      text: "Products come in from the store. A signed webhook only says that something changed, so the product is fetched again. The app checks the signature on the raw bytes and handles each delivery id once. Tests cover replayed, forged and out-of-order deliveries.",
      snapshot: (
        <Snapshot label="From the store">
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
        </Snapshot>
      ),
    },
    {
      name: "Audit",
      text: "Thirteen rules flag problems such as thin or placeholder descriptions, missing search text, missing image alt text and phrases that look like instructions to a model.",
      snapshot: (
        <Snapshot label="Findings">
          <ul className="snap-findings" role="list">
            {passed.findings.map((finding) => (
              <li key={finding.rule_id}>
                <span className="snap-id">{finding.rule_id}</span> {auditRule(finding.rule_id)?.name ?? finding.name}
                <span className="snap-severity">{auditRule(finding.rule_id)?.severity} severity</span>
              </li>
            ))}
          </ul>
          <p className="snap-note">
            The visible text is under 60 characters. No other rule fired, and the agent may fix this one.
          </p>
        </Snapshot>
      ),
    },
    {
      name: "Agent",
      text: "A hosted LLM drafts a fix for one product at a time. Its tools are bound to that product, and a statement about store policy has to quote the policy. In the demo, a template script drafts the fixes instead.",
      snapshot: (
        <Snapshot label="Proposed description">
          <FixText fix={passed} />
          <p className="snap-note">
            {citation && `Cites ${citation.chunk_id}, quoted word for word. `}
            Drafted by {passed.model}, a template script.
          </p>
        </Snapshot>
      ),
    },
    {
      name: "Twelve checks",
      text: "Deterministic validators decide. Eleven can stop a fix, and a stopped fix cannot be approved by anyone, admins included.",
      snapshot: (
        <Snapshot label="The gate">
          <GateRun checks={passed.checks as Check[]} animate={false} />
        </Snapshot>
      ),
      aside: (
        <div className="stopped">
          <p className="stopped-product">{stopped.product_title}</p>
          <p className="stopped-lede">
            The demo seeds a few products that make the template write a bad fix on purpose. Here it added a link, so V04
            stopped it:
          </p>
          <p className="stopped-line">
            <span className="catalog">{stopped.after[stopped.after.length - 1]}</span>
          </p>
          <GateRun checks={stopped.checks as Check[]} animate={false} />
        </div>
      ),
    },
    {
      name: "Judge",
      text: "A second model call reads the fix. It can raise the risk score, but it cannot clear a check. The demo has no judge.",
      snapshot: (
        <Snapshot label="Judge">
          <p className="snap-state">The LLM judge did not run.</p>
          <p className="snap-note">
            With a hosted LLM it looks for unsupported claims, meaning drift and tone, as J01 to J03. It never runs on a
            stopped fix.
          </p>
        </Snapshot>
      ),
    },
    {
      name: "A person",
      text: "The reviewer approves the exact change on screen. The approval is bound to a hash of that change.",
      snapshot: (
        <Snapshot label="Review">
          <p className="snap-state">
            <span className="stamp stamp-hold">{STATUS_NAMES[passed.status] ?? passed.status}</span>
          </p>
          <p className="snap-note">
            The demo run was captured at this point, with the fix waiting for a person. Approve sends back a hash of
            what the screen showed. If the product changed in the meantime, the fix goes out of date instead.
          </p>
        </Snapshot>
      ),
    },
    {
      name: "Write-back",
      text: "The worker writes, reads the product back and compares hashes before marking it written. If the merchant edited the product first, the fix is marked out of date instead of overwriting the newer text. On a live Shopify store a gap of about one round trip remains, because its update call takes no precondition. Retrying is safe: in a test, four parallel applies on real Postgres sent one write.",
      snapshot: (
        <Snapshot label="Write-back">
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
        </Snapshot>
      ),
    },
  ];
}

export function Lane() {
  return (
    <ol className="lane" role="list">
      {steps().map((step) => (
        <li key={step.name} className="lane-step">
          <span className="lane-line" aria-hidden="true" />
          <div className="lane-text">
            <h3>{step.name}</h3>
            <p>{step.text}</p>
          </div>
          <div className="lane-stop">{step.snapshot}</div>
          {step.aside && <div className="lane-aside">{step.aside}</div>}
        </li>
      ))}
    </ol>
  );
}
