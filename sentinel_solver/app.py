"""Private, credential-free Chromium executor for the current Sentinel SDK.

Only the two challenge-only endpoints are exposed. The service never receives
an account access token, Cookie, prompt, or image. The caller must supply a
secret and run this container on a Docker-internal network.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright_stealth import Stealth
from pydantic import BaseModel, ConfigDict, Field

HOME = "https://chatgpt.com/"
LOADER = HOME + "backend-api/sentinel/sdk.js"
SDK_URL_RE = re.compile(r"https://chatgpt\.com/sentinel/[A-Za-z0-9_-]+/sdk\.js")
KEY = os.environ.get("SENTINEL_SOLVER_KEY", "").strip()
PROXY = os.environ.get("SENTINEL_SOLVER_PROXY", "").strip()
TIMEZONE = os.environ.get("SENTINEL_SOLVER_TIMEZONE", "").strip()
REFRESH_SECONDS = max(300, int(os.environ.get("SENTINEL_SOLVER_REFRESH_SECONDS", "1800")))

if not KEY:
    raise RuntimeError("SENTINEL_SOLVER_KEY must be set")


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProofChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: bool
    seed: str = Field(max_length=4096)
    difficulty: str = Field(max_length=64)


class TurnstileChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: bool
    dx: str = Field(max_length=262144)


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: str = Field(min_length=36, max_length=36)
    proofofwork: ProofChallenge
    turnstile: TurnstileChallenge


class BrowserSolver:
    def __init__(self) -> None:
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.generation = ""
        self.opened_at = 0.0
        self.lock = asyncio.Lock()
        self.user_agent = ""
        self.timezone = ""
        self.timezone_offset_min = 0

    async def start(self) -> None:
        self.playwright = await async_playwright().start()
        kwargs: dict = {
            "headless": True,
            "channel": "chromium",
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        }
        if PROXY:
            kwargs["proxy"] = {"server": PROXY}
        self.browser = await self.playwright.chromium.launch(**kwargs)
        major = self.browser.version.split(".", 1)[0]
        self.user_agent = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        )
        await self._open()

    async def stop(self) -> None:
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def _open(self) -> None:
        if self.context:
            await self.context.close()
        if not self.browser:
            raise RuntimeError("Chromium not started")
        context_kwargs: dict = {"locale": "en-US", "viewport": {"width": 1440, "height": 900},
                                "user_agent": self.user_agent}
        if TIMEZONE:
            context_kwargs["timezone_id"] = TIMEZONE
        self.context = await self.browser.new_context(**context_kwargs)
        await Stealth().apply_stealth_async(self.context)
        page = await self.context.new_page()
        response = await page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        # Cloudflare can initially return a challenge document with HTTP 403
        # and then reload the page. Do not mistake that interim status for the
        # final page state; give Chromium time to complete the browser flow.
        deadline = time.monotonic() + 60
        while True:
            title = await page.title()
            if not any(marker in title for marker in ("Just a moment", "请稍候", "Attention Required")):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"anonymous page unavailable: HTTP {response.status if response else 'none'}, title={title!r}")
            await asyncio.sleep(1)
        # An interim challenge document may still navigate after its title has
        # changed. Install the SDK only after that navigation settles.
        await asyncio.sleep(3)
        # Fetch the live SDK via this same anonymous Chromium page. Patch only
        # a private hook exposing the vendor's own p/PoW/challenge functions.
        loader = await page.evaluate("url => fetch(url).then(r => r.text())", LOADER)
        match = SDK_URL_RE.search(loader)
        if not match:
            raise RuntimeError("sentinel SDK URL missing")
        sdk = await page.evaluate("url => fetch(url).then(r => r.text())", match.group(0))
        if "var P=new _;" in sdk:
            marker, pow_name, turnstile_name = "var P=new _;", "P", "_n"
        elif "var E=new O;" in sdk:
            marker, pow_name, turnstile_name = "var E=new O;", "E", "Rn"
        else:
            raise RuntimeError(f"unexpected Sentinel SDK layout: length={len(sdk)}")
        if "var SentinelSDK=function" not in sdk or f"function {turnstile_name}(t,n)" not in sdk:
            raise RuntimeError("Sentinel challenge entrypoint missing")
        sdk = sdk.replace("var SentinelSDK=function", "var __PrivateSentinelSDK=function", 1)
        hook = """__MARKER__
          window.__privateSentinelSolver = {
            requirements: () => __POW__.getRequirementsToken(),
            challenge: async (data) => {
              const proof = data.proofofwork.required
                ? await __POW__.getEnforcementToken(data) : '';
              if (data.proofofwork.required && (!proof || !proof.startsWith('gAAAAAB') || !proof.endsWith('~S')))
                throw new Error('invalid proof result');
              D(data, proof);
              const turnstile = data.turnstile.required
                ? await __TURN__(data, data.turnstile.dx) : '';
              if (data.turnstile.required && !turnstile)
                throw new Error('empty turnstile result');
              return {proofofwork: proof, turnstile};
            }
          };""".replace("__MARKER__", marker).replace("__POW__", pow_name).replace("__TURN__", turnstile_name)
        sdk = sdk.replace(marker, hook, 1)
        await page.add_script_tag(content=sdk)
        if not await page.evaluate("typeof window.__privateSentinelSolver?.requirements === 'function'"):
            raise RuntimeError("Sentinel SDK hook not ready")
        fingerprint = await page.evaluate("""() => ({
          user_agent: navigator.userAgent,
          sec_ch_ua: (navigator.userAgentData?.brands || []).map(x => `"${x.brand}";v="${x.version}"`).join(', '),
          sec_ch_ua_platform: navigator.userAgentData?.platform || 'Linux',
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          timezone_offset_min: new Date().getTimezoneOffset()
        })""")
        self.page = page
        self.user_agent = fingerprint["user_agent"]
        self.sec_ch_ua = fingerprint["sec_ch_ua"]
        self.sec_ch_ua_platform = fingerprint["sec_ch_ua_platform"]
        self.timezone = fingerprint["timezone"]
        self.timezone_offset_min = fingerprint["timezone_offset_min"]
        self.generation = str(uuid.uuid4())
        self.opened_at = time.monotonic()

    async def _ready(self) -> Page:
        stale = self.page is None or self.page.is_closed() or time.monotonic() - self.opened_at > REFRESH_SECONDS
        if not stale:
            try:
                stale = not await self.page.evaluate("typeof window.__privateSentinelSolver?.requirements === 'function'")
            except Exception:
                stale = True
        if stale:
            await self._open()
        assert self.page is not None
        return self.page

    async def requirements(self) -> dict:
        async with self.lock:
            page = await self._ready()
            p = await page.evaluate("window.__privateSentinelSolver.requirements()")
            if not isinstance(p, str) or not p.startswith("gAAAAAC"):
                raise RuntimeError("invalid requirements p")
            return {
                "p": p,
                "generation": self.generation,
                "user_agent": self.user_agent,
                "sec_ch_ua": self.sec_ch_ua,
                "sec_ch_ua_platform": self.sec_ch_ua_platform,
                "timezone": self.timezone,
                "timezone_offset_min": self.timezone_offset_min,
            }

    async def challenge(self, request: ChallengeRequest) -> dict:
        async with self.lock:
            page = await self._ready()
            if request.generation != self.generation:
                raise HTTPException(status_code=409, detail="browser generation changed; restart prepare")
            return await page.evaluate(
                "data => window.__privateSentinelSolver.challenge(data)",
                {"proofofwork": request.proofofwork.model_dump(),
                 "turnstile": request.turnstile.model_dump()},
            )


solver = BrowserSolver()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await solver.start()
    yield
    await solver.stop()


app = FastAPI(title="private sentinel solver", lifespan=lifespan)


def authorize(authorization: str | None) -> None:
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, KEY):
        raise HTTPException(status_code=401, detail="invalid solver key")


@app.get("/health")
async def health(authorization: str | None = Header(default=None)) -> dict:
    authorize(authorization)
    return {"ready": solver.page is not None and not solver.page.is_closed(),
            "generation": solver.generation, "timezone": solver.timezone,
            "user_agent": solver.user_agent}


@app.post("/v1/requirements")
async def requirements(_: EmptyRequest, authorization: str | None = Header(default=None)) -> dict:
    authorize(authorization)
    try:
        return await solver.requirements()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"requirements failed: {exc}") from exc


@app.post("/v1/challenge")
async def challenge(body: ChallengeRequest, authorization: str | None = Header(default=None)) -> dict:
    authorize(authorization)
    try:
        return await solver.challenge(body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"challenge failed: {exc}") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
