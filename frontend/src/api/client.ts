import type { components } from "./schema";

export type Meta = components["schemas"]["Meta"];
export type Queue = components["schemas"]["Queue"];
export type QueueItem = components["schemas"]["QueueItem"];
export type ProposalDetail = components["schemas"]["ProposalDetail"];
export type ValidationRow = components["schemas"]["ValidationRow"];
export type FieldDiff = components["schemas"]["FieldDiff"];
export type ProductOut = components["schemas"]["ProductOut"];
export type AuditEntry = components["schemas"]["AuditEntry"];
export type Whoami = components["schemas"]["Whoami"];
export type Ops = components["schemas"]["Ops"];
export type ShopifySession = components["schemas"]["ShopifySession"];

const TOKEN_KEY = "gatekeeper.token";
// The API sends this on a 401 when a fresh session token may succeed.
const RETRY_SESSION_HEADER = "X-Shopify-Retry-Invalid-Session-Request";
let memoryToken: string | null = null;

/** Inside the Shopify admin, App Bridge is loaded and supplies session tokens. Nothing is stored in the browser. */
export function isEmbedded(): boolean {
  return typeof window !== "undefined" && typeof window.shopify?.idToken === "function";
}

async function authorization(): Promise<string | null> {
  if (isEmbedded()) return `Bearer ${await window.shopify!.idToken()}`;
  const token = getToken();
  return token ? `Bearer ${token}` : null;
}

export function getToken(): string | null {
  if (memoryToken) return memoryToken;
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  memoryToken = token;
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Private windows can block storage. The in-memory token still works until reload.
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const auth = await authorization();
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(auth ? { Authorization: auth } : {}),
    },
  });
  // A session token lives a minute, and Shopify may refuse one that is about to expire. One retry with a new one.
  if (response.status === 401 && !retried && isEmbedded() && response.headers.get(RETRY_SESSION_HEADER) === "1") {
    return request<T>(path, init, true);
  }
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // A non-JSON error body keeps the generic message above.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  meta: () => request<Meta>("/api/meta"),
  whoami: () => request<Whoami>("/api/whoami"),
  queue: () => request<Queue>("/api/queue"),
  ops: () => request<Ops>("/api/ops"),
  proposal: (id: string) => request<ProposalDetail>(`/api/proposals/${id}`),
  approve: (id: string, reviewHash: string, confirmHighRisk: boolean) =>
    request(`/api/proposals/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ review_hash: reviewHash, confirm_high_risk: confirmHighRisk }),
    }),
  reject: (id: string, note: string) =>
    request(`/api/proposals/${id}/reject`, { method: "POST", body: JSON.stringify({ note }) }),
  products: () => request<ProductOut[]>("/api/products"),
  auditLog: () => request<AuditEntry[]>("/api/audit-log"),
  runAgent: (limit: number) =>
    request<{ queued: number }>("/api/admin/runs", { method: "POST", body: JSON.stringify({ limit }) }),
  connect: () => request<ShopifySession>("/api/shopify/session", { method: "POST" }),
  setWriteback: (enabled: boolean) =>
    request("/api/admin/writeback", { method: "POST", body: JSON.stringify({ enabled }) }),
  simulateEdit: (productId: number) =>
    request("/demo/simulate-edit", { method: "POST", body: JSON.stringify({ product_id: productId }) }),
};
