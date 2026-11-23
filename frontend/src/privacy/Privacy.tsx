// Every statement here is checked against the code: the product fields against QUERY_PRODUCTS in
// src/gatekeeper/shopify/live.py, deletion against src/gatekeeper/services/shop_lifecycle.py, the reset interval
// against render.yaml. Change them together.

export const UPDATED = "23 November 2026";

export function Privacy() {
  return (
    <>
      <header className="masthead">
        {/* Relative, so the link works under the API server and under a static host's base path alike. */}
        <a className="wordmark" href="../">
          Catalog Gatekeeper
        </a>
      </header>
      <main className="privacy">
        <h1>Privacy</h1>
        <p className="lede">
          Catalog Gatekeeper is a personal project, not a commercial service. This page says what the app reads from a
          Shopify store, what it keeps, who else handles it, and how it is deleted. It covers the app installed in a
          store&apos;s admin and the public demo.
        </p>
        <p className="updated">Last changed {UPDATED}.</p>

        <section aria-labelledby="reads">
          <h2 id="reads">What the app reads</h2>
          <p>
            Products only. The app asks Shopify for two permissions, to read products and to write products. It reads
            each product&apos;s title, description, search title and search description, tags, vendor, type and
            status, its first 25 variants with their SKU, price and options, and the alt text of its first 10 images.
          </p>
          <p>It never asks for customers, orders, payments or staff accounts.</p>
        </section>

        <section aria-labelledby="keeps">
          <h2 id="keeps">What it keeps</h2>
          <ul>
            <li>Copies of those product fields.</li>
            <li>The defects its rules find, and the fixes it drafts with the results of their checks.</li>
            <li>The decisions of the people who review those fixes, and an activity log of who did what, and when.</li>
            <li>The store&apos;s access token, encrypted.</li>
          </ul>
          <p>
            Someone who opens the app from the Shopify admin is identified by their Shopify user ID, which is a number.
            The app keeps that ID with their decisions, and does not store their name or email address.
          </p>
        </section>

        <section aria-labelledby="others">
          <h2 id="others">Who else handles it</h2>
          <p>Render hosts the app, and Neon hosts its database.</p>
          <p>
            In the app and in the public demo, fixes are drafted by a template script that runs inside the app, so no
            store data goes to an AI model provider. A deployment set up with a hosted LLM instead sends each
            product&apos;s fields to that provider, to draft the fix.
          </p>
          <p>
            The pages set no cookies and load no analytics or advertising scripts. The review screen loads
            Shopify&apos;s own components from cdn.shopify.com. The hosting provider&apos;s logs may record the
            address a request came from.
          </p>
        </section>

        <section aria-labelledby="deletion">
          <h2 id="deletion">When a store removes the app</h2>
          <p>
            Uninstalling the app ends its access at once: the stored token is deleted, writing stops, and queued syncs
            and drafting runs are dropped. Shopify asks the app 48 hours later to erase the store&apos;s data. The app then deletes the
            products, defects and fixes it kept, with everything derived from them.
          </p>
          <p>
            The activity log keeps its entries, because the database refuses to change them, by design. They hold IDs,
            statuses, Shopify user IDs, and any note a reviewer typed when rejecting a fix.
          </p>
        </section>

        <section aria-labelledby="customers">
          <h2 id="customers">Requests from a store&apos;s customers</h2>
          <p>
            The app holds no customer data. When Shopify passes on a customer&apos;s request to see or erase their
            data, the app records that it holds none. It does not store the request itself, because the request names
            the customer.
          </p>
        </section>

        <section aria-labelledby="demo">
          <h2 id="demo">The public demo</h2>
          <p>
            The public demo runs on a simulated store with invented products. It never connects to a real store.
            Visitors share one sandbox, which goes back to its first state every hour, activity log included.
          </p>
        </section>

        <section aria-labelledby="contact">
          <h2 id="contact">Questions</h2>
          <p>
            Open an issue in the project&apos;s repository,{" "}
            <a href="https://github.com/UTSGJohnsonSong/catalog-gatekeeper/issues">
              github.com/UTSGJohnsonSong/catalog-gatekeeper
            </a>
            .
          </p>
        </section>
      </main>
    </>
  );
}
