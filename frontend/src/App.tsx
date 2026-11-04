import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { ApiError, api, getToken, setToken } from "./api/client";
import { ActivityPage, LoginPage, NotFoundPage, ProductsPage } from "./pages/Other";
import { ProposalPage } from "./pages/Proposal";
import { QueuePage } from "./pages/Queue";

export { ModeBanner } from "./components/ModeBanner";

// Polaris has no navigation component outside the Shopify admin, so the top bar is ours.
export function App() {
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
          <Routes>
            <Route path="/" element={<QueuePage />} />
            <Route path="/proposals/:id" element={<ProposalPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
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
