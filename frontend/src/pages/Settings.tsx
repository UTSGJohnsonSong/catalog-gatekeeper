import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorBanner, Page } from "../components/Page";

function writingSentence(envOn: boolean, shopOn: boolean): string {
  if (!envOn) return "Writing is switched off for this deployment. Approved fixes wait until an operator turns it on.";
  if (!shopOn) return "Writing to the store is paused. Approved fixes wait, and nothing changes in the store.";
  return "Approved fixes are written to the store, then read back and compared before they count as written.";
}

export function SettingsPage() {
  const client = useQueryClient();
  const { data: meta, error } = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const { data: me, error: meError } = useQuery({ queryKey: ["whoami"], queryFn: api.whoami, retry: false });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.setWriteback(enabled),
    onSuccess: () => client.invalidateQueries({ queryKey: ["meta"] }),
  });
  const shopOn = Boolean(meta?.writeback_shop_enabled);
  const envOn = Boolean(meta?.writeback_env_enabled);

  return (
    <Page heading="Settings">
      <s-stack gap="base">
        {error && <ErrorBanner>Could not load the settings: {error.message}</ErrorBanner>}
        {meError && (
          <ErrorBanner>Could not check your role, so the switch for writing is hidden: {meError.message}</ErrorBanner>
        )}
        {meta && (
          <s-section heading="Writing to the store">
            <s-stack gap="base">
              <s-paragraph>{writingSentence(envOn, shopOn)}</s-paragraph>
              {me?.role === "admin" && envOn && (
                <s-button
                  variant="secondary"
                  loading={toggle.isPending}
                  onClick={() => toggle.mutate(!shopOn)}
                >
                  {shopOn ? "Pause writing" : "Resume writing"}
                </s-button>
              )}
              {toggle.isError && <ErrorBanner>Could not change it: {toggle.error.message}</ErrorBanner>}
            </s-stack>
          </s-section>
        )}
        {meta && (
          <s-section heading="Who drafts the fixes">
            <s-paragraph>
              {meta.model_mode === "demo"
                ? "A template script drafts the fixes. It is not an LLM: it fills a fixed template from the product's fields."
                : "A hosted LLM drafts the fixes, one product at a time, with tools bound to that product."}{" "}
              Every fix must pass 12 checks, and a person approves the exact change before anything is written.
            </s-paragraph>
          </s-section>
        )}
        <s-section heading="Your store's data">
          <s-stack gap="base">
            <s-paragraph>
              The app reads products only: titles, descriptions, search text, variants and image alt text. It never
              reads customers or orders.
            </s-paragraph>
            <s-paragraph>
              Uninstalling the app ends its access at once. Shopify then asks the app to erase your store&apos;s data,
              and it deletes the products, findings and fixes it kept. The activity log keeps who decided what, and when.
            </s-paragraph>
            <s-link href="/privacy/" target="_blank">
              Privacy policy
            </s-link>
          </s-stack>
        </s-section>
      </s-stack>
    </Page>
  );
}
