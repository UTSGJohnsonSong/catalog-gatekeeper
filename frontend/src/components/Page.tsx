import type { ReactNode } from "react";
import { useHref, useNavigate } from "react-router-dom";
import { ModeBanner } from "./ModeBanner";

// Polaris links render a real anchor inside their shadow root. A plain click stays in the app; a click that asks for
// a new tab or window is left to the browser.
export function AppLink({ to, id, slot, children }: { to: string; id?: string; slot?: "breadcrumb-actions"; children: ReactNode }) {
  const href = useHref(to);
  const navigate = useNavigate();
  return (
    <s-link
      id={id}
      slot={slot}
      href={href}
      onClick={(event: Event) => {
        const mouse = event as MouseEvent;
        if (mouse.defaultPrevented || mouse.button > 0 || mouse.metaKey || mouse.ctrlKey || mouse.shiftKey || mouse.altKey) {
          return;
        }
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </s-link>
  );
}

const STATUS_NAMES: Record<string, string> = {
  pending_review: "Needs review",
  blocked: "Blocked",
  approved: "Approved",
  applying: "Writing",
  applied: "Written",
  rejected: "Rejected",
  stale: "Out of date",
  failed: "Write failed",
};

type BadgeTone = "info" | "success" | "warning" | "critical" | "neutral" | "caution";

const STATUS_TONES: Record<string, BadgeTone> = {
  pending_review: "warning",
  blocked: "critical",
  approved: "info",
  applying: "info",
  applied: "success",
  rejected: "neutral",
  stale: "neutral",
  failed: "critical",
};

export function statusName(status: string): string {
  return STATUS_NAMES[status] ?? status;
}

export function StatusBadge({ status }: { status: string }) {
  return <s-badge tone={STATUS_TONES[status] ?? "neutral"}>{statusName(status)}</s-badge>;
}

type PageProps = {
  heading: string;
  inlineSize?: "small" | "base" | "large";
  breadcrumb?: { to: string; label: string };
  primaryAction?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
};

// Every page carries the mode banner, so nobody mistakes the demo for a live store.
export function Page({ heading, inlineSize = "base", breadcrumb, primaryAction, aside, children }: PageProps) {
  return (
    <s-page heading={heading} inlineSize={inlineSize}>
      {breadcrumb && (
        <AppLink slot="breadcrumb-actions" to={breadcrumb.to}>
          {breadcrumb.label}
        </AppLink>
      )}
      {primaryAction}
      <ModeBanner />
      {children}
      {aside}
    </s-page>
  );
}

// Polaris gives a critical banner role="alert" inside its shadow root (other tones get role="status"), so screen
// readers announce it when it appears and no extra live region goes around it.
export function ErrorBanner({ children }: { children: ReactNode }) {
  return <s-banner tone="critical">{children}</s-banner>;
}
