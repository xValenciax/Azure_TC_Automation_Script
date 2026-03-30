"""
Azure DevOps Test Case Automation
==================================
Creates structured Test Case work items linked to parent User Stories.

Requirements:
    pip install requests

Usage:
    1. Set your credentials in the CONFIG block below.
    2. Define your test cases in the TEST_CASES list at the bottom.
    3. Run: python ado_test_case_creator.py
"""

import base64
import json
import pathlib
import xml.etree.ElementTree as ET
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
    required = {"organization", "project", "pat", "api_version"}
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

    # Step 1: verify auth and project access before touching work items
    print("\n[1/3] Verifying authentication and project access...")
    verify_auth(ORGANIZATION, PROJECT, PAT)

    # Step 2: load test cases from external file
    print(f"\n[2/3] Loading test cases from '{TEST_CASES_FILE.name}'...")
    TEST_CASES = load_test_cases()
    print(f"  Loaded {len(TEST_CASES)} test case(s).")

    # Step 3: create all test cases
    print(f"\n[3/3] Creating {len(TEST_CASES)} test case(s) in project '{PROJECT}'...")
    created = create_test_cases_bulk(
        test_cases=TEST_CASES,
        organization=ORGANIZATION,
        project=PROJECT,
        pat=PAT,
        api_version=API_VERSION,
    )

    # Step 4: print summary with direct links
    if created:
        print(f"\nSummary — open these in Azure DevOps to verify:")
        for item in created:
            print(f"  #{item['id']:>6}  {item['title'][:60]}")
            print(f"          {item['url']}")
