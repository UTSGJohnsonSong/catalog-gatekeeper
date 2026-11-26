import { useRef, useState, type KeyboardEvent } from "react";

// A tab can be a button or a group inside a drawing.
type TabElement = HTMLElement | SVGElement;

// One chosen item and one visible panel, as in the ARIA tabs pattern with automatic activation: choosing follows
// focus, the arrow keys move between the tabs, and only one tab is in the page's tab order. The page uses it wherever
// choosing one thing shows its detail: the seven steps, the rule tiles, the states of a fix and the boxes of the
// architecture drawing. `name` keeps the ids of one set apart from the others. With `initial` at -1 nothing is chosen
// until the visitor chooses.
export function useTabs(name: string, count: number, { initial = 0, vertical = false } = {}) {
  const [active, setActive] = useState(initial);
  const elements = useRef<(TabElement | null)[]>([]);
  // With nothing chosen yet, the first tab is the way in.
  const reachable = active < 0 ? 0 : active;

  function onKeyDown(event: KeyboardEvent) {
    const [back, forward] = vertical ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
    const focused = elements.current.indexOf(event.target as TabElement);
    const from = focused < 0 ? reachable : focused;
    let next: number;
    if (event.key === forward) next = (from + 1) % count;
    else if (event.key === back) next = (from - 1 + count) % count;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = count - 1;
    else return;
    event.preventDefault();
    elements.current[next]?.focus(); // Focusing a tab chooses it.
  }

  return {
    active,
    select: setActive,
    onKeyDown,
    tab: (index: number) => ({
      role: "tab" as const,
      id: `${name}-tab-${index}`,
      "aria-selected": index === active,
      "aria-controls": `${name}-panel-${index}`,
      tabIndex: index === reachable ? 0 : -1,
      ref: (element: TabElement | null) => {
        elements.current[index] = element;
      },
      // Safari does not focus a button on click, so a click chooses as well.
      onClick: () => setActive(index),
      onFocus: () => setActive(index),
    }),
    panel: (index: number) => ({
      role: "tabpanel" as const,
      id: `${name}-panel-${index}`,
      "aria-labelledby": `${name}-tab-${index}`,
      // In the tab order, so a keyboard reaches the detail straight after its tab.
      tabIndex: 0,
      hidden: index !== active,
    }),
  };
}
