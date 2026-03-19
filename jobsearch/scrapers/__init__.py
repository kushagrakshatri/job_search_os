"""Multi-source scraper entrypoints."""

from jobsearch.scrapers.ats_scraper import scrape_ats
from jobsearch.scrapers.llm_parser import parse_urls
from jobsearch.scrapers.serp_scraper import scrape_serp

__all__ = ["parse_urls", "scrape_ats", "scrape_serp"]
