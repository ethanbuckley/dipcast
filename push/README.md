# Dipspot push alerts

This is a Cloudflare Worker that sends Web Push notifications for Dipspot. A visitor turns on alerts on the Saved page, and their browser registers with the Worker along with the ids of their saved spots. Every 2 minutes the Worker reads the site's `data/alerts.json` and either revalidates a pending batch or identifies newly high spots. When a saved spot has just become high or very high, it queues one notification for each visitor who saved it, and at most one for each spot in 20 hours: the site is rebuilt several times a day, and a spot near the line can cross it more than once. It has no npm dependencies. The encryption (RFC 8291) and sender signature (RFC 8292) use the Web Crypto API built into Workers.

## What it stores

One Workers KV entry per browser, holding:

- the push subscription: the push service endpoint URL, its expiry time if the browser gave one, and the two public keys the browser supplied (`p256dh` and `auth`);
- the list of saved spot ids;
- the time of the last change.

No personal names, email addresses or IP addresses. Cloudflare sees each request's IP address, as any host does, but the Worker does not store it. A second entry, `state`, holds the rank of every spot at the last run, so the next run can tell what has changed. While alerts are pending, `queue` holds subscription key hashes, the affected spots' public forecast details, the comparison state needed to recover an interrupted run, the queue creation time, and bounded retry counters/times.

The endpoint and keys are enough to send that browser a notification, so treat the KV contents as private. Logs name a subscription by part of its hash, never by endpoint.

## Free-tier limits that matter

- Workers: 100,000 requests a day. Each subscribe, unsubscribe or CORS preflight is one request.
- KV (Cloudflare's KV limits page, read 29 Sep 2026): 100,000 reads and 1,000 writes a day, and 1,000 operations in one run. Each subscribe or change of saved spots is one write, so about 1,000 subscribes or changes a day. Finding affected subscribers lists metadata; subscriptions too large for metadata also need individual reads. Sending a batch reads its subscription records. Include queue/state writes and retries in the daily budget.
- Cron triggers: free.

## How many alerts go out, and how fast

Cloudflare's limits page (read 29 Sep 2026) gives the free plan 50 outgoing requests and 10 ms of CPU time per run, cron runs included, and 6 open connections at a time. Encrypting one notification took 0.28 ms of CPU in Node on a Mac, and signing once per push service 0.19 ms. So a run sends at most 15 (`SENDS_PER_RUN`) and keeps the rest in a queue for the next runs, 2 minutes apart: an ideal ceiling of about 450 attempts an hour before retries or runtime overhead. Every batch rechecks `alerts.json`. Queues expire after 30 minutes, so the free-plan batch size is suitable only for a small pilot; a large backlog can expire before everyone is reached.

On the Workers Paid plan (about $5 a month) the limits are higher: set `SENDS_PER_RUN` in `wrangler.toml` to send more per run. `npx wrangler tail` shows each run's count, and a run cut short by a limit.

Finding who to alert needs no reads of the records: each subscription's spots are also kept in its key's metadata (up to 1,024 bytes, about 40 spot ids), so a run lists keys and reads only the records it sends to. KV can take up to a minute to show a write in every location, so a batch can occasionally go out twice; the second copy replaces the first on the device without a sound, because both carry the same tag.

### Queue recovery and changed saved spots

The complete queue is saved before advancing the comparison state. The first batch is sent on the next scheduled run, normally about two minutes later. This avoids writing the same KV key twice in rapid succession: [KV permits one write per second to a key](https://developers.cloudflare.com/kv/platform/limits/). If the state write fails after the queue was stored, a later run restores that checkpoint before sending. An interrupted batch retains its unsent recipients, although already-sent notifications can repeat if its checkpoint fails.

Before sending each queued notification, the Worker rereads the subscription and limits the notification to spots still saved in that record. A combined alert becomes a single-spot alert if only one affected spot remains. Removing all affected spots or deleting the subscription skips that notification. Updates are subject to [KV's eventual consistency](https://developers.cloudflare.com/kv/concepts/how-kv-works/): this is not an immediate-revocation or exactly-once guarantee, and these recovery steps do not serialize overlapping cron runs.

Upgrade from the older queue format: undated queues are discarded; dated single-spot notifications can be checked using their spot tag. Older combined notifications have no spot IDs and are discarded because their membership cannot be checked safely. Existing subscriptions are preserved.

### Retries and expiry

Network errors, timeouts, HTTP 408/429/5xx and temporary subscription-read failures are retried, at most four attempts total. Delays start at two minutes, then four and eight; a later `Retry-After` (seconds or HTTP date) is respected. Pending retries go behind unattempted recipients. HTTP 404/410 removes the subscription; other permanent failures are logged without exposing service bodies or endpoint-bearing errors. Requests time out after 15 seconds.

Each batch rechecks the latest forecast and uses its current wording. Spots that disappeared or are no longer high are skipped. Failed forecast refreshes leave the queue intact and send nothing. Invalid, future-dated and regressed issue times are refused. Forecasts expire after eight hours or at the next UK midnight, whichever is sooner, and a pending queue expires 30 minutes after creation. These are conservative pilot defaults, not a promise of delivery within 30 minutes.

Push TTL is capped at the remaining lifetime. Messages include an issue time and expiry; the device shows the UK issue time, or a neutral check-latest notice if delivery was delayed past expiry. It does not silently discard the push, because user-visible subscriptions require a visible result. Already-visible notifications cannot be withdrawn by this implementation. [Web Push TTL](https://www.rfc-editor.org/rfc/rfc8030#section-5.2), [visible push subscriptions](https://developer.mozilla.org/en-US/docs/Web/API/PushManager/subscribe).

Newly rising spots are queued after the current queue drains or expires. KV still does not serialize overlapping cron invocations, so duplicate processing remains possible. Real-device push delivery and runtime-limit tests remain necessary before promising dependable paid alerts. Follow [the iPhone test guide](DEVICE_TEST.md); no physical-device test has been claimed yet.

## Setup

Run every command from the `push/` folder: `cd push`.

1. Log in to Cloudflare: `npx wrangler login`. The first run downloads wrangler, then a browser tab asks you to allow access. The terminal then says you are logged in.
2. Create the KV namespace: `npx wrangler kv namespace create PUSH`. It prints an `id`. Paste it into `wrangler.toml` in place of `REPLACE_WITH_KV_NAMESPACE_ID`.
3. Make the key pair: `node scripts/vapid-keys.mjs`. It prints two lines, `VAPID_PUBLIC_KEY=...` and `VAPID_PRIVATE_KEY=...`. It writes nothing to disk. Keep the terminal open.
4. Store the private key as a secret: `npx wrangler secret put VAPID_PRIVATE_KEY`. When asked, paste only the text after `VAPID_PRIVATE_KEY=`. If wrangler says the Worker does not exist yet and offers to create it, answer yes. Never commit this key.
5. Edit `wrangler.toml`. Set `VAPID_PUBLIC_KEY` to the text after `VAPID_PUBLIC_KEY=`. Set `VAPID_SUBJECT` to a contact address such as `"mailto:you@example.com"`, removing the `REPLACE_WITH_` prefix. Push services use it to contact you if the Worker misbehaves.
6. Deploy: `npx wrangler deploy`. It prints the Worker's URL, `https://dipspot-push.<account>.workers.dev`.
7. In the GitHub repository, open Settings → Secrets and variables → Actions → Variables, and add two repository variables:
   - `DIPCAST_PUSH_URL` = the Worker's URL with a trailing slash, `https://dipspot-push.<account>.workers.dev/`
   - `DIPCAST_VAPID_PUBLIC_KEY` = the public key from step 3

   The site reads these when it builds, so they take effect at the next build.

## How you will know it worked

- Run `npx wrangler tail` and leave it open. Within 2 minutes you see a scheduled run. The first one logs `cron: first run, saved ranks for N spots, sent nothing`. Later runs log `cron: alerts.json unchanged (...)` or a line saying how many spots rose and how many alerts were sent, removed or failed.
- After the next site build, the Saved page shows an alerts button.

A run that fails with `VAPID_SUBJECT must be a mailto: or https: URL` or `public key must be a 65-byte uncompressed P-256 point` means step 5 was not finished.

## The mistake to avoid

The most likely mistake is pasting the private key into `wrangler.toml` or into the GitHub variable instead of the secret. It happens because the two keys are printed together and look alike, and the public key does go in both of those places. `wrangler.toml` is committed and the GitHub variable ends up in the published site, so either one publishes the private key. If it happens, make a new pair and repeat steps 3 to 7. Changing the key pair breaks every existing subscription, so everyone has to turn alerts on again.

## Tests

`node --test test/*.test.js` (or `npm test`). They need nothing installed. They were run on Node 26.
