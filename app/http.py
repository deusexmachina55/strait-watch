import httpx

# Some sources (Japan MOD) reject non-browser user agents
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"

client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)


def get(url: str, **kwargs) -> httpx.Response:
    r = client.get(url, **kwargs)
    r.raise_for_status()
    return r
