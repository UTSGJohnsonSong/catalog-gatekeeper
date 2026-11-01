import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { ApiError, api, getToken, setToken } from "./api/client";
import { ActivityPage, LoginPage, ProductsPage } from "./pages/Other";
import { ProposalPage } from "./pages/Proposal";
import { QueuePage } from "./pages/Queue";

function ModeBanner() {
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  if (!meta) return null;
  const notes: string[] = [];
  if (meta.model_mode === "demo") notes.push("Demo model: fixes come from a template script, not an LLM.");
  if (meta.shopify_adapter === "fixture") notes.push("The store is simulated.");
  if (meta.demo_mode) notes.push("Demo sign-in is on.");
  if (!meta.writeback_shop_enabled || !meta.writeback_env_enabled) notes.push("Writing to the store is switched off.");
  if (notes.length === 0) return null;
  return <p className="mode-banner">{notes.join(" ")}</p>;
}

export function App() {
  const client = useQueryClient();
  const [signedIn, setSignedIn] = useState(Boolean(getToken()));
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const { error } = useQuery({ queryKey: ["whoami"], queryFn: api.whoami, enabled: signedIn, retry: false });

  if (signedIn && error instanceof ApiError && error.status === 401) {
    setToken(null);
    setSignedIn(false);
  }

  return (
    <>
      <ModeBanner />
      <header className="top">
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
              className="link"
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
      <main>
        {!signedIn ? (
          <LoginPage demo={Boolean(meta?.demo_mode)} key={String(meta?.demo_mode)} onDone={() => setSignedIn(true)} />
        ) : (
          <Routes>
            <Route path="/" element={<QueuePage />} />
            <Route path="/proposals/:id" element={<ProposalPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="*" element={<p className="hint">This page does not exist.</p>} />
          </Routes>
        )}
      </main>
    </>
  );
}
