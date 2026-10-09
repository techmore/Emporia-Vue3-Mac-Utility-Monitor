# Public HTTPS deployment gates

## Verified state (2026-10-08)

SER8's Incus instance serves the reviewed collector on guest loopback port 5051,
reached privately through host loopback 15033. The collector continues
independently of the Mac SSH tunnel. The energy.stoverparc.org HTTPS probe
failed its TLS handshake, and the active Caddyfile has no route for this host.
The Caddyfile is root-owned; the collector SSH user has no passwordless sudo.

Creating DNS alone does not install a proxy route or issue a certificate.
Do not publish the backend without authentication: its dashboard is not an
authenticated public application, and includes settings and device controls.

## Required implementation

1. Have the proxy administrator confirm DNS, inbound routing, and certificate
   issuance for energy.stoverparc.org without changing other hosted sites.
2. Configure an identity-provider-backed access gateway with an explicit user
   allowlist. Protect every dashboard route, not just the landing page.
3. Add an opt-in trusted-proxy configuration to the application. Currently
   web.validate_local_request rejects non-loopback Host values. Preserve this
   default and test HTTPS same-origin writes before permitting the public host.
   Never blindly rewrite Host or Origin to bypass these checks.
4. Design separate revocable machine authentication for desktop sync. An OAuth
   browser cookie must not be required for unattended sync. Existing sync bearer
   authorization must remain enforced; do not strip or replace that header.
5. Keep Flask bound to 127.0.0.1. Keep credentials and private keys out of Git,
   proxy access logs, and deployment output. Disable public device-control
   endpoints until authorization and CSRF protection are verified.

## Cutover acceptance tests

- Anonymous dashboard, API, and mutation requests cannot reach the backend.
- An allowed signed-in user can load dashboard and history over valid HTTPS.
- A disallowed account cannot access any route.
- Cross-origin mutations fail; approved same-origin actions still work.
- Sync with missing or incorrect credentials fails; authorized sync succeeds
  without granting device-control permission.
- Collector data continues advancing while the laptop is disconnected.
- Reconnection catches up without duplicate readings or fabricated live values.
- Proxy reload does not interrupt other hosted services.

Until these gates pass, retain the existing private SSH connection and cached
history. Public deployment is not complete. The private SSH tunnel watchdog
is installed and verified; see CONNECTION_RECOVERY.md. Its transport recovery
does not provide public authentication or establish fresh sensor measurements.
