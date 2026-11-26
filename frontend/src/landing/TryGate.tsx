import { useState } from "react";
import gate from "./gate.json";
import { Lights, Verdict, lightsOf, useRun, type GateResult } from "./GateRun";
import { CheckIcon, PlusIcon } from "./Icons";

// The signature: the visitor tries to get a bad fix past the gate. The page decides nothing here. gate.json holds
// what the project's own validators answered for every combination of the four additions
// (scripts/build_page_samples.py), and the page looks the answer up.

const RESULTS: Record<string, GateResult> = gate.results;

// gate.json keys each answer by the chosen ids, joined with "+" in the order of `additions`. "" is the plain draft.
export function resultKey(chosen: ReadonlySet<string>): string {
  return gate.additions
    .filter((addition) => chosen.has(addition.id))
    .map((addition) => addition.id)
    .join("+");
}

// One line of the description, with the words a check found in it marked. The match ignores case, because V11
// reports the phrase it matched in lower case. Catalog text stays text: the line is cut up, never parsed.
function Line({ text, found }: { text: string; found: string[] }) {
  const lower = text.toLowerCase();
  const hit = found
    .map((phrase) => ({ at: lower.indexOf(phrase.toLowerCase()), size: phrase.length }))
    .filter((match) => match.at >= 0 && match.size > 0)
    .sort((a, b) => a.at - b.at)[0];
  if (!hit) return <>{text}</>;
  return (
    <>
      {text.slice(0, hit.at)}
      <mark>{text.slice(hit.at, hit.at + hit.size)}</mark>
      {text.slice(hit.at + hit.size)}
    </>
  );
}

export function TryGate({ animate = true }: { animate?: boolean }) {
  const [chosen, setChosen] = useState<ReadonlySet<string>>(() => new Set());
  const result = RESULTS[resultKey(chosen)];
  const shown = useRun(result.checks, animate);
  const done = shown >= result.checks.length;
  // The plain draft clears all twelve, so whatever a check found is in a line the visitor added.
  const found = done ? result.stopped_by.map((stop) => stop.found) : [];

  function toggle(id: string) {
    setChosen((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  return (
    <figure className="gate" id="gate">
      <div className="docket">
        <div className="gate-head">
          <p className="gate-product">{gate.product.title}</p>
          <span className="tag">Simulated store</span>
        </div>

        {/* Every addition keeps its line whether it is chosen or not, as a blank ruled line until then, so choosing
            one never moves the buttons under the visitor's thumb. */}
        <ol className="gate-lines" role="list" aria-label="The proposed description">
          {gate.base.map((line) => (
            <li key={line} className="gate-line">
              {line}
            </li>
          ))}
          {gate.additions.map((addition) => {
            const added = chosen.has(addition.id);
            return (
              <li
                key={addition.id}
                className={added ? "gate-line gate-line-added" : "gate-line gate-line-empty"}
                aria-hidden={added ? undefined : true}
              >
                <ins>
                  <Line text={addition.line} found={found} />
                </ins>{" "}
                <span className="tag">added</span>
              </li>
            );
          })}
        </ol>

        <div className="gate-ask" role="group" aria-labelledby="gate-prompt">
          <p className="gate-prompt" id="gate-prompt">
            What if the fix also tries to...
          </p>
          <div className="gate-toggles">
            {gate.additions.map((addition) => {
              const pressed = chosen.has(addition.id);
              return (
                <button
                  key={addition.id}
                  type="button"
                  className="toggle"
                  aria-pressed={pressed}
                  onClick={() => toggle(addition.id)}
                >
                  {pressed ? <CheckIcon /> : <PlusIcon />}
                  {addition.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="gate-run">
          <Lights items={lightsOf(result.checks, shown)} />
          {/* The page's one live region. It is filled only when a run has finished, so a screen reader hears the
              result once and not twelve updates. */}
          <div className="gate-verdict" role="status">
            {done && <Verdict checks={result.checks} stops={result.stopped_by} />}
          </div>
        </div>
      </div>
      <figcaption>
        The demo&apos;s template script, not an LLM, drafted the first four lines and adds the link on purpose, to
        show a blocked fix. This page adds the other three. Every answer is recorded from the project&apos;s own
        checks.
      </figcaption>
    </figure>
  );
}
