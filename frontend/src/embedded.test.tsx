import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { api, isEmbedded } from "./api/client";
import { App, EmbeddedApp } from "./App";
import { SettingsPage } from "./pages/Settings";

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers });

const META = {
  demo_mode: false,
  public_demo: false,
  demo_token: null,
  demo_reset_minutes: 0,
  model_mode: "demo",
  model: "heuristic-v1",
  provider: "heuristic",
  shopify_adapter: "live",
  judge: "disabled",
  writeback_env_enabled: true,
  writeback_shop_enabled: true,
  embedded: true,
};

// App Bridge hands out a new session token on every call, as the real one does.
function embed() {
  let issued = 0;
  const idToken = vi.fn(async () => `header.payload-${++issued}.signature`);
  vi.stubGlobal("shopify", { idToken });
  return idToken;
}

function serve(handler: (url: string, init: RequestInit) => Response) {
  const stub = vi.fn(async (url: string, init: RequestInit = {}) => handler(String(url), init));
  vi.stubGlobal("fetch", stub);
  return stub;
}

const authOf = (init?: RequestInit) => (init?.headers as Record<string, string> | undefined)?.Authorization;

function renderWithProviders(node: React.ReactNode, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("requests inside the admin", () => {
  it("knows when App Bridge is there", () => {
    expect(isEmbedded()).toBe(false);
    embed();
    expect(isEmbedded()).toBe(true);
  });

  it("signs every request with a fresh session token", async () => {
    embed();
    const fetchStub = serve(() => json({ name: "shopify:42", role: "admin" }));
    await api.whoami();
    await api.whoami();
    expect(authOf(fetchStub.mock.calls[0][1])).toBe("Bearer header.payload-1.signature");
    expect(authOf(fetchStub.mock.calls[1][1])).toBe("Bearer header.payload-2.signature");
  });

  it("retries once with a new token when the API says a new one may work", async () => {
    embed();
    let calls = 0;
    const fetchStub = serve(() =>
      ++calls === 1
        ? json({ detail: "expired" }, 401, { "X-Shopify-Retry-Invalid-Session-Request": "1" })
        : json({ name: "shopify:42", role: "admin" }),
    );
    await expect(api.whoami()).resolves.toEqual({ name: "shopify:42", role: "admin" });
    expect(fetchStub).toHaveBeenCalledTimes(2);
    expect(authOf(fetchStub.mock.calls[1][1])).toBe("Bearer header.payload-2.signature");
  });

  it("does not retry forever, nor without the header", async () => {
    embed();
    const retrying = serve(() => json({ detail: "no" }, 401, { "X-Shopify-Retry-Invalid-Session-Request": "1" }));
    await expect(api.whoami()).rejects.toThrow("no");
    expect(retrying).toHaveBeenCalledTimes(2);
    const plain = serve(() => json({ detail: "forbidden" }, 401));
    await expect(api.whoami()).rejects.toThrow("forbidden");
    expect(plain).toHaveBeenCalledTimes(1);
  });
});

describe("the app inside the admin", () => {
  it("shows no sign-in page, connects the store, and puts its menu in the admin", async () => {
    embed();
    serve((url) => {
      if (url.startsWith("/api/shopify/session")) return json({ shop_domain: "x.myshopify.com", first_connection: true });
      if (url.startsWith("/api/meta")) return json(META);
      if (url.startsWith("/api/queue")) return json({ items: [], counts: {} });
      if (url.startsWith("/api/whoami")) return json({ name: "shopify:42", role: "admin" });
      if (url.startsWith("/api/ops")) return json({});
      return json({}, 404);
    });
    renderWithProviders(<App />);
    expect(document.querySelector("s-app-nav s-link[rel='home']")?.getAttribute("href")).toBe("/app/");
    expect(screen.getByText("Connecting to your store…")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/reading your products for the first time/)).toBeTruthy());
    expect(screen.queryByText(/Sign in/)).toBeNull();
  });

  it("says so when the store cannot be connected", async () => {
    embed();
    serve(() => json({ detail: "Shopify rejected the session token" }, 401));
    renderWithProviders(<EmbeddedApp />);
    // The connect call is retried once, a second later, before the page gives up.
    await waitFor(() => expect(screen.getByText(/Could not connect to your store/)).toBeTruthy(), { timeout: 4000 });
  });
});

describe("settings", () => {
  it("lets an admin pause writing, and says what that means", async () => {
    const posted: unknown[] = [];
    serve((url, init) => {
      if (url.startsWith("/api/admin/writeback")) {
        posted.push(JSON.parse(String(init.body)));
        return json({ enabled: false });
      }
      if (url.startsWith("/api/meta")) return json(META);
      if (url.startsWith("/api/whoami")) return json({ name: "owner", role: "admin" });
      return json({}, 404);
    });
    renderWithProviders(<SettingsPage />);
    await waitFor(() => expect(screen.getByText(/read back and compared/)).toBeTruthy());
    expect(screen.getByText(/A template script drafts the fixes/)).toBeTruthy();
    const pause = await waitFor(() => {
      const found = [...document.querySelectorAll("s-button")].find((b) => b.textContent === "Pause writing");
      if (!found) throw new Error("no pause button yet");
      return found;
    });
    fireEvent.click(pause);
    await waitFor(() => expect(posted).toEqual([{ enabled: false }]));
  });

  it("offers no switch to a reviewer", async () => {
    serve((url) => {
      if (url.startsWith("/api/meta")) return json(META);
      if (url.startsWith("/api/whoami")) return json({ name: "r", role: "reviewer" });
      return json({}, 404);
    });
    renderWithProviders(<SettingsPage />);
    await waitFor(() => expect(screen.getByText(/read back and compared/)).toBeTruthy());
    expect([...document.querySelectorAll("s-button")].some((b) => b.textContent === "Pause writing")).toBe(false);
  });
});
