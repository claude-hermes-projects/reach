# Warn before the free plan's 50-person limit
Tier: qwen

## Goal
Cloudflare's free plan covers 50 people. The Doors screen warns when the sign-in lists, counted once per person
across all doors, go over 40, and says how to bring the number down.

## Files you may change
app/actions.py
app/ui/app.js

## Test command
python3 tests/run.py tests/test_card_002_people_warning.py

## Notes for the coder
- In `status()` in app/actions.py add `"people"`: the number of different email addresses across all doors, and
  `"people_warning"`: true when that number is above 40.
- In app/ui/app.js, in `vDoors()`, show a warning callout (same style as the existing `cn-callout--warning`) when
  `S.people_warning` is true. Text: say how many people are on the lists, that Cloudflare's free plan covers 50,
  and the steps: open a door's Settings, remove addresses that no longer need access, press Update people.
