"""Trigger LinkedIn scraper on GitHub Actions and download results."""
import io
import json
import os
import time
import zipfile
import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPO")  # e.g. "vanshaj2023/linkedin"
WORKFLOW_FILE = "scrape.yml"

BASE = "https://api.github.com"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def trigger(task: str, keyword: str = "hiring", max_results: int = 5) -> bool:
    resp = requests.post(
        f"{BASE}/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        headers=HEADERS,
        json={
            "ref": "main",
            "inputs": {
                "task": task,
                "keyword": keyword,
                "max_results": str(max_results),
            },
        },
    )
    return resp.status_code == 204


def _latest_run_id() -> int | None:
    resp = requests.get(
        f"{BASE}/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs",
        headers=HEADERS,
        params={"per_page": 1, "event": "workflow_dispatch"},
    )
    runs = resp.json().get("workflow_runs", [])
    return runs[0]["id"] if runs else None


def wait_for_run(run_id: int, timeout: int = 600, poll: int = 15) -> str:
    """Poll until run finishes. Returns final status: success | failure | cancelled."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"{BASE}/repos/{REPO}/actions/runs/{run_id}", headers=HEADERS)
        data = resp.json()
        status = data.get("status")
        conclusion = data.get("conclusion")
        print(f"  run {run_id}: status={status} conclusion={conclusion}")
        if status == "completed":
            return conclusion or "unknown"
        time.sleep(poll)
    return "timeout"


def download_results(run_id: int) -> list:
    """Download the scrape-results artifact and return parsed JSON."""
    resp = requests.get(f"{BASE}/repos/{REPO}/actions/runs/{run_id}/artifacts", headers=HEADERS)
    artifacts = resp.json().get("artifacts", [])
    if not artifacts:
        return []

    artifact_id = artifacts[0]["id"]
    dl = requests.get(
        f"{BASE}/repos/{REPO}/actions/artifacts/{artifact_id}/zip",
        headers=HEADERS,
    )
    with zipfile.ZipFile(io.BytesIO(dl.content)) as z:
        for name in z.namelist():
            if name.endswith(".json"):
                return json.loads(z.read(name))
    return []


def run_remote_scrape(task: str, keyword: str = "hiring", max_results: int = 5) -> list:
    """One-call helper: trigger → wait → return results."""
    print(f"Triggering GitHub Actions: task={task} keyword={keyword}")
    if not trigger(task, keyword, max_results):
        raise RuntimeError("Failed to trigger workflow — check GITHUB_TOKEN and GITHUB_REPO")

    time.sleep(5)  # give GitHub a moment to register the run
    run_id = _latest_run_id()
    if not run_id:
        raise RuntimeError("Could not find the triggered run")

    print(f"Run ID: {run_id} — waiting for completion...")
    conclusion = wait_for_run(run_id)
    if conclusion != "success":
        raise RuntimeError(f"Run ended with: {conclusion}")

    results = download_results(run_id)
    print(f"Got {len(results)} results from GitHub Actions")
    return results


if __name__ == "__main__":
    import sys
    task = sys.argv[1] if len(sys.argv) > 1 else "feed"
    keyword = sys.argv[2] if len(sys.argv) > 2 else "hiring"
    data = run_remote_scrape(task, keyword, max_results=5)
    print(json.dumps(data, indent=2))
