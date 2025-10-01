from fastapi import APIRouter, Request
from fastapi import Depends
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates
import os
from app.models.auth import TelegramAuth
from app.services.auth_service import validate_telegram_data, TelegramLoginWidget
from app.core.config import settings


router = APIRouter(prefix="/auth", tags=["Telegram Auth"])

templates = Jinja2Templates(directory=os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "templates"))

@router.get("/", name='index')
async def index(request: Request):
    """
    Index page just redirects to login page.
    """
    return RedirectResponse(url=request.url_for('login'))


@router.get("/login", name='login')
async def login(request: Request,
                query_params: TelegramAuth = Depends(TelegramAuth)):
    """
    Endpoint for authorization through Telegram API.

    If there is no "hash" in the query, it is automatically
    redirected to the 'login' authorization page.

    If a "hash" is received in the query parameters,
    a function is called to validate the received data and, if successful
    it renders a page with information about the authorized user.
    """
    telegram_token = settings.TELEGRAM_BOT_TOKEN
    telegram_login = settings.TELEGRAM_BOT_LOGIN
    
    login_widget = TelegramLoginWidget(telegram_login=telegram_login,
                                       size=settings.TELEGRAM_LOGIN_SIZE,
                                       user_photo=False,
                                       corner_radius=0)
    
    redirect_url = str(request.url_for('login'))
    redirect_widget = login_widget.redirect_telegram_login_widget(
        redirect_url=redirect_url)
    
    if not query_params.model_dump().get('hash'):
        return templates.TemplateResponse(
            'login.html',
            context={
                'request': request,
                'redirect_telegram_login_widget': redirect_widget,
            }
        )

    try:
        validated_data = validate_telegram_data(telegram_token, query_params)

        if validated_data:
            return templates.TemplateResponse('profile.html',
                                              context={'request': request,
                                                       **validated_data})
        
    except ValueError as e:
        return HTMLResponse(content=f"<h1>{e}</h1>", status_code=400)