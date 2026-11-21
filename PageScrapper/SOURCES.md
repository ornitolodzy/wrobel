# Sources - how we pick them, update them and drop them

The full catalog is [sources.yaml](sources.yaml). It's in the repo on purpose: every change to the source list
goes through git, so the history of what we used and when is auditable.

Live view:
- `GET /sources` - the catalog + how many documents each source has + if it's configured (key / file present)
- `GET /stats` - corpus summary: number of documents, number of domains, split by source type, date range
- `GET /scrap/runs` - history of every scraping run (what was added / updated / skipped / failed)

## Source types

| type | what it is | how the system may use it |
|---|---|---|
| `official` | government institutions and primary sources (gov.pl, police, CERT, Dziennik Ustaw, MSZ) | evidence |
| `fact_check` | fact-checking organisations (IFCN / EFCSN signatories, EUvsDisinfo) | evidence, with the publisher shown |
| `disinfo_db` | catalogs of known disinformation cases (EUvsDisinfo database, incident file from the organizer) | **context only** - a match means "a similar claim was already seen", never a verdict by itself |

## Current sources

| id | domain | type | method | license / terms |
|---|---|---|---|---|
| rcb-komunikaty | www.gov.pl | official | gov.pl list | official materials (art. 4 copyright act) |
| mon-aktualnosci | www.gov.pl | official | gov.pl list | official materials |
| odyseusz-msz | odyseusz.gov.pl | official | headless browser | official materials |
| policja-aktualnosci | policja.pl | official | HTML list | official materials |
| policja-mazowiecka | mazowiecka.policja.gov.pl | official | HTML list | official materials |
| cert-polska | cert.pl | official | RSS | CERT Polska / NASK content, cited with link |
| isap-dziennik-ustaw | isap.sejm.gov.pl (via api.sejm.gov.pl) | official | official ELI API | legal acts are not copyrighted (art. 4 pt 1) |
| demagog-fake-news | demagog.org.pl | fact_check | RSS | IFCN/EFCSN, robots.txt `ai-train=no` - search & citing only |
| konkret24 | konkret24.tvn24.pl | fact_check | RSS | publisher's copyright, short quotes with link |
| fakenews-pl | fakenews.pl | fact_check | RSS (without blog / training posts) | publisher's copyright, short quotes with link |
| euvsdisinfo-artykuly | euvsdisinfo.eu | fact_check | RSS | EUvsDisinfo content, cited with link |
| google-factcheck | many fact-checker domains | fact_check | Google Fact Check Tools API | needs `GOOGLE_FACTCHECK_API_KEY` |
| euvsdisinfo-baza | euvsdisinfo.eu | disinfo_db | official MCP server | needs `EUVSDISINFO_TOKEN` |
| baza-incydentow | (file) | disinfo_db | JSON / CSV import | license given by the organizer with the file |

Without any keys this gives 10 domains and ~280 documents per full run (limits are `max_items` in the yaml).

## Rules for adding a source

A source goes in only if **all** of these are true:

1. **Who publishes it is clear** - a public institution, a primary source (e.g. legal acts) or a recognised
   fact-checker (IFCN / EFCSN signatory or a similar public body like EUvsDisinfo).
2. **Free and public** - no login, no paywall, no bot protection we would have to get around.
3. **robots.txt allows it** - the scraper checks it automatically before every run and skips disallowed pages.
4. **Every document has a publication date** - we need it to warn about outdated material. `GET /stats` shows
   `documents_without_date`, it should stay at 0.
5. **Stable format** - we prefer an official API or RSS over parsing HTML, and HTML over a headless browser.
6. **Relevant** - Polish public-safety / public-information context, or disinformation targeting Poland / the EU.

Propaganda or sanctioned outlets are **never** added as evidence. They can only appear as examples inside
`disinfo_db` cases (e.g. "this claim was published by X"), and the source status alone never decides a verdict.

## Rules for removing a source

We remove (or set `active: false`) a source when:
- robots.txt starts disallowing us, or the site adds bot protection
- it stops giving dates, or the feed / list stays empty
- it starts mixing in content we can't filter out (ads, unrelated posts)
- the publisher stops being trustworthy (e.g. loses IFCN status)

Every removal is a commit to `sources.yaml` with the reason in the commit message.

## Sources we checked and did NOT add

| site | reason |
|---|---|
| utk.gov.pl | robots.txt disallows bots |
| ulc.gov.pl | bot protection page instead of content |
| sprawdzam.afp.com | 403 for automated requests |
| umgdy.gov.pl | RSS feed is empty |
| fakehunter.pap.pl, pap.pl | no feed, homepage blocks bots |
| moje.cert.pl | JS app, we use the cert.pl RSS feed with the same warnings instead |
| meteo.imgw.pl, drogi.gddkia.gov.pl maps | live data in JS apps, not documents (could be added later through their APIs) |
| Odyseusz internal API | undocumented internal API - we read the public page in a browser like a normal user instead |

## How documents are kept up to date

- **Automatic refresh** every `SCRAPE_INTERVAL_MINUTES` (set in `.env`, e.g. `360` = every 6 hours).
  You can also start it by hand with `POST /scrap/all` (through the gateway: admin only).
- **New articles** are added. **Known articles** are not downloaded again (saves the sites from extra traffic).
  Sources that change in place (Odyseusz country profiles, `refresh_known: true`) are re-read every time.
- **Changes are detected** with a hash of title + date + text - a changed page becomes `updated`, not a duplicate.
- **Every document stores** `published_at` (from the source) and `retrieved_at` (when we downloaded it),
  so the LLM can prefer fresh material and warn about old one.

## Adding a new source (how-to)

- any gov.pl news list: `adapter: govpl_list` + the list URL
- any RSS / Atom feed: `adapter: rss` (+ `exclude_categories`, `drop_lines_starting_with`, `language` if needed)
- any simple HTML news list: `adapter: html_list` + `link_pattern` (regex for article links) + `date_selector` (CSS)
- a JSON / CSV file: put it in `imports/`, `adapter: file_import` + field mapping

Then check the new source's run in `GET /scrap/runs/{id}` and its documents in `GET /documents?source_id=...`.
