import hashlib
import hmac

from .config import get_settings


def review_token(post_id: str, attempt: int, action: str) -> str:
    """Signed token for the email buttons. A new version (attempt) makes old email links stop working."""
    msg = f"{post_id}:{attempt}:{action}".encode()
    return hmac.new(get_settings().app_secret.encode(), msg, hashlib.sha256).hexdigest()[:40]


def check_review_token(post: dict, action: str, token: str) -> bool:
    return hmac.compare_digest(review_token(post["id"], post["attempt"], action), token or "")


def review_link(post: dict, action: str) -> str:
    s = get_settings()
    return f"{s.base_url}/review/{post['id']}?action={action}&t={review_token(post['id'], post['attempt'], action)}"


def check_password(given: str) -> bool:
    return hmac.compare_digest(given.encode(), get_settings().dashboard_password.encode())
