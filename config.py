import os

# Secrets live in local_secrets.py (git-ignored) or environment variables.
try:
    import local_secrets as _ls
except ImportError:
    _ls = None


def _secret(name):
    return (getattr(_ls, name, "") if _ls else "") or os.environ.get(name, "")


# Get a free API key at https://the-odds-api.com
ODDS_API_KEY = _secret("ODDS_API_KEY")
