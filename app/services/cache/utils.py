import hashlib
from app.config import settings

def normalize_query(query: str) -> str:
    """Normalize query text for consistent cache hits.
    Lowercases, strips surrounding whitespace, merges consecutive spaces,
    and removes common ending punctuation/noise.
    """
    if not query:
        return ""
    # Lowercase
    normalized = query.lower().strip()
    # Replace multiple spaces with a single space
    normalized = " ".join(normalized.split())
    # Remove common trailing punctuation (e.g. "?", ".", "!")
    while normalized and normalized[-1] in "?!.":
        normalized = normalized[:-1].strip()
    return normalized

def build_cache_key(query: str, intent: str, role: str) -> str:
    """Build a deterministic SHA-256 hash based on normalized components."""
    norm_query = normalize_query(query)
    raw_key = f"{norm_query}|{intent}|{role}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

def get_domain_ttl(domain: str) -> int:
    """Get domain-specific TTL in seconds from settings."""
    if not domain:
        return getattr(settings, "cache_ttl_general", 1800)
    
    domain_clean = domain.lower().strip()
    if "cross" in domain_clean or "," in domain_clean:
        return getattr(settings, "cache_ttl_cross_domain", 300)
        
    attr_name = f"cache_ttl_{domain_clean}"
    if hasattr(settings, attr_name):
        return getattr(settings, attr_name)
        
    return getattr(settings, "cache_ttl_general", 1800)
