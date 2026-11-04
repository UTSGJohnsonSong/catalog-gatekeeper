import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, api, setToken } from "../api/client";
import { AppLink, ErrorBanner, Page } from "../components/Page";

export function LoginPage({ demoToken, onDone }: { demoToken: string | null; onDone: () => void }) {
  const demo = demoToken !== null;
  const [value, setValue] = useState(demoToken ?? "");
  const [error, setError] = useState<string | null>(null);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!value.trim()) return;
    setToken(value.trim());
    try {
      await api.whoami();
      onDone();
    } catch (failure) {
      setToken(null);
      setError(failure instanceof ApiError && failure.status === 401 ? "That token was not accepted. Check it and try again." : String(failure));
    }
  }

  return (
    <Page heading="Sign in to review fixes" inlineSize="small">
      <s-section>
        <form onSubmit={submit}>
          <s-stack gap="base">
            {/* The field's own input sits in a shadow root with no form, so Enter is handled here. */}
            <div
              onKeyDown={(event: KeyboardEvent) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void submit();
                }
              }}
            >
              <s-text-field
                label="Reviewer token"
                value={value}
                autocomplete="off"
                onInput={(event: Event) => setValue((event.currentTarget as HTMLInputElement).value)}
              />
            </div>
            {demo && <s-paragraph color="subdued">This demo accepts the filled-in token. A real deployment refuses it.</s-paragraph>}
            {error && <ErrorBanner>{error}</ErrorBanner>}
            <s-button variant="primary" type="submit" disabled={!value.trim()}>
              Sign in
            </s-button>
          </s-stack>
        </form>
      </s-section>
    </Page>
  );
}

export function ProductsPage() {
  const client = useQueryClient();
  const { data, error, isLoading } = useQuery({ queryKey: ["products"], queryFn: api.products });
  const edit = useMutation({
    mutationFn: (id: number) => api.simulateEdit(id),
    onSuccess: () => client.invalidateQueries(),
  });
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.meta });

  return (
    <Page heading="Products">
      <s-stack gap="base">
        {error && <ErrorBanner>Could not load products: {error.message}</ErrorBanner>}
        {edit.isError && <ErrorBanner>Could not simulate the edit: {edit.error.message}</ErrorBanner>}
        {edit.isSuccess && (
          <s-banner tone="success">Edited in the store. Any open fix for that product is now out of date.</s-banner>
        )}
        {!(error && !data) && (
          <s-section padding="none" accessibilityLabel="Products">
            <s-table loading={isLoading}>
              <s-table-header-row>
                <s-table-header listSlot="primary">Product</s-table-header>
                <s-table-header listSlot="labeled">Type</s-table-header>
                <s-table-header listSlot="labeled">Open findings</s-table-header>
                {meta?.demo_mode && <s-table-header listSlot="labeled">Demo</s-table-header>}
              </s-table-header-row>
              <s-table-body>
                {data?.map((product) => (
                  <s-table-row key={product.id}>
                    <s-table-cell>{product.title}</s-table-cell>
                    <s-table-cell>{product.product_type}</s-table-cell>
                    <s-table-cell>{product.open_rules.length ? product.open_rules.join(", ") : "None"}</s-table-cell>
                    {meta?.demo_mode && (
                      <s-table-cell>
                        <s-button variant="secondary" disabled={edit.isPending} onClick={() => edit.mutate(product.id)}>
                          Edit as merchant
                        </s-button>
                      </s-table-cell>
                    )}
                  </s-table-row>
                ))}
              </s-table-body>
            </s-table>
          </s-section>
        )}
      </s-stack>
    </Page>
  );
}

export function ActivityPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ["audit"], queryFn: api.auditLog, refetchInterval: 5000 });
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  return (
    <Page heading="Activity">
      <s-stack gap="base">
        <s-paragraph color="subdued">Every status change and decision. This log cannot be edited, even by the application.</s-paragraph>
        {meta?.public_demo && (
          <s-paragraph color="subdued">
            This shared demo clears the log, with everything else, every {meta.demo_reset_minutes} minutes and on each restart.
          </s-paragraph>
        )}
        {error && <ErrorBanner>Could not load activity: {error.message}</ErrorBanner>}
        {data && data.length === 0 && <s-paragraph color="subdued">No activity yet.</s-paragraph>}
        {!(error && !data) && (
          <s-section padding="none" accessibilityLabel="Activity log">
            <s-table loading={isLoading}>
              <s-table-header-row>
                <s-table-header listSlot="secondary">When</s-table-header>
                <s-table-header listSlot="labeled">Who</s-table-header>
                <s-table-header listSlot="primary">What</s-table-header>
                <s-table-header listSlot="labeled">On</s-table-header>
              </s-table-header-row>
              <s-table-body>
                {data?.map((entry) => (
                  <s-table-row key={entry.id}>
                    <s-table-cell>{new Date(entry.at).toLocaleString()}</s-table-cell>
                    <s-table-cell>{entry.actor}</s-table-cell>
                    <s-table-cell>{entry.action}</s-table-cell>
                    <s-table-cell>{entry.entity_id.slice(0, 8)}</s-table-cell>
                  </s-table-row>
                ))}
              </s-table-body>
            </s-table>
          </s-section>
        )}
      </s-stack>
    </Page>
  );
}

export function NotFoundPage() {
  return (
    <Page heading="This page does not exist">
      <s-paragraph>
        <AppLink to="/">Go to the review queue.</AppLink>
      </s-paragraph>
    </Page>
  );
}
