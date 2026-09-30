# SwimSignal review notes

## Scope

Rebrands the public app as SwimSignal; makes day planning consistent across the map, list and detail headline; adds saved-spot comparison, evidence summaries, and a private email feedback composer. `SWIMMER_TESTING.md` is the plan for real participant research, not completed research.

## Compatibility

The GitHub repository, Python package, environment variables, Cloudflare Worker name, notification deduplication tags, local-storage keys, service-worker cache name, installation scope and public paths remain unchanged. Existing saved spots and subscriptions therefore do not require a migration on the current origin. A future custom domain is a separate origin migration: it does not automatically inherit saved spots, subscriptions or an installed PWA. Export/share saved lists and explain reinstallation before moving domains. No domain configuration, purchase, hosting deployment or worker deployment is part of this PR.

## Evidence limits

Evidence labels use the existing snapshot only. Individual bacterial sample results and live short-term bathing warnings are not ingested. Annual EA ratings keep their year, algae checks keep their date, and absent monitoring is never labelled a clear result. The selected date drives season-limit wording. Existing risk thresholds and scoring remain unchanged.

## Feedback delivery

`feedback.html` works on static GitHub Pages and `/feedback` on FastAPI. It prepares an encoded mailto to ethan@ethanbuckley.me.uk and shows a copyable preview. Preparing a draft sends nothing. Actual sending requires an email client, or copying the draft into webmail. No new receiving service or credentials are required. Input is not stored by this form. Existing optional analytics settings are unchanged.

## Suggested review checks

- Pick different days in Explore and on the map; check list levels, ordering, low filter, counts, links and opened headline agree.
- Choose a missing-data day and a poorly rated bathing water: no-data must not become low, and standing advice must survive.
- Save three spots; compare two and three, choose duplicate options, remove/undo a saved spot, open a comparison detail and return.
- Use 320–390 px phone widths and desktop layout; scroll the comparison horizontally with touch or keyboard. Comparison starts collapsed so it does not bury Saved cards.
- Read the evidence card for river, lake, undesignated, failed forecast and missing feeds. Change from September to an October forecast day.
- Prepare a feedback/spot-request draft, edit it and prepare again. Nothing should say sent; reserved characters must remain in the message body.
- Check Home Screen name and share preview after deployment; existing installed names/previews may remain cached by platforms.

## Remaining external validation

5–10 real swimmers still need to take part in the study. iPhone install and push delivery require physical-device testing, and the alert service still needs staging deployment (see `push/DEVICE_TEST.md`). The local preview serves a copied snapshot and deliberately shows its original age; it does not run the data pipeline.
