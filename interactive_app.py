import os
import sys
import getpass
from ado_test_case_creator import (
    trigger_n8n_workflow,
    verify_auth,
    load_test_cases,
    create_test_cases_bulk,
    TEST_CASES_FILE
)

# Enable ANSI escape sequences on Windows
os.system('')

# Terminal Colors
CYAN = '\033[96m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
MAGENTA = '\033[95m'
RESET = '\033[0m'
BOLD = '\033[1m'

# Fixed N8N Configurations
N8N_WEBHOOK = "https://n8n-latest-45jk.onrender.com/webhook/3003f6ba-db33-4dcb-b0d7-d81beeedae33"
N8N_TIMEOUT = 120
API_VERSION = "7.1"

def print_header(title):
    print(f"\n{MAGENTA}{BOLD}{'━' * 60}{RESET}")
    print(f"{CYAN}{BOLD}{title.center(60)}{RESET}")
    print(f"{MAGENTA}{BOLD}{'━' * 60}{RESET}\n")

def main():
    print_header("✨ Azure DevOps Test Case Automation ✨")
    
    print(f"{CYAN}Please provide the following details to begin:{RESET}\n")
    
    try:
        organization = input(f"{YELLOW}▶ 1. Azure Organization Name: {RESET}").strip()
        project = input(f"{YELLOW}▶ 2. Azure Project Name: {RESET}").strip()
        pat = getpass.getpass(f"{YELLOW}▶ 3. Azure PAT (input hidden): {RESET}").strip()
        user_story_id = input(f"{YELLOW}▶ 4. User Story ID: {RESET}").strip()
        
        print(f"{YELLOW}▶ 5. User Story Description & Acceptance Criteria:{RESET}")
        print(f"  {CYAN}(Press Enter after typing, or provide a single line summary){RESET}")
        user_story_desc = input(f"  {YELLOW}❯ {RESET}").strip()
        
        if not all([organization, project, pat, user_story_id, user_story_desc]):
            print(f"\n{RED}✖ All fields are required. Exiting...{RESET}")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print(f"\n\n{RED}✖ Operation cancelled by user. Exiting...{RESET}")
        sys.exit(0)

    print_header("🚀 Starting Automation Process 🚀")

    # Step 1: trigger N8N workflow (N8N Configs are hardcoded to the Render URL)
    print(f"{CYAN}[1/4] Triggering N8N Workflow for User Story #{user_story_id}...{RESET}")
    n8n_output = trigger_n8n_workflow(
        webhook_url=N8N_WEBHOOK,
        output_file=TEST_CASES_FILE,
        user_story_id=user_story_id,
        user_story_desc=user_story_desc,
        timeout=N8N_TIMEOUT,
    )
    
    if n8n_output is not None:
        if isinstance(n8n_output, list):
            print(f"  {GREEN}✓ Successfully retrieved {len(n8n_output)} test cases from N8N.{RESET}")
        else:
            print(f"  {GREEN}✓ Workflow triggered successfully and saved response.{RESET}")
            
        print(f"\n  {MAGENTA}⏸ [PAUSE]{RESET} The script has paused so you can manually review/edit '{TEST_CASES_FILE.name}'.")
        input(f"  {YELLOW}Press ENTER when you are ready to continue and upload to Azure DevOps...{RESET}")
    else:
        print(f"\n  {RED}✖ N8N did not return a valid response or timed out.{RESET}")
        print(f"      {RED}Exiting to prevent creating malformed work items.{RESET}")
        sys.exit(1)

    # Step 2: verify auth
    print(f"\n{CYAN}[2/4] Verifying Authentication and Project Access...{RESET}")
    try:
        verify_auth(organization, project, pat)
    except RuntimeError as e:
        print(f"\n{RED}✖ Authentication Error: {e}{RESET}")
        sys.exit(1)

    # Step 3: load test cases
    print(f"\n{CYAN}[3/4] Loading Test Cases from '{TEST_CASES_FILE.name}'...{RESET}")
    try:
        TEST_CASES = load_test_cases()
        print(f"  {GREEN}✓ Loaded {len(TEST_CASES)} test case(s).{RESET}")
    except Exception as e:
        print(f"\n{RED}✖ Error loading test cases: {e}{RESET}")
        sys.exit(1)

    # Step 4: create all test cases
    print(f"\n{CYAN}[4/4] Creating {len(TEST_CASES)} Test Case(s) in project '{project}'...{RESET}")
    created = create_test_cases_bulk(
        test_cases=TEST_CASES,
        organization=organization,
        project=project,
        pat=pat,
        api_version=API_VERSION,
    )

    # Check for failures that the original function collected
    _created_titles = {r["title"] for r in created}
    _failures = [
        {"title": tc["title"], "error": "Failed — see output above"}
        for tc in TEST_CASES
        if tc["title"] not in _created_titles
    ]

    print_header("📊 Summary 📊")
    
    if created:
        print(f"{GREEN}Successfully created {len(created)} work item(s):{RESET}")
        for item in created:
            print(f"  {CYAN}#{item['id']:>6}{RESET}  {item['title'][:60]}")
            print(f"          {item['url']}")
            
    if _failures:
        print(f"\n{RED}Failed to create {len(_failures)} work item(s):{RESET}")
        for f in _failures:
            print(f"  {RED}- {f['title']}{RESET}")

    print(f"\n{GREEN}{BOLD}Automation Complete!{RESET}\n")

if __name__ == "__main__":
    main()
