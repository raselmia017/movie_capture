# Movie Scraper & Link Capture Pipeline

Combined automation to scrape movie catalogs, capture video stream links, extract real upload dates, and maintain a sorted, continuously updated database.

## Features
- **Homepage First:** Top 200 trending/recently updated movies from homepage stay at the very top.
- **Full Catalog:** Iterates all 407 pages with multi-threaded requests.
- **Detail Link & Date Capture:** Extracts direct .mp4/.mkv video links and Uploaded: timestamps from detail pages.
- **Incremental Caching:** Reuses previously captured video links and dates so it only fetches newly added movies.
- **Dual Output:** 
  - movies.json / output_movies.json: Final list with only title, details (always empty), img, link, date.
  - movies_cache.json: Full internal data (id, details, video_url...) used for incremental caching.
- **GitHub Actions:** Automated run every 6 hours via .github/workflows/scrape_and_capture.yml.

## Local Usage
```bash
pip install -r requirements.txt
python scraper.py
```

