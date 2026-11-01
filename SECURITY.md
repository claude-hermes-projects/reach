# Security

## What Reach protects

| Risk | Protection |
|---|---|
| Strangers using your AI tools | Every door is a Cloudflare Access application. Only listed email addresses (one-time code) or listed device keys get through. |
| An address opening before its sign-in rule exists | Reach creates the sign-in rule first and opens the address only after it exists. If a later step fails, the door stays closed. |
| A sign-in rule being changed or removed by mistake in Cloudflare | The connector on your Mac also checks each request's Access sign-in (origin check). If Cloudflare refuses that setting, Reach says so on the Doors screen; Cloudflare's own sign-in still applies. |
| Open ports and port scans | None are opened. The connector makes outward connections only. |
| Secrets leaking from files | The Cloudflare key and the connector's key live in the macOS keychain. The background job reads the connector's key from the keychain when it starts. Device secrets are shown once and never stored. |
| Other websites pressing Reach's buttons | The screen is served on 127.0.0.1 only, checks the Host header, needs a secret made fresh at each start, and can't be shown in another page's frame. |
| Needing to shut everything quickly | "Close everything" stops the connector at once. |

## The Cloudflare key Reach needs

Reach keeps its own key, separate from any other app's. It needs only: Cloudflare Tunnel (edit),
Access apps and policies (edit), Access service tokens (edit), Access organizations and identity providers
(edit, to switch on email codes), and DNS (edit) for the one domain you use.

## Known limits

- Anyone who can sign in to a door can use that tool as fully as you can on the Mac. List only people you trust.
- A sign-in lasts 24 hours. Removing someone from a door's list takes effect at their next sign-in; to cut access
  at once, close the door or use "Close everything".
- Device keys expire after a year. Remove a key as soon as a device is lost.
- Your Mac is trusted: anything running as you can read your keychain items while it is unlocked.

## Reporting a problem

Please report security issues privately through GitHub's "Report a vulnerability" button on this repository's
Security tab rather than in a public issue.
