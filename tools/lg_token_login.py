#!/usr/bin/env python3
"""
Holt einen LG ThinQ Refresh-Token über den Browser-Login.

Hintergrund: LG hat 10/2026 den searchKey-Endpoint entfernt, der Passwort-Login
der inoffiziellen API funktioniert seitdem nicht mehr
(ollo69/ha-smartthinq-sensors#1016). Der Browser-Login liefert aber weiterhin
einen refresh_token, mit dem der Publisher starten kann.

Ablauf:
    python tools/lg_token_login.py
    1. Ausgegebene URL im Browser öffnen und anmelden.
    2. Die Seite danach zeigt evtl. "nicht gefunden" – egal. Die komplette URL
       aus der Adressleiste (bzw. F12 -> Netzwerk) kopieren und hier einfügen.
    3. LG_REFRESH_TOKEN und LG_OAUTH_URL in die .env übernehmen.
"""

import asyncio
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wideq.core_async import CoreAsync, Gateway, lg_client_session  # noqa: E402


async def login_url(country: str, language: str) -> str:
    session = lg_client_session()
    try:
        core = CoreAsync(country, language, session=session)
        gateway = await Gateway.discover(core)
        return gateway.oauth_login_url()
    finally:
        await session.close()


def main() -> int:
    country = os.getenv("LG_COUNTRY", "DE")
    language = os.getenv("LG_LANGUAGE", "de-DE")

    print("1. Diese URL im Browser öffnen und anmelden:\n")
    print(asyncio.run(login_url(country, language)))
    print("\n2. Die URL nach dem Login (enthält refresh_token=...) hier einfügen:")
    callback = input("> ").strip()

    params = {k: v[0] for k, v in parse_qs(urlparse(callback).query).items()}
    refresh_token = params.get("refresh_token")
    if not refresh_token:
        print("Kein refresh_token in der URL gefunden.", file=sys.stderr)
        return 1

    print("\n3. In die .env übernehmen:\n")
    print(f"LG_REFRESH_TOKEN={refresh_token}")
    if oauth_url := params.get("oauth2_backend_url"):
        print(f"LG_OAUTH_URL={oauth_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
