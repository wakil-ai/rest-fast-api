import asyncio
from typing import List
import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter
from app.core.config import settings
router = APIRouter(prefix="/rss", tags=["RSS Reader"])

rss_links: List[str] = []

async def update_rss_links():
    """Fetches and updates the list of RSS links."""
    global rss_links
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(settings.RSS_URL)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "xml")
            links = []
            for item in soup.find_all("item"):
                link_tag = item.find("link")
                if link_tag and link_tag.text:
                    relative_url = link_tag.text
                    if relative_url.startswith("/"):
                        absolute_url = f"https://lex.uz{relative_url}"
                        links.append(absolute_url)
            
            rss_links = links
            print(f"[INFO] Updated RSS links. Found {len(rss_links)} links.")

    except Exception as e:
        print(f"[ERROR] Failed to update RSS links: {e}")

async def run_rss_updater():
    """Runs the RSS updater in a loop."""
    while True:
        await update_rss_links()
        await asyncio.sleep(settings.UPDATE_INTERVAL)

@router.get("/links", response_model=List[str])
async def get_rss_links():
    """Returns the latest list of URLs from the RSS feed."""
    return rss_links
