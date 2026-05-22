from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class Actor:
    name: str
    headers: dict[str, str]


def _replace_path_params(path: str) -> str:
    # Replace templated ids with safe dummy ids.
    return (
        path.replace("{document_id}", "1")
        .replace("{workspace_id}", "1")
        .replace("{job_id}", "1")
        .replace("{user_id}", "1")
    )


def _classify(code: int) -> str:
    if 200 <= code < 300:
        return "ok"
    if code in (401, 403):
        return "auth_or_permission"
    if code == 404:
        return "not_found"
    if code == 422:
        return "validation_error"
    if code >= 500:
        return "server_error"
    return "other"


def _login(base_url: str, email: str, password: str, device_id: str) -> tuple[str, dict[str, Any]]:
    r = requests.post(
        f"{base_url}/api/v1/auth/login/email",
        json={"email": email, "password": password, "device_id": device_id},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data["access_token"], data


def _pick_actor(path: str, method: str, user: Actor, admin: Actor | None, internal: Actor) -> Actor:
    if path.startswith("/api/v1/internal/ai/"):
        return internal
    if path.startswith("/api/v1/admin/"):
        return admin or user
    return user


def run() -> dict[str, Any]:
    base_url = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    user_email = os.getenv("TEST_USER_EMAIL", "").strip()
    user_password = os.getenv("TEST_USER_PASSWORD", "").strip()
    admin_email = os.getenv("TEST_ADMIN_EMAIL", "").strip()
    admin_password = os.getenv("TEST_ADMIN_PASSWORD", "").strip()
    internal_token = os.getenv("INTERNAL_API_TOKEN", "").strip()

    if not user_email or not user_password:
        raise RuntimeError("Missing TEST_USER_EMAIL/TEST_USER_PASSWORD")
    if not internal_token:
        raise RuntimeError("Missing INTERNAL_API_TOKEN")

    user_token, user_login = _login(base_url, user_email, user_password, "api-test-user")
    user_actor = Actor("user", {"Authorization": f"Bearer {user_token}"})

    admin_actor: Actor | None = None
    admin_login: dict[str, Any] | None = None
    if admin_email and admin_password:
        admin_token, admin_login = _login(base_url, admin_email, admin_password, "api-test-admin")
        admin_actor = Actor("admin", {"Authorization": f"Bearer {admin_token}"})

    internal_actor = Actor("internal", {"x-internal-token": internal_token})

    spec = requests.get(f"{base_url}/openapi.json", timeout=20).json()
    results: list[dict[str, Any]] = []

    for path, path_item in spec.get("paths", {}).items():
        if path.startswith("/openapi.json") or path.startswith("/docs") or path.startswith("/redoc"):
            continue
        for method in path_item.keys():
            m = method.lower()
            if m not in {"get", "post", "put", "patch", "delete"}:
                continue
            full_path = _replace_path_params(path)
            actor = _pick_actor(path, m, user_actor, admin_actor, internal_actor)
            url = f"{base_url}{full_path}"
            try:
                # Safe smoke mode: send no body, do not upload files.
                resp = requests.request(method.upper(), url, headers=actor.headers, timeout=20)
                code = resp.status_code
                category = _classify(code)
                try:
                    detail = json.dumps(resp.json(), ensure_ascii=False)[:300]
                except Exception:
                    detail = (resp.text or "")[:300]
            except Exception as exc:
                code = 0
                category = "exception"
                detail = str(exc)

            results.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "request_path": full_path,
                    "actor": actor.name,
                    "status": code,
                    "category": category,
                    "detail": detail,
                }
            )

    summary = Counter(x["category"] for x in results)
    return {
        "base_url": base_url,
        "total": len(results),
        "summary": dict(summary),
        "user_login": {"email": user_email, "role": (user_login.get("user") or {}).get("role")},
        "admin_login": (
            {"email": admin_email, "role": (admin_login.get("user") or {}).get("role")}
            if admin_login
            else None
        ),
        "results": results,
    }


if __name__ == "__main__":
    report = run()
    output_path = os.getenv("API_AUTH_REPORT", "/tmp/api_authenticated_report.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"total={report['total']}")
    print(f"report={output_path}")
