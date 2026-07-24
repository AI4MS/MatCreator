"""Small backend-only client for the mat-agent-bench run-scoped API."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx


_BANK_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def sanitize_bank_id(value: str, *, prefix: str = "user-") -> str:
    """Derive a safe, stable custom-bank identifier for one MatCreator owner."""
    cleaned = _BANK_ID_RE.sub("-", value.strip()).strip("-.")[:64]
    return f"{prefix}{cleaned}" if cleaned else f"{prefix}unknown"


class BenchmarkApiError(RuntimeError):
    """A non-success response from the benchmark service."""

    def __init__(self, method: str, path: str, status_code: int, detail: str) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{method} {path} returned HTTP {status_code}: {detail or 'unknown error'}")


class BenchmarkClient:
    def __init__(self, server_url: str, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = server_url.rstrip("/")
        self.token = token
        self._client = client

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"X-API-Token": self.token}
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    async def register_token(
        server_url: str, *, client: httpx.AsyncClient | None = None
    ) -> str:
        """Request a development token from a bench server with registration enabled."""
        base_url = server_url.rstrip("/")
        if not base_url:
            raise ValueError("Benchmark server URL is required")
        if client is not None:
            response = await client.post(f"{base_url}/token")
        else:
            async with httpx.AsyncClient(timeout=30) as http_client:
                response = await http_client.post(f"{base_url}/token")
        if not response.is_success:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text
            raise BenchmarkApiError("POST", "/token", response.status_code, str(detail or "unknown error"))
        try:
            token = str(response.json()["token"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise BenchmarkApiError("POST", "/token", 502, "Benchmark token response is invalid") from exc
        if not token:
            raise BenchmarkApiError("POST", "/token", 502, "Benchmark token response is empty")
        return token

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self.base_url:
            raise ValueError("Benchmark server URL is required")
        if not self.token:
            raise ValueError("Benchmark API token is required")
        if self._client is not None:
            response = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(method, f"{self.base_url}{path}", **kwargs)
        if response.is_success:
            return response
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = response.text
        raise BenchmarkApiError(method, path, response.status_code, str(detail or "unknown error"))

    async def list_questions(self, **filters: Any) -> dict[str, Any]:
        params = {key: value for key, value in filters.items() if value not in (None, "", [])}
        response = await self._request("GET", "/questions", params=params)
        data = response.json()
        if isinstance(data, list):
            return {"questions": data, "total": None, "offset": None, "limit": None, "facets": {}}
        if not isinstance(data, dict):
            raise BenchmarkApiError("GET", "/questions", 502, "Benchmark catalog response is invalid")
        questions = data.get("items", data.get("questions", []))
        if not isinstance(questions, list):
            raise BenchmarkApiError("GET", "/questions", 502, "Benchmark catalog items are invalid")
        facets = data.get("facets", {})
        return {
            "questions": questions,
            "total": data.get("total"),
            "offset": data.get("offset"),
            "limit": data.get("limit"),
            "facets": facets if isinstance(facets, dict) else {},
        }

    async def create_session(self, model_name: str) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/sessions",
            headers=self._headers({"Content-Type": "application/json"}),
            json={"model_name": model_name},
        )
        return response.json()

    async def create_run(self, session_id: str, selection: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/runs",
            params={"session_id": session_id},
            headers=self._headers({"Content-Type": "application/json"}),
            json=selection,
        )
        return response.json()

    async def get_task(self, run_id: str, question_id: str) -> dict[str, Any]:
        response = await self._request(
            "GET",
            f"/runs/{run_id}/tasks/{question_id}",
            headers=self._headers(),
        )
        return response.json()

    async def download_data_file(self, question_id: str, filename: str, destination: Path) -> Path:
        response = await self._request("GET", f"/questions/{question_id}/data/{filename}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    async def submit_attempt(
        self,
        *,
        run_id: str,
        question_id: str,
        idempotency_key: str,
        meta: dict[str, Any],
        artifacts: list[Path],
    ) -> dict[str, Any]:
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("meta", (None, json.dumps(meta), "application/json"))
        ]
        for artifact in artifacts:
            files.append(
                (
                    "output",
                    (artifact.name, artifact.read_bytes(), "application/octet-stream"),
                )
            )
        response = await self._request(
            "POST",
            f"/submit/{question_id}",
            params={"run_id": run_id},
            headers=self._headers({"Idempotency-Key": idempotency_key}),
            files=files,
        )
        return response.json()

    async def list_banks(self) -> list[dict[str, Any]]:
        """List the custom question banks owned by this client's token."""
        response = await self._request("GET", "/banks", headers=self._headers())
        data = response.json()
        banks = data.get("banks", data) if isinstance(data, dict) else data
        if not isinstance(banks, list):
            raise BenchmarkApiError("GET", "/banks", 502, "Benchmark bank listing response is invalid")
        return banks

    async def create_bank(self, bank_id: str, *, display_name: str | None = None) -> dict[str, Any]:
        """Create a new token-owned custom question bank."""
        payload: dict[str, Any] = {"bank_id": bank_id}
        if display_name:
            payload["display_name"] = display_name
        response = await self._request(
            "POST",
            "/banks",
            headers=self._headers({"Content-Type": "application/json"}),
            json=payload,
        )
        return response.json()

    async def ensure_bank(self, bank_id: str, *, display_name: str | None = None) -> dict[str, Any]:
        """Create the bank if needed, or return the caller's existing bank record."""
        try:
            return await self.create_bank(bank_id, display_name=display_name)
        except BenchmarkApiError as exc:
            if exc.status_code != 409:
                raise
            for bank in await self.list_banks():
                if bank.get("bank_id") == bank_id:
                    return bank
            raise

    async def publish_question(
        self,
        bank_id: str,
        *,
        question: dict[str, Any],
        data_files: list[tuple[str, Path]] | None = None,
    ) -> dict[str, Any]:
        """Add one question, with its declared data files, to a custom question bank."""
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("question", (None, json.dumps(question), "application/json"))
        ]
        for declared_path, source in data_files or []:
            files.append(
                (
                    "data_files",
                    (declared_path, source.read_bytes(), "application/octet-stream"),
                )
            )
        response = await self._request(
            "POST",
            f"/banks/{bank_id}/questions",
            headers=self._headers(),
            files=files,
        )
        return response.json()

    async def get_grading_job(self, job_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/grading-jobs/{job_id}", headers=self._headers())
        return response.json()

    async def get_results(self, *, question_id: str, session_id: str) -> dict[str, Any]:
        response = await self._request(
            "GET",
            "/results",
            params={"session_id": session_id},
            headers=self._headers(),
        )
        payload = response.json()
        result = next(
            (
                item
                for item in payload.get("results", [])
                if item.get("question_id") == question_id
            ),
            None,
        )
        if result is None:
            raise BenchmarkApiError("GET", "/results", 404, f"No result found for '{question_id}'")
        normalized = dict(result)
        if "overall_weighted_score" in normalized:
            normalized["weighted_score"] = normalized["overall_weighted_score"]
        return normalized
