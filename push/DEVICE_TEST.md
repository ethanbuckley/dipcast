# iPhone notification test

Status: **not yet run on a physical iPhone**. The alert service and HTTPS site are deployed, and the checked-in public key matches the site's configured key (checked 3 October 2026). Local regression tests do not prove APNs delivery, permissions or Home Screen behaviour. Follow the isolated test steps below before claiming device verification.

## Before the phone test

After code review, configure an HTTPS test site and Worker following `README.md`. Use a separate test KV namespace and test subscription so forecast fixtures never generate warnings for public subscribers. The site must use the same VAPID public key as the Worker. Do not use localhost links on the phone: they refer to the phone itself.

Do not deploy or enable production alerts as part of an automated test. Record iPhone model, iOS version, test site address, source commit and test date with the results.

## Owner's steps

1. Open the HTTPS test site in Safari, add it to the Home Screen, then launch its icon.
2. Save a test spot. Enable alerts and accept the notification prompt yourself.
3. Lock the phone. Have the reviewer send the single test notification described below.
4. Confirm that it says **SwimSignal test notification**, arrives on the lock screen or in Notification Centre, and opens the app's Saved page when tapped. Note Focus mode and notification settings if no banner appears.
5. Remove the test spot, then turn alerts off. Confirm the Worker subscription is updated/deleted after propagation; a subsequent test cron should not send to it.
6. Close and reopen the Home Screen app. Check that the saved spots, permission state and offline forecast timestamp remain understandable.

## Send one test, without inventing a pollution incident

The reviewer securely retrieves **only the owner's test subscription** from the test KV namespace into a private local JSON file. Either the whole stored record or its `subscription` object is accepted. Do not paste it into a public issue, commit it, or print endpoint/key contents in logs.

With `SITE_URL`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT` already supplied securely in the local environment, run from the repository root:

```sh
node push/scripts/send-test.mjs /private/path/subscription.json
```

This performs one real outbound push to that subscription, clearly labelled as a test, with a 60-second lifetime. It does not modify any forecast or subscriber record. HTTP acceptance means the push service accepted the message; the owner must confirm actual display and tap behaviour. Remove the temporary private file after testing.

## Reviewer checks in the isolated test service

Use fresh, dated fixtures for the test site's `data/alerts.json` and the test subscriber only. Check low → high, high → low before delivery, removal of one spot from a combined alert, and a queue that waits out an expired forecast and resumes with the next one. Do not alter the public forecast to test these transitions. HTTP 429/503 responses and network failures are simulated in the automated suite; do not deliberately overload Apple's service.

For an offline phone, confirm that an accepted push does not later display an expired risk claim. The push TTL is capped by expiry; if a message reaches the service worker late, it displays a neutral invitation to check the latest forecast. Already-visible notifications are not remotely withdrawn.

Record actual results, including delivery delay, whether the app was open/closed/locked, and any missing messages. An unrun step remains **not tested**, never a pass.
