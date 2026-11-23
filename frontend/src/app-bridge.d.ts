// App Bridge, which the API server adds to the review page when the app runs inside the Shopify admin
// (embedded_review_html in src/gatekeeper/api/app.py). Only what this app uses is declared.
import type { DetailedHTMLProps, HTMLAttributes } from "react";

declare global {
  interface Window {
    shopify?: {
      /** A session token for this app and store. It lasts a minute, so ask for one per request. */
      idToken(): Promise<string>;
    };
  }
}

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      /** The app's menu in the admin's sidebar. Its links are read by App Bridge, not shown in the page. */
      "s-app-nav": DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}

export {};
