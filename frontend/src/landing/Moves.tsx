import { Arrowhead } from "./Arrowhead";
import { MOVES } from "./content";
import { useTabs } from "./tabs";

// How a fix can move, drawn from MOVES. Choosing a state marks the states it can become and says them in a sentence,
// so everything the drawing shows is also there as text. Colours come from classes in landing.css.

const WIDTH = 340;
const HEIGHT = 436;
// On a phone the drawing is shown at about its own size, so a box is as tall as a finger needs.
const BOX = { w: 100, h: 44 };

// Three columns: the fix's main path runs down the middle and the ways out sit on either side. `x` and `y` are a
// box's top-left corner. The order is top to bottom, which is the order the arrow keys follow.
const NODES: { state: string; x: number; y: number }[] = [
  { state: "New", x: 120, y: 4 },
  { state: "Blocked", x: 4, y: 52 },
  { state: "Needs review", x: 120, y: 100 },
  { state: "Rejected", x: 236, y: 148 },
  { state: "Approved", x: 120, y: 196 },
  { state: "Out of date", x: 4, y: 244 },
  { state: "Writing", x: 120, y: 292 },
  { state: "Write failed", x: 236, y: 340 },
  { state: "Written", x: 120, y: 388 },
];

// One route for each move in MOVES, ending on the edge of the box it enters. Landing.test.tsx checks that no move
// is left without one.
const ROUTES: Record<string, string> = {
  "New>Blocked": "M120 26 H54 V52",
  "New>Needs review": "M170 48 V100",
  "Needs review>Approved": "M170 144 V196",
  "Needs review>Rejected": "M220 122 H286 V148",
  "Needs review>Out of date": "M120 130 H40 V244",
  "Approved>Writing": "M170 240 V292",
  "Approved>Rejected": "M220 210 H286 V192",
  "Approved>Out of date": "M120 226 H68 V244",
  "Writing>Written": "M170 336 V388",
  "Writing>Out of date": "M120 314 H54 V288",
  "Writing>Write failed": "M220 322 H286 V340",
  "Writing>Approved": "M220 304 H250 V228 H220",
};

const movesFrom = (state?: string) => MOVES.find((move) => move.from === state);

function sentence(state: string): string {
  const move = movesFrom(state);
  return move ? `From ${state}, a fix can become ${move.to}.` : `From ${state}, a fix cannot move again.`;
}

export function Moves() {
  // Needs review is chosen at first. It is where a fix waits for a person, and seeing its three moves marked
  // shows that the states can be pressed.
  const tabs = useTabs("state", NODES.length, {
    initial: NODES.findIndex((node) => node.state === "Needs review"),
    vertical: true,
  });
  const chosen = tabs.active < 0 ? undefined : NODES[tabs.active].state;
  const reachable = new Set(movesFrom(chosen)?.next);

  return (
    <figure className="moves">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="group" aria-label="How a fix can move">
        <defs>
          <Arrowhead id="move-arrow" className="move-head" />
          <Arrowhead id="move-arrow-on" className="move-head move-head-on" />
        </defs>

        <g aria-hidden="true">
          {MOVES.flatMap((move) =>
            move.next.map((to) => {
              const on = move.from === chosen;
              return (
                <path
                  key={`${move.from}>${to}`}
                  d={ROUTES[`${move.from}>${to}`]}
                  className={on ? "move-edge move-edge-on" : "move-edge"}
                  markerEnd={on ? "url(#move-arrow-on)" : "url(#move-arrow)"}
                />
              );
            }),
          )}
        </g>

        <g role="tablist" aria-label="The states of a fix" aria-orientation="vertical" onKeyDown={tabs.onKeyDown}>
          {NODES.map((node, index) => {
            // A state with no move out is drawn as a pill, so its shape says that a fix ends there.
            const corner = movesFrom(node.state) ? 3 : BOX.h / 2;
            return (
              <g
                key={node.state}
                className={reachable.has(node.state) ? "move-node move-node-next" : "move-node"}
                aria-label={node.state}
                {...tabs.tab(index)}
              >
                {/* Shown only with keyboard focus, three units outside the box. */}
                <rect
                  className="move-ring"
                  x={node.x - 3}
                  y={node.y - 3}
                  width={BOX.w + 6}
                  height={BOX.h + 6}
                  rx={corner + 3}
                />
                <rect className="move-box" x={node.x} y={node.y} width={BOX.w} height={BOX.h} rx={corner} />
                <text className="move-label" x={node.x + BOX.w / 2} y={node.y + BOX.h / 2 + 4.5} textAnchor="middle">
                  {node.state}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      <div className="readout">
        {NODES.map((node, index) => (
          <p key={node.state} {...tabs.panel(index)}>
            {sentence(node.state)}
          </p>
        ))}
      </div>
      <figcaption>
        A database trigger enforces these moves, keeps a fix&apos;s content from changing and writes one audit row for
        each move.
      </figcaption>
    </figure>
  );
}
