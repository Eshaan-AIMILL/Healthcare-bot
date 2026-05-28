from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
import hmac
import hashlib
import json
import time

from app.config import settings
from app.utils.logger import logger

class SecurityContext(BaseModel):
    """
    Immutable SecurityContext passed through the LangGraph state.
    Provides deterministic role resolution across all agents and tools.
    """
    user_id: str = Field(frozen=True)
    email: str = Field(frozen=True)
    openwebui_role: str = Field(frozen=True)
    enterprise_role: str = Field(frozen=True)
    timestamp: int = Field(frozen=True)

    def has_role(self, allowed_roles: List[str]) -> bool:
        """Check if the user has an allowed role."""
        return self.enterprise_role in allowed_roles

def _map_openwebui_role(raw_role: str, email: str) -> str:
    """
    Maps Open WebUI's generic roles ('admin', 'user') to our enterprise roles.
    This now explicitly checks the email addresses first to ensure exact mapping.
    """
    email = email.lower().strip()
    
    # 1. Explicit Email Overrides (Highest Priority)
    if email == "admin@localhost":
        return "admin"
    elif email == "reception@localhost":
        return "reception"
    elif email == "guest@localhost":
        return "guest"
        
    # 2. Fallback to Open WebUI native roles
    raw_role = raw_role.lower()
    if raw_role == "admin":
        return "admin"
    elif raw_role == "user":
        return "reception"
    else:
        return "guest"

def verify_and_create_context(metadata: Dict[str, Any]) -> Optional[SecurityContext]:
    """
    Verifies the HMAC signature from the Open WebUI filter and creates an
    immutable SecurityContext.
    """
    if not metadata:
        logger.warning("No metadata found in request. Denying access.")
        return None

    security_context_data = metadata.get("security_context")
    signature = metadata.get("security_signature")

    if not security_context_data or not signature:
        logger.warning("Missing security_context or security_signature. Denying access.")
        return None

    # Validate timestamp to prevent replay attacks (e.g., max 5 minutes old)
    timestamp = security_context_data.get("timestamp", 0)
    if abs(int(time.time()) - timestamp) > 300:
        logger.warning(f"Security context timestamp expired: {timestamp}. Denying access.")
        return None

    # Reconstruct the payload exactly as it was signed
    payload_str = json.dumps(security_context_data, separators=(',', ':'), sort_keys=True)
    
    # Generate expected signature
    expected_signature = hmac.new(
        settings.openwebui_secret_key.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    # Constant-time comparison
    if not hmac.compare_digest(expected_signature, signature):
        logger.error("HMAC signature verification failed. Potential spoofing attempt.")
        return None

    enterprise_role = _map_openwebui_role(
        security_context_data.get("openwebui_role", "guest"),
        security_context_data.get("email", "")
    )

    return SecurityContext(
        user_id=security_context_data.get("user_id", "unknown"),
        email=security_context_data.get("email", "unknown"),
        openwebui_role=security_context_data.get("openwebui_role", "guest"),
        enterprise_role=enterprise_role,
        timestamp=timestamp
    )

def require_role(roles: List[str]):
    """
    Decorator for Tool-level RBAC and API endpoints.
    Enforces that the executing SecurityContext has one of the allowed roles.
    """
    from functools import wraps
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Try to find SecurityContext in kwargs
            context = kwargs.get("security_context")
            if not context or not isinstance(context, SecurityContext):
                logger.error(f"Unauthorized tool execution attempted on {func.__name__} without SecurityContext.")
                raise PermissionError(f"Access denied: Missing security context for {func.__name__}")
            
            if not context.has_role(roles):
                logger.warning(f"Role {context.enterprise_role} denied access to {func.__name__}. Required: {roles}")
                raise PermissionError(f"Insufficient permissions. Required role(s): {roles}")
            
            return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            context = kwargs.get("security_context")
            if not context or not isinstance(context, SecurityContext):
                logger.error(f"Unauthorized tool execution attempted on {func.__name__} without SecurityContext.")
                raise PermissionError(f"Access denied: Missing security context for {func.__name__}")
            
            if not context.has_role(roles):
                logger.warning(f"Role {context.enterprise_role} denied access to {func.__name__}. Required: {roles}")
                raise PermissionError(f"Insufficient permissions. Required role(s): {roles}")
            
            return func(*args, **kwargs)

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator
