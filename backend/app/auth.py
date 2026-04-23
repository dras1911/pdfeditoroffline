"""No-auth stub. Auth removed — internal network only.
All requests treated as user 'local'.
"""


def current_user() -> str:
    return "local"
