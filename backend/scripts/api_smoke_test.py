from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import app


SAFE_METHODS = {"get"}
SKIP_PREFIXES = {"/openapi.json", "/docs", "/redoc"}


def _build_minimal_payload(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not schema:
        return None
    if schema.get("type") != "object":
        return None
    payload: dict[str, Any] = {}
    required = schema.get("required", [])
    props = schema.get("properties", {})
    for key in required:
        prop = props.get(key, {})
        t = prop.get("type")
        if t == "string":
            payload[key] = "x"
        elif t == "integer":
            payload[key] = 1
        elif t == "number":
            payload[key] = 1
        elif t == "boolean":
            payload[key] = False
        elif t == "array":
            payload[key] = []
        elif t == "object":
            payload[key] = {}
        else:
            payload[key] = None
    return payload


def _classify_status(code: int) -> str:
    if 200 <= code < 300:
        return "ok"
    if code in (401, 403):
        return "auth_required"
    if code == 404:
        return "not_found"
    if code == 405:
        return "method_not_allowed"
    if code == 422:
        return "validation_error"
    if code >= 500:
        return "server_error"
    return "other_client_error"


def _replace_path_params(path: str) -> str:
    return (
        path.replace("{document_id}", "1")
        .replace("{workspace_id}", "1")
        .replace("{job_id}", "1")
        .replace("{user_id}", "1")
        .replace("{chunk_id}", "1")
    )


def run() -> dict[str, Any]:
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    components = spec.get("components", {}).get("schemas", {})

    results: list[dict[str, Any]] = []
    for path, path_item in spec.get("paths", {}).items():
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            continue
        for method, operation in path_item.items():
            if method.lower() not in SAFE_METHODS:
                continue

            body = None
            content = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
            )
            schema = content.get("schema")
            if schema and "$ref" in schema:
                ref_name = schema["$ref"].split("/")[-1]
                schema = components.get(ref_name, {})
            body = _build_minimal_payload(schema)

            operation_security = operation.get("security", spec.get("security", []))
            requires_auth = bool(operation_security)
            request_path = _replace_path_params(path)

            try:
                response = client.request(method.upper(), request_path, json=body, timeout=15)
                status = response.status_code
                category = _classify_status(status)
                if category == "auth_required" and requires_auth:
                    category = "auth_required_expected"
                detail = ""
                if status >= 400:
                    try:
                        detail = str(response.json())[:400]
                    except Exception:
                        detail = response.text[:400]
            except Exception as exc:  # pragma: no cover
                status = 0
                category = "exception"
                detail = str(exc)

            results.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "request_path": request_path,
                    "requires_auth": requires_auth,
                    "status": status,
                    "category": category,
                    "detail": detail,
                }
            )

    summary = Counter(item["category"] for item in results)
    return {"total": len(results), "summary": dict(summary), "results": results}


if __name__ == "__main__":
    report = run()
    out = Path(__file__).resolve().parents[1] / "api_smoke_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"total={report['total']}")
    print(f"report={out}")
