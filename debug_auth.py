#!/usr/bin/env python3

import hashlib
import hmac
from urllib.parse import parse_qs, urlparse
import os

def debug_telegram_auth():
    # The URL from the error log
    url = "GET /api/auth/login?id=5904877504&first_name=Mirsaid&username=mirsaidAI&photo_url=https%3A%2F%2Ft.me%2Fi%2Fuserpic%2F320%2F62x424KVtu3i7pkE-U7SgXf7EBT3RYkPVnPiq-MqDYuDNJHgyZ-Nj6yV5D7YIkwd.jpg&auth_date=1759300262&hash=bc140c0f7e564e274c996d0ddb2b6d5a0658ae6691c7011a31619e072a29b8b6"
    
    # Extract query parameters
    query_part = url.split('?')[1].split(' ')[0]  # Remove HTTP/1.1 part
    print(f"Query string: {query_part}")
    
    # Parse query parameters
    parsed_params = parse_qs(query_part)
    
    # Convert to single values (parse_qs returns lists)
    params = {k: v[0] if v else None for k, v in parsed_params.items()}
    print(f"Parsed params: {params}")
    
    # Extract the received hash
    received_hash = params.pop('hash', None)
    print(f"Received hash: {received_hash}")
    
    # Sort parameters alphabetically
    sorted_params = sorted(params.items())
    print(f"Sorted params: {sorted_params}")
    
    # Create data check string
    data_check_string = '\n'.join(f'{key}={value}' for key, value in sorted_params)
    print(f"Data check string: {repr(data_check_string)}")
    
    # You'll need to set your actual bot token here
    # For now, let's see what we get from env or config
    try:
        from app.core.config import settings
        bot_token = settings.TELEGRAM_BOT_TOKEN
        print(f"Bot token loaded from settings (first 10 chars): {bot_token[:10]}...")
    except Exception as e:
        print(f"Could not load bot token from settings: {e}")
        bot_token = "YOUR_BOT_TOKEN_HERE"  # Replace with actual token for testing
    
    # Generate hash according to Telegram's algorithm
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    generated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256
    ).hexdigest()
    
    print(f"Generated hash: {generated_hash}")
    print(f"Hashes match: {generated_hash == received_hash}")
    
    return generated_hash, received_hash

if __name__ == "__main__":
    debug_telegram_auth()