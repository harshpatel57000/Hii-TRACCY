import time
import json
import httpx


def test_api_endpoint(
    method: str,
    url: str,
    token: str = "",
    body_json: str = "",
    runs: int = 1,
) -> dict:
    """Executes single or multi-run API checks and measures response latency."""
    headers = {"Content-Type": "application/json"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    payload = None
    if body_json.strip():
        try:
            payload = json.loads(body_json)
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"Invalid JSON body: {str(e)}"}

    latencies = []
    last_status = None
    last_body = ""

    try:
        with httpx.Client(timeout=15.0) as client:
            for _ in range(max(1, runs)):
                start = time.perf_counter()
                resp = client.request(method.upper(), url.strip(), headers=headers, json=payload)
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies.append(round(elapsed_ms, 2))
                last_status = resp.status_code
                last_body = resp.text[:2500]

        avg_ms = round(sum(latencies) / len(latencies), 2)
        min_ms = min(latencies)
        max_ms = max(latencies)

        return {
            "ok": True,
            "method": method.upper(),
            "url": url.strip(),
            "status": last_status,
            "runs": len(latencies),
            "avg_ms": avg_ms,
            "min_ms": min_ms,
            "max_ms": max_ms,
            "body": last_body,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}