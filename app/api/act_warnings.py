import asyncio
import httpx
from fastapi import APIRouter
from bs4 import BeautifulSoup
from typing import List

from app.models.act_warnings_models import ActWarningURLRequest, ActWarningResponse
from app.core.config import settings
router = APIRouter(prefix="/act-warnings", tags=["Act Warnings"])

async def get_act_warning(client: httpx.AsyncClient, url: str) -> str | None:
    """Return the text of the first element with class 'act_warning', or None if not found."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        resp = await client.get(url, timeout=settings.TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        tag = soup.find(class_="act_warning")
        if tag:
            return tag.get_text(strip=True)
        return None

    except httpx.RequestError as e:
        print(f"[ERROR] Fetching {url}: {e}")
        return None
    except Exception as e:
        print(f"[ERROR] Processing {url}: {e}")
        return None

@router.post("/check-urls", response_model=ActWarningResponse)
async def check_urls_from_list(req: ActWarningURLRequest):
    """
    Checks a list of URLs for 'act_warning' and returns two lists of URLs.
    """
    with_warnings = []
    without_warnings = []

    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(req.concurrency)

        async def process_url(url):
            async with sem:
                warning_text = await get_act_warning(client, str(url))
                if warning_text:
                    with_warnings.append(url)
                else:
                    without_warnings.append(url)

        await asyncio.gather(*(process_url(url) for url in req.urls))

    return ActWarningResponse(with_warnings=with_warnings, without_warnings=without_warnings)
