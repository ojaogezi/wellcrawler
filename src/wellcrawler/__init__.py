"""wellcrawler — surface the wells hiding in temp(14).

An open-source implementation of the idea described in NthDS's post
(https://lnkd.in/p/ez_4ftYN): crawl a messy folder, standardize records by
well, draw cross-sections automatically, and keep every data point one
click from its source.
"""
from .crawl import crawl as run_crawl

__version__ = "0.1.0"
__all__ = ["run_crawl"]
