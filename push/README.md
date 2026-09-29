# Dipspot push alerts

This is a Cloudflare Worker that sends Web Push notifications for Dipspot. A visitor turns on alerts on the Saved page, and their browser registers with the Worker along with the ids of their saved spots. Every 30 minutes the Worker reads the site's `data/alerts.json`. When a saved spot has just become high or very high, it sends that visitor one notification, and at most one for each spot in 20 hours: the site is rebuilt several times a day, and a spot near the line can cross it more than once. It has no npm dependencies. The encryption (RFC 8291) and sender signature (RFC 8292) use the Web Crypto API built into Workers.

## What it stores

One Workers KV entry per browser, holding:

- the push subscription: the push service endpoint URL, its expiry time if the browser gave one, and the two public keys the browser supplied (`p256dh` and `auth`);
- the list of saved spot ids;
- the time of the last change.

Nothing else. No names, no email addresses, no IP addresses. Cloudflare sees each request's IP address, as any host does, but the Worker does not store it. A second entry, `state`, holds the rank of every spot at the last run, so the next run can tell what has changed.

The endpoint and keys are enough to send that browser a notification, so treat the KV contents as private. Logs name a subscription by part of its hash, never by endpoint.

## Free-tier limits that matter

- Workers: 100,000 requests a day. Each subscribe, unsubscribe or CORS preflight is one request.
- KV (Cloudflare's KV limits page, read 29 Sep 2026): 100,000 reads and 1,000 writes a day, and 1,000 operations in one run. Each subscribe or change of saved spots is one write, so about 1,000 subscribes or changes a day. Each cron run that finds a newly high spot reads every subscription once, so one run can read at most about 1,000; the CPU limit below bites long before that.
- Cron triggers: free.

## How many alerts one run can send

Cloudflare's limits page (read 29 Sep 2026) gives the free plan 50 outgoing requests and 10 ms of CPU time per run, cron runs included, and 6 open connections at a time. Each notification is one outgoing request, and fetching `alerts.json` is another, so requests allow 49 notifications a run. CPU is the tighter limit: encrypting one notification took 0.28 ms of CPU in Node on a Mac, and signing once per push service 0.19 ms, so 10 ms fits roughly 25 to 30 notifications, fewer if Cloudflare's machines are slower. Past the limit the run is stopped, and the people not yet reached miss that alert.

That is enough while few people use alerts. Past that, either move to the Workers Paid plan (about $5 a month), which raises both limits, or send in batches across runs, which this Worker does not do yet. `npx wrangler tail` shows when a run is cut short.

The compatibility date needed for `fetch(..., { cache: 'no-store' })` was not checked; `wrangler.toml` sets `2026-09-01`. If `wrangler deploy` or the first run complains about `cache`, remove that option from `runCron`.

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

- Run `npx wrangler tail` and leave it open. Within 30 minutes you see a scheduled run. The first one logs `cron: first run, saved ranks for N spots, sent nothing`. Later runs log `cron: alerts.json unchanged (...)` or a line saying how many spots rose and how many alerts were sent, removed or failed.
- After the next site build, the Saved page shows an alerts button.

A run that fails with `VAPID_SUBJECT must be a mailto: or https: URL` or `public key must be a 65-byte uncompressed P-256 point` means step 5 was not finished.

## The mistake to avoid

The most likely mistake is pasting the private key into `wrangler.toml` or into the GitHub variable instead of the secret. It happens because the two keys are printed together and look alike, and the public key does go in both of those places. `wrangler.toml` is committed and the GitHub variable ends up in the published site, so either one publishes the private key. If it happens, make a new pair and repeat steps 3 to 7. Changing the key pair breaks every existing subscription, so everyone has to turn alerts on again.

## Tests

`node --test test/*.test.js` (or `npm test`). They need nothing installed. They were run on Node 26.
