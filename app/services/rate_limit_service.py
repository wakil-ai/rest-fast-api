# app/services/rate_limit_service.py

from datetime import datetime, timezone
from typing import Optional
from app.db.mongo_handler import MongoHandler
from app.core.logger import logger

class RateLimitService:
    """
    Service to manage rate limiting for users.
    Tracks daily request counts and enforces limits.
    Different limits apply based on endpoint type:
    - Agentic RAG (deepresearch): 5 requests per day
    - Regular assistants (umumiy/soliq): 20 requests per day
    """
    
    RATE_LIMIT_COLLECTION = "rate_limits"
    DAILY_LIMIT_DEEPRESEARCH = 5  # For agentic RAG endpoints
    DAILY_LIMIT_ASSISTANT = 20     # For regular assistant endpoints (umumiy/soliq)
    
    def __init__(self):
        self.mongo_handler = MongoHandler()
        self._promo_code_service = None
    
    @property
    def promo_code_service(self):
        """Lazy initialization of PromoCodeService to avoid circular imports."""
        if self._promo_code_service is None:
            from app.services.promo_code_service import PromoCodeService
            self._promo_code_service = PromoCodeService()
        return self._promo_code_service
    
    def _get_today_date(self) -> str:
        """Get today's date in YYYY-MM-DD format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    def check_and_increment_limit(self, user_id: str, is_deepresearch: bool = False) -> tuple[bool, int, int]:
        """
        Check if user has exceeded daily limit and increment counter if not.
        Users with valid promo codes have unlimited access.
        
        Args:
            user_id: The user's unique identifier
            is_deepresearch: True for agentic RAG endpoints (5/day limit), False for regular assistants (20/day limit)
            
        Returns:
            tuple: (is_allowed: bool, current_count: int, limit: int)
                - is_allowed: True if request is allowed, False if limit exceeded
                - current_count: Number of requests made today (after increment if allowed)
                - limit: The daily limit (or -1 for unlimited)
        """
        try:
            # Check if user has unlimited access via promo code
            if self.promo_code_service.user_has_unlimited_access(user_id):
                logger.info(f"[RateLimitService] User {user_id} has unlimited access via promo code")
                return True, -1, -1  # -1 indicates unlimited
            
            # Determine which limit to use and which field to track
            limit_type = "deepresearch" if is_deepresearch else "assistant"
            daily_limit = self.DAILY_LIMIT_DEEPRESEARCH if is_deepresearch else self.DAILY_LIMIT_ASSISTANT
            count_field = "count_deepresearch" if is_deepresearch else "count_assistant"
            
            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
            
            # Find or create user's rate limit document for today
            query = {"user_id": user_id, "date": today}
            user_limit = collection.find_one(query)
            
            if user_limit:
                current_count = user_limit.get(count_field, 0)
                
                # Check if limit exceeded
                if current_count >= daily_limit:
                    logger.warning(f"[RateLimitService] User {user_id} exceeded {limit_type} daily limit: {current_count}/{daily_limit}")
                    return False, current_count, daily_limit
                
                # Increment counter for the specific limit type
                collection.update_one(
                    query,
                    {"$inc": {count_field: 1}, "$set": {"updated_at": datetime.now(timezone.utc)}}
                )
                new_count = current_count + 1
                logger.info(f"[RateLimitService] User {user_id} {limit_type} request count: {new_count}/{daily_limit}")
                return True, new_count, daily_limit
            else:
                # Create new rate limit entry for today with both counters
                collection.insert_one({
                    "user_id": user_id,
                    "date": today,
                    "count_deepresearch": 1 if is_deepresearch else 0,
                    "count_assistant": 0 if is_deepresearch else 1,
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc)
                })
                logger.info(f"[RateLimitService] User {user_id} first {limit_type} request today: 1/{daily_limit}")
                return True, 1, daily_limit
                
        except Exception as e:
            logger.error(f"[RateLimitService] Error checking rate limit for user {user_id}: {str(e)}")
            # On error, allow the request (fail open)
            daily_limit = self.DAILY_LIMIT_DEEPRESEARCH if is_deepresearch else self.DAILY_LIMIT_ASSISTANT
            return True, 0, daily_limit
    
    def get_remaining_requests(self, user_id: str, is_deepresearch: bool = False) -> int:
        """
        Get the number of remaining requests for today.
        Returns -1 for users with unlimited access via promo code.
        
        Args:
            user_id: The user's unique identifier
            is_deepresearch: True for agentic RAG endpoints, False for regular assistants
            
        Returns:
            int: Number of remaining requests (-1 for unlimited)
        """
        try:
            # Check if user has unlimited access via promo code
            if self.promo_code_service.user_has_unlimited_access(user_id):
                logger.info(f"[RateLimitService] User {user_id} has unlimited access")
                return -1  # -1 indicates unlimited
            
            daily_limit = self.DAILY_LIMIT_DEEPRESEARCH if is_deepresearch else self.DAILY_LIMIT_ASSISTANT
            count_field = "count_deepresearch" if is_deepresearch else "count_assistant"
            
            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
            
            query = {"user_id": user_id, "date": today}
            user_limit = collection.find_one(query)
            
            if user_limit:
                current_count = user_limit.get(count_field, 0)
                remaining = max(0, daily_limit - current_count)
            else:
                remaining = daily_limit
            
            return remaining
            
        except Exception as e:
            logger.error(f"[RateLimitService] Error getting remaining requests for user {user_id}: {str(e)}")
            daily_limit = self.DAILY_LIMIT_DEEPRESEARCH if is_deepresearch else self.DAILY_LIMIT_ASSISTANT
            return daily_limit
    
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
