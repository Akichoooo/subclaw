# Security Policy

## Supported versions

We release security patches for the latest minor version of `subclaw`. Older versions may receive patches at our discretion, but we recommend running the latest.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| older   | :x:                |

## Reporting a vulnerability

**Please do not file a public GitHub issue for security bugs.**

Report privately by emailing the maintainer (Akichoooo) — find the address on the GitHub profile page. Use the PGP key below if you have one; otherwise, regular email is fine for first contact and we'll move to a secure channel if needed.

Include:

- A clear description of the vulnerability
- Steps to reproduce (or a proof-of-concept)
- The impact you believe it has
- Any suggested fix (optional but appreciated)

We will:

- Acknowledge receipt within 72 hours
- Triage and assess severity within 1 week
- Coordinate disclosure timing with you
- Credit you in the fix's release notes (unless you prefer to remain anonymous)

## What we consider in scope

- Anything in `proxy/app.py`, `cli-skills/run-claw-pool.sh`, or other project code
- Default configurations in `keys.example.json` (if they leak secrets or weaken security)
- Documentation that, if followed, would put a user at risk

## What is generally not in scope

- Denial-of-service via intentional misuse of the gateway (the proxy is unauthenticated by design; deploying it on a public network without auth is a configuration choice, not a vulnerability)
- Issues in upstream dependencies (FastAPI, httpx, etc.) — please report those upstream
- "I would prefer a different design" — open a discussion, not a security report

## Security best practices when deploying subclaw

- **Do not bind the proxy to a public network without auth.** It has no built-in auth (yet — see [roadmap](../README.md#-roadmap)). If you must, put it behind nginx with basic auth.
- **Do not commit `keys.json`.** It's in `.gitignore` for a reason. If you accidentally did, rotate the leaked keys immediately.
- **Use the budget circuit breaker.** `max_spend_per_session_usd` and `max_spend_per_day_usd` in `keys.json` are your safety net.
- **Set `mask_secrets_in_payload: true`** in `keys.json` to prevent the proxy from logging real keys.
- **Run the latest version.** We backport security fixes to the latest release only.

## Hall of fame

We thank the following researchers for responsibly disclosing issues:

*(none yet — be the first)*

## License

This security policy is part of the subclaw project and is released under the [MIT License](../LICENSE).
