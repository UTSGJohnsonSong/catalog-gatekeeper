# ADR-0004: Content-hash compare-and-set and approved_hash

**Status:** accepted.

`approved_hash` covers proposal id, shop, product, base hash and the exact proposed content, and a database trigger makes those columns immutable. The UI sends back the hash it was shown, so a person approves exactly what they saw. Write-back claims with a single-winner `UPDATE ... WHERE status = 'approved'`.

A session advisory lock per proposal makes the writer exclusive for the whole operation, and the database releases it if the process dies. That is why one code path can serve first run, retry and crash recovery: recovery does not race a live writer, unless the lock's database session drops while the process lives on, which this design accepts. Whether an earlier attempt landed is answered by comparing hashes against the store, not by trusting local state.

**Honest limit:** Shopify documents no precondition on `productUpdate`, and its idempotent-requests page does not list it. Between refetch and mutation, a merchant edit can slip in, a window of about one round trip. `before_snapshot`, read-back and the audit log bound the damage. They do not close the window.
