// The arrowhead of the page's two drawings. It is sized in the drawing's own units, so it stays the same size on a
// thin line and on a heavy one, and its colour comes from the class.
export function Arrowhead({ id, className }: { id: string; className: string }) {
  return (
    <marker
      id={id}
      viewBox="0 0 10 10"
      refX="10"
      refY="5"
      markerWidth="8"
      markerHeight="8"
      markerUnits="userSpaceOnUse"
      orient="auto"
    >
      <path d="M0 1 L10 5 L0 9 Z" className={className} />
    </marker>
  );
}
