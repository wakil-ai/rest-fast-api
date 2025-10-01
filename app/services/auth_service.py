from app.models.auth import TelegramAuth, Size
from app.models.auth import TelegramDataError, TelegramDataIsOutdated
import hashlib
import hmac
import time


def validate_telegram_data(telegram_bot_token: str,
                           data: TelegramAuth) -> dict:
    """
    Checking the authorization telegram data according to the information.
    Official telegram doc: https://core.telegram.org/widgets/login
    """
    data_dict = data.model_dump()
    
    # Filter out None values before processing
    filtered_data = {k: v for k, v in data_dict.items() if v is not None}
    
    received_hash = filtered_data.pop('hash', None)
    
    auth_date = filtered_data.get('auth_date')

    if _verify_telegram_session_outdate(auth_date):
        raise TelegramDataIsOutdated(
            'Telegram authentication session is expired.'
        )

    generated_hash = _generate_hash(filtered_data, telegram_bot_token)

    if generated_hash != received_hash:
        raise TelegramDataError(
            'Request data is incorrect'
        )

    return filtered_data


def _verify_telegram_session_outdate(auth_date: str) -> bool:
    one_day_in_second = 86400
    unix_time_now = int(time.time())
    unix_time_auth_date = int(auth_date)
    timedelta = unix_time_now - unix_time_auth_date

    if timedelta > one_day_in_second:
        return True
    return False


def _generate_hash(data: dict, token: str) -> str:
    request_data_alph_sorted = sorted(data.items(),
                                      key=lambda v: v[0])

    data_check_string = '\n'.join(f'{key}={value}' for key, value in
                                  request_data_alph_sorted)

    secret_key = hashlib.sha256(token.encode()).digest()
    generated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256
    ).hexdigest()

    return generated_hash
    

class TelegramLoginWidget:
    """
    Class to generate Telegram login Widget according to the information
    from the official documentation: https://core.telegram.org/widgets/login
    """
    
    def __init__(self, telegram_login: str,
                 size: Size = Size.MEDIUM,
                 user_photo: bool = False,
                 corner_radius: int | None = None,
                 access_write: bool = True):
        self.telegram_login = telegram_login
        self.corner_radius = corner_radius
        self.size = size
        self.user_photo = user_photo
        self.access_write = access_write
        self.start_script = ('<script async src='
                             '"https://telegram.org/js/telegram-widget.js?22"')
        self.end_script = '></script>'
    
    def callback_telegram_login_widget(self, func: str, arg: str = '') -> str:
        """
        Generate Telegram Callback Login Widget.
        """
        data_on_auth = f'data-onauth="{func}({arg})"'
        
        params = self._generate_params(self.telegram_login,
                                       self.size,
                                       self.corner_radius,
                                       self.user_photo,
                                       self.access_write)
        return (
            f'{self.start_script} '
            f'{params.get("data_telegram_login")} '
            f'{params.get("data_size")} '
            f'{data_on_auth} '
            f'{params.get("data_user_pic")} '
            f'{params.get("data_radius")} '
            f'{params.get("data_request_access")} '
            f'{self.end_script}'
        )
    
    def redirect_telegram_login_widget(self, redirect_url: str):
        """
        Generate Telegram Callback Login Widget
        """
        
        params = self._generate_params(self.telegram_login,
                                       self.size,
                                       self.corner_radius,
                                       self.user_photo,
                                       self.access_write)
        return (
            f'{self.start_script} '
            f'{params.get("data_telegram_login")} '
            f'{params.get("data_size")} '
            f'data-auth-url="{redirect_url}" '
            f'{params.get("data_user_pic")} '
            f'{params.get("data_radius")} '
            f'{params.get("data_request_access")} '
            f'{self.end_script}'
        )
    
    def _generate_params(self, telegram_login: str, size: Size,
                         corner_radius: int | None = None,
                         user_photo: bool = False,
                         access_write: bool = True):
        data_telegram_login = f'data-telegram-login="{telegram_login}"'
        data_size = f'data-size="{size.value}"'
        data_userpic = f'data-userpic="{user_photo}"' if not user_photo else ''
        data_radius = f'data-radius="{corner_radius}"' if isinstance(
            corner_radius, int) else ''
        data_request_access = f'data-request-access="{access_write}"'
        
        return {
            'data_telegram_login': data_telegram_login,
            'data_size': data_size,
            'data_user_pic': data_userpic,
            'data_radius': data_radius,
            'data_request_access': data_request_access,
        }