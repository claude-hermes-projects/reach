# Reach

Reach is a Mac app for reaching the AI tools running on your Mac (Ollama, LM Studio, Open WebUI, ComfyUI or
anything with a local web address) from your phone or another computer. Each tool gets its own address on your
domain, such as `ai.yourdomain.com`, and every visit asks for a sign-in first.

- **No open ports.** A small connector from Cloudflare connects outward from your Mac.
- **Sign-in on every door.** People get in with a one-time code sent to their email; only the addresses you list
  are let in. Scripts and apps get their own device key, which you can remove at any time.
- **One off switch.** "Close everything" stops the connector, so nothing on the Mac is reachable until you turn it back on.
- **Keys stay in the keychain.** Nothing secret is written to a file.

## What you need

- A Mac with macOS 11 or later.
- A free Cloudflare account with your domain added to it.
- Cloudflare Zero Trust on its Free plan (up to 50 people). Cloudflare asks for a payment card when you switch
  it on, even on the Free plan; nothing is charged.
- Homebrew, the free installer for Mac tools (Reach uses it to install Cloudflare's connector program).

## Install

1. Unzip `Reach-app.zip` and drag `Reach.app` into Applications.
2. Open it. The first time, macOS may block it because it isn't from the App Store: open System Settings → Privacy & Security,
   scroll down, click **Open Anyway** next to Reach, and confirm.
3. Follow **Set up** (about 15 minutes, once), then add your first door.

## How it works

Reach uses Cloudflare Tunnel for the connection and Cloudflare Access for the sign-in. For each door it creates
the sign-in rule first, then the address, then the route to your Mac. The connector on the Mac also checks each
request's sign-in before passing it on. When a door is closed or removed, its route is taken away first.

The connector runs in the background and starts again after a restart, even when the Reach window is closed.

## Limits

- The Mac has to be awake and online.
- Device keys travel as two request headers (`CF-Access-Client-Id`, `CF-Access-Client-Secret`). Most scripts and
  many tools can send them; some phone apps can't. A browser chat page (for example Open WebUI behind its own door)
  works on any phone.

See SECURITY.md for what is protected and how to report a problem.
