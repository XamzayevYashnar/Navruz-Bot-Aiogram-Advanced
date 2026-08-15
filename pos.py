"""POS API klienti: mahsulot o'qish, token yangilash, qayta urinish."""

import asyncio
import base64
import binascii
import json
import logging
import time
from datetime import datetime

import aiohttp

import config

log = logging.getLogger("navruz-bot.pos")

# Tarmoq uzilganda necha marta qayta urinsin (ortib boruvchi kutish bilan).
NETWORK_RETRIES = 3


def jwt_expiry(token: str) -> datetime | None:
    """JWT ichidagi `exp` ni o'qib, tugash vaqtini qaytaradi (imzo tekshirilmaydi)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)          # base64 padding
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return datetime.fromtimestamp(exp, tz=config.TZ) if exp else None
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError, OSError):
        return None


class PosClient:
    """POS API bilan ishlash. Access token xotirada saqlanadi va 401 da yangilanadi."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.access_token = config.ACCESS_TOKEN
        self.refresh_token = config.REFRESH_TOKEN
        self._lock = asyncio.Lock()      # bir vaqtda faqat bitta yangilash
        self.refresh_failed = False      # token tiklab bo'lmadi
        self._login_blocked_until = 0.0  # hisob bloklanmasligi uchun kutish vaqti

    # ------------------------------------------------------------ headers --

    def _headers(self) -> dict:
        """DIQQAT: `branch` — URL parametri emas, aynan HEADER.

        Usiz server stock=0, cost=0 qaytaradi (haqiqiy API'da tekshirib tasdiqlangan).
        """
        return {
            "Authorization": f"Bearer {self.access_token}",
            "branch": config.BRANCH_ID,
            "Accept": "application/json",
        }

    # -------------------------------------------------------------- token --

    def access_expiry(self) -> datetime | None:
        return jwt_expiry(self.access_token)

    def refresh_expiry(self) -> datetime | None:
        return jwt_expiry(self.refresh_token)

    def _adopt_header_token(self, resp: aiohttp.ClientResponse) -> None:
        """Server tokenni yangilaganda uni javob header'ida qaytaradi.

        YesPOS frontendi aynan shunday ishlaydi (`withRefresh`): har bir javobdagi
        `X-Refresh-Token` header'i tekshiriladi va farq qilsa yangi token sifatida
        saqlanadi. Bot muntazam so'rov yuborib turgani uchun token amalda hech qachon
        tugamaydi — server uni o'zi uzaytirib beradi.
        """
        new_token = resp.headers.get("X-Refresh-Token")
        if new_token and new_token != self.access_token:
            self.access_token = new_token
            log.info("Token server tomonidan yangilandi (X-Refresh-Token). "
                     "Yangi muddat: %s", self.access_expiry())

    async def login(self) -> bool:
        """POS'ga qaytadan login qilib yangi token oladi.

        Endpoint frontend kodidan aniqlangan:
            POST {AUTH_URL}/signIn
            body: {"org_id": ..., "login": ..., "password": ..., "device": {...}}
            javob: {"code": 0, "data": {"token": "...", "base_url": "..."}}
        """
        if not (config.POS_ORG_ID and config.POS_LOGIN and config.POS_PASSWORD):
            log.error("POS_ORG_ID / POS_LOGIN / POS_PASSWORD to'ldirilmagan — "
                      "avtomatik login ishlamaydi.")
            return False

        # Hisob "too_many_attempts" bilan bloklanmasligi uchun tez-tez urinmaymiz.
        now = time.monotonic()
        if now < self._login_blocked_until:
            log.warning("Login vaqtincha to'xtatilgan, %.0f soniya qoldi.",
                        self._login_blocked_until - now)
            return False
        self._login_blocked_until = now + config.LOGIN_MIN_INTERVAL

        payload = {
            "org_id": config.POS_ORG_ID,
            "login": config.POS_LOGIN,
            "password": config.POS_PASSWORD,
            "device": {"device": "navruz-bot", "ip": "0.0.0.0"},
        }

        try:
            async with self.session.post(
                f"{config.AUTH_URL}/signIn", json=payload,
                headers={"Accept": "application/json"}, timeout=30,
            ) as resp:
                data = await resp.json(content_type=None)

                # Server ko'p urinishdan keyin bloklaydi — kutish muddatiga rioya qilamiz.
                if resp.status == 429 or (data or {}).get("error") == "too_many_attempts":
                    wait = int((data or {}).get("retry_after_seconds") or 60)
                    self._login_blocked_until = time.monotonic() + wait
                    log.error("Login bloklandi (ko'p urinish), %s soniya kutamiz.", wait)
                    return False

                if resp.status != 200 or data.get("code") != 0:
                    # Login yoki parol xato — takror urinish foydasiz, uzoqroq kutamiz.
                    self._login_blocked_until = time.monotonic() + 300
                    log.error("Login muvaffaqiyatsiz: HTTP %s — %s",
                              resp.status, str(data)[:200])
                    return False
        except Exception as e:
            log.exception("Login so'rovida xato: %s", e)
            return False

        body = data.get("data") or {}
        token = body.get("token")
        if not token:
            log.error("Login javobida token topilmadi: %s", str(data)[:200])
            return False

        self.access_token = token
        self._login_blocked_until = 0
        log.info("Qaytadan login qilindi. Token muddati: %s", self.access_expiry())

        # Server tenant API manzilini ham qaytaradi — mos kelmasa ogohlantiramiz.
        base_url = (body.get("base_url") or "").rstrip("/")
        if base_url and base_url != config.BASE_URL:
            log.warning("DIQQAT: server BASE_URL sifatida %s ni ko'rsatyapti "
                        "(sozlamada %s).", base_url, config.BASE_URL)
        return True

    async def renew_token(self) -> bool:
        """401 dan keyin tokenni tiklash — hozircha yagona ishonchli yo'l bu login."""
        return await self.login()

    # ------------------------------------------------------------ so'rov ---

    async def _get(self, path: str, params: dict) -> dict | None:
        """GET so'rov: tarmoq xatosida qayta urinadi, 401 da tokenni yangilab retry qiladi."""
        url = f"{config.BASE_URL}/{path}"
        refreshed = False

        for attempt in range(1, NETWORK_RETRIES + 1):
            try:
                async with self.session.get(
                    url, params=params, headers=self._headers(), timeout=30
                ) as resp:
                    if resp.status == 401:
                        if refreshed:
                            log.error("401 — login qilingandan keyin ham ruxsat yo'q.")
                            return None
                        log.warning("401 Token expired — qaytadan login qilinmoqda...")
                        async with self._lock:
                            ok = await self.renew_token()
                        self.refresh_failed = not ok
                        if not ok:
                            return None
                        refreshed = True
                        continue

                    if resp.status != 200:
                        log.error("%s HTTP %s: %s", path, resp.status, (await resp.text())[:200])
                        return None

                    # Server tokenni uzaytirgan bo'lsa — shu yerda qabul qilamiz.
                    self._adopt_header_token(resp)
                    return await resp.json(content_type=None)

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Tarmoq uzildi — ortib boruvchi kutish bilan qayta urinamiz.
                if attempt == NETWORK_RETRIES:
                    log.error("%s: %s urinishdan keyin ham ulanib bo'lmadi (%s)", path, attempt, e)
                    return None
                wait = 2 ** (attempt - 1)
                log.warning("%s: ulanish xatosi (%s), %s soniyadan keyin qayta...", path, e, wait)
                await asyncio.sleep(wait)
            except Exception as e:
                log.exception("%s: kutilmagan xato: %s", path, e)
                return None

        return None

    # ----------------------------------------------------------- mahsulot --

    async def fetch_item(self, key: str) -> dict | None:
        """Bitta mahsulotni sku (key) bo'yicha oladi."""
        data = await self._get(
            "itemsAgr", {"priceType": "1", "limit": "1000", "offset": "0", "key": key}
        )
        if data is None:
            return None

        items = (data.get("data") or {}).get("items") or []
        if not items:
            log.warning("Mahsulot topilmadi (key=%s).", key)
            return None

        # Bir nechta natija kelsa — PRODUCT_ID bo'yicha aniqlaymiz.
        for item in items:
            if str(item.get("id")) == str(config.PRODUCT_ID):
                return item
        return items[0]

    async def fetch_all(self) -> dict[str, dict]:
        """Sozlangan barcha mahsulotlar: {key: item}. Olinmaganlari tushib qoladi."""
        result = {}
        for key in config.PRODUCT_KEYS:
            item = await self.fetch_item(key)
            if item is not None:
                result[key] = item
        return result
