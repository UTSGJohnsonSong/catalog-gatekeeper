import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createElement, useEffect, useState, type ReactNode } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { ApiError, api, getToken, isEmbedded, setToken } from "./api/client";
import { ErrorBanner, Page } from "./components/Page";
import { ActivityPage, LoginPage, NotFoundPage, ProductsPage } from "./pages/Other";
import { ProposalPage } from "./pages/Proposal";
import { QueuePage } from "./pages/Queue";
import { SettingsPage } from "./pages/Settings";

export { ModeBanner } from "./components/ModeBanner";

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<QueuePage />} />
      <Route path="/proposals/:id" element={<ProposalPage />} />
      <Route path="/products" element={<ProductsPage />} />
      <Route path="/activity" element={<ActivityPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

export function App() {
  return isEmbedded() ? <EmbeddedApp /> : <StandaloneApp />;
}

// App Bridge reads rel="home" from the menu's first link. Polaris types s-link without rel, so it is set directly.
function MenuLink({ href, home = false, children }: { href: string; home?: boolean; children: ReactNode }) {
  return createElement("s-link", home ? { href, rel: "home" } : { href }, children);
}

// Inside the admin: no sign-in page, because App Bridge signs every request in. The first call connects the store.
export function EmbeddedApp() {
  const session = useQuery({ queryKey: ["session"], queryFn: api.connect, retry: 1, staleTime: Infinity });
  return (
    <>
      <s-app-nav>
        <MenuLink href="/app/" home>
          Needs review
        </MenuLink>
        <MenuLink href="/app/products">Products</MenuLink>
        <MenuLink href="/app/activity">Activity</MenuLink>
        <MenuLink href="/app/settings">Settings</MenuLink>
      </s-app-nav>
      <div className="app-main">
        {session.error ? (
          <Page heading="Catalog Gatekeeper">
            <ErrorBanner>
              Could not connect to your store: {session.error.message} Open the app again from the Shopify admin to retry.
            </ErrorBanner>
          </Page>
        ) : !session.data ? (
          <Page heading="Catalog Gatekeeper">
            <s-paragraph>Connecting to your store…</s-paragraph>
          </Page>
        ) : (
          <>
            {session.data.first_connection && (
              <s-banner tone="info" heading="Reading your catalog">
                The app is reading your products for the first time. Findings appear on the Products page within a
                minute, and no fix is drafted until you ask for one.
              </s-banner>
            )}
            <AppRoutes />
          </>
        )}
      </div>
    </>
  );
}

// Outside the admin Polaris has no navigation component, so the top bar is ours.
function StandaloneApp() {
  const client = useQueryClient();
  const [signedIn, setSignedIn] = useState(Boolean(getToken()));
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const { error } = useQuery({ queryKey: ["whoami"], queryFn: api.whoami, enabled: signedIn, retry: false });

  const expired = signedIn && error instanceof ApiError && error.status === 401;
  useEffect(() => {
    if (!expired) return;
    setToken(null);
    // Without this the cached 401 would sign the next sign-in straight out again.
    client.clear();
    setSignedIn(false);
  }, [expired, client]);

  return (
    <>
      <header className="app-bar">
        <a className="brand" href="/">
          Catalog Gatekeeper
        </a>
        {signedIn && (
          <nav aria-label="Main">
            <NavLink to="/" end>
              Queue
            </NavLink>
            <NavLink to="/products">Products</NavLink>
            <NavLink to="/activity">Activity</NavLink>
            <NavLink to="/settings">Settings</NavLink>
            <button
              type="button"
              className="sign-out"
              onClick={() => {
                setToken(null);
                client.clear();
                setSignedIn(false);
              }}
            >
              Sign out
            </button>
          </nav>
        )}
      </header>
      {/* Each Polaris page brings its own main landmark. */}
      <div className="app-main">
        {!signedIn ? (
          <LoginPage demoToken={meta?.demo_token ?? null} key={String(meta?.demo_token)} onDone={() => setSignedIn(true)} />
        ) : (
          <AppRoutes />
        )}
      </div>
    </>
  );
}

// Shown instead of the app when Shopify's CDN did not deliver the components: empty shells would look broken.
export function ComponentsMissing() {
  return (
    <main className="components-missing">
      <h1>The review screen could not load</h1>
      <p>
        It is built from Shopify&apos;s Polaris components, which load from cdn.shopify.com, and they did not arrive. Check the
        connection or any content blocker, then reload the page.
      </p>
      <p>
        <a href="/">Back to the overview</a>
      </p>
    </main>
  );
}
