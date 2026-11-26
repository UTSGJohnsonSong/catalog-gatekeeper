import type { ReactNode } from "react";

// Small line icons. Each one repeats a word that stands next to it, so colour is never the only signal and assistive
// technology can skip them. Their colour comes from the text around them, through .icon in landing.css, so they
// follow both themes.
function Icon({ children }: { children: ReactNode }) {
  return (
    <svg className="icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

export function CheckIcon() {
  return (
    <Icon>
      <path d="M4 10.5 8.5 15 16 6" />
    </Icon>
  );
}

export function CrossIcon() {
  return (
    <Icon>
      <path d="M5 5l10 10M15 5 5 15" />
    </Icon>
  );
}

export function PlusIcon() {
  return (
    <Icon>
      <path d="M10 4v12M4 10h12" />
    </Icon>
  );
}

// A dashed circle: not there yet.
export function NotYetIcon() {
  return (
    <Icon>
      <circle className="icon-dashed" cx="10" cy="10" r="7" />
    </Icon>
  );
}

export function AlertIcon() {
  return (
    <Icon>
      <path d="M10 3 18 17H2Z" />
      <path d="M10 8.5v3.5M10 14.4v.1" />
    </Icon>
  );
}
