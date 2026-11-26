import { Arrowhead } from "./Arrowhead";
import { useTabs } from "./tabs";

// Drawn from the diagram in docs/ARCHITECTURE.md. Colours come from the page's CSS variables through classes in
// landing.css, so the drawing follows the light and dark themes. It is drawn at its natural size, which keeps every
// label at 12px or more, and on narrow screens it scrolls sideways inside its own frame. A box says little more than
// its name; pointing at it, focusing it or tapping it puts what that part does on the line under the drawing.

const WIDTH = 960;
const HEIGHT = 372;
const LINE = 18;

type Part = {
  title: string;
  // A second line that stays in the box.
  tag?: string;
  // The line under the drawing when this part is chosen.
  about: string;
  x: number;
  y: number;
  w: number;
  h: number;
  // One of the two places that decide. It gets a heavier outline.
  decides?: boolean;
};

// In the order the work flows, which is the order the arrow keys follow.
const PARTS: Part[] = [
  {
    title: "Shopify store",
    tag: "Simulated in the demo",
    about: "Its webhook is only a signal that something changed.",
    x: 16,
    y: 20,
    w: 160,
    h: 56,
  },
  {
    title: "FastAPI",
    about: "The API, which also serves this page and the review screen. It queues jobs in Postgres.",
    x: 336,
    y: 20,
    w: 136,
    h: 56,
  },
  {
    title: "Job queue",
    about: "A queue in Postgres, with no Redis or Celery. A worker takes jobs from it.",
    x: 528,
    y: 20,
    w: 120,
    h: 56,
  },
  {
    title: "Agent loop",
    about:
      "One product per run, with a hosted LLM, a template or a scripted model. Its five tools are get_product, list_findings, search_policy, propose_fix and finish.",
    x: 704,
    y: 20,
    w: 240,
    h: 56,
  },
  {
    title: "Sync",
    about: "Reads products through ShopifyPort, from the fixture or the live store.",
    x: 64,
    y: 176,
    w: 96,
    h: 52,
  },
  { title: "Products", about: "A local copy of what sync read.", x: 200, y: 176, w: 104, h: 52 },
  { title: "Audit", about: "13 rules record findings.", x: 344, y: 176, w: 96, h: 52 },
  {
    title: "Findings",
    about: "Open or resolved. The agent reads them with list_findings.",
    x: 480,
    y: 176,
    w: 120,
    h: 52,
  },
  {
    title: "Proposals",
    about: "What the agent proposes with propose_fix. A proposal is immutable.",
    x: 808,
    y: 176,
    w: 136,
    h: 52,
  },
  {
    title: "12 validators",
    about: "They block or warn. One of the two places that decide.",
    x: 808,
    y: 296,
    w: 136,
    h: 60,
    decides: true,
  },
  { title: "Judge", about: "It can only escalate.", x: 656, y: 296, w: 112, h: 60 },
  {
    title: "Review UI",
    about: "A person sees the diff and the evidence, and approves. The other place that decides.",
    x: 440,
    y: 296,
    w: 176,
    h: 60,
    decides: true,
  },
  { title: "Approved", about: "The approval is bound to a hash.", x: 280, y: 296, w: 120, h: 60 },
  {
    title: "Write-back",
    about: "Checks hashes, writes to the store and reads it back.",
    x: 72,
    y: 296,
    w: 168,
    h: 60,
  },
];

// The arrows, in the order the work flows. Each ends on the edge of the box it enters.
const EDGES: string[] = [
  "M176 48 H336",
  "M472 48 H528",
  "M648 48 H704",
  "M112 76 V176",
  "M160 202 H200",
  "M304 202 H344",
  "M440 202 H480",
  "M600 202 H760 V76",
  "M876 76 V176",
  "M876 228 V296",
  "M808 326 H768",
  "M656 326 H616",
  "M440 326 H400",
  "M280 326 H240",
  "M72 326 H36 V76",
];

// The whole drawing in words, arrows included, for anyone who cannot see it.
const SUMMARY =
  "The Shopify store, simulated in the demo, sends FastAPI a webhook that is only a signal. FastAPI queues jobs in " +
  "Postgres, and the worker runs the agent loop for one product per run, with a hosted LLM, a template or a scripted " +
  "model and five tools. Sync reads products through ShopifyPort, from the fixture or the live store, keeps a local " +
  "copy, and the 13 audit rules record findings, which the agent reads with list_findings. The agent's proposal is " +
  "immutable. It passes 12 validators, which block or warn, then a judge that can only escalate, then the review " +
  "UI, where a person sees the diff and the evidence and approves. The approval is bound to a hash, and write-back " +
  "checks hashes, writes to the store and reads it back.";

export function Architecture() {
  // Nothing is chosen until the visitor points at, focuses or taps a box.
  const tabs = useTabs("part", PARTS.length, { initial: -1 });
  return (
    <figure className="arch">
      <div className="arch-frame">
        <svg
          width={WIDTH}
          height={HEIGHT}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="group"
          aria-label="Architecture"
          aria-describedby="arch-summary"
        >
          <defs>
            <Arrowhead id="arch-arrow" className="arch-head" />
          </defs>

          <g aria-hidden="true">
            {EDGES.map((d) => (
              <path key={d} d={d} className="arch-edge" markerEnd="url(#arch-arrow)" />
            ))}
          </g>

          <g role="tablist" aria-label="Parts of the system" onKeyDown={tabs.onKeyDown}>
            {PARTS.map((part, index) => {
              const middle = part.x + part.w / 2;
              // Centre the one or two lines in the box; +5 moves from a line's middle to its baseline.
              const first = part.y + part.h / 2 - (part.tag ? LINE / 2 : 0) + 5;
              return (
                <g
                  key={part.title}
                  className={part.decides ? "arch-node arch-node-decides" : "arch-node"}
                  aria-label={part.tag ? `${part.title}. ${part.tag}` : part.title}
                  onMouseEnter={() => tabs.select(index)}
                  {...tabs.tab(index)}
                >
                  {/* Shown only with keyboard focus, four units outside the box. */}
                  <rect
                    className="arch-ring"
                    x={part.x - 4}
                    y={part.y - 4}
                    width={part.w + 8}
                    height={part.h + 8}
                    rx={6}
                  />
                  <rect className="arch-box" x={part.x} y={part.y} width={part.w} height={part.h} rx={3} />
                  <text className="arch-title" x={middle} y={first} textAnchor="middle">
                    {part.title}
                  </text>
                  {part.tag && (
                    <text className="arch-detail" x={middle} y={first + LINE} textAnchor="middle">
                      {part.tag}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <p id="arch-summary" hidden>
        {SUMMARY}
      </p>

      <div className="readout">
        {tabs.active < 0 && <p>Choose a box to see what it does.</p>}
        {PARTS.map((part, index) => (
          <p key={part.title} {...tabs.panel(index)}>
            <strong>{part.title}.</strong> {part.about}
          </p>
        ))}
      </div>
      <figcaption className="arch-hint">On this screen the drawing scrolls sideways inside its frame.</figcaption>
    </figure>
  );
}
