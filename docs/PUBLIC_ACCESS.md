# Public HTTPS deployment gates

## Linux native access update (2026-10-18)

The public site now responds over valid HTTPS with Google OAuth redirects; the
historical deployment gates below are not a fresh authentication audit. The
Linux native app instead uses private Tailscale HTTPS at
`https://sdolbec-ser8.tailf80972.ts.net:8445`, with an exact configured user
identity. It requires Tailscale connectivity, not SSH or Google login in WebKit.
The public site's Google gateway is retained. See CONNECTION_RECOVERY.md for
the deployed direct connection and rollback; no public authentication bypass was
added. Full public OAuth/passkey round-trip validation belongs in a regular browser.

## Verified state (2026-10-12)

SER8's Incus instance serves the reviewed collector on guest loopback port 5051,
reached privately through host loopback 15033. The collector continues
independently of the Mac SSH tunnel. The energy.stoverparc.org HTTPS probe
still fails its TLS handshake, although DNS resolves to the same public address
as the existing site. The active Caddy configuration has no route for this
hostname. It is root-owned, and the collector SSH user has no passwordless
sudo. The public service is therefore not deployed or reachable yet. The
desktop currently has private LAN access; that is not an offsite solution.

Creating DNS alone does not install a proxy route or issue a certificate.
Do not publish the backend without authentication: its dashboard is not an
authenticated public application, and includes settings and device controls.

Release 2.3.43 adds application-side support for a single explicitly configured
HTTPS origin through one trusted reverse-proxy hop. This is opt-in using
`ENERGY_PUBLIC_ORIGIN=https://energy.stoverparc.org`; with the variable unset,
the local-only host policy is unchanged. The Flask listener remains on
`127.0.0.1`. This setting is not authentication and must not be enabled until
the proxy protects the complete dashboard and all browser APIs.

## Required implementation

1. Have the proxy administrator confirm DNS, inbound routing, and certificate
   issuance for energy.stoverparc.org without changing other hosted sites.
2. Configure an identity-provider-backed access gateway with an explicit user
   allowlist. Protect every dashboard route, not just the landing page.
3. Set `ENERGY_PUBLIC_ORIGIN` only in the dashboard service's private
   environment file after the authenticated proxy is installed. The app trusts
   exactly one forwarded host and scheme; ensure only the local Caddy proxy can
   reach the loopback upstream. Do not set this on a directly exposed listener.
   HTTPS same-origin writes remain required; arbitrary hosts and HTTP are
   rejected.
4. Keep separate revocable machine authentication for desktop sync. An OAuth
   browser cookie must not be required for unattended sync. Existing sync bearer
   authorization must remain enforced; the proxy must preserve `Authorization`.
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

The history and radon sync routes may bypass browser login only at the proxy
because the application independently requires the existing
`ENERGY_SYNC_TOKEN`. No other path may bypass login. Keep sync tokens out of URLs
and logs.

Add `https://energy.stoverparc.org/oauth2/callback` to the Google OAuth client
used by a dedicated energy OAuth proxy. Do not reuse a host-only cookie from a
different subdomain, broaden the allowed account list, or put OAuth client
secrets in this repository. Verify OAuth callback and denial flows in a normal
browser before changing the desktop collector origin to
`https://energy.stoverparc.org`.

Until all gates pass from an offsite network, retain the working private path
and cached history. Public deployment is not complete; the private tunnel does
not provide public authentication or establish fresh sensor measurements.
