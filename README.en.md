<div align="center">

# AniSignal

**Bring updates together. Follow what changes.**

Keyword-driven information collection, event tracking, and subscriptions.

`Python 3.12+` · `Nuxt 4` · `Self-hosted`

[Trial site](http://anisignal.nanamiyako.cn/) · [Feature guide](docs/features.md) · [Deployment](docs/deployment.md) · [Issues](https://github.com/tianyin231/AniSignal/issues)

[简体中文](README.md) · **English**

</div>

---

AniSignal brings public information from multiple platforms into one workspace. It collects configured sources, filters low-relevance material, groups eligible items into events, and preserves source links, evidence, and revision history. Readers can browse updates, build a keyword-based feed, or create independent monitors, with email and optional bot delivery.

**The topics shown on the trial site reflect its current collection settings, not a fixed product catalog.** Coverage still depends on the implemented platform adapters, registered sections, and supported monitor types.

## Capabilities

| Capability | What it does |
| --- | --- |
| **Collection and filtering** | Configure keywords, accounts, or announcement sources, with polling intervals, co-occurring terms, popularity thresholds, and source-health visibility. |
| **Browsing and search** | Browse across sections or within one section, filter content, read available previews, and return to the original source. |
| **Traceable events** | Group eligible official announcements using rules, with facts, evidence, revision history, and preserved manual corrections. |
| **Product progress** | Associate public updates with watched products; distinguish official progress, consumer arrivals, quality feedback, and unverified signals. |
| **Personal feeds and monitors** | Filter collected content with watch keywords, or create separate Bilibili UID/keyword monitors with public or private visibility. |
| **AI and delivery** | Optional summaries, translation, and content screening; SMTP-backed morning digests, on-demand email, and delivery records. AstrBot is an optional integration. |
| **Administration** | Manage collection targets, content, events, products, rules, users, and site settings; inspect AI usage and delivery status. |

```text
Site targets → Scheduled collection → Filtering and versioned storage → Events and evidence → Reading and delivery
Custom monitors → Separate collection and storage → Private viewing or public sharing → Optional email
```

> **Collection keywords, watch keywords, and custom monitors are different.** Administrators define site-wide collection targets. Watch keywords filter content already in the site. Custom monitors create separate Bilibili collection tasks; their items do not enter the main content library or event engine.

## Quick start

Use **Docker Compose** for the full stack. For a first deployment:

```bash
git clone https://github.com/tianyin231/AniSignal.git
cd AniSignal
cp .env.example .env
```

Edit `.env`: set different, long random values for `ANISIGNAL_ADMIN_TOKEN` and `MYSQL_ROOT_PASSWORD`. Add `WEB_PORT=127.0.0.1:8090` to start with loopback-only access. Then run:

```bash
docker compose up -d --build
```

Open **[http://127.0.0.1:8090](http://127.0.0.1:8090)**. Sign in at `/admin` with your administrator token, configure collection targets, then enable SMTP, AI, or bot delivery as needed.

See the [deployment guide](docs/deployment.md) for local development, configuration forwarding, and first-run checks. Local mode uses SQLite and an in-process queue; it is not equivalent to the full deployment. Detailed guides are currently in Chinese.

## Boundaries

- **Configured coverage, not the entire web.** Availability and freshness depend on supported sources and collection settings. New platforms need adapters; new sections need registration and configuration.
- **Evidence, not automatic verification.** The event engine is primarily rule-based and does not promise cross-language semantic deduplication. Check AI output and candidate signals against their sources. Product tracking does not predict individual order shipment dates.
- **Delivery requires setup.** Email needs SMTP, AI needs a model service, and bot delivery needs an additional channel. The trial URL is not a claim that every integration is enabled or production-validated.

## Documentation and feedback

[Features](docs/features.md) · [Deployment](docs/deployment.md) · [Backend](server/README.md) · [Frontend](web/README.md)

Include the affected page, reproduction steps, deployment method, and redacted logs in issue reports. Never include administrator tokens, SMTP passwords, model credentials, or personal subscription data.

No license has been specified; the repository currently contains no `LICENSE` file.
