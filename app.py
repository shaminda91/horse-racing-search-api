import os
import re
import time
import sqlite3
import hashlib
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin, urldefrag

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field

APP_NAME = "Horse Racing Search API"
DB_PATH = os.getenv("DB_PATH", "horse_racing_search.db")
API_KEY = os.getenv("API_KEY", "")
USER_AGENT = "HorseRacingSearchBot/0.1 (+respectful research crawler)"
REQUEST_TIMEOUT = 15
MAX_PAGE_CHARS = 100_000

# Start with a small allowlist. Add domains only after checking their terms and robots.txt.
ALLOWED_DOMAINS = {
    "horseracingintfed.com",
    "racingpost.com",
    "equibase.com",
    "racingaustralia.horse",
    "horseracingireland.ie",
    "indiarace.com",
    "goldcircle.co.za",
    "sportinglife.com",
}

app = FastAPI(title=APP_NAME, version="0.1.0",
              description="Search API for horse-racing pages crawled from approved domains.")

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                domain TEXT NOT NULL,
                title TEXT,
                content TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts
            USING fts5(url UNINDEXED, title, content, content='pages', content_rowid='id')
        """)
        con.executescript("""
            CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
              INSERT INTO pages_fts(rowid, url, title, content)
              VALUES (new.id, new.url, new.title, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
              INSERT INTO pages_fts(pages_fts, rowid, url, title, content)
              VALUES ('delete', old.id, old.url, old.title, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
              INSERT INTO pages_fts(pages_fts, rowid, url, title, content)
              VALUES ('delete', old.id, old.url, old.title, old.content);
              INSERT INTO pages_fts(rowid, url, title, content)
              VALUES (new.id, new.url, new.title, new.content);
            END;
        """)

init_db()

def require_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    return urldefrag(url)[0]

def domain_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)

def robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        # Fail closed when robots.txt cannot be checked.
        return False

def fetch_page(url: str):
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT,
                            allow_redirects=True)
    response.raise_for_status()
    final_url = urldefrag(response.url)[0]
    if not domain_allowed(final_url):
        raise ValueError("Redirected outside the approved horse-racing domain allowlist")
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        raise ValueError("Only HTML pages can be indexed")
    html = response.text[:MAX_PAGE_CHARS]
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text_content = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    return final_url, title[:500], text_content[:MAX_PAGE_CHARS], soup

class CrawlRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=10,
                            description="One to ten page URLs from approved horse-racing domains")
    delay_seconds: float = Field(default=2.0, ge=1.0, le=10.0)

class SearchRequest(BaseModel):
    q: str = Field(..., min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=25)
    domain: str | None = None

@app.get("/health")
def health():
    return {"status": "ok", "service": APP_NAME, "time_utc": datetime.now(timezone.utc).isoformat()}

@app.get("/sources")
def sources():
    return {
        "allowed_domains": sorted(ALLOWED_DOMAINS),
        "note": "Only pages you crawl are searchable. Check each site's terms and robots.txt before crawling."
    }

@app.post("/crawl")
def crawl(req: CrawlRequest, x_api_key: str | None = Header(default=None)):
    require_key(x_api_key)
    results = []
    for index, raw_url in enumerate(req.urls):
        url = normalize_url(raw_url)
        if not domain_allowed(url):
            results.append({"url": url, "status": "rejected", "reason": "Domain is not in the allowlist"})
            continue
        if not robots_allowed(url):
            results.append({"url": url, "status": "skipped", "reason": "robots.txt disallows crawling or could not be checked"})
            continue
        try:
            final_url, title, content, _ = fetch_page(url)
            if len(content) < 80:
                results.append({"url": final_url, "status": "skipped", "reason": "Page has too little readable text"})
                continue
            digest = hashlib.sha256((title + "\n" + content).encode("utf-8")).hexdigest()
            with connect() as con:
                existing = con.execute("SELECT id, content_hash FROM pages WHERE url=?", (final_url,)).fetchone()
                now = datetime.now(timezone.utc).isoformat()
                if existing:
                    con.execute("UPDATE pages SET domain=?, title=?, content=?, fetched_at=?, content_hash=? WHERE url=?",
                                (urlparse(final_url).hostname or "", title, content, now, digest, final_url))
                    status = "updated"
                else:
                    con.execute("INSERT INTO pages(url, domain, title, content, fetched_at, content_hash) VALUES(?,?,?,?,?,?)",
                                (final_url, urlparse(final_url).hostname or "", title, content, now, digest))
                    status = "indexed"
            results.append({"url": final_url, "title": title, "status": status, "characters_indexed": len(content)})
        except Exception as e:
            results.append({"url": url, "status": "error", "reason": str(e)[:240]})
        if index < len(req.urls) - 1:
            time.sleep(req.delay_seconds)
    return {"results": results}

@app.get("/search")
def search(q: str, limit: int = 10, domain: str | None = None,
           x_api_key: str | None = Header(default=None)):
    require_key(x_api_key)
    if len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="Query must contain at least 2 characters")
    limit = max(1, min(limit, 25))
    # Quote each token to avoid malformed FTS syntax from user input.
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._'-]*", q)
    if not tokens:
        return {"query": q, "count": 0, "results": []}
    match_query = " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens[:12])
    sql = """
        SELECT p.url, p.domain, p.title, p.content, p.fetched_at,
               bm25(pages_fts) AS score
        FROM pages_fts
        JOIN pages p ON p.id = pages_fts.rowid
        WHERE pages_fts MATCH ?
    """
    params = [match_query]
    if domain:
        clean_domain = domain.lower().strip()
        if not any(clean_domain == d or clean_domain.endswith("." + d) for d in ALLOWED_DOMAINS):
            raise HTTPException(status_code=400, detail="Domain is not in the allowlist")
        sql += " AND p.domain LIKE ?"
        params.append("%" + clean_domain)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    try:
        with connect() as con:
            rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    results = []
    for row in rows:
        content = row["content"] or ""
        pos = content.lower().find(q.lower())
        if pos < 0:
            pos = 0
        start = max(0, pos - 160)
        snippet = content[start:start + 360]
        results.append({
            "title": row["title"],
            "url": row["url"],
            "domain": row["domain"],
            "snippet": snippet,
            "fetched_at": row["fetched_at"]
        })
    return {"query": q, "count": len(results), "results": results,
            "note": "Results are limited to pages already crawled and indexed by this service."}

@app.get("/stats")
def stats(x_api_key: str | None = Header(default=None)):
    require_key(x_api_key)
    with connect() as con:
        count = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        domains = con.execute("SELECT domain, COUNT(*) AS pages FROM pages GROUP BY domain ORDER BY pages DESC").fetchall()
    return {"indexed_pages": count, "pages_by_domain": [dict(r) for r in domains]}

@app.get("/")
def root():
    return {
        "service": APP_NAME,
        "docs": "/docs",
        "endpoints": ["/health", "/sources", "/crawl (POST)", "/search?q=horse+name", "/stats"],
        "warning": "This is a focused crawler/search index, not a full-web search engine."
    }
