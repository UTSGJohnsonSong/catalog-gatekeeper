import { diffWords } from "diff";
import { useEffect, useState } from "react";
import { GateRun, type Check } from "./GateRun";
import sample from "./sample.json";

type Fix = (typeof sample.fixes)[number];

const DEMO_URL = "/app/";

// Every figure below is copied from a report in eval/reports/, and Landing.test.tsx pins the copies. Change both
// together: validators-test-20261101T014916Z.json, citations-test-20261101T014916Z.json,
// audit-test-20261101T015645Z.json (A13 and PASS_WITH_KNOWN_FAILURES), retrieval-test-20261101T014916Z.json and
// agent-dev-20261102T095137Z.json.

// Catalog text is shown as text, never as markup, as in the review screen.
function FixText({ fix }: { fix: Fix }) {
  return (
    <p className="fix-text">
      {diffWords(fix.before.join("\n"), fix.after.join("\n")).map((part, index) => {
        if (part.added) return <ins key={index}>{part.value}</ins>;
        if (part.removed) return <del key={index}>{part.value}</del>;
        return <span key={index}>{part.value}</span>;
      })}
    </p>
  );
}

function Docket({ fix, animate }: { fix: Fix; animate: boolean }) {
  const citation = fix.citations[0];
  return (
    <figure className="docket">
      <div className="docket-head">
        <p className="docket-product">{fix.product_title}</p>
        <p className="docket-field">Description, before and after</p>
      </div>
      <FixText fix={fix} />
      {citation && (
        <p className="docket-note">
          Cites {citation.chunk_id}. The quoted sentence was found word for word in the policy passage retrieved for this
          fix.
        </p>
      )}
      <GateRun checks={fix.checks as Check[]} animate={animate} />
      <figcaption>
        Drafted by the demo&apos;s template script, not an LLM, on the simulated store. Captured from a demo run.
      </figcaption>
    </figure>
  );
}

const STEPS: { name: string; text: string }[] = [
  { name: "Sync", text: "Products come in from the store. A signed webhook only says that something changed, so the product is fetched again." },
  {
    name: "Audit",
    text: "Thirteen rules flag problems such as thin or placeholder descriptions, missing search text, missing image alt text and phrases that look like instructions to a model.",
  },
  {
    name: "Agent",
    text: "A hosted LLM drafts a fix for one product at a time. Its tools are bound to that product, and a statement about store policy has to quote the policy. In the demo, a template script drafts the fixes instead.",
  },
  {
    name: "Twelve checks",
    text: "Deterministic validators decide. Eleven can stop a fix, and a stopped fix cannot be approved by anyone, admins included.",
  },
  {
    name: "Judge",
    text: "A second model call reads the fix. It can raise the risk score, but it cannot clear a check. The demo has no judge.",
  },
  { name: "A person", text: "The reviewer approves the exact change on screen. The approval is bound to a hash of that change." },
  {
    name: "Write-back",
    text: "The worker writes, reads the product back and compares hashes before marking it written. If the merchant edited the product first, the fix is marked out of date instead of overwriting the newer text. On a live Shopify store a gap of about one round trip remains, because its update call takes no precondition.",
  },
];

function useResetMinutes(): number | null {
  const [minutes, setMinutes] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    fetch("/api/meta")
      .then((response) => (response.ok ? response.json() : null))
      .then((meta) => {
        if (live && meta?.public_demo && meta.demo_reset_minutes > 0) setMinutes(meta.demo_reset_minutes);
      })
      .catch(() => undefined); // The page reads fine without it; only the reset sentence is left out.
    return () => {
      live = false;
    };
  }, []);
  return minutes;
}

export function Landing({ animate = true }: { animate?: boolean }) {
  const [passed, stopped] = sample.fixes;
  const resetMinutes = useResetMinutes();
  return (
    <>
      <header className="masthead">
        <a className="wordmark" href="/">
          Catalog Gatekeeper
        </a>
        <a className="masthead-link" href={DEMO_URL}>
          Open the demo
        </a>
      </header>

      <main>
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-copy">
            <h1 id="hero-title">An agent proposes. Twelve checks and a person decide.</h1>
            <p className="lede">
              Catalog Gatekeeper audits a Shopify-style product catalog and lets a hosted LLM propose fixes. Every fix
              must clear a gate of deterministic checks, then waits for a reviewer to approve the exact change. It is a
              personal project, built on synthetic data and a simulated store.
            </p>
            <p className="actions">
              <a className="button button-primary" href={DEMO_URL}>
                Open the demo
              </a>
              <a className="button" href="#how">
                How a fix moves
              </a>
            </p>
          </div>
          <Docket fix={passed} animate={animate} />
        </section>

        <section id="how" className="steps-section" aria-labelledby="how-title">
          <h2 id="how-title">How a fix moves</h2>
          <ol className="steps">
            {STEPS.map((step) => (
              <li key={step.name}>
                <h3>{step.name}</h3>
                <p>{step.text}</p>
              </li>
            ))}
          </ol>
          <div className="stopped">
            <p className="stopped-lede">
              The demo seeds a few products that make the template write a bad fix on purpose. Here it added a link, so V04
              stopped it:
            </p>
            <p className="stopped-line">
              <span className="catalog">{stopped.after[stopped.after.length - 1]}</span>
            </p>
            <GateRun checks={stopped.checks as Check[]} animate={false} />
          </div>
        </section>

        <section className="numbers-section" aria-labelledby="numbers-title">
          <h2 id="numbers-title">What has been measured, and what has not</h2>
          <p>
            The rules were measured on a frozen synthetic test split. The rules, the data and the tests share one author,
            so these numbers check the rules against their own specification. They do not show how the rules behave on a
            merchant&apos;s catalog.
          </p>
          <dl className="numbers">
            <div>
              <dt>Block rules, precision and recall</dt>
              <dd>
                <strong>323 of 329</strong> and <strong>323 of 330</strong>. Two of 102 fixes that should have passed
                were blocked.
              </dd>
            </div>
            <div>
              <dt>Citation checker</dt>
              <dd>
                Rejected <strong>57 of 57</strong> invalid citations and wrongly rejected <strong>0 of 25</strong> valid
                ones. This measures the checker, not a model.
              </dd>
            </div>
            <div className="number-failed">
              <dt>Injection phrases, rule A13</dt>
              <dd>
                Found <strong>10 of 30</strong>, below its floor of 0.50. The report says PASS_WITH_KNOWN_FAILURES, not
                PASS. A phrase list cannot recognise wording it has not seen; the structural limits around the agent are
                the real defence.
              </dd>
            </div>
            <div>
              <dt>Policy search with reworded questions</dt>
              <dd>
                <strong>2 of 8</strong> found in the top three. The embeddings are lexical, not semantic.
              </dd>
            </div>
            <div>
              <dt>One run with a hosted LLM</dt>
              <dd>
                All <strong>40 of 40</strong> fixes for synthetic products reached human review, at about 0.025 USD
                each. None was blocked, so this run did not test the checks against a bad fix. Repair quality has no
                automatic score yet.
              </dd>
            </div>
          </dl>
          <p className="limits">
            Not done: the Shopify adapter has been tested only against a mock, and it has never run against a merchant&apos;s
            store.
          </p>
        </section>

        <section className="try-section" aria-labelledby="try-title">
          <h2 id="try-title">Try the review screen</h2>
          <p>
            Sign in with the token that is already filled in. Approve or reject a fix, then edit a product as the merchant
            and watch its open fix go out of date.
            {resetMinutes && ` Other visitors share the same sandbox, and it resets every ${resetMinutes} minutes.`}
          </p>
          <p className="actions">
            <a className="button button-primary" href={DEMO_URL}>
              Open the demo
            </a>
          </p>
        </section>
      </main>

      <footer className="colophon">
        <p>
          FastAPI, Postgres with pgvector and a Postgres job queue behind the scenes. The review screen is built with
          Shopify&apos;s Polaris web components; this page is not.
        </p>
      </footer>
    </>
  );
}
