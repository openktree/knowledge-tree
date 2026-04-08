---
sidebar_position: 8
title: Fetch Providers
---

# Fetch Providers — How URLs Become Raw Sources

Knowledge Tree is built on the principle that **every fact traces back to a real
external source**. Before a URL can become facts, it has to become content — and
different sites need very different strategies to be fetched. Some are open
HTML, some are behind WAFs that block plain HTTP clients, some are academic
publishers that don't need to be scraped at all because their metadata is
available through public APIs.

The **fetch provider chain** is the layer that handles all of that. It's a
configurable pipeline of strategies that's tried in order for each URL, with a
full audit trail of which strategies were attempted, which one won, and how
long each took.

## The chain

Every URL submitted for ingestion (via the research UI, the API, or a search
workflow) goes through `FetchProviderRegistry.fetch(uri)`. The registry tries
providers in the configured order and short-circuits on the first success:

```
URL → SSRF guard → DOI shortcut → curl_cffi → httpx → flaresolverr → fail
                       ↓               ↓          ↓          ↓
                    success?       success?   success?   success?
                       ↓               ↓          ↓          ↓
                     done            done       done       done
```

Each provider self-disables when its required configuration or dependency is
missing, so a deployment without `curl_cffi` or without a Byparr container
still works — the chain just skips those tiers.

## Providers shipped today

### `doi` — academic publisher shortcut

For URLs from known publishers (cell.com, sciencedirect.com, nature.com,
springer.com, wiley.com, ieeexplore.ieee.org, dl.acm.org, jstor.org, biorxiv.org,
and others), the provider extracts the DOI from the URL or the page's
`<meta name="citation_doi">` tag, then queries:

- **Crossref** (`api.crossref.org/works/{doi}`) — canonical metadata: title,
  authors, abstract, journal, published date
- **Unpaywall** (`api.unpaywall.org/v2/{doi}`) — open-access PDF link, when one
  exists

This sidesteps scraping entirely for academic links. Crossref and Unpaywall
are free public APIs with no bot challenges. Configure the contact emails
under `crossref_email` and `unpaywall_email` settings (Crossref uses them
for its "polite pool" rate limits; Unpaywall *requires* one).

### `httpx` — plain HTTP baseline

Stock async HTTP client (`httpx.AsyncClient`) with realistic browser headers
(User-Agent, Accept, Accept-Language, Sec-Fetch-\*). Cheapest possible tier
and what most "open" sites need.

### `curl_cffi` — TLS fingerprint impersonation

A drop-in replacement for `httpx` that uses `libcurl-impersonate` to mimic a
real Chrome browser at the **TLS / JA3 fingerprint** level. A surprising
fraction of "Cloudflare blocks" are actually TLS-fingerprint blocks against
the OpenSSL handshake `httpx` uses; Chrome's BoringSSL handshake gets through
them with **no extra infrastructure** (no headless browser, no extra
container). This is the recommended first tier above plain `httpx` and is
what unblocks publishers like cell.com.

The Chrome version it impersonates is configurable via `fetch_curl_cffi_impersonate`
(default `chrome124`).

### `flaresolverr` — headless Chromium fallback

For sites that detect TLS impersonation and run a JavaScript challenge.
Speaks the FlareSolverr v1 HTTP protocol, so it works against either
[upstream FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) or its
more actively maintained fork [Byparr](https://github.com/ThePhaseless/Byparr).
A single shared container handles every blocked request for the whole cluster.
Self-disables until `fetch_flaresolverr_url` is set.

## Preferred fetcher resolution

For each fetch, the registry resolves an **effective preferred provider** to
try first. On failure, it falls back to the rest of the chain (skipping the
already-tried preferred id so the same provider isn't tried twice).

The preferred provider comes from three sources, in priority order:

1. **Explicit caller arg** — `registry.fetch(uri, preferred="flaresolverr")`.
   Useful when re-trying after a known failure or when the caller already
   knows which strategy will work.
2. **Learned per-host preference** — when a non-default provider succeeds for
   a host, the registry records `host → provider_id` in Redis with a 30-day
   TTL. The next time we see the same host, that provider is tried first
   instead of marching through the chain from the top. Stale preferences are
   forgotten on total failure so we re-learn.
3. **Static per-host overrides** — the `fetch_host_overrides` setting maps
   hosts (exact or parent suffix) to provider ids, e.g.
   `{"cell.com": "flaresolverr"}`. Lowest priority of the three so an explicit
   call still wins.

This means once `curl_cffi` unblocks `cell.com` once, every subsequent
`cell.com` URL goes straight to `curl_cffi` without paying the latency of
trying `doi` first.

## SSRF guard

Knowledge Tree ingests **user-supplied URLs**. Without a guard, that's a direct
path to:

- Internal services on the cluster (Redis, Postgres, Hatchet) via
  `http://10.x` or `http://127.0.0.1:6379`
- Cloud instance metadata endpoints (`http://169.254.169.254/`) — credential
  exfiltration
- Non-HTTP schemes like `file:///etc/passwd` or `gopher://`

The registry calls `validate_fetch_url()` at the very entry of `fetch()`,
**before any provider runs**. The validator:

1. Rejects any scheme that isn't `http` or `https`.
2. Resolves the hostname via DNS.
3. Inspects **every** A/AAAA record returned and rejects if any of them lands
   in a private, loopback, link-local, multicast, reserved, or unspecified
   range. (Checking every record closes a small DNS-rebinding window where the
   first response is public but a later one isn't.)

Rejections are returned as a normal failed `FetchResult` with a synthetic
`url_safety` attempt entry — never raised — so callers see them on the same
code path as ordinary fetch failures, and the audit trail records why.

This is best-effort defense in depth. Full DNS-rebinding immunity would
require a custom resolver inside every HTTP client we ship, which is out of
scope.

## Audit trail

Every fetch records a `FetchResult` with:

| Field          | Meaning                                                          |
| -------------- | ---------------------------------------------------------------- |
| `provider_id`  | id of the strategy that produced the successful result, or null |
| `attempts`     | ordered list of `(provider_id, success, error, elapsed_ms)`     |
| `error`        | last meaningful error message, when nothing succeeded            |

The audit trail is persisted on `RawSource.provider_metadata.fetcher` so it
survives across pipeline restarts and is readable from the API. The source
detail page in the research UI renders it as:

```
Fetched via curl_cffi (tried doi → curl_cffi)
✗ doi        — 261ms   no DOI found for URL
✓ curl_cffi  — 3135ms
```

This means users can finally tell **"the URL is wrong"** apart from
**"the WAF blocked us, here's exactly what we tried"** — which was the
motivating bug for the whole feature.

## Configuration

All settings live in `kt_config.Settings` (sourced from `.env` or environment
variables):

| Setting                            | Default                                  | Purpose                                                 |
| ---------------------------------- | ---------------------------------------- | ------------------------------------------------------- |
| `fetch_provider_chain`             | `doi,curl_cffi,httpx,flaresolverr`       | Comma-separated provider ids in fallback order          |
| `fetch_user_agent`                 | A Chrome 131 UA string                   | UA used by `httpx` and `doi` providers                  |
| `fetch_curl_cffi_impersonate`      | `chrome124`                              | TLS fingerprint profile for `curl_cffi`                 |
| `fetch_flaresolverr_url`           | empty                                    | Byparr/FlareSolverr endpoint; empty disables the tier   |
| `fetch_flaresolverr_timeout`       | `60.0`                                   | Seconds to wait for the headless browser                |
| `fetch_host_overrides`             | `{}`                                     | Static per-host preferred providers                     |
| `fetch_host_pref_ttl_seconds`      | 30 days                                  | TTL on the Redis-backed learned-preference cache        |
| `crossref_email` / `unpaywall_email` | empty                                  | Contact emails for the DOI provider's external APIs     |

## Adding a new provider

A fetch provider is any class that subclasses `ContentFetcherProvider`
(in `kt_providers.fetch.base`) and implements three methods:

```python
class MyContentFetcher(ContentFetcherProvider):
    @property
    def provider_id(self) -> str:
        return "my_fetcher"

    async def is_available(self) -> bool:
        # Return False when the provider's config/dep is missing.
        return bool(get_settings().my_endpoint)

    async def fetch(self, uri: str) -> FetchResult:
        # Should NEVER raise on transport-level failures — catch and put
        # the description in FetchResult.error.  The registry will then
        # fall through to the next provider in the chain.
        ...
```

Then register it in `build_fetch_registry()` (`libs/kt-providers/src/kt_providers/fetch/builder.py`)
and add the provider id to your `fetch_provider_chain` setting. The shared
extraction helpers in `kt_providers.fetch.extract` (`extract_html`,
`extract_pdf`, `extract_image`, `extract_text`) handle the content-type
branching once you have bytes/text in hand, so most new providers are a
few dozen lines.
