import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App, ComponentsMissing } from "./App";
import "./styles.css";

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });
const root = createRoot(document.getElementById("root")!);

// The Polaris script is a blocking tag before this bundle, so by now its elements are defined, unless it failed.
if (!customElements.get("s-page")) {
  root.render(<ComponentsMissing />);
} else {
  root.render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <BrowserRouter basename="/app">
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}
