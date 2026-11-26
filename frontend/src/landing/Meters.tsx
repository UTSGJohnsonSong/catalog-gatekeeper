import { useState } from "react";
import { METERS, type Meter } from "./content";
import { AlertIcon } from "./Icons";

// "Measured, and not measured" as a meter list: one row for each figure, each bar against its own whole, all on one
// scale from nothing to the whole. It is a table, so assistive technology reads a name, a share and a figure in every
// row; the roles are spelled out because the stylesheet lays the rows out as grids. A row's longer explanation shows
// when the row is pointed at, when its name has keyboard focus, and when the name is tapped. The stylesheet previews
// it for the first two, and `open` remembers the tap, which opens it in place. Its words also describe the button,
// so a screen reader hears them on focus.

// The bar is drawn with attributes only, like the architecture drawing: colours come from landing.css, and the page's
// Content-Security-Policy allows no inline style.
function Bar({ meter }: { meter: Meter }) {
  const share = (meter.part / meter.whole) * 100;
  return (
    <svg className="meter-svg" width="100%" height="16" role="img" aria-label={`${Math.round(share)} percent`}>
      <rect className="meter-track" y="4" width="100%" height="8" rx="4" />
      {meter.part > 0 && <rect className="meter-fill" y="4" width={`${share.toFixed(2)}%`} height="8" rx="4" />}
      {meter.floor !== undefined && (
        <line className="meter-floor" x1={`${meter.floor * 100}%`} x2={`${meter.floor * 100}%`} y1="0" y2="16" />
      )}
    </svg>
  );
}

export function Meters() {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <table className="meters" role="table" aria-labelledby="numbers-title">
      <tbody role="rowgroup">
        {METERS.map((meter) => {
          const belowFloor = meter.floor !== undefined && meter.part / meter.whole < meter.floor;
          const classes = ["meter"];
          if (belowFloor) classes.push("meter-failed");
          if (open === meter.id) classes.push("meter-open");
          return (
            <tr key={meter.id} role="row" className={classes.join(" ")}>
              <th scope="row" role="rowheader" className="meter-head">
                <button
                  type="button"
                  className="meter-name"
                  aria-expanded={open === meter.id}
                  aria-controls={`meter-${meter.id}`}
                  aria-describedby={`meter-${meter.id}`}
                  onClick={() => setOpen(open === meter.id ? null : meter.id)}
                >
                  <span className="meter-title">{meter.name}</span>{" "}
                  {meter.note && (
                    <span className="meter-note">
                      {belowFloor && <AlertIcon />}
                      {meter.note}
                    </span>
                  )}
                </button>
              </th>
              <td role="cell">
                <Bar meter={meter} />
              </td>
              <td role="cell" className="meter-figure">
                {meter.part} of {meter.whole}
              </td>
              <td role="cell" className="meter-more" id={`meter-${meter.id}`}>
                {meter.more}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
