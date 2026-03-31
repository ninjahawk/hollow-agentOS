import pytest
import json
from pathlib import Path

API_URL = "http://localhost:7777"
TOKEN_PATHS = [
    Path("/agentOS/config.json"),
    Path("config.json"),
]

def _load_token():
    for p in TOKEN_PATHS:
        if p.exists():
            cfg = json.loads(p.read_text())
            t = cfg.get("api", {}).get("token", "")
            if t:
                return t
    return ""

@pytest.fixture(scope="session")
def api_url():
    return API_URL

@pytest.fixture(scope="session")
def master_token():
    return _load_token()

@pytest.fixture(scope="session")
def auth_headers(master_token):
    return {"Authorization": f"Bearer {master_token}"}
