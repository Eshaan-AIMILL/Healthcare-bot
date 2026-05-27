from enum import Enum
from typing import List, Dict

class Role(str, Enum):
    ADMIN = "admin"
    RECEPTION = "reception"
    GUEST = "guest"

class RBACManager:
    """
    Role-Based Access Control Manager.
    Defines what domains and actions each role can access.
    """
    
    # Define accessible domains per role
    DOMAIN_ACCESS = {
        Role.ADMIN: ["billing", "compliance", "pharmacy", "patient", "dispatch"],
        Role.RECEPTION: ["patient", "dispatch"], # Operational only
        Role.GUEST: [] # No domain access, general FAQ only
    }
    
    @classmethod
    def can_access_domain(cls, role: Role, domain: str) -> bool:
        """Check if a role can access a specific domain (agent)."""
        return domain in cls.DOMAIN_ACCESS.get(role, [])

    @classmethod
    def get_role_context(cls, role: Role) -> str:
        """Returns context string to inject into prompts for role awareness."""
        if role == Role.ADMIN:
            return "Role: Admin. Full access to analytics, compliance, and patient data. Exports allowed."
        elif role == Role.RECEPTION:
            return "Role: Reception. Operational access only. Limited to appointments, check-ins, and basic patient status. NO compliance or billing analytics allowed."
        elif role == Role.GUEST:
            return "Role: Guest. General hospital info and FAQs only. NO access to patient, billing, or internal data."
        return "Role: Unknown."
