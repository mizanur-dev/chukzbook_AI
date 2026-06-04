from rest_framework.throttling import SimpleRateThrottle


class EmailRateThrottle(SimpleRateThrottle):
    """1 request per email per day."""
    scope = "email"
    rate = "1/day"

    def get_cache_key(self, request, view):
        email = request.data.get("email")
        if not email:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": email.lower().strip(),
        }


class IPRateThrottle(SimpleRateThrottle):
    """4 requests per IP per day (~1 per 6 hours)."""
    scope = "ip"
    rate = "4/day"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        if not ident:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": ident,
        }
