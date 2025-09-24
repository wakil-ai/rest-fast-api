# app/services/location_service.py

import requests
from typing import Optional
from fastapi import Request
from app.models.chat_history import LocationData
from app.core.logger import logger


class LocationService:
    """Service for detecting user location from IP address"""

    @staticmethod
    def get_client_ip(request: Request) -> str:
        """Extract client IP address from request"""
        # Check for forwarded headers first (proxy/load balancer)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP if multiple are present
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fallback to direct client IP
        if hasattr(request.client, 'host'):
            return request.client.host

        return "unknown"

    @staticmethod
    async def detect_location_from_ip(ip_address: str) -> Optional[LocationData]:
        """
        Detect location from IP address using ipapi.co (free tier: 1000 requests/month)
        Alternative services: ip-api.com, ipinfo.io, geojs.io
        """
        if not ip_address or ip_address in ["127.0.0.1", "localhost", "unknown"]:
            logger.warning(f"Cannot detect location for IP: {ip_address}")
            return None

        try:
            # Using ipapi.co free API
            response = requests.get(
                f"http://ipapi.co/{ip_address}/json/",
                timeout=5,
                headers={'User-Agent': 'Wakil-AI-Location-Service/1.0'}
            )

            if response.status_code == 200:
                data = response.json()

                # Check if we got valid data
                if data.get("error"):
                    logger.warning(f"IP API error for {ip_address}: {data.get('reason')}")
                    return None

                location = LocationData(
                    type="ip",
                    latitude=data.get("latitude"),
                    longitude=data.get("longitude"),
                    country=data.get("country_name"),
                    city=data.get("city"),
                    region=data.get("region"),
                    ip_address=ip_address
                )

                logger.info(f"Location detected for IP {ip_address}: {location.city}, {location.country}")
                return location

            else:
                logger.error(f"IP API request failed with status {response.status_code}")
                return None

        except requests.exceptions.Timeout:
            logger.warning(f"Location detection timeout for IP: {ip_address}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Location detection request error for IP {ip_address}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in location detection for IP {ip_address}: {str(e)}")
            return None

    @staticmethod
    async def detect_location_from_request(request: Request) -> Optional[LocationData]:
        """Convenience method to detect location directly from FastAPI request"""
        ip_address = LocationService.get_client_ip(request)
        return await LocationService.detect_location_from_ip(ip_address)

    @staticmethod
    def create_manual_location(country: str = None, city: str = None, region: str = None) -> LocationData:
        """Create manual location data when GPS/IP detection fails"""
        return LocationData(
            type="manual",
            country=country,
            city=city,
            region=region
        )

    @staticmethod
    def validate_gps_location(latitude: float, longitude: float) -> bool:
        """Validate GPS coordinates are within valid ranges"""
        return (
            -90 <= latitude <= 90 and
            -180 <= longitude <= 180
        )