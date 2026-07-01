from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup


SITEMAP_URL = "https://nhaxinh.com/sitemap_index.xml"
OUTPUT_FILE = Path(__file__).resolve().parents[1] / "data" / "product_urls.csv"


def main():
    xml = requests.get(SITEMAP_URL, timeout=30).text
    soup = BeautifulSoup(xml, "xml")

    links = []
    for loc in soup.find_all("loc"):
        links.append(loc.text)

    print(len(links))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"url": links}).to_csv(OUTPUT_FILE, index=False)


if __name__ == "__main__":
    main()

#chạy bằng lệnh: .\.venv\Scripts\python.exe crawler\crawl_sitemap.py
