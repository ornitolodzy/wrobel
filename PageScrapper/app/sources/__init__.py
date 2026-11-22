from app.sources import euvsdisinfo_mcp, file_import, live_only, google_factcheck, govpl, html_list, odyseusz, rss, sejm_eli

ADAPTERS = {
    "govpl_list": govpl.collect,
    "html_list": html_list.collect,
    "odyseusz": odyseusz.collect,
    "rss": rss.collect,
    "sejm_eli": sejm_eli.collect,
    "google_factcheck": google_factcheck.collect,
    "euvsdisinfo_mcp": euvsdisinfo_mcp.collect,
    "file_import": file_import.collect,
    "live_only": live_only.collect,
}
