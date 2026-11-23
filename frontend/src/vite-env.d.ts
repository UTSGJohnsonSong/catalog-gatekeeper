/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The hosted demo's review screen, for a static copy of the landing page. Unset, the page links to /app/. */
  readonly VITE_DEMO_URL?: string;
}
