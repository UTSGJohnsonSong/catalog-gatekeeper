import { useEffect, useState, type ReactNode } from "react";
import { Architecture } from "./Architecture";
import { HARD_LIMITS, STACK, STATUS_DONE, STATUS_NOT_DONE, STORE_DATA, type Item } from "./content";
import { CheckIcon, NotYetIcon } from "./Icons";
import { Meters } from "./Meters";
import { Moves } from "./Moves";
import { Rules } from "./Rules";
import { Steps } from "./Steps";
import { TryGate } from "./TryGate";

// One idea to a screen: each section is one figure the visitor can operate, with a line or two of text under it. The
// longer explanations open from the figure they belong to.

// The API serves the review screen at /app/. A static copy of this page (scripts/publish_pages.py) has no API, so
// its build names the hosted demo instead.
const DEMO_URL: string = import.meta.env.VITE_DEMO_URL ?? "/app/";
const ON_STATIC_HOST = /^https?:/.test(DEMO_URL);

// Every figure in the numbers section is copied from a report in eval/reports/ into METERS in content.ts, and
// Landing.test.tsx pins the copies. Change both together: validators-test-20261101T014916Z.json (the block rules),
// citations-test-20261101T014916Z.json, audit-test-20261101T015645Z.json (A13 and PASS_WITH_KNOWN_FAILURES),
// retrieval-test-20261101T014916Z.json and agent-dev-20261102T095137Z.json.

const SECTIONS: { id: string; label: string }[] = [
  { id: "how", label: "Seven steps" },
  { id: "rules", label: "What it looks for" },
  { id: "limits", label: "Hard limits" },
  { id: "numbers", label: "Numbers" },
  { id: "inside", label: "Inside" },
  { id: "status", label: "Status" },
  { id: "data", label: "Store data" },
];

// Short statements, each behind an icon that repeats its list's heading. Whatever a lead leaves out opens under it.
function Items({ items, icon }: { items: Item[]; icon?: ReactNode }) {
  return (
    <ul className={icon ? "items items-marked" : "items"} role="list">
      {items.map((item) => (
        <li key={item.lead}>
          {icon}
          {item.rest ? (
            <details>
              <summary>{item.lead}</summary>
              {item.rest.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
            </details>
          ) : (
            <span>{item.lead}</span>
          )}
        </li>
      ))}
    </ul>
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
          <div>
            <h1 id="hero-title">An agent proposes. Twelve checks and a person decide.</h1>
            <p className="lede">
              Catalog Gatekeeper audits a Shopify-style catalog. A hosted LLM is meant to propose fixes; in the demo, a
              template script drafts them. Every fix must clear deterministic checks, then a person approves the exact
              change. It is a personal project, built on synthetic data.
            </p>
            <p className="actions">
              <a className="button button-primary" href={DEMO_URL}>
                Open the demo
              </a>
            </p>
          </div>
          <TryGate animate={animate} />
        </section>

        <section id="how" className="section" aria-labelledby="how-title">
          <h2 id="how-title">One product, seven steps</h2>
          <Steps />
        </section>

        <section id="rules" className="section" aria-labelledby="rules-title">
          <h2 id="rules-title">What it looks for</h2>
          <Rules />
        </section>

        <section id="limits" className="section" aria-labelledby="limits-title">
          <h2 id="limits-title">What it can never do</h2>
          <div className="split">
            <Moves />
            <ul className="limit-cards" role="list">
              {HARD_LIMITS.map((limit) => (
                <li key={limit.lead}>
                  <details>
                    <summary>{limit.lead}</summary>
                    <p>{limit.detail}</p>
                  </details>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="numbers" className="section" aria-labelledby="numbers-title">
          <h2 id="numbers-title">Measured, and not measured</h2>
          <Meters />
          {/* These two stay on screen whatever the visitor opens: they say how far the figures above go. */}
          <div className="numbers-notes">
            <p>
              The rules, the synthetic data and the tests share one author, so these numbers check the rules against
              their own specification. They do not show how the rules behave on a merchant&apos;s catalog.
            </p>
            <p>
              Not done: the Shopify adapter has been tested only against a mock, and it has never run against a
              merchant&apos;s store.
            </p>
          </div>
        </section>

        <section id="inside" className="section" aria-labelledby="inside-title">
          <h2 id="inside-title">Inside</h2>
          <Architecture />
          <ul className="tags stack" role="list" aria-label="Stack">
            {STACK.map((item) => (
              <li key={item} className="tag">
                {item}
              </li>
            ))}
          </ul>
        </section>

        <div className="closing">
          <section id="status" className="section" aria-labelledby="status-title">
            <h2 id="status-title">Status</h2>
            <div className="status-groups">
              <div className="status-done">
                <h3>Done</h3>
                <Items items={STATUS_DONE} icon={<CheckIcon />} />
              </div>
              <div>
                <h3>Not done yet</h3>
                <Items items={STATUS_NOT_DONE} icon={<NotYetIcon />} />
              </div>
            </div>
          </section>

          <section id="data" className="section" aria-labelledby="data-title">
            <h2 id="data-title">Store data</h2>
            <Items items={STORE_DATA} />
            <p className="link-row">
              {/* Relative, so it works under the API server and under a static host's base path alike. */}
              <a href="privacy/">Read the privacy page</a>
            </p>
          </section>
        </div>

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
