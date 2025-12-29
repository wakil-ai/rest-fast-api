# app/services/rate_limit_service.py

from datetime import datetime, timezone
from typing import Optional
from app.db.mongo_handler import MongoHandler
from app.core.logger import logger

class RateLimitService:
    """
    Service to manage rate limiting for users.
    Tracks daily request counts and enforces limits.
    """
    
    RATE_LIMIT_COLLECTION = "rate_limits"
    DAILY_LIMIT = 5
    
    def __init__(self):
        self.mongo_handler = MongoHandler()
    
    def _get_today_date(self) -> str:
        """Get today's date in YYYY-MM-DD format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    def check_and_increment_limit(self, user_id: str) -> tuple[bool, int, int]:
        """
        Check if user has exceeded daily limit and increment counter if not.
        
        Args:
            user_id: The user's unique identifier
            
        Returns:
            tuple: (is_allowed: bool, current_count: int, limit: int)
                - is_allowed: True if request is allowed, False if limit exceeded
                - current_count: Number of requests made today (after increment if allowed)
                - limit: The daily limit
        """
        try:
            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
            
            # Find or create user's rate limit document for today
            query = {"user_id": user_id, "date": today}
            user_limit = collection.find_one(query)
            
            if user_limit:
                current_count = user_limit.get("count", 0)
                
                # Check if limit exceeded
                if current_count >= self.DAILY_LIMIT:
                    logger.warning(f"[RateLimitService] User {user_id} exceeded daily limit: {current_count}/{self.DAILY_LIMIT}")
                    return False, current_count, self.DAILY_LIMIT
                
                # Increment counter
                collection.update_one(
                    query,
                    {"$inc": {"count": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}}
                )
                new_count = current_count + 1
                logger.info(f"[RateLimitService] User {user_id} request count: {new_count}/{self.DAILY_LIMIT}")
                return True, new_count, self.DAILY_LIMIT
            else:
                # Create new rate limit entry for today
                collection.insert_one({
                    "user_id": user_id,
                    "date": today,
                    "count": 1,
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc)
                })
                logger.info(f"[RateLimitService] User {user_id} first request today: 1/{self.DAILY_LIMIT}")
                return True, 1, self.DAILY_LIMIT
                
        except Exception as e:
            logger.error(f"[RateLimitService] Error checking rate limit for user {user_id}: {str(e)}")
            # On error, allow the request (fail open)
            return True, 0, self.DAILY_LIMIT
    
    def get_remaining_requests(self, user_id: str) -> int:
        """
        Get the number of remaining requests for today.
        
        Args:
            user_id: The user's unique identifier
            
        Returns:
            int: Number of remaining requests
        """
        try:
            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
            
            query = {"user_id": user_id, "date": today}
            user_limit = collection.find_one(query)
            
            if user_limit:
                current_count = user_limit.get("count", 0)
                remaining = max(0, self.DAILY_LIMIT - current_count)
            else:
                remaining = self.DAILY_LIMIT
            
            return remaining
            
        except Exception as e:
            logger.error(f"[RateLimitService] Error getting remaining requests for user {user_id}: {str(e)}")
            return self.DAILY_LIMIT
    
    def reset_user_limit(self, user_id: str) -> bool:
        """
        Reset rate limit for a specific user (admin function).
        
        Args:
            user_id: The user's unique identifier
            
        Returns:
            bool: True if reset successful
        """
        try:
            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
            
            result = collection.delete_one({"user_id": user_id, "date": today})
            logger.info(f"[RateLimitService] Reset limit for user {user_id}, deleted {result.deleted_count} records")
            return result.deleted_count > 0
            
        except Exception as e:
            logger.error(f"[RateLimitService] Error resetting limit for user {user_id}: {str(e)}")
            return False
