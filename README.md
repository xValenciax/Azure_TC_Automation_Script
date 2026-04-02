# Azure DevOps Test Case Automation

A lightweight Python script that **bulk-creates structured Test Case work items** in Azure DevOps and automatically links them to a parent User Story — all from a simple JSON file.

---

## How It Works

1. The script reads your Azure DevOps credentials from **`config.json`**
2. It smoke-tests your authentication and confirms the target project is reachable
3. It reads your test case definitions from **`test_cases.json`**
4. It creates each test case as a Work Item of type **Test Case**, with all steps properly formatted as Azure DevOps XML, linked to the specified parent User Story

---

## Project Structure

```
Azure_TC_Automation_Script/
├── ado_test_case_creator.py   # Main script — do not edit unless customising behaviour
├── config.json                # Your Azure DevOps credentials and project settings
├── test_cases.json            # Your test case definitions
├── .gitignore                 # Excludes config.json from source control
└── README.md                  # This file
```

---

## Prerequisites

- **Python 3.10+** (uses `list[dict]` type hints introduced in 3.10)
- The `requests` library:

```bash
pip install requests
```

---

## Setup

### 1. Configure `config.json`

Fill in your Azure DevOps details:

```json
{
    "organization": "your-org-name",
    "project": "your-project-name",
    "pat": "your-personal-access-token",
    "api_version": "7.1"
}
```

| Key            | Description                                                               |
| -------------- | ------------------------------------------------------------------------- |
| `organization` | The slug from your ADO URL: `https://dev.azure.com/{organization}/`       |
| `project`      | The exact project name (case-sensitive) as it appears in Azure DevOps     |
| `pat`          | A Personal Access Token with **Work Items → Read & Write** scope          |
| `api_version`  | Azure DevOps REST API version — `7.1` is recommended and works by default |

#### How to create a PAT

1. Sign in to your Azure DevOps organization:
   `https://dev.azure.com/{your-organization}`

2. Click the **User settings** icon in the top-right corner of the page (the person icon next to your profile picture).

3. Select **Personal access tokens** from the dropdown menu.

4. Click **+ New Token**.

5. Fill in the token details:
   - **Name** — give it a recognizable name, e.g. `TC Automation Script`
   - **Organization** — select your organization from the dropdown
   - **Expiration** — choose a suitable expiry date (e.g. 30 or 90 days)
   - **Scopes** — select **Custom defined**, then scroll down to **Work Items** and check **Read & write**

6. Click **Create**.

7. **Copy the token immediately** — Azure DevOps will only show it once. Paste it into `config.json` as the value for `"pat"`.

> [!CAUTION]
> If you navigate away without copying the token, it cannot be retrieved. You will need to delete it and create a new one.

> [!CAUTION]
> `config.json` is excluded from Git via `.gitignore`. **Never commit it** — it contains your PAT which grants write access to your Azure DevOps project.

---

### 2. Define your test cases in `test_cases.json`

This file must be a JSON array. Each element represents one Test Case work item:

```json
[
    {
        "title": "Verify user can log in with valid credentials",
        "parent_story_id": 12345,
        "steps": [
            {
                "action": "Navigate to the login page",
                "expected": "Login page is displayed with username and password fields"
            },
            {
                "action": "Enter valid username and password, then click Login",
                "expected": "User is redirected to the dashboard"
            }
        ]
    },
    {
        "title": "Verify error message on invalid login attempt",
        "parent_story_id": 12345,
        "steps": [
            {
                "action": "Enter an invalid password and click Login",
                "expected": "An error message 'Invalid credentials' is displayed"
            }
        ]
    }
]
```

| Field              | Type      | Description                                                         |
| ------------------ | --------- | ------------------------------------------------------------------- |
| `title`            | `string`  | The title of the Test Case work item                                |
| `parent_story_id`  | `integer` | The Work Item ID of the parent User Story to link to                |
| `steps`            | `array`   | List of test steps                                                  |
| `steps[].action`   | `string`  | What the tester should do                                           |
| `steps[].expected` | `string`  | The expected result (can be an empty string `""` if not applicable) |

> [!TIP]
> You can have **multiple test cases** in the array, each targeting the **same or different** parent User Story IDs.

---

## Running the Script

```bash
python ado_test_case_creator.py
```

### Example Output

```
Azure DevOps Test Case Automation
============================================================

[1/3] Verifying authentication and project access...
✓ Auth OK — project 'NDCIntegrations' accessible (ID: 9395195f-...)

[2/3] Loading test cases from 'test_cases.json'...
  Loaded 2 test case(s).

[3/3] Creating 2 test case(s) in project 'NDCIntegrations'...
[1/2] Creating: 'Verify user can log in with valid credentials' → parent #12345
  ✓ Created Work Item #40937 → https://dev.azure.com/...
[2/2] Creating: 'Verify error message on invalid login attempt' → parent #12345
  ✓ Created Work Item #40938 → https://dev.azure.com/...

────────────────────────────────────────────────────────────
  Created : 2
  Failed  : 0

Summary — open these in Azure DevOps to verify:
  # 40937  Verify user can log in with valid credentials
           https://dev.azure.com/...
  # 40938  Verify error message on invalid login attempt
           https://dev.azure.com/...
```

---

## Troubleshooting

| Error                          | Likely Cause                                             | Fix                                                                               |
| ------------------------------ | -------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `Authentication failed (401)`  | PAT is wrong, expired, or has insufficient scope         | Regenerate your PAT with **Work Items → Read & Write** scope                      |
| `Project not found (404)`      | `organization` or `project` is misspelled                | Check them against your ADO URL — both are case-sensitive                         |
| `Config file not found`        | `config.json` is missing                                 | Create it next to the script using the template above                             |
| `Test cases file not found`    | `test_cases.json` is missing                             | Create it with at least one test case entry                                       |
| `Missing required config keys` | A key is missing from `config.json`                      | Ensure all four keys are present: `organization`, `project`, `pat`, `api_version` |
| `JSONDecodeError`              | `config.json` or `test_cases.json` contains invalid JSON | Validate the file at [jsonlint.com](https://jsonlint.com)                         |

---

## Notes

- Test cases are created **sequentially**, not in parallel. If one fails, the rest still run.
- Each test case is linked to its parent User Story using a **Hierarchy-Reverse** relation (child → parent).
- Test steps are stored internally as XML in the `Microsoft.VSTS.TCM.Steps` field, which is the format Azure DevOps expects.
- The Azure DevOps REST API version is configurable via `api_version` in `config.json`. Version `7.1` is the current stable default.
