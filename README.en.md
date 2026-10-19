<div align="center">

<img src="web/public/icon.png" width="88" height="88" alt="AniSignal logo">

# AniSignal

**Let what matters find you.**

A self-hosted signal desk. Gather public updates, trace their sources, and receive what you follow.

<img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-111111">
<img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-111111">
<img alt="Nuxt 4" src="https://img.shields.io/badge/Nuxt-4-111111">
<img alt="Docker Compose" src="https://img.shields.io/badge/Docker-Compose-111111">
<img alt="Self-hosted" src="https://img.shields.io/badge/Self--hosted-111111">

[Live demo](http://anisignal.nanamiyako.cn/) · [Product tour](http://anisignal.nanamiyako.cn/intro) · [Quick start](#quick-start) · [Deployment](docs/deployment.md) · [Issues](https://github.com/tianyin231/AniSignal/issues)

[简体中文](README.md) · **English**

</div>

---

Following a release, a product line, or a shipping update often means checking announcements, account posts, and community discussions. AniSignal collects the public sources you configure, reduces repeated reading through filtering and event grouping, and preserves original links, evidence, and revision history.

Read on the website, build a keyword feed, or create an independent monitor. Email and optional AstrBot channels deliver updates. Game and AI sections are available today; the demo site's topics follow its collection settings. Coverage depends on implemented adapters and registered sections.

## Preview

<a href="http://anisignal.nanamiyako.cn/intro"><img src="docs/assets/intro-preview.png" alt="AniSignal product tour showing keyword collection, source cards, and traceable events" width="1280"></a>

Screenshot of the product tour, with clearly labeled sample data. [Try the interactive tour](http://anisignal.nanamiyako.cn/intro) · [Browse the demo](http://anisignal.nanamiyako.cn/)

## Capabilities

| Capability | What you get |
| --- | --- |
| **Collection** | Keyword, account, and announcement targets with polling intervals, co-occurring terms, popularity thresholds, and source health. |
| **Reading** | An aggregated home and individual sections, with platform, category, and keyword filters, previews, and original links. |
| **Traceable events** | Eligible official announcements grouped with key facts, source evidence, revision history, and preserved manual corrections. |
| **AI news** | A dedicated feed for model, product, and industry updates, with report/rumor labels, topic filters, and source links. |
| **Product progress** | Official updates, consumer arrivals, and quality feedback, with unverified candidates distinguished from verified signals. |
| **Personal watches** | Keyword feeds over collected content, or independent Bilibili UID/keyword monitors with public or private visibility. |
| **Delivery** | Optional summaries, translation, and screening; morning digests and on-demand email; AstrBot session notifications and queries. |
| **Administration** | Manage targets, content, events, products, rules, and users; inspect collection runs, model usage, and delivery status. |

```text
Site targets → Scheduled collection → Filtering and versioned storage → Events and evidence → Reading and delivery
Custom monitors → Separate collection and storage → Private viewing or public sharing → Optional email
```

**Collection keywords** define what the site fetches. **Watch keywords** filter content already stored. **Custom monitors** create independent Bilibili tasks whose results stay outside the main library and event engine. See the [feature guide](docs/features.md) for details.

## Quick start

You need Git, Docker, and Docker Compose. The full stack includes MySQL, Redis, Web/API, and a collection worker.

```bash
git clone https://github.com/tianyin231/AniSignal.git
cd AniSignal
cp .env.example .env
```

In `.env`, set separate long random values for `ANISIGNAL_ADMIN_TOKEN` and `MYSQL_ROOT_PASSWORD`; hexadecimal characters are recommended for the database password. Add `WEB_PORT=127.0.0.1:8090` for loopback access, then start:

```bash
docker compose up -d --build
docker compose ps
```

Open **[http://127.0.0.1:8090](http://127.0.0.1:8090)** and sign in at `/admin` with your administrator token. Configure a collection target, trigger it manually, and check source status and content. Enable SMTP, a model service, and bot channels as needed.

Local development uses SQLite and an in-process queue. The steps are in the [deployment guide](docs/deployment.md), currently written in Chinese. Local mode is not the full deployment.

## Sources and runtime

| Source | Implemented support |
| --- | --- |
| Bilibili | Account updates and keyword video searches through the `bili` CLI. |
| X | Low-frequency collection from publicly readable accounts; login restrictions and access failures appear in source health. |
| YouTube | Channel uploads through Data API v3 when an API key is configured, with RSS fallback when unavailable. |
| Kuro official sites and Miyoushe | Dedicated collectors and parsers for those sites. |

The backend uses **FastAPI + SQLAlchemy + Alembic + arq**; the frontend uses **Nuxt 4 + Tailwind CSS 4**. Full deployments use MySQL and Redis. Local development uses SQLite and an in-process queue. New platforms need adapters; new sections need registration and configuration.

Check model output and product candidates against their original sources. Product tracking does not predict a personal order's ship date. Email, model features, and bots each require the corresponding services; enabled integrations depend on the actual deployment.

## Documentation

[Features](docs/features.md) · [Deployment](docs/deployment.md) · [Backend](server/README.md) · [Frontend](web/README.md)

Issue reports should include the page, the steps, the deployment, and redacted logs. Keep administrator tokens, mailbox passwords, model credentials, and personal subscription data off the issue.

No license has been specified.
