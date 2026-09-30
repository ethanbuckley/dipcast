# SwimSignal swimmer testing

This is a test plan, not evidence of completed research. Run after Claude reviews the PR. Recruit 5–10 swimmers who use rivers or lakes, including some who are unfamiliar with water-quality terminology. Start with England, the app's current coverage.

## A 15-minute session

Ask permission to take anonymised notes. No recording, medical history, home address or precise location is needed. Give a public spot name or let the participant choose one. Tell them the prototype may use an older forecast and must not be used to decide whether to enter the water during this exercise.

Read each task without explaining the interface first:

1. Find a spot you recognise. Describe what the app covers and what it does not.
2. Choose a day for a hypothetical swim. Explain the pollution level in your own words.
3. Find what the level is based on. Is it a water test, a forecast, or an official rating? Where would you check current local warnings?
4. Save two or three spots and compare them on the same day. Explain whether a low level guarantees clean water.
5. Open one of the compared forecasts, then return. Can you find your saved spots again?
6. On an iPhone, find the Home Screen instructions. Try installation if the participant wants to. Check Saved in the installed app; Safari and the installed app can keep separate lists.
7. Use Feedback to prepare a message describing something confusing. Sending it is optional. Confirm the form does not say the message has already been sent.

Ask afterwards: What would make you return before your next swim? What did you distrust? What information was missing? Only then ask whether any proposed convenience feature would be worth paying for; do not imply a price or record a hypothetical yes as a sale.

## Record one row per task

Participant code | Device/browser | Task | Completed without help? | Time to complete | Misunderstanding or friction | Severity | Proposed change

Use participant codes P01–P10. Keep individual notes privately; put only aggregated findings in public GitHub issues. Delete unnecessary notes after synthesising the findings.

## Release decisions

Treat any interpretation of “low” as a safety guarantee, or any confusion between an annual rating and today's sample, as a release-blocking comprehension issue. Fix repeat navigation failures before adding new features. Re-test the changed task with a few fresh participants.

Success measures: unaided task completion, correct understanding of evidence and limitations, successful save/return/install, and voluntary return use. The feedback form includes an optional question about whether the forecast changed a plan. It is qualitative feedback, not population-level evidence or analytics.

Physical push delivery is a separate test: use push/DEVICE_TEST.md after staging deployment. No notification service is deployed by this PR.
