"""
Azure DevOps Test Case Automation
==================================
Creates structured Test Case work items linked to parent User Stories.
Optionally triggers an N8N workflow via webhook and saves the resulting
JSON output to a file.

Requirements:
    pip install requests

Usage:
    1. Set your credentials and optional N8N config in config.json.
    2. Define your test cases in test_cases.json.
    3. Run: python ado_test_case_creator.py

N8N integration (optional):
    Add an "n8n" block to config.json:
    {
      "n8n": {
        "webhook_url": "http://localhost:5678/webhook/your-path",
        "output_file":  "n8n_output.json"   // optional, default shown
      }
    }
    The script will POST run results to the N8N webhook, wait for the
    workflow to complete, and save the JSON response to 'output_file'.
"""

import base64
import json
import pathlib
import xml.etree.ElementTree as ET
from datetime import datetime
from html import escape
from typing import Optional
import requests

# ─────────────────────────────────────────────
# CONFIG — loaded from external JSON file
# ─────────────────────────────────────────────
CONFIG_FILE = pathlib.Path(__file__).parent / "config.json"


def load_config(path: pathlib.Path = CONFIG_FILE) -> dict:
    """
    Loads configuration from an external JSON file.
    Expected keys: organization, project, pat, api_version
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Create '{path.name}' next to this script with your Azure DevOps settings."
        )
    with path.open(encoding="utf-8") as f:
        cfg = json.load(f)
    required = {"organization", "project", "pat", "api_version", "user_story_id", "user_story_description"}
    missing = required - cfg.keys()
    if missing:
        raise ValueError(f"Missing required config keys in '{path.name}': {missing}")
    return cfg
# ─────────────────────────────────────────────


def build_auth_header(pat: str) -> dict:
    """
    Azure DevOps PAT auth encodes ':PAT' (note the leading colon, empty username).
    Using 'Bearer' or omitting the colon are both silent failures.
    """
    encoded = base64.b64encode(f":{pat}".encode("ascii")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def build_steps_xml(steps: list[dict]) -> str:
    """
    Azure DevOps stores test steps as a single XML string in
    Microsoft.VSTS.TCM.Steps. Each step's action and expected result
    are HTML-encoded strings inside <parameterizedString> elements.

    steps: list of {"action": str, "expected": str}

    Returns the XML string that Azure DevOps expects verbatim.
    """
    root = ET.Element("steps", attrib={"id": "0", "last": str(len(steps) + 1)})

    for i, step in enumerate(steps, start=2):
        step_el = ET.SubElement(root, "step", attrib={
            "id": str(i),
            "type": "ActionStep"
        })

        action_ps = ET.SubElement(step_el, "parameterizedString", attrib={"isformatted": "true"})
        # Azure DevOps wraps step content in a <DIV><P> structure
        action_html = f"<DIV><P>{escape(step.get('action', ''))}</P></DIV>"
        action_ps.text = action_html

        expected_ps = ET.SubElement(step_el, "parameterizedString", attrib={"isformatted": "true"})
        expected_html = f"<DIV><P>{escape(step.get('expected', ''))}</P></DIV>"
        expected_ps.text = expected_html

        ET.SubElement(step_el, "description")

    return ET.tostring(root, encoding="unicode")


def build_create_test_case_payload(
    title: str,
    steps: list[dict],
    parent_user_story_id: int,
    organization: str,
    project: str,
) -> list[dict]:
    """
    Builds the JSON Patch document for creating a Test Case work item.

    Key facts baked into this implementation:
    - Content body must be a JSON array (JSON Patch format), not a flat object.
    - Test steps live in Microsoft.VSTS.TCM.Steps as XML (see build_steps_xml).
    - Parent link uses relation type System.LinkTypes.Hierarchy-Reverse.
      Hierarchy-Reverse = "I am the child, target is the parent."
      Hierarchy-Forward = opposite — do not use for child→parent.
    - The relation URL must be the full Azure DevOps work item URL.
    """
    steps_xml = build_steps_xml(steps)
    parent_url = (
        f"https://dev.azure.com/{organization}/{project}/_apis/wit/workItems/{parent_user_story_id}"
    )

    return [
        {
            "op": "add",
            "path": "/fields/System.Title",
            "value": title,
        },
        {
            "op": "add",
            "path": "/fields/Microsoft.VSTS.TCM.Steps",
            "value": steps_xml,
        },
        {
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "System.LinkTypes.Hierarchy-Reverse",
                "url": parent_url,
                "attributes": {
                    "comment": "Linked to parent User Story via automation"
                },
            },
        },
    ]


def create_test_case(
    title: str,
    steps: list[dict],
    parent_user_story_id: int,
    organization: str,
    project: str,
    pat: str,
    api_version: str = "7.1",
) -> dict:
    """
    Creates a single Test Case work item in Azure DevOps.

    Uses POST (not PATCH) to /_apis/wit/workitems/$Test%20Case.
    Content-Type must be application/json-patch+json — not application/json.

    Returns the created work item dict on success.
    Raises RuntimeError with full diagnostic info on failure.
    """
    url = (
        f"https://dev.azure.com/{organization}/{project}"
        f"/_apis/wit/workitems/$Test%20Case"
        f"?api-version={api_version}"
    )

    headers = {
        **build_auth_header(pat),
        # This content type is mandatory. application/json returns 415.
        "Content-Type": "application/json-patch+json",
        "Accept": "application/json",
    }

    payload = build_create_test_case_payload(
        title=title,
        steps=steps,
        parent_user_story_id=parent_user_story_id,
        organization=organization,
        project=project,
    )

    response = requests.post(
        url,
        headers=headers,
        data=json.dumps(payload),   # data= not json= to preserve Content-Type header
        timeout=30,
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to create test case '{title}'.\n"
            f"  Status : {response.status_code}\n"
            f"  URL    : {url}\n"
            f"  Body   : {response.text}\n"
            f"  Payload: {json.dumps(payload, indent=2)}"
        )

    result = response.json()
    return {
        "id": result["id"],
        "title": result["fields"]["System.Title"],
        "url": result["_links"]["html"]["href"],
        "state": result["fields"]["System.State"],
    }


def create_test_cases_bulk(
    test_cases: list[dict],
    organization: str,
    project: str,
    pat: str,
    api_version: str = "7.1",
) -> list[dict]:
    """
    Creates multiple test cases sequentially and reports results.

    Each item in test_cases must have:
        - title            : str
        - steps            : list of {"action": str, "expected": str}
        - parent_story_id  : int  (Work Item ID of the parent User Story)

    Returns a list of result dicts. Failures are collected and reported
    at the end rather than aborting the entire batch.
    """
    results = []
    failures = []

    for i, tc in enumerate(test_cases, start=1):
        title = tc["title"]
        print(f"[{i}/{len(test_cases)}] Creating: '{title}' → parent #{tc['parent_story_id']}")

        try:
            result = create_test_case(
                title=title,
                steps=tc["steps"],
                parent_user_story_id=tc["parent_story_id"],
                organization=organization,
                project=project,
                pat=pat,
                api_version=api_version,
            )
            results.append(result)
            print(f"  ✓ Created Work Item #{result['id']} → {result['url']}")

        except RuntimeError as e:
            failures.append({"title": title, "error": str(e)})
            print(f"  ✗ FAILED: {e}")

    print(f"\n{'─' * 60}")
    print(f"  Created : {len(results)}")
    print(f"  Failed  : {len(failures)}")

    if failures:
        print("\nFailed items:")
        for f in failures:
            print(f"  - {f['title']}")
            print(f"    {f['error'][:300]}")

    return results


def trigger_n8n_workflow(
    webhook_url: str,
    output_file: pathlib.Path,
    user_story_id: str,
    user_story_desc: str,
    timeout: int = 120,
) -> Optional[dict]:
    """
    Triggers an N8N workflow via its webhook trigger node, waits for the
    workflow to finish, and saves the returned JSON output to *output_file*.

    How it works:
      1. POSTs a trigger request to the webhook URL.
      2. Blocks until N8N responds — the workflow executes synchronously
         from the script's perspective (N8N webhook node waits for the
         last node to finish before returning the response).
      3. Parses the JSON response body as the workflow output and writes
         it to *output_file* on disk.

    Returns the parsed JSON output dict on success, or None on error.
    Fails gracefully — a network error prints a warning but never crashes
    the main script.
    """
    print(f"\n[N8N] Triggering workflow → {webhook_url}")
    print(f"  Waiting for workflow to complete (timeout: {timeout}s)...")
    payload = {
        "UserStoryID": user_story_id,
        "UserStory": user_story_desc
    }
    try:
        response = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )

        if response.status_code not in (200, 201):
            print(
                f"  ⚠ N8N responded with HTTP {response.status_code}. "
                f"Check your webhook node is active (Production mode).\n"
                f"  Response: {response.text[:300]}"
            )
            return None

        print(f"  ✓ Workflow completed (HTTP {response.status_code})")

        # Parse the workflow's JSON output
        try:
            output_data = response.json()
            print(f"  [Debug] Response: {str(output_data)[:200]}")
        except ValueError:
            print(
                f"  ⚠ N8N response is not valid JSON — saving raw text instead.\n"
                f"  Raw response (first 300 chars): {response.text[:300]}"
            )
            output_file.write_text(response.text, encoding="utf-8")
            print(f"  📄 Raw response saved → {output_file.name}")
            return None

        # Check if response is empty or just the n8n webhook test payload
        if not output_data or (isinstance(output_data, dict) and ("webhookUrl" in output_data or "headers" in output_data)):
             print(
                 f"  ⚠ N8N returned its default/empty response instead of test cases.\n"
                 f"    Please check your N8N Webhook node settings."
             )

        # Save JSON output to file anyway so we can inspect it
        output_file.write_text(
            json.dumps(output_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  📄 Workflow JSON output saved to → {output_file.name}")
        return output_data

    except requests.exceptions.ConnectionError:
        print(
            "  ⚠ Could not reach N8N — is it running at the configured URL?\n"
            f"  URL tried: {webhook_url}"
        )
    except requests.exceptions.Timeout:
        print(
            f"  ⚠ N8N webhook timed out after {timeout}s.\n"
            "  The workflow may still be running. Increase 'timeout' in config.json if needed."
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ Unexpected error triggering N8N workflow: {e}")

    return None


def verify_auth(organization: str, project: str, pat: str) -> None:
    """
    Smoke-tests authentication and project access before running bulk creation.
    Queries the project endpoint — a cheap, read-only call.
    """
    url = f"https://dev.azure.com/{organization}/_apis/projects/{project}?api-version=7.1"
    headers = {**build_auth_header(pat), "Accept": "application/json"}
    response = requests.get(url, headers=headers, timeout=10)

    if response.status_code == 200:
        data = response.json()
        print(f"✓ Auth OK — project '{data['name']}' accessible (ID: {data['id']})")
    elif response.status_code == 401:
        raise RuntimeError(
            "Authentication failed (401). Check that:\n"
            "  1. The PAT is copied correctly with no leading/trailing whitespace.\n"
            "  2. The PAT has not expired.\n"
            "  3. The PAT has 'Work Items (Read, Write & Manage)' scope enabled."
        )
    elif response.status_code == 404:
        raise RuntimeError(
            f"Project not found (404). Check that:\n"
            f"  1. ORGANIZATION='{organization}' matches your ADO org URL slug.\n"
            f"  2. PROJECT='{project}' matches the project name exactly (case-sensitive)."
        )
    else:
        raise RuntimeError(
            f"Unexpected auth check response: {response.status_code}\n{response.text}"
        )


# ─────────────────────────────────────────────
# TEST CASES — loaded from external JSON file
# ─────────────────────────────────────────────
TEST_CASES_FILE = pathlib.Path(__file__).parent / "test_cases.json"


def load_test_cases(path: pathlib.Path = TEST_CASES_FILE) -> list[dict]:
    """
    Loads test cases from an external JSON file.
    The file must contain a JSON array of objects, each with:
        - title           : str
        - parent_story_id : int
        - steps           : list of {"action": str, "expected": str}
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Test cases file not found: {path}\n"
            f"Create '{path.name}' next to this script with your test case definitions."
        )
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in '{path.name}', got {type(data).__name__}.")
    return data
# ─────────────────────────────────────────────


if __name__ == "__main__":
    print("Azure DevOps Test Case Automation")
    print("=" * 60)

    # Step 0: load configuration
    cfg = load_config()
    ORGANIZATION  = cfg["organization"]
    PROJECT       = cfg["project"]
    PAT           = cfg["pat"]
    API_VERSION   = cfg["api_version"]
    N8N_CFG       = cfg.get("n8n", {})
    N8N_WEBHOOK   = N8N_CFG.get("webhook_url", "").strip()
    N8N_TIMEOUT   = int(N8N_CFG.get("timeout", 120))
    USER_STORY_ID = cfg["user_story_id"]
    USER_STORY_DESC = cfg["user_story_description"]

    # Step 1: trigger N8N workflow and capture its JSON output
    n8n_output = None
    if N8N_WEBHOOK:
        print(f"\n[1/4] Triggering N8N workflow for User Story #{USER_STORY_ID}...")
        n8n_output = trigger_n8n_workflow(
            webhook_url=N8N_WEBHOOK,
            output_file=TEST_CASES_FILE,  # Save directly to test_cases.json
            user_story_id=USER_STORY_ID,
            user_story_desc=USER_STORY_DESC,
            timeout=N8N_TIMEOUT,
        )
        
        if n8n_output and isinstance(n8n_output, list):
            print(f"  ✓ Successfully retrieved {len(n8n_output)} test cases from N8N.")
        else:
            print("\n  [!] N8N did not return a valid list of test cases in the response.")
            print("      Exiting to prevent creating malformed work items.")
            exit(1)
            
    else:
        print("\n[1/4] No webhook_url configured — skipping N8N workflow trigger.")
        print("  To enable: add an \"n8n\" block to config.json with your webhook_url.")

    # Step 2: verify auth and project access before touching work items
    print("\n[2/4] Verifying authentication and project access...")
    verify_auth(ORGANIZATION, PROJECT, PAT)

    # Step 3: load test cases from external file
    print(f"\n[3/4] Loading test cases from '{TEST_CASES_FILE.name}'...")
    TEST_CASES = load_test_cases()
    print(f"  Loaded {len(TEST_CASES)} test case(s).")

    # Step 4: create all test cases
    print(f"\n[4/4] Creating {len(TEST_CASES)} test case(s) in project '{PROJECT}'...")
    _failures: list[dict] = []
    created = create_test_cases_bulk(
        test_cases=TEST_CASES,
        organization=ORGANIZATION,
        project=PROJECT,
        pat=PAT,
        api_version=API_VERSION,
    )
    # Collect failures from test cases that were not created
    _created_titles = {r["title"] for r in created}
    _failures = [
        {"title": tc["title"], "error": "Failed — see output above"}
        for tc in TEST_CASES
        if tc["title"] not in _created_titles
    ]

    # Print summary with direct links
    if created:
        print(f"\nSummary — open these in Azure DevOps to verify:")
        for item in created:
            print(f"  #{item['id']:>6}  {item['title'][:60]}")
            print(f"          {item['url']}")
