"""Strict challenge-only client for the private Chromium Sentinel service."""
from __future__ import annotations

import json
import base64
import os
import urllib.error
import urllib.request
from typing import Any

from utils.pow import verify_proof_token


class SentinelSolverError(RuntimeError):
    pass


class SentinelSolverClient:
    def __init__(self) -> None:
        self.url = os.environ.get("SENTINEL_SOLVER_URL", "").strip().rstrip("/")
        self.key = os.environ.get("SENTINEL_SOLVER_KEY", "").strip()
        if not self.url or not self.key:
            raise SentinelSolverError("SENTINEL_SOLVER_URL and SENTINEL_SOLVER_KEY are required")

    def _post(self, route: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url + route,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise SentinelSolverError(f"solver {route} HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise SentinelSolverError(f"solver {route} unavailable: {type(exc).__name__}") from exc
        if not isinstance(result, dict):
            raise SentinelSolverError(f"solver {route} returned non-object")
        return result

    def requirements(self, timeout: float = 90.0) -> dict[str, Any]:
        result = self._post("/v1/requirements", {}, timeout)
        p = str(result.get("p") or "")
        if not p.startswith("gAAAAAC") or not p.endswith("~S") or not result.get("generation"):
            raise SentinelSolverError("invalid requirements response")
        try:
            fields = json.loads(base64.b64decode(p[7:-2], validate=True))
        except (ValueError, base64.binascii.Error) as exc:
            raise SentinelSolverError("malformed requirements p") from exc
        if not isinstance(fields, list) or len(fields) != 25 or fields[4] != result.get("user_agent"):
            raise SentinelSolverError("requirements p/UA mismatch")
        return result

    def challenge(self, generation: str, prepare_data: dict[str, Any], timeout: float = 90.0) -> dict[str, str]:
        proof = prepare_data.get("proofofwork") or {}
        turnstile = prepare_data.get("turnstile") or {}
        # No prepare_token, account token, Cookie, or prompt leaves the app.
        request = {
            "generation": generation,
            "proofofwork": {
                "required": bool(proof.get("required")),
                "seed": str(proof.get("seed") or ""),
                "difficulty": str(proof.get("difficulty") or ""),
            },
            "turnstile": {
                "required": bool(turnstile.get("required")),
                "dx": str(turnstile.get("dx") or ""),
            },
        }
        result = self._post("/v1/challenge", request, timeout)
        proof_token = str(result.get("proofofwork") or "")
        turnstile_token = str(result.get("turnstile") or "")
        if proof.get("required") and not verify_proof_token(
            str(proof.get("seed") or ""), str(proof.get("difficulty") or ""), proof_token,
        ):
            raise SentinelSolverError("invalid proof token")
        if turnstile.get("required") and not turnstile_token:
            raise SentinelSolverError("missing turnstile token")
        return {"proofofwork": proof_token, "turnstile": turnstile_token}
