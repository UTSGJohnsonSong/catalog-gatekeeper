/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

// Two pages: the landing page at / and the review app under /app. The app is a page of its own because it loads
// Shopify's Polaris components from their CDN, and the landing page does not need them. In development, deep links
// such as /app/proposals/<id> get the app page, as the API server does with the build.
const appPageFallback: Plugin = {
  name: "app-page-fallback",
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url && /^\/app(\/[^.?]*)?(\?.*)?$/.test(req.url)) req.url = "/app/index.html";
      next();
    });
  },
};

export default defineConfig({
  plugins: [react(), appPageFallback],
  build: {
    rolldownOptions: {
      input: {
        landing: fileURLToPath(new URL("./index.html", import.meta.url)),
        app: fileURLToPath(new URL("./app/index.html", import.meta.url)),
        // Static, like the landing page: a store's privacy policy must open without the API.
        privacy: fileURLToPath(new URL("./privacy/index.html", import.meta.url)),
      },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000", "/demo": "http://127.0.0.1:8000" },
  },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test-setup.ts"] },
});
