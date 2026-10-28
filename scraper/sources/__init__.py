"""各來源爬蟲。每個模組提供 crawl() -> list[dict]，欄位見 RAW_FIELDS。"""

RAW_FIELDS = [
    "country", "chain", "item_id", "name_original", "price_tax_incl", "currency",
    "release_date", "regions", "source_category", "source_url", "list_page",
]
