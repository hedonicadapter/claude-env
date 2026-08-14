# hidden-from-llms

Picks content that should be invisible to an LLM reading the web, then proves
with Crawlee whether it actually is.

## The hypothesis

Web content is hidden from LLMs two different ways:

- **Policy-hidden** — `robots.txt` tells AI crawlers to go away. The bytes are
  served to a browser, but a well-behaved crawler must not take them.
- **Render-hidden** — the server sends an empty shell and the content only
  exists after JavaScript runs and calls the site's private APIs. Nothing is
  forbidding you; an HTTP-only fetcher just cannot see it.

The second kind is the interesting one, because most LLM "fetch this URL" tools
are HTTP-only. Anything render-hidden is silently missing, with no error to
signal it.

## Target

`avanza.se` (Swedish stockbroker), stock detail page for ABB.

Chosen because its `robots.txt` is `User-agent: *` with **no** `Disallow` — it
invites crawlers — while the page itself is an Angular SPA that ships **6
characters** of visible text. It is render-hidden without being policy-hidden,
so the experiment tests the rendering gap and nothing else.

Sites that opt out in `robots.txt` are only *audited* here, never crawled.

## Running it

```sh
npm install crawlee playwright
NODE_USE_ENV_PROXY=1 node crawl.mjs
```

Override the page with `TARGET=<url>`.

## Result

```
robots audit
  nytimes.com     ClaudeBot, Claude-Web, anthropic-ai, GPTBot, CCBot, Google-Extended → Disallow: /
  linkedin.com    same six, plus User-agent: * → Disallow: /
  reddit.com      User-agent: * → Disallow: /        (blanket ban, no AI crawler named)
  avanza.se       no restrictions

target: avanza.se stock page for ABB
  HTTP only (CheerioCrawler)    10 267 B HTML →     6 chars of text: "Avanza"
  rendered (PlaywrightCrawler)  550 439 B HTML → 5 119 chars of text

  instrument   ABB
  last price   974.8 SEK   at 15:41
  OMXS30       -0,30%
  top owners   Investor AB 14,6% · UBS Fund Management 5,0% · BlackRock 4,2%

  priceFoundInServedHtml: false
```

`priceFoundInServedHtml: false` is the actual assertion: the script searches the
served HTML for the price string it read out of the rendered DOM, and does not
find it. The page is a 91× text amplification between what curl gets and what a
browser gets. Re-running minutes apart returns a different price, confirming the
value is live rather than cached markup.

## Notes on the implementation

Two things were non-obvious.

**The browser could not do its own TLS.** This sandbox routes egress through a
proxy that re-terminates TLS. Node trusts the proxy CA via `NODE_EXTRA_CA_CERTS`;
Chromium's verifier does not, and every HTTPS navigation died on
`ERR_CERT_AUTHORITY_INVALID`, then `ERR_CONNECTION_RESET`. Rather than turn
verification off, `routeThroughNode` intercepts every browser request with
`page.route` and serves it from Node's `fetch` — Chromium renders, Node does the
network, verification stays on.

**Crawlers share a request queue.** All three phases hit the same URL, and the
default queue deduplicates, so the Playwright phase silently processed zero
requests. Each phase now opens and drops its own named queue.
