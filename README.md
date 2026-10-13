# Horse Racing Search API (starter)

A small FastAPI service that crawls and indexes horse-racing pages from an allowlist, then searches the pages it has already indexed. It does **not** use Google, Bing, Tavily, or another search API.

## Important limitations
- This does not automatically search the entire web. Search results only include pages submitted to `/crawl` and successfully indexed.
- Many racing websites use dynamic pages or restrict automated access. This starter may not extract those pages.
- Check the website's terms and `robots.txt` before crawling. Do not bypass login, paywalls, CAPTCHAs, or anti-bot protections.
- Keep crawl rate low. The API crawls only the URLs you explicitly submit; it does not recursively spider a site.
- Render's free service can sleep and its local filesystem may be reset. For a persistent index, use a VPS with persistent disk or an external database and backups.
- Do not expose `/crawl` publicly without setting a strong `API_KEY`.

## Included starter domains
- horseracingintfed.com
- racingpost.com
- equibase.com
- racingaustralia.horse
- horseracingireland.ie
- indiarace.com
- goldcircle.co.za
- sportinglife.com

The allowlist is deliberately small. Confirm permissions and access rules before using each domain. Add or remove domains in `app.py`.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export API_KEY='replace-with-a-long-random-secret'
uvicorn app:app --host 0.0.0.0 --port 8000
```
Open `http://127.0.0.1:8000/docs`.

## Use the API
### Health
`GET /health`

### Sources
`GET /sources`

### Crawl approved URLs
`POST /crawl` with header `X-API-Key: YOUR_API_KEY`
```json
{
  "urls": [
    "https://www.horseracingintfed.com/Default.asp?area=0&section=Racing"
  ],
  "delay_seconds": 2
}
```
Use a URL only after checking its site's terms and robots rules.

### Search indexed pages
`GET /search?q=horse%20name&limit=10` with header `X-API-Key: YOUR_API_KEY`

### Stats
`GET /stats` with header `X-API-Key: YOUR_API_KEY`

## Deploy
The included `render.yaml` is a quick demo deployment. Free hosting may sleep and local index data may disappear after restarts/redeploys. For continuous service and persistent data, deploy on a paid VPS and set up backups and `systemd`.
