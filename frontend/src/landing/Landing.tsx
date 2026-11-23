import { useEffect, useState } from "react";
import { Architecture } from "./Architecture";
import {
  AUDIT_RULES,
  HARD_LIMITS,
  MOVES,
  STACK,
  STATUS_DONE,
  STATUS_NOT_DONE,
  STORE_DATA,
} from "./content";
import { FixText, type Fix } from "./FixText";
import { CHECK_NAMES, GateRun, type Check } from "./GateRun";
import { Lane } from "./Lane";
import sample from "./sample.json";

// The API serves the review screen at /app/. A static copy of this page (scripts/publish_pages.py) has no API, so
// its build names the hosted demo instead.
const DEMO_URL: string = import.meta.env.VITE_DEMO_URL ?? "/app/";
const ON_STATIC_HOST = /^https?:/.test(DEMO_URL);

// Every figure in the numbers section is copied from a report in eval/reports/, and Landing.test.tsx pins the copies.
// Change both together: validators-test-20261101T014916Z.json, citations-test-20261101T014916Z.json,
// audit-test-20261101T015645Z.json (A13 and PASS_WITH_KNOWN_FAILURES), retrieval-test-20261101T014916Z.json and
// agent-dev-20261102T095137Z.json.

const SECTIONS: { id: string; label: string }[] = [
  { id: "how", label: "Start to finish" },
  { id: "rules", label: "What it looks for" },
  { id: "limits", label: "Hard limits" },
  { id: "numbers", label: "Numbers" },
  { id: "inside", label: "Inside" },
  { id: "data", label: "Store data" },
  { id: "status", label: "Status" },
];

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

function useResetMinutes(): number | null {
  const [minutes, setMinutes] = useState<number | null>(null);
  useEffect(() => {
    if (ON_STATIC_HOST) return; // No API behind a static copy, so there is nothing to ask.
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
  const [passed] = sample.fixes;
  const resetMinutes = useResetMinutes();
  return (
    <>
      <header className="masthead">
        <div className="masthead-row">
          <a className="wordmark" href="/">
            Catalog Gatekeeper
          </a>
          <a className="masthead-link" href={DEMO_URL}>
            Open the demo
          </a>
        </div>
        <nav aria-label="On this page">
          <ul className="section-nav" role="list">
            {SECTIONS.map((section) => (
              <li key={section.id}>
                <a href={`#${section.id}`}>{section.label}</a>
              </li>
            ))}
          </ul>
        </nav>
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

        <section id="how" className="section" aria-labelledby="how-title">
          <h2 id="how-title">One product, start to finish</h2>
          <p className="section-lede">
            Follow one product through the seven steps: the {passed.product_title}, the first product in the simulated
            store. Each snapshot shows its state at that step, copied from the store&apos;s seed data and from a demo run.
          </p>
          <Lane />
        </section>

        <section id="rules" className="section" aria-labelledby="rules-title">
          <h2 id="rules-title">What it looks for</h2>
          <p className="section-lede">
            Thirteen audit rules look for defects in a product. Twelve validators then check every fix before a person
            sees it. All of them are deterministic code, not model calls.
          </p>
          <div className="split">
            <div>
              <h3>Audit rules</h3>
              <table className="rule-table">
                <thead>
                  <tr>
                    <th scope="col">Id</th>
                    <th scope="col">Rule, and when it fires</th>
                    <th scope="col">Severity</th>
                  </tr>
                </thead>
                <tbody>
                  {AUDIT_RULES.map((rule) => (
                    <tr key={rule.id}>
                      <th scope="row">{rule.id}</th>
                      <td>
                        <span className="rule-name">{rule.name}</span>
                        <span className="rule-when">{rule.when}</span>
                      </td>
                      <td>{rule.severity}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="table-note">The agent may draft fixes for A01 to A08. The other five are left to a person.</p>
            </div>
            <div className="docket checks-docket">
              <h3>Checks on every fix</h3>
              <ul className="check-list" role="list">
                {Object.entries(CHECK_NAMES).map(([id, name]) => (
                  <li key={id}>
                    <span className="check-id">{id}</span> {name}
                  </li>
                ))}
              </ul>
              <p className="docket-note">
                Every check runs on every fix, so the reviewer sees all the evidence. V10 only warns; the other eleven
                stop the fix.
              </p>
            </div>
          </div>
        </section>

        <section id="limits" className="section" aria-labelledby="limits-title">
          <h2 id="limits-title">What it can never do</h2>
          <p className="section-lede">
            These limits are in the code and the database, so none of them depends on the model behaving well.
          </p>
          <div className="split">
            <ul className="never-list" role="list">
              {HARD_LIMITS.map((limit) => (
                <li key={limit.lead}>
                  <strong>{limit.lead}</strong> {limit.detail}
                </li>
              ))}
            </ul>
            <div className="docket moves">
              <table className="moves-table">
                <caption>How a fix can move</caption>
                <thead>
                  <tr>
                    <th scope="col">A fix that is</th>
                    <th scope="col">Can become</th>
                  </tr>
                </thead>
                <tbody>
                  {MOVES.map((move) => (
                    <tr key={move.from}>
                      <th scope="row">{move.from}</th>
                      <td>{move.to}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="docket-note">
                A database trigger enforces these moves, keeps a fix&apos;s content from changing and writes one audit row
                for each move.
              </p>
            </div>
          </div>
        </section>

        <section id="numbers" className="section numbers-section" aria-labelledby="numbers-title">
          <h2 id="numbers-title">Measured, and not measured</h2>
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
                were blocked. Precision counts each rule on each fix: all six wrong stops were V07, two of them on those
                fixes and four on fixes that had a different defect; all seven misses were V06.
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

        <section id="inside" className="section" aria-labelledby="inside-title">
          <h2 id="inside-title">Inside</h2>
          <div className="split">
            <p>
              One container image runs three processes: the API, which also serves this page and the review screen; a
              worker that takes jobs from a queue in Postgres; and an MCP server with read-only tools. Database migrations
              run once, as a separate step.
            </p>
            <ul className="stack-list" role="list" aria-label="Stack">
              {STACK.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <Architecture />
        </section>

        <section id="data" className="section" aria-labelledby="data-title">
          <h2 id="data-title">Store data</h2>
          {STORE_DATA.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
          <p>
            {/* Relative, so it works under the API server and under a static host's base path alike. */}
            <a href="privacy/">Read the privacy page</a> for what is kept and how a store&apos;s data is deleted.
          </p>
        </section>

        <section id="status" className="section" aria-labelledby="status-title">
          <h2 id="status-title">Status</h2>
          <p className="section-lede">What runs today, and what is not done yet.</p>
          <div className="split">
            <div>
              <h3>Done</h3>
              <ul className="status-list" role="list">
                {STATUS_DONE.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3>Not done yet</h3>
              <ul className="status-list" role="list">
                {STATUS_NOT_DONE.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="section try-section" aria-labelledby="try-title">
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
          Shopify&apos;s Polaris web components; this page is not. <a href="privacy/">Privacy</a>
        </p>
      </footer>
    </>
  );
}
