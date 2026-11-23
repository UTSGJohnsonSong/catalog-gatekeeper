// Drawn from the diagram in docs/ARCHITECTURE.md. Colours come from the page's CSS variables through classes in
// landing.css, so the drawing follows the light and dark themes. It is drawn at its natural size, which keeps every
// label at 12px or more, and on narrow screens it scrolls sideways inside its own frame.

const WIDTH = 960;
const HEIGHT = 396;
const LINE = 18;

type BoxProps = {
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  detail?: string[];
  decides?: boolean;
  align?: "middle" | "start";
};

function Box({ x, y, w, h, title, detail = [], decides = false, align = "middle" }: BoxProps) {
  const textX = align === "middle" ? x + w / 2 : x + 16;
  // Centre the block of lines in the box; +5 moves from the line's middle to its baseline.
  const first = y + h / 2 - (detail.length * LINE) / 2 + 5;
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={3} className={decides ? "arch-box arch-box-decides" : "arch-box"} />
      <text x={textX} y={first} textAnchor={align} className="arch-title">
        {title}
      </text>
      {detail.map((line, index) => (
        <text key={line} x={textX} y={first + LINE * (index + 1)} textAnchor={align} className="arch-detail">
          {line}
        </text>
      ))}
    </g>
  );
}

type EdgeProps = { d: string; label?: string; at?: [number, number]; anchor?: "middle" | "start" | "end" };

function Edge({ d, label, at, anchor = "middle" }: EdgeProps) {
  return (
    <g>
      <path d={d} className="arch-edge" markerEnd="url(#arch-arrow)" />
      {label && at && (
        <text x={at[0]} y={at[1]} textAnchor={anchor} className="arch-label">
          {label}
        </text>
      )}
    </g>
  );
}

const SUMMARY =
  "The Shopify store, simulated in the demo, sends FastAPI a webhook that is only a signal. FastAPI queues jobs in " +
  "Postgres, and the worker runs the agent loop for one product per run, with a hosted LLM, a template or a scripted " +
  "model and five tools. Sync reads products through ShopifyPort, from the fixture or the live store, keeps a local " +
  "copy, and the 13 audit rules record findings, which the agent reads with list_findings. The agent's proposal is " +
  "immutable. It passes 12 validators, which block or warn, then a judge that can only escalate, then the review UI, " +
  "where a person sees the diff and the evidence and approves. The approval is bound to a hash, and write-back checks " +
  "hashes, writes to the store and reads it back.";

export function Architecture() {
  return (
    <figure className="arch">
      <div className="arch-frame" tabIndex={0} role="region" aria-label="Architecture diagram">
        <svg width={WIDTH} height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={SUMMARY}>
          <defs>
            <marker
              id="arch-arrow"
              viewBox="0 0 10 10"
              refX="10"
              refY="5"
              markerWidth="8"
              markerHeight="8"
              markerUnits="userSpaceOnUse"
              orient="auto"
            >
              <path d="M0 1 L10 5 L0 9 Z" className="arch-head" />
            </marker>
          </defs>

          <Box x={16} y={44} w={160} h={56} title="Shopify store" detail={["Simulated in the demo"]} />
          <Edge d="M176 72 H336" label="Webhook, a signal only" at={[256, 64]} />
          <Box x={336} y={44} w={136} h={56} title="FastAPI" detail={["Serves both pages"]} />
          <Edge d="M472 72 H528" label="Queues" at={[500, 64]} />
          <Box x={528} y={44} w={120} h={56} title="Job queue" detail={["In Postgres"]} />
          <Edge d="M648 72 H704" label="Worker" at={[676, 64]} />
          <Box
            x={704}
            y={20}
            w={240}
            h={116}
            align="start"
            title="Agent loop"
            detail={[
              "One product per run",
              "Hosted LLM, template or scripted",
              "Tools: get_product, list_findings,",
              "search_policy, propose_fix, finish",
            ]}
          />

          <Edge d="M112 100 V200" label="ShopifyPort, fixture or live" at={[122, 154]} anchor="start" />
          <Box x={64} y={200} w={96} h={52} title="Sync" />
          <Edge d="M160 226 H200" />
          <Box x={200} y={200} w={104} h={52} title="Products" detail={["Local copy"]} />
          <Edge d="M304 226 H344" />
          <Box x={344} y={200} w={96} h={52} title="Audit" detail={["13 rules"]} />
          <Edge d="M440 226 H480" />
          <Box x={480} y={200} w={120} h={52} title="Findings" detail={["Open or resolved"]} />
          <Edge d="M600 226 H760 V136" label="list_findings" at={[612, 216]} anchor="start" />
          <Edge d="M876 136 V200" label="propose_fix" at={[866, 172]} anchor="end" />
          <Box x={808} y={200} w={136} h={52} title="Proposals" detail={["Immutable"]} />

          <Edge d="M876 252 V320" />
          <Box x={808} y={320} w={136} h={60} title="12 validators" detail={["Block or warn"]} decides />
          <Edge d="M808 350 H768" />
          <Box x={656} y={320} w={112} h={60} title="Judge" detail={["Escalate only"]} />
          <Edge d="M656 350 H616" />
          <Box x={440} y={320} w={176} h={60} title="Review UI" detail={["Diff, evidence, approve"]} decides />
          <Edge d="M440 350 H400" />
          <Box x={280} y={320} w={120} h={60} title="Approved" detail={["Bound to a hash"]} />
          <Edge d="M280 350 H240" />
          <Box x={72} y={320} w={168} h={60} title="Write-back" detail={["Hash checks, read-back"]} />
          <Edge d="M72 350 H36 V100" label="Writes" at={[44, 292]} anchor="start" />
        </svg>
      </div>
      <figcaption>
        Drawn from the diagram in docs/ARCHITECTURE.md. The thicker outlines mark the two places that decide: the twelve
        validators and the person in the review UI.
        <span className="arch-hint"> On this screen the drawing scrolls sideways inside its frame.</span>
      </figcaption>
    </figure>
  );
}
