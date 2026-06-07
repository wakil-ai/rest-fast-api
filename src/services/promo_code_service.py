from datetime import datetime, timezone
from typing import Any

from dateutil import parser as date_parser

from core.config import settings
from core.dependencies import get_mongo_handler
from core.logger import logger


class PromoCodeService:
    """
    Service to manage promo codes for unlimited access.
    Handles CRUD operations for promo codes and user-promo code assignments.
    """

    PROMO_CODE_COLLECTION = settings.PROMO_CODE_COLLECTION
    USER_PROMO_CODE_COLLECTION = settings.USER_PROMO_CODE_COLLECTION

    def __init__(self):
        self.mongo_handler = get_mongo_handler()

    async def create_promo_code(
        self,
        code: str,
        created_by: str | None = None,
        description: str | None = None,
        expiration_date: datetime | None = None,
        credit_amount: int | None = None,
    ) -> bool:
        """
        Create a new promo code.

        Args:
            code: The promo code string (must be unique)
            created_by: Admin user ID who created the code
            description: Optional description
            expiration_date: Expiration date (None = forever)
            credit_amount: Daily credit amount (None = unlimited)

        Returns:
            bool: True if created successfully, False if code already exists
        """
        try:
            collection = self.mongo_handler.db[self.PROMO_CODE_COLLECTION]

            # Check if code already exists
            existing = await collection.find_one({"code": code})
            if existing:
                logger.warning(f"[PromoCodeService] Promo code '{code}' already exists")
                return False

            # Create the promo code
            promo_code_doc = {
                "code": code,
                "is_active": True,
                "expiration_date": expiration_date,
                "credit_amount": credit_amount,
                "created_at": datetime.now(timezone.utc),
                "created_by": created_by,
                "description": description,
            }

            await collection.insert_one(promo_code_doc)
            logger.info(
                f"[PromoCodeService] Created promo code: {code} (expires: {expiration_date or 'never'}, credits: {credit_amount or 'unlimited'})"
            )
            return True

        except Exception as e:
            logger.error(f"[PromoCodeService] Error creating promo code: {str(e)}")
            return False

    async def get_promo_code(self, code: str) -> dict[str, Any] | None:
        """
        Get a specific promo code by code string.

        Args:
            code: The promo code string

        Returns:
            Dict or None: Promo code document if found
        """
        try:
            collection = self.mongo_handler.db[self.PROMO_CODE_COLLECTION]
            promo_code = await collection.find_one({"code": code})
            return promo_code

        except Exception as e:
            logger.error(
                f"[PromoCodeService] Error getting promo code {code}: {str(e)}"
            )
            return None

    async def list_all_promo_codes(
        self, active_only: bool = False
    ) -> list[dict[str, Any]]:
        """
        List all promo codes.

        Args:
            active_only: If True, return only active promo codes

        Returns:
            List of promo code documents
        """
        try:
            collection = self.mongo_handler.db[self.PROMO_CODE_COLLECTION]

            query = {"is_active": True} if active_only else {}
            promo_codes = list(collection.find(query).sort("created_at", -1))

            logger.info(f"[PromoCodeService] Retrieved {len(promo_codes)} promo codes")
            return promo_codes

        except Exception as e:
            logger.error(f"[PromoCodeService] Error listing promo codes: {str(e)}")
            return []

    async def activate_promo_code(self, code: str) -> bool:
        """
        Activate a promo code.

        Args:
            code: The promo code string

        Returns:
            bool: True if activated successfully
        """
        try:
            collection = self.mongo_handler.db[self.PROMO_CODE_COLLECTION]

            result = await collection.update_one(
                {"code": code},
                {"$set": {"is_active": True, "updated_at": datetime.now(timezone.utc)}},
            )

            if result.modified_count > 0:
                logger.info(f"[PromoCodeService] Activated promo code: {code}")
                return True
            return False

        except Exception as e:
            logger.error(
                f"[PromoCodeService] Error activating promo code {code}: {str(e)}"
            )
            return False

    async def deactivate_promo_code(self, code: str) -> bool:
        """
        Deactivate a promo code.

        Args:
            code: The promo code string

        Returns:
            bool: True if deactivated successfully
        """
        try:
            collection = self.mongo_handler.db[self.PROMO_CODE_COLLECTION]

            result = await collection.update_one(
                {"code": code},
                {
                    "$set": {
                        "is_active": False,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )

            if result.modified_count > 0:
                logger.info(f"[PromoCodeService] Deactivated promo code: {code}")
                return True
            return False

        except Exception as e:
            logger.error(
                f"[PromoCodeService] Error deactivating promo code {code}: {str(e)}"
            )
            return False

    async def delete_promo_code(self, code: str) -> bool:
        """
        Delete a promo code and all user assignments.

        Args:
            code: The promo code string

        Returns:
            bool: True if deleted successfully
        """
        try:
            # Delete the promo code
            promo_collection = self.mongo_handler.db[self.PROMO_CODE_COLLECTION]
            promo_result = promo_collection.delete_one({"code": code})

            # Delete all user assignments
            user_promo_collection = self.mongo_handler.db[
                self.USER_PROMO_CODE_COLLECTION
            ]
            user_result = await user_promo_collection.delete_many({"promo_code": code})

            if promo_result.deleted_count > 0:
                logger.info(
                    f"[PromoCodeService] Deleted promo code '{code}' and {user_result.deleted_count} user assignments"
                )
                return True
            return False

        except Exception as e:
            logger.error(
                f"[PromoCodeService] Error deleting promo code {code}: {str(e)}"
            )
            return False

    async def assign_promo_code_to_user(
        self, user_id: str, promo_code: str
    ) -> tuple[bool, int]:
        """
        Assign a promo code to a user for unlimited access.

        Args:
            user_id: The user's unique identifier
            promo_code: The promo code to assign

        Returns:
            bool: True if assigned successfully
            status_code: int
        """
        try:
            # Verify promo code exists and is active
            promo = await self.get_promo_code(promo_code)
            if not promo:
                logger.warning(
                    f"[PromoCodeService] Promo code '{promo_code}' not found"
                )
                return False, 404

            if not promo.get("is_active", False):
                logger.warning(
                    f"[PromoCodeService] Promo code '{promo_code}' is not active"
                )
                return False, 400

            # Check if user already has a promo code
            user_promo_collection = self.mongo_handler.db[
                self.USER_PROMO_CODE_COLLECTION
            ]
            existing = await user_promo_collection.find_one({"user_id": user_id})

            if existing:
                # Update existing assignment
                await user_promo_collection.update_one(
                    {"user_id": user_id},
                    {
                        "$set": {
                            "promo_code": promo_code,
                            "assigned_at": datetime.now(timezone.utc),
                            "has_unlimited_access": True,
                        }
                    },
                )
                logger.info(
                    f"[PromoCodeService] Updated promo code for user {user_id} to '{promo_code}'"
                )
            else:
                # Create new assignment
                assignment_doc = {
                    "user_id": user_id,
                    "promo_code": promo_code,
                    "assigned_at": datetime.now(timezone.utc),
                    "has_unlimited_access": True,
                }
                await user_promo_collection.insert_one(assignment_doc)
                logger.info(
                    f"[PromoCodeService] Assigned promo code '{promo_code}' to user {user_id}"
                )

            return True, 200

        except Exception as e:
            logger.error(
                f"[PromoCodeService] Error assigning promo code to user: {str(e)}"
            )
            return False

    async def remove_user_promo_code(self, user_id: str) -> bool:
        """
        Remove promo code from a user.

        Args:
            user_id: The user's unique identifier

        Returns:
            bool: True if removed successfully
        """
        try:
            user_promo_collection = self.mongo_handler.db[
                self.USER_PROMO_CODE_COLLECTION
            ]
            result = await user_promo_collection.delete_one({"user_id": user_id})

            if result.deleted_count > 0:
                logger.info(
                    f"[PromoCodeService] Removed promo code from user {user_id}"
                )
                return True
            return False

        except Exception as e:
            logger.error(
                f"[PromoCodeService] Error removing promo code from user: {str(e)}"
            )
            return False

    async def get_user_promo_code(self, user_id: str) -> dict[str, Any] | None:
        """
        Get the promo code assigned to a user.

        Args:
            user_id: The user's unique identifier

        Returns:
            Dict or None: User promo code assignment if found
        """
        try:
            user_promo_collection = self.mongo_handler.db[
                self.USER_PROMO_CODE_COLLECTION
            ]
            assignment = await user_promo_collection.find_one({"user_id": user_id})
            return assignment

        except Exception as e:
            logger.error(f"[PromoCodeService] Error getting user promo code: {str(e)}")
            return None

    async def get_user_promo_status(self, user_id: str) -> tuple[bool, int | None]:
        """
        Check if a user has a valid promo code and get their daily credit limit.

        Args:
            user_id: The user's unique identifier

        Returns:
            Tuple[bool, Optional[int]]: (has_promo_code, daily_credit_limit)
                - has_promo_code: True if user has a valid, non-expired promo code
                - daily_credit_limit: None for unlimited, or specific credit amount
        """
        try:
            # Get user's promo code assignment
            assignment = await self.get_user_promo_code(user_id)
            if not assignment:
                return False, None

            # Check if the promo code is still active
            promo_code = assignment.get("promo_code")
            if not promo_code:
                return False, None

            promo = await self.get_promo_code(promo_code)
            if not promo:
                logger.warning(
                    f"[PromoCodeService] Promo code '{promo_code}' not found for user {user_id}"
                )
                return False, None

            if not promo.get("is_active", False):
                logger.warning(
                    f"[PromoCodeService] Promo code '{promo_code}' is not active for user {user_id}"
                )
                return False, None

            # Check expiration date
            expiration_date = promo.get("expiration_date")
            if expiration_date:
                try:
                    # Ensure expiration_date is datetime object
                    if isinstance(expiration_date, str):
                        expiration_date = date_parser.parse(expiration_date)

                    # Make timezone-aware if not already
                    if (
                        not hasattr(expiration_date, "tzinfo")
                        or expiration_date.tzinfo is None
                    ):
                        expiration_date = expiration_date.replace(tzinfo=timezone.utc)

                    # Make current time timezone-aware
                    now = datetime.now(timezone.utc)

                    if now > expiration_date:
                        logger.info(
                            f"[PromoCodeService] Promo code '{promo_code}' expired for user {user_id} (now: {now}, exp: {expiration_date})"
                        )
                        return False, None

                except Exception as e:
                    logger.error(
                        f"[PromoCodeService] Failed to compare expiration date for '{promo_code}': {str(e)}"
                    )
                    return False, None
            else:
                logger.info(
                    f"[PromoCodeService] Promo code '{promo_code}' has no expiration (valid forever)"
                )

            # Get credit amount (None = unlimited)
            credit_amount = promo.get("credit_amount")
            logger.info(
                f"[PromoCodeService] User {user_id} has valid promo code '{promo_code}' with {credit_amount or 'unlimited'} daily credits"
            )
            return True, credit_amount

        except Exception as e:
            logger.error(
                f"[PromoCodeService] Error checking promo status for user {user_id}: {str(e)}"
            )
            return False, None

    async def user_has_unlimited_access(self, user_id: str) -> bool:
        """
        Check if a user has unlimited access via a valid promo code.

        Args:
            user_id: The user's unique identifier

        Returns:
            bool: True if user has unlimited access (promo code with no credit limit)
        """
        has_promo, credit_limit = await self.get_user_promo_status(user_id)
        return has_promo and credit_limit is None
