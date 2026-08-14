import http.cookiejar
import random
import re
import time
import urllib.parse
import urllib.request
from typing import Generator, List, Tuple

from bs4 import BeautifulSoup

from src.models.signalhire import SignalHireEmployee
from src.utils.logging import logger

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]


def slugify_company(input_str: str) -> str:
    """Converts a company name, domain, or SignalHire URL into a company slug."""
    input_clean = input_str.strip()
    
    # Handle full URL
    if "signalhire.com" in input_clean:
        parsed = urllib.parse.urlparse(input_clean)
        path = parsed.path.strip("/")
        parts = path.split("/")
        if "companies" in parts:
            idx = parts.index("companies")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    
    # Handle domain (e.g. google.com -> google)
    if "." in input_clean and " " not in input_clean:
        input_clean = input_clean.split(".")[0]
        
    # Slugify name
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", input_clean.lower()).strip("-")
    return slug or "company"


def build_target_page_url(company_input: str, page: int) -> str:
    """
    Builds or updates a SignalHire target page URL, preserving any query parameters
    (such as location filters like ?locations[]=in) and appending the page number.
    """
    input_str = company_input.strip()
    if "signalhire.com" in input_str:
        parsed = urllib.parse.urlparse(input_str)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        qs["page"] = [str(page)]
        new_query = urllib.parse.urlencode(qs, doseq=True)
        path = parsed.path
        if not path.rstrip("/").endswith("/employees"):
            path = path.rstrip("/") + "/employees"
        return urllib.parse.urlunparse((
            parsed.scheme or "https",
            parsed.netloc or "www.signalhire.com",
            path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
    else:
        slug = slugify_company(input_str)
        return f"https://www.signalhire.com/companies/{slug}/employees?page={page}"


def _fetch_html_with_session(opener: urllib.request.OpenerDirector, url: str, referer: str = None, retries: int = 5) -> Tuple[str, int]:
    """
    Fetches HTML from URL using a persistent session opener, automatically retrying 429 rate limits quietly.
    Returns tuple of (html_content, status_code).
    """
    if not referer:
        referer = "https://www.signalhire.com/"

    for attempt in range(retries + 1):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = opener.open(req, timeout=12.0)
            return (resp.read().decode("utf-8", errors="ignore"), resp.status)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                logger.info(f"HTTP 404 reached at '{url}'")
                print(f"[SignalHire Terminal Log] HTTP 404 end of pages at '{url}'", flush=True)
                return ("", 404)
            if err.code == 429 and attempt < retries:
                backoff_time = 4.0 + (attempt * 3.0) + random.uniform(0.2, 1.0)
                logger.debug(f"SignalHire 429 backoff at '{url}'. Sleeping {backoff_time:.1f}s (attempt {attempt+1}/{retries})...")
                time.sleep(backoff_time)
                continue
            logger.warning(f"HTTP error {err.code} fetching '{url}': {err}")
            return ("", err.code)
        except Exception as exc:
            logger.warning(f"Error fetching '{url}': {exc}")
            return ("", 500)
    return ("", 429)


def crawl_signalhire_employees_generator(company_input: str) -> Generator[List[SignalHireEmployee], None, None]:
    """
    Yields lists of extracted SignalHireEmployee records page-by-page in real-time.
    Prints live log progress directly to the terminal stdout.
    """
    slug = slugify_company(company_input)
    base_company_url = f"https://www.signalhire.com/companies/{slug}"
    
    print(f"[SignalHire Terminal Log] Starting crawl for '{company_input}' (slug: {slug})", flush=True)
    logger.info(f"[SignalHire] Starting crawl for '{company_input}' (slug: {slug})")
    
    # Initialize persistent cookie jar session
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    
    # Warm up session with initial company visit
    try:
        init_req = urllib.request.Request(
            base_company_url if "signalhire.com" not in company_input else company_input,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        opener.open(init_req, timeout=10.0)
    except Exception:
        pass
    
    seen_names = set()
    company_display = slug.replace("-", " ").title()
    
    page = 1
    empty_consecutive_pages = 0

    while True:
        target_url = build_target_page_url(company_input, page)
        print(f"[SignalHire Terminal Log] Fetching Page {page}: {target_url}", flush=True)
        logger.info(f"[SignalHire] Fetching Page {page}: {target_url}")
        
        html, status_code = _fetch_html_with_session(opener, target_url, referer=base_company_url)
        
        # Stop immediately if HTTP 404 (Page Not Found)
        if status_code == 404:
            print(f"[SignalHire Terminal Log] HTTP 404 reached at Page {page}. Ending crawl.", flush=True)
            logger.info(f"SignalHire crawl reached 404 end of pages at page {page}.")
            break

        # If HTTP 429 persists after retries, pause 6s and retry quietly
        if status_code == 429:
            print(f"[SignalHire Terminal Log] Persistent 429 on Page {page}. Waiting 6s...", flush=True)
            time.sleep(6.0)
            html, status_code = _fetch_html_with_session(opener, target_url, referer=base_company_url, retries=2)
            if status_code == 429 or not html:
                print(f"[SignalHire Terminal Log] Skipping Page {page} due to persistent 429.", flush=True)
                logger.warning(f"Skipping page {page} due to persistent 429 rate limit.")
                page += 1
                time.sleep(4.0)
                continue

        if not html:
            empty_consecutive_pages += 1
            if empty_consecutive_pages >= 3:
                print(f"[SignalHire Terminal Log] 3 consecutive empty responses. Ending crawl at page {page}.", flush=True)
                break
            page += 1
            time.sleep(2.0)
            continue

        soup = BeautifulSoup(html, "html.parser")
        
        # Extract official company title if available
        title_tag = soup.title.string if soup.title else ""
        if "Information | SignalHire" in title_tag:
            company_display = title_tag.replace("Information | SignalHire Company Profile", "").strip() or company_display

        profile_links = soup.find_all("a", href=lambda h: h and "/profiles/" in h)
        if not profile_links:
            empty_consecutive_pages += 1
            if empty_consecutive_pages >= 3:
                print(f"[SignalHire Terminal Log] No profile links found on page {page}. Ending crawl.", flush=True)
                break
            page += 1
            time.sleep(2.0)
            continue
            
        empty_consecutive_pages = 0
        page_batch: List[SignalHireEmployee] = []
        
        for a in profile_links:
            name = a.text.strip()
            if not name or name.lower() in ("email sequences", "pricing", "sign in", "sign up", "companies", "profiles"):
                continue
                
            name_key = name.lower()
            if name_key in seen_names:
                continue
                
            title = "Employee"
            parent = a.find_parent(["div", "li", "tr", "p", "article"])
            if parent:
                text = parent.get_text(separator=" | ", strip=True)
                parts = [p.strip() for p in text.split("|") if p.strip()]
                if len(parts) >= 2 and parts[0] == name:
                    title = parts[1]
                elif len(parts) >= 2:
                    for p_part in parts[1:]:
                        if p_part.lower() != name.lower() and len(p_part) > 2:
                            title = p_part
                            break

            seen_names.add(name_key)
            page_batch.append(SignalHireEmployee(
                name=name,
                title=title,
                company=company_display
            ))
            
        if not page_batch and page > 1:
            print(f"[SignalHire Terminal Log] Page {page} produced 0 new unique records. Ending crawl.", flush=True)
            break

        if page_batch:
            print(f"[SignalHire Terminal Log] Page {page} complete: Extracted {len(page_batch)} new employees (Total extracted so far: {len(seen_names)})", flush=True)
            logger.info(f"[SignalHire] Page {page}: Extracted {len(page_batch)} new employees (Total: {len(seen_names)})")
            yield page_batch
            
        page += 1
        # Polite 4-second delay with random jitter to prevent rate limit triggers
        time.sleep(4.0 + random.uniform(0.3, 1.2))

    print(f"[SignalHire Terminal Log] Crawl finished. Total unique employees extracted: {len(seen_names)}", flush=True)
    logger.info(f"[SignalHire] Crawl finished. Total unique employees extracted: {len(seen_names)}")


def crawl_signalhire_employees(company_input: str) -> List[SignalHireEmployee]:
    """Synchronous helper returning all extracted records."""
    all_results = []
    for batch in crawl_signalhire_employees_generator(company_input):
        all_results.extend(batch)
    return all_results
