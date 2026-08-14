/**
 * Verify that content invisible to HTTP-only fetchers (the way most LLM tools
 * read the web) is reachable with a real browser driven by Crawlee.
 *
 * Three phases:
 *   1. robots  — which sites explicitly Disallow AI crawlers (policy-hidden)
 *   2. baseline — CheerioCrawler, HTTP only: what a naive fetcher sees
 *   3. render   — PlaywrightCrawler: what a browser sees
 *
 * Browser traffic is routed through Node's fetch (see routeThroughNode) because
 * this sandbox's egress proxy re-terminates TLS; Node trusts the proxy CA via
 * NODE_EXTRA_CA_CERTS, Chromium's own verifier does not. Verification stays on.
 */
import { CheerioCrawler, PlaywrightCrawler, ProxyConfiguration, Dataset, RequestQueue } from 'crawlee';

const TARGET = process.env.TARGET ?? 'https://www.avanza.se/aktier/om-aktien.html/5447/abb';
const PROXY = process.env.HTTPS_PROXY || undefined;
const CHROME = '/opt/pw-browsers/chromium';

const AI_AGENTS = ['ClaudeBot', 'Claude-Web', 'anthropic-ai', 'GPTBot', 'CCBot', 'Google-Extended'];
const ROBOTS_SITES = [
    'https://www.nytimes.com',
    'https://www.linkedin.com',
    'https://www.reddit.com',
    'https://www.avanza.se',
];

const proxyConfiguration = PROXY ? new ProxyConfiguration({ proxyUrls: [PROXY] }) : undefined;

// ---------------------------------------------------------------- helpers

const visibleText = (html) =>
    html
        .replace(/<(script|style|noscript)[^>]*>[\s\S]*?<\/\1>/gi, ' ')
        .replace(/<[^>]+>/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

/** Fresh queue per phase — a shared one dedupes the same URL across crawlers. */
const freshQueue = async (name) => {
    const q = await RequestQueue.open(name);
    await q.drop();
    return RequestQueue.open(name);
};

/** Serve every browser request from Node so TLS is verified against the proxy CA. */
const routeThroughNode = async ({ page }) => {
    const drop = ['host', 'connection', 'content-length', 'accept-encoding'];
    await page.route('**/*', async (route) => {
        const req = route.request();
        if (!/^https?:/i.test(req.url())) return route.abort();
        const headers = Object.fromEntries(
            Object.entries(req.headers()).filter(([k]) => !drop.includes(k.toLowerCase())),
        );
        try {
            const res = await fetch(req.url(), {
                method: req.method(),
                headers,
                body: ['GET', 'HEAD'].includes(req.method()) ? undefined : req.postDataBuffer(),
                redirect: 'follow',
            });
            const body = Buffer.from(await res.arrayBuffer());
            const out = {};
            res.headers.forEach((v, k) => {
                if (!['content-encoding', 'content-length', 'transfer-encoding'].includes(k)) out[k] = v;
            });
            await route.fulfill({ status: res.status, headers: out, body });
        } catch {
            await route.abort();
        }
    });
};

// ---------------------------------------------------------------- phase 1

/** Report which AI crawlers each site blocks at the root path. */
async function robotsAudit() {
    const rows = [];
    await new CheerioCrawler({
        proxyConfiguration,
        requestQueue: await freshQueue('robots'),
        maxRequestRetries: 1,
        additionalMimeTypes: ['text/plain'], // robots.txt
        requestHandler({ request, body, response }) {
            const txt = body.toString();
            const blocked = AI_AGENTS.filter((ua) => {
                // Grab the block for this user-agent, stop at the next blank line
                const m = txt.match(new RegExp(`User-agent:\\s*${ua}\\s*\\n([\\s\\S]*?)(\\n\\s*\\n|$)`, 'i'));
                return m ? /Disallow:\s*\/\s*$/im.test(m[1]) : false;
            });
            // Reddit-style blanket ban catches AI crawlers without naming them
            const wildcard = /User-agent:\s*\*\s*\n(?:(?!User-agent:)[^\n]*\n)*?\s*Disallow:\s*\/\s*$/im.test(txt);
            rows.push({ site: new URL(request.url).host, status: response.statusCode, blocksAllCrawlers: wildcard, blocked });
        },
        failedRequestHandler({ request }, err) {
            rows.push({ site: new URL(request.url).host, status: 'unreachable', blocked: [`fetch failed: ${err.message.split('\n')[0]}`] });
        },
    }).run(ROBOTS_SITES.map((s) => `${s}/robots.txt`));
    return rows;
}

// ---------------------------------------------------------------- phase 2

/** What an HTTP-only client (curl, most LLM fetch tools) receives. */
async function baseline(url) {
    let out;
    await new CheerioCrawler({
        proxyConfiguration,
        requestQueue: await freshQueue('baseline'),
        maxRequestRetries: 2,
        requestHandler({ body, response }) {
            const html = body.toString();
            out = {
                status: response.statusCode,
                htmlBytes: html.length,
                visibleChars: visibleText(html).length,
                visibleSample: visibleText(html).slice(0, 120),
                html,
            };
        },
    }).run([url]);
    return out;
}

// ---------------------------------------------------------------- phase 3

/** What a real browser renders after the SPA executes and calls its APIs. */
async function render(url) {
    let out;
    await new PlaywrightCrawler({
        headless: true,
        requestQueue: await freshQueue('render'),
        maxRequestRetries: 2,
        navigationTimeoutSecs: 120,
        requestHandlerTimeoutSecs: 180,
        launchContext: { launchOptions: { executablePath: CHROME, args: ['--no-sandbox'] } },
        preNavigationHooks: [routeThroughNode],
        async requestHandler({ page }) {
            await page.waitForLoadState('networkidle').catch(() => {});
            // SPA hydrates after networkidle; wait for the quote to actually paint
            await page.getByText('Senast betalt').first().waitFor({ timeout: 60_000 }).catch(() => {});
            await page.waitForTimeout(3000);

            const html = await page.content();
            out = await page.evaluate(() => {
                const text = document.body.innerText;
                const grab = (re) => (text.match(re) ?? [])[1]?.trim() ?? null;
                const owners = [...document.querySelectorAll('table')]
                    .filter((t) => /största ägare/i.test(t.innerText))
                    .flatMap((t) =>
                        [...t.querySelectorAll('tbody tr')].map((tr) =>
                            [...tr.querySelectorAll('td, th')].map((c) => c.innerText.trim()),
                        ),
                    );
                return {
                    instrument: grab(/Stockholmsbörsen\s*\|\s*Aktie\s*\n(.+)/),
                    lastPrice: grab(/Senast betalt\s*\n([\d\s.,]+ SEK)/),
                    omxs30: grab(/OMXS30\s*\n\s*([-+]?[\d,]+\s*%)/),
                    quoteTime: grab(/OMXS30\s*\n[^\n]*\n\s*(\d{1,2}:\d{2})/),
                    owners: owners.slice(0, 5),
                    textChars: text.length,
                };
            });
            out.html = html;
            out.htmlBytes = html.length;
        },
    }).run([url]);
    return out;
}

// ---------------------------------------------------------------- report

const robots = await robotsAudit();
const base = await baseline(TARGET);
const live = await render(TARGET);

// The claim under test: the rendered value is absent from the served HTML
const inSource = live.lastPrice ? base.html.includes(live.lastPrice.replace(' SEK', '')) : null;

const report = {
    target: TARGET,
    robots,
    httpOnly: { status: base.status, htmlBytes: base.htmlBytes, visibleChars: base.visibleChars, visibleSample: base.visibleSample },
    rendered: {
        htmlBytes: live.htmlBytes,
        textChars: live.textChars,
        instrument: live.instrument,
        lastPrice: live.lastPrice,
        omxs30: live.omxs30,
        quoteTime: live.quoteTime,
        owners: live.owners,
    },
    priceFoundInServedHtml: inSource,
};

console.log('\n================ RESULT ================');
console.log(JSON.stringify(report, null, 2));
await Dataset.pushData(report);
