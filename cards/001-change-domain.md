# Move all doors to another domain
Tier: deepseek

## Goal
A person can move every door to a different domain in one step, for example when a domain expires. Each door keeps
its short name (chat.old.com becomes chat.new.com), its list of people and its device keys.

## Files you may change
app/actions.py
app/ui/app.js

## Test command
python3 tests/run.py tests/test_card_001_change_domain.py

## Notes for the coder
- Add an action `change_zone` (args: `zone_id`) to `ACTIONS` in app/actions.py, titled "Move doors to another domain".
- Refuse a domain the key can't see (not in `zones_seen`) and the domain already in use.
- Check every new address first (`GET /zones/{id}/dns_records?name=...`). If any is taken by another record, refuse
  and change nothing; name the taken address in the message.
- Move each door's sign-in rule in place with `PUT /accounts/{acc}/access/apps/{app_id}` (send name, domain, type
  "self_hosted", session_duration, app_launcher_visible). Updating in place keeps people lists and device keys.
- Then create the new addresses (reuse `ensure_dns`), send the door list (`push_config`), and only then delete the
  old address records. Save state at the end.
- In app/ui/app.js, the done "Domain" row in Set up gets a "Change" button that shows the domain picker and a
  "Move doors to this domain" button wired to `change_zone`, with a confirm message.
- Keep the existing tone: plain words, say what to do next in every error.
