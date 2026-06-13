"""Shared configuration and constants."""

from __future__ import annotations

from pathlib import Path

DEVECO_BASE_URL = "https://cn.devecostudio.huawei.com"
DEVECO_AUTH_URL = "console/DevEcoIDE/apply"
DEVECO_TEMP_TOKEN_CHECK_URL = "authrouter/auth/api/temptoken/check"
DEVECO_JWT_TOKEN_CHECK_URL = "authrouter/auth/api/jwToken/check"
DEVECO_SUCCESS_REDIRECT_URL = "console/DevEcoCode/loginSuccess"
DEVECO_FAILED_REDIRECT_URL = "console/DevEcoCode/loginFailed"
DEVECO_APP_ID = "1008"
DEVECO_DEFAULT_AUTH_PORT = 10101

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_PROXY = ""

CRED_DIR = Path("./cred")
TOKEN_FILE = CRED_DIR / "token.enc"
AUTH_FILE = CRED_DIR / "auth.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
