import urllib.request
import urllib.parse
import json
import re
import os
import sys
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

BASE_URL = "http://103.203.93.2"
MOVIES_URL = f"{BASE_URL}/movies"
OUTPUT_FILE = "movies.json"
COMPAT_FILE = "output_movies.json"
SUMMARY_FILE = "movies_summary.json"
CACHE_FILE = "movies_cache.json"  # full data (id, details...) used only for incremental caching
FULL_REFRESH = os.environ.get("FULL_REFRESH", "").strip().lower() in ("1", "true", "yes")
OUTPUT_FIELDS = ["title", "details", "img", "link", "date"]
EMPTY_FIELDS = {"details"}  # kept in output but always empty

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def fetch_url(url, timeout=12, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception:
            if attempt == retries:
                return None
            time.sleep(1)
    return None

def extract_movie_id(url):
    m = re.search(r'/video-details/(\d+)', url or '')
    return m.group(1) if m else ""

def parse_cards_from_html(html):
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".single-item")
    movies = []
    
    for item in items:
        a_detail = item.find("a", href=re.compile(r"/video-details/\d+"))
        if not a_detail:
            continue
        detail_href = a_detail.get("href", "").strip()
        movie_id = extract_movie_id(detail_href)
        if not movie_id:
            continue
            
        img_el = item.find("img")
        img_src = ""
        if img_el:
            img_src = (img_el.get("src") or img_el.get("data-src") or "").strip()
            
        title_el = item.find("h3", class_="item-title")
        title = ""
        if title_el:
            a_t = title_el.find("a")
            if a_t:
                title = a_t.get("title") or a_t.get_text(strip=True)
            else:
                title = title_el.get_text(strip=True)
                
        desig = item.find(class_="item-designation")
        year = ""
        if desig:
            m_year = re.search(r"\((\d{4})\)", desig.get_text())
            if m_year:
                year = m_year.group(1)
                
        movies.append({
            "id": movie_id,
            "title": title,
            "year": year,
            "img": img_src,
            "thumbnail": img_src,
            "details": detail_href if detail_href.startswith("http") else f"{BASE_URL}{detail_href}",
            "download_link": f"{BASE_URL}/download-file/{movie_id}",
            "link": "",
            "video_url": "",
            "date": "",
            "date_source": "detail"
        })
    return movies

def fetch_detail_info(movie):
    movie_id = movie.get("id") or extract_movie_id(movie.get("details", ""))
    if not movie_id:
        return movie
        
    detail_url = movie.get("details") or f"{BASE_URL}/video-details/{movie_id}"
    html = fetch_url(detail_url, timeout=10, retries=2)
    if not html:
        return movie
        
    soup = BeautifulSoup(html, "html.parser")
    
    # 1. Video link
    src_tag = soup.select_one("video source") or soup.select_one("video")
    video_url = ""
    if src_tag and src_tag.get("src"):
        video_url = src_tag["src"].strip()
        
    # 2. Upload date
    upload_date = ""
    m_date = re.search(r"Uploaded:\s*([0-9A-Za-z\-:\s]+)", html)
    if m_date:
        upload_date = m_date.group(1).strip().rstrip(".")
        
    if video_url:
        movie["video_url"] = video_url
        movie["link"] = f"{video_url}|Referer={BASE_URL}/"
    if upload_date:
        movie["date"] = upload_date
        movie["date_source"] = "detail"
        
    return movie

def parse_upload_date(value):
    """Parse dates like '24-Sep-2026 04:20:02pm'. Returns None if unparseable."""
    v = (value or "").strip()
    if not v:
        return None
    for fmt in ("%d-%b-%Y %I:%M:%S%p", "%d-%b-%Y %I:%M%p", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(v.upper().replace(" PM", "PM").replace(" AM", "AM"), fmt)
        except ValueError:
            continue
    return None

def get_max_catalog_pages():
    html = fetch_url(MOVIES_URL)
    if not html:
        return 407
    soup = BeautifulSoup(html, "html.parser")
    pagination = soup.select(".pagination a")
    page_nums = []
    for a in pagination:
        m = re.search(r'page=(\d+)', a.get("href", ""))
        if m:
            page_nums.append(int(m.group(1)))
    return max(page_nums) if page_nums else 407

def main():
    start_time = time.time()
    print("=== Starting Unified Movie Scraper & Link Capture ===")
    
    # 1. Load existing database/cache if exists
    existing_cache = {}
    cache_source = CACHE_FILE if os.path.exists(CACHE_FILE) else OUTPUT_FILE
    if os.path.exists(cache_source):
        try:
            with open(cache_source, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                if isinstance(existing_data, list):
                    for item in existing_data:
                        mid = item.get("id") or extract_movie_id(item.get("details", ""))
                        if mid:
                            existing_cache[mid] = item
                elif isinstance(existing_data, dict) and "movies" in existing_data:
                    for item in existing_data["movies"]:
                        mid = item.get("id") or extract_movie_id(item.get("details", ""))
                        if mid:
                            existing_cache[mid] = item
            print(f"Loaded {len(existing_cache)} existing movies from {cache_source}")
        except Exception as e:
            print(f"Error loading {cache_source}: {e}")
            
    # 2. Scrape Homepage (Top 200 items in exact homepage order)
    print("Scraping homepage...")
    hp_html = fetch_url(BASE_URL)
    hp_movies = parse_cards_from_html(hp_html)
    print(f"Homepage movies collected: {len(hp_movies)}")
    
    # 3. Detect total catalog pages
    max_pages = get_max_catalog_pages()
    print(f"Detected max catalog pages: {max_pages}")
    
    # Limit pages if specified in env (useful for testing/workflows)
    limit_pages_env = os.environ.get("MAX_CATALOG_PAGES")
    if limit_pages_env:
        max_pages = min(max_pages, int(limit_pages_env))
        print(f"Limiting to first {max_pages} pages based on MAX_CATALOG_PAGES")
        
    # 4. Scrape Catalog Pages in parallel
    print(f"Scraping {max_pages} catalog pages...")
    catalog_movies_by_page = {}
    
    def scrape_page(page_no):
        url = f"{MOVIES_URL}?page={page_no}"
        html = fetch_url(url)
        return page_no, parse_cards_from_html(html)
        
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(scrape_page, p): p for p in range(1, max_pages + 1)}
        for fut in as_completed(futures):
            p, items = fut.result()
            catalog_movies_by_page[p] = items
            
    # Assemble catalog items in exact page order
    catalog_movies = []
    for p in range(1, max_pages + 1):
        catalog_movies.extend(catalog_movies_by_page.get(p, []))
    print(f"Total raw catalog movies from {max_pages} pages: {len(catalog_movies)}")
    
    # 5. Order: Homepage items first, then remaining catalog items
    ordered_movies = []
    seen_ids = set()
    
    for m in hp_movies:
        mid = m["id"]
        if mid not in seen_ids:
            seen_ids.add(mid)
            ordered_movies.append(m)
            
    for m in catalog_movies:
        mid = m["id"]
        if mid not in seen_ids:
            seen_ids.add(mid)
            ordered_movies.append(m)
            
    print(f"Combined unique movies: {len(ordered_movies)}")
    
    # 6. Merge with existing cache
    to_fetch_detail = []
    for m in ordered_movies:
        mid = m["id"]
        if mid in existing_cache:
            cached = existing_cache[mid]
            # Copy over cached video link and date if present
            if cached.get("link"):
                m["link"] = cached["link"]
            if cached.get("video_url"):
                m["video_url"] = cached["video_url"]
            if cached.get("date"):
                m["date"] = cached["date"]
            if cached.get("date_source"):
                m["date_source"] = cached["date_source"]
                
        # If still missing video link or date, queue for detail page fetch
        # Full refresh: re-capture every movie (cached values stay if a fetch fails)
        if FULL_REFRESH or not m.get("link") or not m.get("date"):
            to_fetch_detail.append(m)
            
    if FULL_REFRESH:
        print(f"FULL REFRESH mode: re-capturing links/dates for all {len(to_fetch_detail)} movies")
    else:
        print(f"Movies needing detail page fetch (new or missing link/date): {len(to_fetch_detail)}")
    
    # 7. Fetch detail pages in parallel
    if to_fetch_detail:
        print(f"Fetching {len(to_fetch_detail)} detail pages with multi-threading...")
        with ThreadPoolExecutor(max_workers=15) as executor:
            list(executor.map(fetch_detail_info, to_fetch_detail))
            
    # 8. Stats & Counts
    total_count = len(ordered_movies)
    with_links = sum(1 for m in ordered_movies if m.get("link"))
    with_dates = sum(1 for m in ordered_movies if m.get("date"))
    duration = time.time() - start_time
    
    print(f"Finished in {duration:.1f}s. Total: {total_count}, Links: {with_links}, Dates: {with_dates}")
    
    # 9. Save JSON outputs
    # Full data cache (keeps id/details so next run only fetches new movies)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(ordered_movies, f, ensure_ascii=False, indent=2)
    print(f"Saved {CACHE_FILE}")

    # Sort newest upload first; movies without a valid date go to the bottom
    ordered_movies.sort(key=lambda m: parse_upload_date(m.get("date")) or datetime.min, reverse=True)

    # Final output: only the fields the app needs
    final_movies = [{k: ("" if k in EMPTY_FIELDS else m.get(k, "")) for k in OUTPUT_FIELDS} for m in ordered_movies]

    # Form 1: movies.json
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_movies, f, ensure_ascii=False, indent=2)
    print(f"Saved {OUTPUT_FILE}")

    # Form 2: output_movies.json (Identical copy for legacy URL compatibility)
    with open(COMPAT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_movies, f, ensure_ascii=False, indent=2)
    print(f"Saved {COMPAT_FILE}")

    # Form 3: movies_summary.json (Metadata)
    summary = {
        "total_movies": total_count,
        "with_video_links": with_links,
        "with_dates": with_dates,
        "homepage_first_count": len(hp_movies),
        "last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "duration_seconds": round(duration, 2)
    }
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved {SUMMARY_FILE}")

if __name__ == "__main__":
    main()

