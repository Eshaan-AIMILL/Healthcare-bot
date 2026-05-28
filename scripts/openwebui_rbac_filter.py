"""
Open WebUI Filter: Enterprise RBAC Enforcer
Upload this script to the Open WebUI Admin -> Functions tab as a 'Filter'.

This filter intercepts every user message, extracts the Open WebUI user's identity,
and generates an HMAC-SHA256 signed SecurityContext. This signed context is injected
into the payload so the FastAPI backend can trust the role without exposing an API bypass.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel
import hmac
import hashlib
import time
import json
import logging

# Set this to match your FastAPI backend's OPENWEBUI_SECRET_KEY
SHARED_SECRET = "super-secret-enterprise-key-change-in-prod"

class Filter:
    class Valves(BaseModel):
        priority: int = 0
        shared_secret: str = SHARED_SECRET

    def __init__(self):
        self.valves = self.Valves()
        self.logger = logging.getLogger(__name__)

    def _generate_hmac_signature(self, payload: str, secret: str) -> str:
        """Generates an HMAC SHA-256 signature for the given payload."""
        return hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """
        Intercepts the request BEFORE it is sent to the FastAPI backend.
        Injects an immutable, signed SecurityContext into the payload.
        """
        if __user__ is None:
            # Default to guest if no user context is provided by Open WebUI
            role = "guest"
            user_id = "anonymous"
            email = "guest@local"
        else:
            # Extract Open WebUI native roles
            # Open WebUI standard roles are typically 'admin' and 'user'.
            # We will pass these down and let FastAPI map them to our enterprise roles
            # (e.g., mapping specific user groups/emails to 'reception').
            role = __user__.get("role", "guest")
            user_id = __user__.get("id", "unknown")
            email = __user__.get("email", "unknown@local")

        # Create the SecurityContext payload
        timestamp = int(time.time())
        security_context = {
            "user_id": user_id,
            "email": email,
            "openwebui_role": role,
            "timestamp": timestamp
        }

        # Sign the payload
        payload_str = json.dumps(security_context, separators=(',', ':'), sort_keys=True)
        signature = self._generate_hmac_signature(payload_str, self.valves.shared_secret)

        injected_payload = {
            "context": security_context,
            "signature": signature
        }
        
        # Inject into the last user message's content to guarantee it survives Open WebUI's OpenAI formatting
        if "messages" in body and len(body["messages"]) > 0:
            for msg in reversed(body["messages"]):
                if msg.get("role") == "user":
                    msg["content"] = str(msg.get("content", "")) + f"\n\n[SECURITY_CONTEXT:{json.dumps(injected_payload)}]"
                    break

        # Optional: Frontend UI Restriction Logging
        # We can implement basic UI-level blocks here to improve UX 
        # (e.g. blocking a guest from selecting an admin model) before even hitting the backend.
        # However, backend validation is the ultimate source of truth.

        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """
        Intercepts the response AFTER it returns from FastAPI.
        Used to strip or format authorization errors.
        """
        return body
