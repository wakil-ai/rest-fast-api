from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

import requests
import markdown
from app.utils.encoding import token_store

router = APIRouter(prefix="", tags=["Util Endpoints"])

@router.get("/contracts/{file_id}", summary="View contract (Markdown rendered)")
def view_contract(file_id: str):
    decoded_url = token_store.decode(file_id)

    if not decoded_url:
        raise HTTPException(status_code=404, detail="Link expired or invalid")

    try:
        response = requests.get(decoded_url, timeout=10)
        response.raise_for_status()
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to fetch contract")

    md_text = response.content.decode("utf-8")


    raw_html = markdown.markdown(
        md_text,
        extensions=["extra", "tables", "toc"]
    )

    final_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Contract Viewer</title>

        <style>
            body {{
                font-family: Arial, sans-serif;
                max-width: 900px;
                margin: 40px auto;
                line-height: 1.7;
                color: #222;
            }}
            h1, h2, h3 {{
                margin-top: 24px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 15px 0;
            }}
            th, td {{
                border: 1px solid #ccc;
                padding: 8px;
            }}
            a {{
                color: #0066cc;
            }}
        </style>
    </head>
    <body>
        {raw_html}
    </body>
    </html>
    """

    return HTMLResponse(content=final_html)
