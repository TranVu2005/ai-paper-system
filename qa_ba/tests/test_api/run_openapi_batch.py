from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ALL_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
DEFAULT_DANGEROUS_WRITE_PATH_PATTERNS = [
    r"/jobs(?:/|$)",
    r"/workers(?:/|$)",
    r"/recover(?:/|$)",
]


def resolve_json_pointer(root: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    current = root
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
        if current is None:
            return None
    return current


def sample_from_schema(schema: dict[str, Any] | None, openapi: dict[str, Any], depth: int = 0) -> Any:
    if schema is None or depth > 6:
        return None

    if "$ref" in schema:
        resolved = resolve_json_pointer(openapi, str(schema["$ref"]))
        if isinstance(resolved, dict):
            return sample_from_schema(resolved, openapi, depth + 1)
        return None

    for key in ("oneOf", "anyOf", "allOf"):
        candidates = schema.get(key)
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                return sample_from_schema(first, openapi, depth + 1)

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]

    schema_type = str(schema.get("type", "")).lower()
    if schema_type == "string":
        return "sample-text"
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        item = sample_from_schema(schema.get("items"), openapi, depth + 1)
        return [item]

    props = schema.get("properties")
    if isinstance(props, dict):
        required = schema.get("required") or []
        required_set = set(required if isinstance(required, list) else [])
        obj: dict[str, Any] = {}
        for k, v in props.items():
            if required_set and k not in required_set:
                continue
            if isinstance(v, dict):
                obj[k] = sample_from_schema(v, openapi, depth + 1)
        return obj

    return None


def status_label(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "PASS_OK"
    if status_code in {400, 401, 403, 404, 405, 409, 415, 422}:
        return "PASS_REACHABLE"
    if 500 <= status_code < 600:
        return "FAIL_SERVER"
    if status_code == 0:
        return "FAIL_CONNECT"
    return "FAIL_OTHER"


def merge_parameters(path_params: list[dict[str, Any]], op_params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for p in path_params + op_params:
        if not isinstance(p, dict):
            continue
        merged[f"{p.get('in','')}::{p.get('name','')}"] = p
    return list(merged.values())


def param_value(param: dict[str, Any], openapi: dict[str, Any]) -> Any:
    name = str(param.get("name", ""))
    schema = param.get("schema")
    if not isinstance(schema, dict):
        schema = {}
    if "$ref" in schema:
        resolved = resolve_json_pointer(openapi, str(schema["$ref"]))
        if isinstance(resolved, dict):
            schema = resolved

    if re.search(r"(?:^|_)id$", name, flags=re.IGNORECASE):
        return 1 if str(schema.get("type")) == "integer" else "1"

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]

    stype = str(schema.get("type", "")).lower()
    if stype == "integer":
        return 1
    if stype == "number":
        return 1.0
    if stype == "boolean":
        return "true"
    return "sample"


def http_call(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    timeout_sec: int,
    body_json: str | None = None,
) -> tuple[int, str]:
    data: bytes | None = None
    req_headers = dict(headers)
    if body_json is not None:
        data = body_json.encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=data, headers=req_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            return int(resp.status), ""
    except error.HTTPError as exc:
        return int(exc.code), str(exc)
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def load_openapi(base_url: str, openapi_path: str, timeout_sec: int) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{openapi_path}"
    req = request.Request(url=url, headers={"Accept": "application/json"}, method="GET")
    with request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def quick_invoke(
    *,
    base_url: str,
    method: str,
    path: str,
    operation_id: str,
    timeout_sec: int,
    include_write_methods: bool,
    exclude_methods: set[str],
    exclude_path_regexes: list[re.Pattern[str]],
    internal_token: str,
    bearer_token: str,
) -> dict[str, Any]:
    if not include_write_methods and method not in SAFE_METHODS:
        return {
            "method": method,
            "path": path,
            "operation_id": operation_id,
            "url": f"{base_url}{path}",
            "status_code": -1,
            "result": "SKIPPED_WRITE_METHOD",
            "error": "",
        }
    if method in exclude_methods:
        return {
            "method": method,
            "path": path,
            "operation_id": operation_id,
            "url": f"{base_url}{path}",
            "status_code": -1,
            "result": "SKIPPED_POLICY",
            "error": f"Excluded method: {method}",
        }
    for pat in exclude_path_regexes:
        if pat.search(path):
            return {
                "method": method,
                "path": path,
                "operation_id": operation_id,
                "url": f"{base_url}{path}",
                "status_code": -1,
                "result": "SKIPPED_POLICY",
                "error": f"Excluded path regex: {pat.pattern}",
            }

    headers = {"Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if path.startswith("/api/v1/internal") and internal_token:
        headers["x-internal-token"] = internal_token

    url_path = re.sub(r"\{[^}]+\}", "1", path)
    url = f"{base_url}{url_path}"
    body = "{}" if method in {"POST", "PUT", "PATCH"} else None
    status_code, err = http_call(method=method, url=url, headers=headers, timeout_sec=timeout_sec, body_json=body)
    return {
        "method": method,
        "path": path,
        "operation_id": operation_id,
        "url": url,
        "status_code": status_code,
        "result": status_label(status_code),
        "error": err,
    }


def run(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        openapi = load_openapi(base_url=base_url, openapi_path=args.openapi_path, timeout_sec=args.timeout_sec)
    except Exception as exc:  # noqa: BLE001
        print(f"Cannot load OpenAPI from {base_url}{args.openapi_path}. Ensure backend is running. Details: {exc}")
        return 1

    paths = openapi.get("paths", {})
    if not isinstance(paths, dict):
        print("Invalid OpenAPI: missing paths")
        return 1

    results: list[dict[str, Any]] = []
    exclude_methods = {m.strip().upper() for m in (args.exclude_method or []) if m.strip()}
    exclude_path_regexes: list[re.Pattern[str]] = []
    for rx in args.exclude_path_regex or []:
        try:
            exclude_path_regexes.append(re.compile(rx, flags=re.IGNORECASE))
        except re.error as exc:
            print(f"Invalid --exclude-path-regex pattern: {rx} ({exc})")
            return 1

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_params = path_item.get("parameters") or []
        if not isinstance(path_params, list):
            path_params = []

        for method_name, operation in path_item.items():
            method = str(method_name).upper()
            if method not in ALL_METHODS:
                continue
            if not isinstance(operation, dict):
                operation = {}

            operation_id = str(operation.get("operationId", ""))
            if not args.include_write_methods and method not in SAFE_METHODS:
                results.append(
                    {
                        "method": method,
                        "path": path,
                        "operation_id": operation_id,
                        "url": f"{base_url}{path}",
                        "status_code": -1,
                        "result": "SKIPPED_WRITE_METHOD",
                        "error": "",
                    }
                )
                continue
            if method in exclude_methods:
                results.append(
                    {
                        "method": method,
                        "path": path,
                        "operation_id": operation_id,
                        "url": f"{base_url}{path}",
                        "status_code": -1,
                        "result": "SKIPPED_POLICY",
                        "error": f"Excluded method: {method}",
                    }
                )
                continue
            excluded_by_path = next((pat.pattern for pat in exclude_path_regexes if pat.search(path)), None)
            if excluded_by_path:
                results.append(
                    {
                        "method": method,
                        "path": path,
                        "operation_id": operation_id,
                        "url": f"{base_url}{path}",
                        "status_code": -1,
                        "result": "SKIPPED_POLICY",
                        "error": f"Excluded path regex: {excluded_by_path}",
                    }
                )
                continue

            op_params = operation.get("parameters") or []
            if not isinstance(op_params, list):
                op_params = []
            params = merge_parameters(path_params, op_params)

            headers = {"Accept": "application/json"}
            if args.bearer_token:
                headers["Authorization"] = f"Bearer {args.bearer_token}"

            url_path = path
            query_pairs: list[str] = []
            for p in params:
                pin = str(p.get("in", ""))
                pname = str(p.get("name", ""))
                required = bool(p.get("required", False))
                pval = param_value(p, openapi)

                if pin == "path":
                    url_path = url_path.replace(f"{{{pname}}}", parse.quote(str(pval), safe=""))
                elif pin == "query" and required:
                    query_pairs.append(f"{parse.quote(pname, safe='')}={parse.quote(str(pval), safe='')}")
                elif pin == "header" and pname.lower() == "x-internal-token" and args.internal_token:
                    headers[pname] = args.internal_token

            if "/internal/" in path and args.internal_token and "x-internal-token" not in {k.lower(): v for k, v in headers.items()}:
                headers["x-internal-token"] = args.internal_token

            body_json: str | None = None
            request_body = operation.get("requestBody")
            if isinstance(request_body, dict):
                content = request_body.get("content")
                if isinstance(content, dict):
                    app_json = content.get("application/json")
                    if isinstance(app_json, dict):
                        schema = app_json.get("schema")
                        if isinstance(schema, dict):
                            sample = sample_from_schema(schema, openapi)
                            if sample is not None:
                                body_json = json.dumps(sample, ensure_ascii=False)

            url = f"{base_url}{url_path}"
            if query_pairs:
                url = f"{url}?{'&'.join(query_pairs)}"

            status_code, err = http_call(
                method=method,
                url=url,
                headers=headers,
                timeout_sec=args.timeout_sec,
                body_json=body_json,
            )
            results.append(
                {
                    "method": method,
                    "path": path,
                    "operation_id": operation_id,
                    "url": url,
                    "status_code": status_code,
                    "result": status_label(status_code),
                    "error": err,
                }
            )

    if args.include_internal_compat_alias:
        for path, path_item in paths.items():
            if not isinstance(path_item, dict) or not path.startswith("/api/v1/internal/ai/"):
                continue
            alias_path = re.sub(r"^/api/v1/internal/ai/", "/api/v1/internal/", path)
            for method_name, operation in path_item.items():
                method = str(method_name).upper()
                if method not in ALL_METHODS:
                    continue
                operation_id = ""
                if isinstance(operation, dict):
                    operation_id = str(operation.get("operationId", ""))
                results.append(
                    quick_invoke(
                        base_url=base_url,
                        method=method,
                        path=alias_path,
                        operation_id=f"compat::{operation_id}",
                        timeout_sec=args.timeout_sec,
                        include_write_methods=args.include_write_methods,
                        exclude_methods=exclude_methods,
                        exclude_path_regexes=exclude_path_regexes,
                        internal_token=args.internal_token,
                        bearer_token=args.bearer_token,
                    )
                )

    if args.include_hidden_routes:
        hidden_routes = [
            ("POST", "/api/v1/auth/forgot-password", "hidden::forgot_password_alias"),
            ("POST", "/api/v1/auth/verify-reset-code", "hidden::verify_reset_code_alias"),
            ("POST", "/api/v1/auth/reset-password", "hidden::reset_password_alias"),
            ("GET", "/api/v1/auth/google/start", "hidden::google_start_legacy"),
        ]
        for method, path, operation_id in hidden_routes:
            results.append(
                quick_invoke(
                    base_url=base_url,
                    method=method,
                    path=path,
                    operation_id=operation_id,
                    timeout_sec=args.timeout_sec,
                    include_write_methods=args.include_write_methods,
                    exclude_methods=exclude_methods,
                    exclude_path_regexes=exclude_path_regexes,
                    internal_token=args.internal_token,
                    bearer_token=args.bearer_token,
                )
            )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_file = out_dir / f"openapi_batch_result_{ts}.csv"
    json_file = out_dir / f"openapi_batch_result_{ts}.json"
    latest_csv = out_dir / "openapi_batch_result_latest.csv"
    latest_json = out_dir / "openapi_batch_result_latest.json"

    fields = ["method", "path", "operation_id", "url", "status_code", "result", "error"]
    with csv_file.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    latest_csv.write_text(csv_file.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")

    json_text = json.dumps(results, ensure_ascii=False, indent=2)
    json_file.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")

    total = len(results)
    ok = sum(1 for r in results if str(r.get("result", "")).startswith("PASS_"))
    fail = sum(1 for r in results if str(r.get("result", "")).startswith("FAIL_"))
    skipped = sum(1 for r in results if r.get("result") == "SKIPPED_WRITE_METHOD")

    print(f"Done. total={total} pass={ok} fail={fail} skipped={skipped}")
    print(f"CSV : {csv_file}")
    print(f"JSON: {json_file}")
    print(f"Latest CSV : {latest_csv}")
    print(f"Latest JSON: {latest_json}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch test API routes from OpenAPI.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--openapi-path", default="/openapi.json")
    parser.add_argument("--internal-token", default="")
    parser.add_argument("--bearer-token", default="")
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--include-write-methods", action="store_true")
    parser.add_argument("--include-hidden-routes", action="store_true")
    parser.add_argument("--include-internal-compat-alias", action="store_true")
    parser.add_argument(
        "--safe-write-profile",
        action="store_true",
        help="Apply safe write exclusions: DELETE method + /jobs|/workers|/recover paths.",
    )
    parser.add_argument(
        "--exclude-method",
        action="append",
        default=[],
        help="Exclude a method from execution. Can be used multiple times. Example: --exclude-method DELETE",
    )
    parser.add_argument(
        "--exclude-path-regex",
        action="append",
        default=[],
        help="Exclude routes whose path matches regex. Can be used multiple times.",
    )
    parser.add_argument("--out-dir", default="")
    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if args.safe_write_profile:
        existing_methods = {m.strip().upper() for m in args.exclude_method if m.strip()}
        if "DELETE" not in existing_methods:
            args.exclude_method.append("DELETE")
        existing_patterns = set(args.exclude_path_regex)
        for pat in DEFAULT_DANGEROUS_WRITE_PATH_PATTERNS:
            if pat not in existing_patterns:
                args.exclude_path_regex.append(pat)
    raise SystemExit(run(args))
