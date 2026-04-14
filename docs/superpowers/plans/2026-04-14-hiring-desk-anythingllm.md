# Hiring Desk AnythingLLM Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision the public hiring desk chatbot on AnythingLLM at `chat.eduserver.au` for all 6 companies, harden `setup-chatbots.py` so it's namespace-safe and non-destructive, embed the chat widget on every company's careers page, and verify end-to-end on at least one live company site.

**Architecture:** This plan only touches Layer 1 of the three-layer architecture from the design spec. Completely independent of Plan 1 (Layer 3 team communications) — the two plans can run in any order, or in parallel, without conflict. Nothing in workready-api talks to AnythingLLM at runtime; the embed widget on each company's static careers page talks directly to AnythingLLM's embed endpoint in the browser. If AnythingLLM goes down, the simulation's in-sim conversations keep working perfectly.

**Tech Stack:** Python 3 + `requests` (setup script), YAML (UUID registry), Jinja2 (company site templates), AnythingLLM API v1 (external), AnythingLLM chat widget (JavaScript embed loaded from the AnythingLLM host).

**Source spec:** `docs/superpowers/specs/2026-04-14-workready-team-communications-design.md` (Subsystem F)
**Related:** Plan 1 at `2026-04-14-team-communications-layer3.md` (independent, both can execute in any order)
**Future work:** `docs/superpowers/specs/2026-04-14-workready-team-communications-future-work.md`

---

## Safety constraints

**CRITICAL — read before writing any code:**

1. **`chat.eduserver.au` is a shared AnythingLLM instance** used for other projects outside WorkReady. This plan must NEVER delete, rename, or modify any workspace that isn't clearly WorkReady-managed.
2. **Namespace:** WorkReady workspaces are named `workready-<slug>-hiring`. Nothing else in the instance uses this prefix.
3. **Marker:** every workspace the hardened script creates has `[workready-managed] Do not remove this line.` as the very first line of its system prompt. Before updating an existing workspace with a matching name, the script MUST verify the marker is present. If the marker is missing, the script logs a warning naming the workspace and SKIPS it — no updates, no deletes.
4. **Delete/rebuild pattern:** when refreshing a workspace's RAG document, the script deletes the *documents inside* the workspace and re-uploads fresh ones. It NEVER deletes the workspace itself. The workspace's slug and the embed's UUID are preserved across refreshes, so the live widget on a company careers page stays bound to the same embed forever.
5. **Failure mode for partial runs:** if the script fails partway through (network error, auth error, one workspace broken), it writes whatever UUIDs it already harvested to `embed-uuids.yaml`, logs which companies succeeded/failed, and exits cleanly. Re-running is idempotent.

These constraints are absolute. Any task in this plan that seems to violate them is a bug in the plan — stop and escalate before implementing it.

---

## Conventions for this plan

- **Working directories:**
  - Setup script work: `/Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy`
  - Per-company site work: `/Users/michael/Projects/loco-lab/loco-ensyo/<company-slug>`
  - Each `cd` in a task's steps is absolute.
- **This plan is operator-facing, not TDD-driven.** Most blocks involve running an existing script (hardened), verifying its output, and committing the result. The one block with real test coverage is Block 1 (script hardening) where we add unit-ish smokes for the marker check and dry-run coverage.
- **Stub mode does NOT apply to Block 2** — that block runs the real script against the real AnythingLLM server and requires a real `ANYTHINGLLM_API_KEY`. All other blocks run offline.
- **Live API tests live in Block 2 only.** Block 1 uses a mocked HTTP layer so the hardening tests can run without network.
- **Commits land in multiple repos.** workready-deploy is one repo; each of the 6 company sites is its own repo. Commit per repo, push per repo.

---

## File structure

### Modified files

| File | Change |
|---|---|
| `workready-deploy/setup-chatbots.py` | Add marker constant + marker check. Refactor workspace update to full-refresh (delete docs → re-upload → reassign → update prompt). Extend `--dry-run` to cover the full update path without network calls. Add `embed-uuids.yaml` write-on-success. |

### New files (central)

| File | Purpose |
|---|---|
| `workready-deploy/embed-uuids.yaml` | Source of truth for embed UUIDs per company. Written by the setup script on each successful run. Committed to git. |
| `workready-deploy/tests/test_setup_chatbots.py` | Unit tests for the marker check, dry-run coverage, and fail-safe paths. Uses `unittest.mock` to stub the AnythingLLM API — never hits the network. |

### New files (per company — replicated across all 6)

| File | Purpose |
|---|---|
| `<company>/site/templates/_chat_widget.html.j2` | Jinja partial that renders the AnythingLLM embed script tag. Takes `embed_uuid` and `anythingllm_base_url` from template context. |

### Modified files (per company — replicated across all 6)

| File | Change |
|---|---|
| `<company>/site/build.py` | Read `../../workready-deploy/embed-uuids.yaml`, extract this company's embed UUID, pass it into the Jinja context as `embed_uuid`. If the yaml is missing or this company's UUID is blank, render the widget as empty (graceful degradation). |
| `<company>/site/templates/careers.html.j2` | `{% include '_chat_widget.html.j2' %}` just before `</body>` (or wherever the other scripts load). |

### The 6 company repo paths

For reference throughout the plan:

```
/Users/michael/Projects/loco-lab/loco-ensyo/ironvale-resources
/Users/michael/Projects/loco-lab/loco-ensyo/nexuspoint-systems
/Users/michael/Projects/loco-lab/loco-ensyo/meridian-advisory
/Users/michael/Projects/loco-lab/loco-ensyo/metro-council-wa
/Users/michael/Projects/loco-lab/loco-ensyo/southern-cross-financial
/Users/michael/Projects/loco-lab/loco-ensyo/horizon-foundation
```

---

## Block 1 — Harden `setup-chatbots.py`

Every code change in this block is in `workready-deploy/`. The block ends with a mocked unit-test run that verifies the hardened behaviour without touching the real AnythingLLM server.

### Task 1.1: Add the marker constant + marker helpers

**Files:**
- Modify: `workready-deploy/setup-chatbots.py`

- [ ] **Step 1: Locate the configuration section**

Open `workready-deploy/setup-chatbots.py`. Find the `COMPANIES` dict near the top (around line 73-104). The marker work will sit right after this dict.

- [ ] **Step 2: Add the marker constant and helpers**

Insert after the `COMPANIES` dict:

```python
# ============================================================
# Namespace safety: marker-based workspace ownership
# ============================================================

# Every workspace WorkReady creates starts its system prompt with this
# exact line. Updates refuse to touch any workspace that doesn't carry
# this marker — that way a name collision with a user-managed workspace
# can never cause us to overwrite someone else's work.
WORKREADY_MARKER = "[workready-managed] Do not remove this line."


def prompt_with_marker(body: str) -> str:
    """Prefix a system prompt body with the WorkReady marker."""
    return f"{WORKREADY_MARKER}\n\n{body.lstrip()}"


def workspace_has_marker(workspace: dict) -> bool:
    """Check if an existing workspace's system prompt carries the marker.

    AnythingLLM returns the workspace object with an `openAiPrompt` key
    (the system prompt text). We check the very first line.
    """
    prompt = workspace.get("openAiPrompt") or ""
    first_line = prompt.split("\n", 1)[0].strip()
    return first_line == WORKREADY_MARKER
```

- [ ] **Step 3: Update the prompt builder to include the marker**

Find `build_system_prompt` (around line 169). Change the end of the function to wrap its return value in the marker helper:

```python
    # Change this at the end of build_system_prompt:
    prompt = f"""You are the hiring desk assistant at {company_name}.
... (existing prompt body) ...
"""
    return prompt_with_marker(prompt.strip())
```

Keep the existing prompt body intact — the only change is wrapping the final return value.

- [ ] **Step 4: Commit**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
git add setup-chatbots.py
git commit -m "setup-chatbots: add namespace-safety marker + helpers"
```

### Task 1.2: Refactor workspace update to full-refresh

**Files:**
- Modify: `workready-deploy/setup-chatbots.py`

- [ ] **Step 1: Find the existing `create_or_update_workspace`**

It's around line 334. The existing update branch looks like:

```python
if ws:
    log(f"Updating existing workspace: {ws_name}")
    result = api_post(f"/workspace/{ws_name}/update", {
        "openAiPrompt": prompt,
        "openAiTemp": 0.7,
        "openAiHistory": 20,
    })
    return result.get("workspace", {})
```

- [ ] **Step 2: Replace the update branch with marker-check + full-refresh**

Replace the `if ws:` block above with:

```python
if ws:
    if not workspace_has_marker(ws):
        fail(f"Workspace exists but has no WorkReady marker: {ws_name}")
        fail(f"  Refusing to touch it — manual inspection required.")
        fail(f"  (If this workspace really is ours, restore the marker "
             f"at the start of its system prompt and re-run.)")
        return None  # signal "skipped"

    log(f"Updating existing workspace: {ws_name} (marker verified)")

    # Full-refresh: delete existing documents, re-upload fresh, reassign.
    # Preserves workspace UUID + embed UUID so the live widget keeps working.
    existing_docs = ws.get("documents", []) or []
    ws_slug = ws.get("slug", ws_name)

    for doc in existing_docs:
        doc_location = doc.get("docpath") or doc.get("location")
        if not doc_location:
            continue
        # The AnythingLLM API removes docs from a workspace via update-embeddings
        unassign_result = api_post(
            f"/workspace/{ws_slug}/update-embeddings",
            {"adds": [], "deletes": [doc_location]},
        )
        if unassign_result.get("workspace"):
            ok(f"Unassigned document: {doc_location}")
        else:
            fail(f"Unassign failed: {unassign_result}")

    # Now update the system prompt with the (marker-wrapped) fresh prompt
    result = api_post(f"/workspace/{ws_name}/update", {
        "openAiPrompt": prompt,
        "openAiTemp": 0.7,
        "openAiHistory": 20,
    })
    return result.get("workspace", {})
```

- [ ] **Step 3: Import check**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
python3 -c "
import ast
with open('setup-chatbots.py') as f:
    tree = ast.parse(f.read())
print('OK: setup-chatbots.py parses cleanly')
"
```

Expected: `OK: setup-chatbots.py parses cleanly`

- [ ] **Step 4: Commit**

```bash
git add setup-chatbots.py
git commit -m "setup-chatbots: marker check + full-refresh update (preserve UUID)"
```

### Task 1.3: Update `setup_company` to unify create + update + RAG reupload

**Files:**
- Modify: `workready-deploy/setup-chatbots.py`

- [ ] **Step 1: Find `setup_company`**

It's around line 445. The existing flow is:

1. Build prompt + RAG doc
2. `create_or_update_workspace` (creates or updates prompt)
3. `create_embed`
4. `upload_rag_document` (unconditionally uploads, even if workspace already had the doc)

This flow needs three changes:

- Respect the "skipped" signal from marker-check failures (don't try to embed or upload RAG for a skipped workspace)
- After a full-refresh update, always upload a fresh RAG doc (the old docs were just unassigned)
- On first creation, the embed UUID is new; on subsequent runs, the embed already exists and we need to discover its UUID, not create a new one

- [ ] **Step 2: Replace `setup_company` body**

Find the existing `def setup_company(slug):` and replace the function body:

```python
def setup_company(slug: str) -> dict | None:
    """Full setup for one company: workspace + embed + RAG doc.

    Idempotent. Running twice is safe:
    - First run: creates workspace, creates embed, uploads RAG.
    - Second run: verifies marker, refreshes RAG (delete old + upload
      new), updates system prompt. Preserves workspace + embed UUIDs.

    Returns a dict with company name, workspace slug, embed UUID, and
    domain on success. Returns None if the workspace exists but is
    not workready-managed (marker missing).
    """
    company_info = COMPANIES.get(slug)
    if not company_info:
        fail(f"Unknown company: {slug}")
        return None

    print(f"\n{'=' * 60}")
    log(f"Setting up {company_info['name']} ({slug})")
    print(f"{'=' * 60}")

    # Load data
    data = load_company_data(slug)
    if not data["brief"]:
        fail(f"No brief.yaml found for {slug} in {SITES_DIR / slug}")
        return None

    # Create/update workspace (with marker check)
    ws = create_or_update_workspace(slug, company_info, data)
    if ws is None:
        # Marker missing → skipped. Not a failure, but nothing to do.
        return {"company": company_info["name"], "status": "skipped_no_marker"}

    ws_slug = ws.get("slug", company_info["workspace_name"])

    # Discover-or-create embed — never create a second embed for the same workspace
    embed_uuid = find_or_create_embed(ws_slug, company_info)

    # Upload fresh RAG document (old ones were already unassigned in the update path)
    upload_rag_document(ws_slug, slug, data)

    return {
        "company": company_info["name"],
        "workspace_slug": ws_slug,
        "embed_uuid": embed_uuid,
        "domain": company_info["domain"],
        "status": "ok",
    }
```

- [ ] **Step 3: Add the `find_or_create_embed` helper**

Right before `setup_company` in the file, add:

```python
def find_or_create_embed(ws_slug: str, company_info: dict) -> str | None:
    """Return the existing embed UUID for this workspace, or create one.

    AnythingLLM doesn't dedupe embed creation — every /embed/new call
    creates a new embed row even on the same workspace. This helper
    checks the existing embeds first and returns the existing UUID if
    one exists, so re-running the script doesn't proliferate embeds.
    """
    # Try the per-workspace embeds endpoint
    try:
        existing = api_get(f"/workspace/{ws_slug}/embed")
        embeds = existing.get("embeds") or existing.get("embed") or []
        if isinstance(embeds, dict):
            embeds = [embeds]
        if embeds:
            uuid = embeds[0].get("uuid") or embeds[0].get("id")
            if uuid:
                ok(f"Found existing embed: {uuid}")
                return uuid
    except Exception:
        pass

    # Fallback to global embed list filtered by workspace slug
    try:
        all_embeds = api_get("/embed").get("embeds", []) or []
        for e in all_embeds:
            if e.get("workspaceSlug") == ws_slug or e.get("workspace_slug") == ws_slug:
                uuid = e.get("uuid") or e.get("id")
                if uuid:
                    ok(f"Found existing embed via /embed list: {uuid}")
                    return uuid
    except Exception:
        pass

    # No existing embed found → create a new one
    log(f"Creating embed for {ws_slug}")
    result = api_post(f"/workspace/{ws_slug}/embed/new", {
        "chat_mode": "query",
        "allowlist_domains": EMBED_ALLOWLIST,
    })
    if not result.get("embed"):
        result = api_post("/embed/new", {
            "workspaceSlug": ws_slug,
            "chat_mode": "query",
            "allowlist_domains": EMBED_ALLOWLIST,
        })

    embed = result.get("embed", {})
    uuid = embed.get("uuid", "")
    if uuid:
        ok(f"Embed UUID: {uuid}")
        return uuid

    fail(f"Embed creation failed: {result}")
    return None
```

- [ ] **Step 4: Remove the old `create_embed` function**

The old `create_embed` function (around line 376) is now superseded by `find_or_create_embed`. Delete the old function entirely to avoid two codepaths.

- [ ] **Step 5: Commit**

```bash
git add setup-chatbots.py
git commit -m "setup-chatbots: unify setup_company with idempotent find_or_create_embed"
```

### Task 1.4: Add embed-uuids.yaml write

**Files:**
- Modify: `workready-deploy/setup-chatbots.py`

- [ ] **Step 1: Add the YAML write helpers**

Near the top of the file (after `load_env_file` and before `API_KEY`), add:

```python
EMBED_UUIDS_FILE = Path(__file__).parent / "embed-uuids.yaml"


def load_existing_uuids() -> dict:
    """Load the current embed UUIDs file. Return {} if missing."""
    if not EMBED_UUIDS_FILE.is_file():
        return {}
    try:
        return yaml.safe_load(EMBED_UUIDS_FILE.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log(f"  (failed to load existing uuids file: {exc})")
        return {}


def save_uuids(uuids: dict) -> None:
    """Write the UUIDs file with a sorted, human-readable layout."""
    from datetime import datetime, timezone

    # Separate metadata from UUIDs
    meta = uuids.get("_meta", {})
    clean = {k: v for k, v in uuids.items() if not k.startswith("_")}

    content_lines = [
        "# workready-deploy/embed-uuids.yaml",
        "# Source of truth for AnythingLLM hiring desk embed UUIDs.",
        "# Written by setup-chatbots.py on each successful run.",
        "# Read by each company site's build pipeline to render the",
        "# AnythingLLM chat widget on their careers page.",
        "",
    ]
    for slug in sorted(clean.keys()):
        uuid = clean[slug]
        content_lines.append(f"{slug}: \"{uuid}\"")
    content_lines.append("")
    content_lines.append("_meta:")
    content_lines.append(f"  last_run: \"{datetime.now(timezone.utc).isoformat()}\"")
    content_lines.append(f"  anythingllm_base_url: \"{BASE_URL}\"")
    content_lines.append(f"  script_version: \"1.0\"")
    content_lines.append("")

    EMBED_UUIDS_FILE.write_text("\n".join(content_lines), encoding="utf-8")
    ok(f"Wrote {EMBED_UUIDS_FILE}")
```

- [ ] **Step 2: Write UUIDs after each successful company in main**

Find the main loop in `main()` that calls `setup_company` for each slug. After the results are collected, add a persistence block. The existing main has:

```python
    # Specific company or all
    targets = args if args else list(COMPANIES.keys())
    results = []

    for slug in targets:
        result = setup_company(slug)
        if result:
            results.append(result)
        time.sleep(1)  # rate limit courtesy
```

Change it to also persist UUIDs incrementally — that way a partial run still saves what it managed to harvest:

```python
    # Specific company or all
    targets = args if args else list(COMPANIES.keys())
    results = []

    uuids = load_existing_uuids()

    for slug in targets:
        result = setup_company(slug)
        if result:
            results.append(result)
            # Persist UUID incrementally so partial runs survive
            if result.get("status") == "ok" and result.get("embed_uuid"):
                uuids[slug] = result["embed_uuid"]
                save_uuids(uuids)
        time.sleep(1)  # rate limit courtesy
```

- [ ] **Step 3: Commit**

```bash
git add setup-chatbots.py
git commit -m "setup-chatbots: persist embed UUIDs to embed-uuids.yaml"
```

### Task 1.5: Extend `--dry-run` to cover the full update path

**Files:**
- Modify: `workready-deploy/setup-chatbots.py`

- [ ] **Step 1: Find the existing `--dry-run` branch in `main()`**

Around line 488 of the existing file. It currently only prints prompt + RAG stats without touching the network. We want it to also print the marker status ("would be skipped" / "would be refreshed" / "would be created") without actually calling any API.

- [ ] **Step 2: Extend the dry-run branch**

Replace the existing `if "--dry-run" in args:` block with:

```python
    if "--dry-run" in args:
        log("Dry run — generating prompts without calling AnythingLLM")
        targets = [a for a in args if a != "--dry-run"] or list(COMPANIES.keys())
        for slug in targets:
            if slug not in COMPANIES:
                continue
            data = load_company_data(slug)
            if not data["brief"]:
                fail(f"No brief.yaml for {slug}")
                continue
            prompt = build_system_prompt(slug, data)
            rag = build_rag_document(slug, data)

            print(f"\n{'=' * 60}")
            print(f"{COMPANIES[slug]['name']} ({slug})")
            print(f"{'=' * 60}")
            print(f"System prompt: {len(prompt)} chars (~{len(prompt)//4} tokens)")
            print(f"RAG document:  {len(rag)} chars (~{len(rag)//4} tokens)")
            print(f"Jobs: {len(data['jobs'])}")
            print(f"Employees: {len(data['brief'].get('employees', []))}")
            print(f"Marker in prompt: {prompt.startswith(WORKREADY_MARKER)}")
            print(f"\n--- Prompt preview (first 600 chars) ---")
            print(prompt[:600])
            print("...")
            print(f"\nWould call AnythingLLM endpoints:")
            print(f"  GET  /workspace/{COMPANIES[slug]['workspace_name']}")
            print(f"  IF exists and marker verified:")
            print(f"    POST /workspace/{{slug}}/update-embeddings  (unassign docs)")
            print(f"    POST /workspace/{COMPANIES[slug]['workspace_name']}/update")
            print(f"    POST /document/upload  (fresh RAG)")
            print(f"    POST /workspace/{{slug}}/update-embeddings  (assign new doc)")
            print(f"  IF exists but marker missing:")
            print(f"    SKIP (log warning)")
            print(f"  IF not exists:")
            print(f"    POST /workspace/new")
            print(f"    POST /workspace/{{slug}}/embed/new")
            print(f"    POST /document/upload")
            print(f"    POST /workspace/{{slug}}/update-embeddings")
        return
```

- [ ] **Step 3: Smoke test the dry-run locally**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
ANYTHINGLLM_API_KEY=dummy_for_dryrun uv run python setup-chatbots.py --dry-run 2>&1 | head -40
```

Expected: prints per-company sections, shows "Marker in prompt: True", lists the AnythingLLM endpoints the real run would call. No network traffic.

- [ ] **Step 4: Commit**

```bash
git add setup-chatbots.py
git commit -m "setup-chatbots: expand --dry-run to show full update path"
```

### Task 1.6: Add mocked unit tests for the hardening

**Files:**
- Create: `workready-deploy/tests/__init__.py`
- Create: `workready-deploy/tests/test_setup_chatbots.py`

- [ ] **Step 1: Create the tests directory**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: Write the mocked test**

Create `tests/test_setup_chatbots.py`:

```python
"""Mocked unit tests for setup-chatbots.py hardening.

No network calls. Uses unittest.mock to stub api_get/api_post/api_upload
at the module level. Verifies:

1. Marker is prepended to every new workspace's system prompt
2. workspace_has_marker correctly detects present/absent markers
3. create_or_update_workspace REFUSES to touch an unmarked workspace
4. create_or_update_workspace REFRESHES a marked workspace (unassigns docs then updates prompt)
5. --dry-run does not make any network calls
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


def _load_setup_chatbots():
    """Load setup-chatbots.py as a module despite the hyphen in the name."""
    path = Path(__file__).parent.parent / "setup-chatbots.py"
    spec = importlib.util.spec_from_file_location("setup_chatbots", path)
    module = importlib.util.module_from_spec(spec)
    # Prevent the module from sys.exit()-ing on missing API key at import time
    with mock.patch.dict("os.environ", {"ANYTHINGLLM_API_KEY": "dummy"}):
        spec.loader.exec_module(module)
    return module


sc = _load_setup_chatbots()


class TestMarkerHelpers(unittest.TestCase):
    def test_prompt_with_marker_adds_marker_line(self):
        prompt = sc.prompt_with_marker("You are a helpful assistant.")
        self.assertTrue(prompt.startswith(sc.WORKREADY_MARKER))
        self.assertIn("You are a helpful assistant.", prompt)

    def test_workspace_has_marker_detects_present(self):
        ws = {
            "openAiPrompt": f"{sc.WORKREADY_MARKER}\n\nYou are...",
        }
        self.assertTrue(sc.workspace_has_marker(ws))

    def test_workspace_has_marker_detects_absent(self):
        ws = {"openAiPrompt": "You are a different assistant."}
        self.assertFalse(sc.workspace_has_marker(ws))

    def test_workspace_has_marker_detects_empty_prompt(self):
        self.assertFalse(sc.workspace_has_marker({}))
        self.assertFalse(sc.workspace_has_marker({"openAiPrompt": ""}))

    def test_build_system_prompt_includes_marker(self):
        # Minimal stub company data
        data = {
            "brief": {
                "company": {
                    "name": "Test Co",
                    "tagline": "Testing",
                    "location": "Testville",
                    "profile": {"founded": 2020, "description": "Test"},
                    "scenario": {"name": "Test", "description": "Test"},
                },
            },
            "jobs": [],
        }
        prompt = sc.build_system_prompt("ironvale-resources", data)
        self.assertTrue(prompt.startswith(sc.WORKREADY_MARKER))


class TestCreateOrUpdateWorkspace(unittest.TestCase):
    def setUp(self):
        # Minimal stub data
        self.slug = "ironvale-resources"
        self.company_info = {
            "name": "IronVale Resources",
            "domain": "ironvaleresources.eduserver.au",
            "workspace_name": "workready-ironvale-hiring",
        }
        self.data = {
            "brief": {
                "company": {
                    "name": "IronVale",
                    "tagline": "Mining",
                    "location": "Perth",
                    "profile": {"founded": 2000},
                    "scenario": {},
                },
            },
            "jobs": [],
        }

    def test_create_new_workspace_when_none_exists(self):
        with mock.patch.object(sc, "api_get", side_effect=Exception("not found")), \
             mock.patch.object(sc, "api_post") as mock_post:
            mock_post.return_value = {
                "workspace": {"slug": "workready-ironvale-hiring"},
            }
            result = sc.create_or_update_workspace(
                self.slug, self.company_info, self.data,
            )
            self.assertEqual(result.get("slug"), "workready-ironvale-hiring")
            # Must have called the create endpoint
            self.assertTrue(
                any("/workspace/new" in call.args[0] for call in mock_post.call_args_list)
            )

    def test_skips_unmarked_workspace(self):
        unmarked = {
            "workspace": {
                "slug": "workready-ironvale-hiring",
                "openAiPrompt": "You are a different assistant entirely.",
            },
        }
        with mock.patch.object(sc, "api_get", return_value=unmarked), \
             mock.patch.object(sc, "api_post") as mock_post:
            result = sc.create_or_update_workspace(
                self.slug, self.company_info, self.data,
            )
            # Must return None (skipped) and must NOT have called update
            self.assertIsNone(result)
            for call in mock_post.call_args_list:
                self.assertNotIn("/update", call.args[0])

    def test_refreshes_marked_workspace(self):
        marked = {
            "workspace": {
                "slug": "workready-ironvale-hiring",
                "openAiPrompt": f"{sc.WORKREADY_MARKER}\n\nOld prompt.",
                "documents": [
                    {"docpath": "custom-documents/old-rag-ironvale.md"},
                ],
            },
        }
        with mock.patch.object(sc, "api_get", return_value=marked), \
             mock.patch.object(sc, "api_post") as mock_post:
            mock_post.return_value = {
                "workspace": {"slug": "workready-ironvale-hiring"},
            }
            result = sc.create_or_update_workspace(
                self.slug, self.company_info, self.data,
            )
            self.assertIsNotNone(result)
            # Must have called update-embeddings (to unassign) AND update (for prompt)
            endpoints = [call.args[0] for call in mock_post.call_args_list]
            self.assertTrue(
                any("update-embeddings" in e for e in endpoints),
                f"Expected update-embeddings, got {endpoints}",
            )
            self.assertTrue(
                any("/update" in e and "embeddings" not in e for e in endpoints),
                f"Expected /update (prompt), got {endpoints}",
            )


class TestDryRunDoesNotCallNetwork(unittest.TestCase):
    def test_dry_run_exit_without_api_calls(self):
        # --dry-run short-circuits before any api_get/api_post
        with mock.patch.object(sc, "api_get") as mock_get, \
             mock.patch.object(sc, "api_post") as mock_post, \
             mock.patch.object(sc, "api_upload") as mock_upload, \
             mock.patch.object(sys, "argv", ["setup-chatbots.py", "--dry-run"]):
            sc.main()
            mock_get.assert_not_called()
            mock_post.assert_not_called()
            mock_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
ANYTHINGLLM_API_KEY=dummy python3 -m unittest tests.test_setup_chatbots -v
```

Expected: all tests pass (`OK`)

If the test complains about missing `pyyaml`, add it:

```bash
uv add --dev pyyaml  # or: pip install pyyaml
```

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "setup-chatbots: mocked unit tests for marker + update refresh"
```

---

## Block 2 — Run against the real AnythingLLM server

This is the only block in this plan that hits the real `chat.eduserver.au` instance. Read the safety constraints at the top of this plan again before starting. Every step is reversible until Task 2.4.

### Task 2.1: Verify environment and existing state

**Files:**
- (none — read-only)

- [ ] **Step 1: Confirm `ANYTHINGLLM_API_KEY` is set**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
# Either the env var is exported, or a .env file contains it
test -n "$ANYTHINGLLM_API_KEY" && echo "OK: env var set" || \
  (test -f .env && grep -q ANYTHINGLLM_API_KEY .env && echo "OK: .env has key" || \
   echo "FAIL: no API key — ask operator to set ANYTHINGLLM_API_KEY")
```

Expected: `OK: env var set` or `OK: .env has key`. If FAIL, stop and escalate to the operator — they need to create the key in the AnythingLLM admin UI and add it to `workready-deploy/.env`.

- [ ] **Step 2: Confirm the script can reach the server**

```bash
uv run python setup-chatbots.py --list 2>&1 | head -30
```

Expected: prints the existing workspaces on `chat.eduserver.au`. Each row is `<slug> — <name>`. **Read this output carefully.** You're looking for:

- Any workspaces with the `workready-` prefix → these are the ones we'll manage
- Any workspaces that look like WorkReady but don't have the prefix → hand-rolled or from an earlier attempt, do NOT touch
- Unrelated workspaces (other projects) → do NOT touch

If there are workready-prefixed workspaces that weren't created by THIS plan's hardened script, they probably lack the marker and will be skipped by the marker check. That's the safe outcome.

- [ ] **Step 3: Capture the baseline**

```bash
uv run python setup-chatbots.py --list > /tmp/anythingllm-baseline-before.txt
cat /tmp/anythingllm-baseline-before.txt
```

Keep this file — the summary at the end of the block will diff against it.

### Task 2.2: Dry-run preview

- [ ] **Step 1: Run dry-run against all 6 companies**

```bash
uv run python setup-chatbots.py --dry-run 2>&1 | tee /tmp/anythingllm-dryrun.txt
```

Expected: one section per company, each showing prompt length, RAG length, job count, employee count, "Marker in prompt: True", and the endpoint walk. No network calls.

- [ ] **Step 2: Sanity-check one company's prompt content**

Scroll through the dry-run output for IronVale. Verify:

- Prompt starts with `[workready-managed] Do not remove this line.`
- Prompt includes the company description + key facts
- Prompt lists current open roles
- RAG document length is non-zero

If any of these fail, stop and debug before the live run.

### Task 2.3: Live run

**Files:**
- Modify (via script): `workready-deploy/embed-uuids.yaml` (file created on successful run)

- [ ] **Step 1: Run setup against all 6 companies**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
uv run python setup-chatbots.py 2>&1 | tee /tmp/anythingllm-live-run.txt
```

Expected: per-company sections showing:

- `Creating workspace: workready-<slug>-hiring` (first run) OR `Updating existing workspace: workready-<slug>-hiring (marker verified)` (subsequent runs)
- `Found existing embed: <uuid>` OR `Creating embed for <ws_slug>` followed by `Embed UUID: <uuid>`
- `Uploading RAG document for <slug> (<N> chars)`
- `Uploaded: custom-documents/workready-rag-<slug>.md`
- `Assigned to workspace`

Final summary lists all 6 companies with UUIDs.

**If any company fails:** the script continues to the next company and prints the error. The UUIDs harvested for successful companies are still written to `embed-uuids.yaml`. Re-running is idempotent and safe — the marker check ensures it'll refresh rather than duplicate.

**If the marker check skips a workspace:** you'll see a `[skipped]` row with the workspace name. This means that workspace exists on the server but doesn't have the marker. Investigate manually in the AnythingLLM UI — either it's a leftover from a pre-hardened run (restore the marker by hand in the system prompt and re-run) or it genuinely belongs to a different project (leave it alone, WorkReady will create a fresh workspace with a different name).

- [ ] **Step 2: Verify `embed-uuids.yaml` was written**

```bash
cat embed-uuids.yaml
```

Expected: 6 rows with `<slug>: "<uuid>"` plus a `_meta` section. Every UUID is non-empty.

- [ ] **Step 3: Run `--list` again to verify state**

```bash
uv run python setup-chatbots.py --list > /tmp/anythingllm-baseline-after.txt
diff /tmp/anythingllm-baseline-before.txt /tmp/anythingllm-baseline-after.txt
```

Expected: either no diff (if workspaces already existed) or 6 new `workready-<slug>-hiring` rows.

### Task 2.4: Commit `embed-uuids.yaml`

- [ ] **Step 1: Verify the file looks right**

```bash
cat embed-uuids.yaml
```

Double-check no UUIDs are obviously garbage (empty strings, `None`, or suspicious formats). Every UUID should match the shape `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.

- [ ] **Step 2: Commit and push workready-deploy**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
git add embed-uuids.yaml setup-chatbots.py tests/
git commit -m "setup-chatbots: hardened + live run, 6 workspaces provisioned

- Marker check + full-refresh update logic prevents namespace collisions
  with other AnythingLLM projects on chat.eduserver.au.
- setup_company is idempotent and re-runnable — preserves workspace
  UUID + embed UUID across runs so embedded widgets on company sites
  never break.
- embed-uuids.yaml is the source of truth for per-company UUIDs,
  consumed by each company site's build pipeline.
- Mocked unit tests verify marker detection and refusal-to-touch
  unmanaged workspaces."
git push
```

---

## Block 3 — Embed widget partial (one company as pilot)

Use IronVale as the pilot. Once it works end-to-end there, Block 4 replicates the pattern across the remaining 5 companies.

### Task 3.1: Write the shared widget partial for IronVale

**Files:**
- Create: `ironvale-resources/site/templates/_chat_widget.html.j2`

- [ ] **Step 1: Write the partial**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/ironvale-resources
cat > site/templates/_chat_widget.html.j2 << 'JINJA'
{# AnythingLLM hiring desk chat widget.

   Rendered on public-facing pages (currently just careers.html). Takes
   `embed_uuid` and `anythingllm_base_url` from the Jinja context,
   injected by build.py from ../workready-deploy/embed-uuids.yaml.

   If embed_uuid is missing or blank, renders nothing — graceful
   degradation for when the setup script hasn't run yet. #}
{% if embed_uuid %}
<script
  data-embed-id="{{ embed_uuid }}"
  data-base-api-url="{{ anythingllm_base_url|default('https://chat.eduserver.au/api/embed') }}"
  src="{{ anythingllm_widget_src|default('https://chat.eduserver.au/embed/anythingllm-chat-widget.min.js') }}">
</script>
{% endif %}
JINJA
```

- [ ] **Step 2: Verify the file is well-formed**

```bash
test -f site/templates/_chat_widget.html.j2 && cat site/templates/_chat_widget.html.j2
```

Expected: file prints with a `{% if embed_uuid %}` block visible.

- [ ] **Step 3: Commit**

```bash
git add site/templates/_chat_widget.html.j2
git commit -m "Add AnythingLLM chat widget partial"
```

### Task 3.2: Update IronVale `build.py` to load embed UUIDs

**Files:**
- Modify: `ironvale-resources/site/build.py`

- [ ] **Step 1: Add a UUID loader function**

At the top of `site/build.py`, just after the existing imports, add:

```python
def load_embed_uuid(company_slug: str) -> str:
    """Load this company's embed UUID from ../workready-deploy/embed-uuids.yaml.

    Returns empty string if the file is missing, the company is not
    listed, or the UUID is blank. The widget partial renders nothing
    in that case — graceful degradation.
    """
    uuids_path = ROOT.parent / "workready-deploy" / "embed-uuids.yaml"
    if not uuids_path.is_file():
        return ""
    try:
        data = yaml.safe_load(uuids_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    return data.get(company_slug, "") or ""
```

- [ ] **Step 2: Add the UUID + base URL to the context**

Find the `ctx = { ... }` dict (around line 223 of the existing build.py). Add two keys:

```python
    ctx = {
        "company": company,
        "profile": profile,
        "scenario": scenario,
        "branding": branding,
        "employees": employees,
        "docs": docs,
        "jobs": jobs_data.get("jobs", []),
        "company_url": jobs_data.get("company_url", ""),
        "year": 2026,
        # Stage 7: AnythingLLM hiring desk embed
        "embed_uuid": load_embed_uuid("ironvale-resources"),
        "anythingllm_base_url": "https://chat.eduserver.au/api/embed",
        "anythingllm_widget_src": "https://chat.eduserver.au/embed/anythingllm-chat-widget.min.js",
    }
```

- [ ] **Step 3: Smoke test — build.py picks up the UUID**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/ironvale-resources
./site/build.py 2>&1 | tail -10
```

Expected: build succeeds, no errors about missing UUID. If the build errors on missing `yaml`, the existing shebang already includes `--with pyyaml` so this should be fine.

- [ ] **Step 4: Verify the UUID actually loaded**

```bash
uv run python -c "
import sys; sys.path.insert(0, 'site')
import build
uuid = build.load_embed_uuid('ironvale-resources')
print(f'loaded uuid: {uuid!r}')
assert uuid, 'empty UUID — did embed-uuids.yaml land?'
print('OK: UUID present')
"
```

Expected: `OK: UUID present` with a non-empty UUID printed.

- [ ] **Step 5: Commit**

```bash
git add site/build.py
git commit -m "build.py: load embed UUID from workready-deploy/embed-uuids.yaml"
```

### Task 3.3: Include the widget partial on the careers page

**Files:**
- Modify: `ironvale-resources/site/templates/careers.html.j2`

- [ ] **Step 1: Find where scripts load**

Open `site/templates/careers.html.j2`. Scroll to the bottom. Look for `</body>` or a `{% block scripts %}` — the widget goes just before the closing body tag.

- [ ] **Step 2: Include the partial**

Just before `</body>` (or at the end of a `{% block scripts %}` if one exists), add:

```jinja
{% include '_chat_widget.html.j2' %}
```

- [ ] **Step 3: Rebuild and check the output**

```bash
./site/build.py 2>&1 | tail -5
grep -l "anythingllm-chat-widget" dist/*.html
```

Expected: the grep returns `dist/careers.html` — and nothing else. The widget should ONLY appear on careers, not on other pages.

- [ ] **Step 4: Verify the UUID is actually in the built HTML**

```bash
grep "data-embed-id" dist/careers.html
```

Expected: one line showing `data-embed-id="<uuid>"` with the real UUID.

- [ ] **Step 5: Commit**

```bash
git add site/templates/careers.html.j2
git commit -m "careers page: embed AnythingLLM hiring desk chat widget"
```

---

## Block 4 — Replicate across the other 5 company sites

Same three changes as Block 3, applied to each of the 5 remaining companies. Because the pattern is identical, this is one parameterised task — the only thing that changes per company is the slug passed to `load_embed_uuid`.

### Task 4.1: Apply the widget partial + build.py + careers include to every remaining company

**Files:**
- Create: `<company>/site/templates/_chat_widget.html.j2` (5 files)
- Modify: `<company>/site/build.py` (5 files)
- Modify: `<company>/site/templates/careers.html.j2` (5 files)

The 5 remaining companies:

```
nexuspoint-systems
meridian-advisory
metro-council-wa
southern-cross-financial
horizon-foundation
```

For each company, perform these three sub-steps. **Commit per company** — each is a separate git repo.

- [ ] **Step 1: Write the widget partial (identical content, 5 times)**

For each company, create `site/templates/_chat_widget.html.j2` with exactly the same content as in Task 3.1:

```bash
for slug in nexuspoint-systems meridian-advisory metro-council-wa southern-cross-financial horizon-foundation; do
  cd /Users/michael/Projects/loco-lab/loco-ensyo/$slug
  cat > site/templates/_chat_widget.html.j2 << 'JINJA'
{# AnythingLLM hiring desk chat widget.

   Rendered on public-facing pages (currently just careers.html). Takes
   `embed_uuid` and `anythingllm_base_url` from the Jinja context,
   injected by build.py from ../workready-deploy/embed-uuids.yaml.

   If embed_uuid is missing or blank, renders nothing — graceful
   degradation for when the setup script hasn't run yet. #}
{% if embed_uuid %}
<script
  data-embed-id="{{ embed_uuid }}"
  data-base-api-url="{{ anythingllm_base_url|default('https://chat.eduserver.au/api/embed') }}"
  src="{{ anythingllm_widget_src|default('https://chat.eduserver.au/embed/anythingllm-chat-widget.min.js') }}">
</script>
{% endif %}
JINJA
done
```

- [ ] **Step 2: Update each company's `build.py`**

Each company's `build.py` already follows the same pattern as IronVale (render_page with ctx dict). The two changes are the same:

A. Add the `load_embed_uuid` helper function (identical to Task 3.2 Step 1 — copy the exact same function)

B. Add the three embed_* keys to the `ctx` dict, passing the company's own slug to `load_embed_uuid`. The slug is the directory name.

Do this manually for each of the 5 remaining companies. The only per-company difference is which slug is passed to `load_embed_uuid`:

| Company | Slug for `load_embed_uuid` |
|---|---|
| nexuspoint-systems | `"nexuspoint-systems"` |
| meridian-advisory | `"meridian-advisory"` |
| metro-council-wa | `"metro-council-wa"` |
| southern-cross-financial | `"southern-cross-financial"` |
| horizon-foundation | `"horizon-foundation"` |

- [ ] **Step 3: Include the partial on each company's careers page**

For each company, edit `site/templates/careers.html.j2` and add `{% include '_chat_widget.html.j2' %}` just before `</body>`. Same location as in Task 3.3.

- [ ] **Step 4: Rebuild each company site**

```bash
for slug in nexuspoint-systems meridian-advisory metro-council-wa southern-cross-financial horizon-foundation; do
  cd /Users/michael/Projects/loco-lab/loco-ensyo/$slug
  echo "=== $slug ==="
  ./site/build.py 2>&1 | tail -3
done
```

Expected: each company builds successfully.

- [ ] **Step 5: Verify the UUID is in each built careers page**

```bash
for slug in nexuspoint-systems meridian-advisory metro-council-wa southern-cross-financial horizon-foundation; do
  cd /Users/michael/Projects/loco-lab/loco-ensyo/$slug
  if grep -q "data-embed-id" dist/careers.html; then
    echo "OK: $slug careers.html has embed"
  else
    echo "FAIL: $slug careers.html is missing the widget"
  fi
done
```

Expected: 5 OK lines. If any fail, revisit the `include` step for that company.

- [ ] **Step 6: Commit per company (one commit per repo)**

```bash
for slug in nexuspoint-systems meridian-advisory metro-council-wa southern-cross-financial horizon-foundation; do
  cd /Users/michael/Projects/loco-lab/loco-ensyo/$slug
  git add site/templates/_chat_widget.html.j2 site/build.py site/templates/careers.html.j2
  git commit -m "Add AnythingLLM hiring desk chat widget to careers page"
done
```

- [ ] **Step 7: Push each company repo**

```bash
for slug in ironvale-resources nexuspoint-systems meridian-advisory metro-council-wa southern-cross-financial horizon-foundation; do
  cd /Users/michael/Projects/loco-lab/loco-ensyo/$slug
  git push 2>&1 | tail -2
done
```

Expected: 6 successful pushes. If any fail, fix and re-push that repo.

---

## Block 5 — Manual verification

This block is operator-driven. It confirms the end-to-end flow actually works by loading a real company site in a real browser and chatting with the real hiring desk chatbot.

### Task 5.1: Serve one company site locally

- [ ] **Step 1: Serve IronVale's built site**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/ironvale-resources/dist
python3 -m http.server 8100
```

Leave this running. In a second terminal, continue with the verification steps.

### Task 5.2: Load the careers page and confirm the widget appears

- [ ] **Step 1: Open the careers page in a browser**

Visit: http://localhost:8100/careers.html

- [ ] **Step 2: Visual check**

Expected: the AnythingLLM chat widget appears as a floating icon or bubble in a corner of the page (typically bottom-right). If the widget doesn't appear:

- Open the browser's DevTools → Console. Look for any script errors from `anythingllm-chat-widget.min.js`.
- Open DevTools → Network. Filter by "embed". You should see a request to `chat.eduserver.au/embed/anythingllm-chat-widget.min.js`. If the request returned 404 or CORS-blocked, the base URL or the embed domain allowlist is wrong.
- View source (Ctrl-U / Cmd-U) and search for `data-embed-id`. Confirm the UUID in the HTML matches `embed-uuids.yaml`.

- [ ] **Step 3: Open the widget and send a message**

Click the widget icon. A chat box should appear. Type:

> "Hi, I'm interested in the Junior Analyst role. What does the hiring process look like?"

Expected: within a few seconds, a response from the IronVale hiring desk that mentions the role, the hiring process (three steps: CV, interview, decision within one week), and the company. The response should be in-character for IronVale.

If the response is generic or doesn't mention IronVale-specific content, the RAG document probably wasn't uploaded or assigned — re-run `setup-chatbots.py --dry-run` to verify the RAG doc length is non-zero, then re-run the live update.

- [ ] **Step 4: Test a different query to verify knowledge breadth**

Ask: `"Who's the Head of Sustainability and what do they care about?"`

Expected: the chatbot names Ravi Mehta (from IronVale's employees roster) and describes him in character. This confirms the RAG document is loaded and retrievable.

- [ ] **Step 5: Stop the local server**

In the terminal running `python3 -m http.server`, press Ctrl-C.

### Task 5.3: (Optional) Spot-check one other company

- [ ] **Step 1: Serve and test NexusPoint Systems**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/nexuspoint-systems/dist
python3 -m http.server 8101
```

Visit http://localhost:8101/careers.html, confirm the widget loads, ask about an open role, verify the response is NexusPoint-specific.

This is optional — if IronVale works end-to-end, the pattern is the same for the other 5. But checking one more company is cheap insurance against per-company content errors.

- [ ] **Step 2: Stop the server**

---

## Block 6 — Done

### Task 6.1: Commit the docs update (if any)

- [ ] **Step 1: Check for any stray edits**

```bash
for slug in ironvale-resources nexuspoint-systems meridian-advisory metro-council-wa southern-cross-financial horizon-foundation; do
  cd /Users/michael/Projects/loco-lab/loco-ensyo/$slug
  echo "=== $slug ==="
  git status --short
done

cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-deploy
echo "=== workready-deploy ==="
git status --short
```

Expected: no untracked or modified files in any repo. If there are any, review and commit.

### Task 6.2: Announce completion

The plan is complete when:

- [ ] `setup-chatbots.py` contains the marker helpers, `workspace_has_marker`, the full-refresh update branch, and the expanded `--dry-run` mode
- [ ] `tests/test_setup_chatbots.py` exists and all tests pass via `python3 -m unittest tests.test_setup_chatbots -v`
- [ ] `embed-uuids.yaml` is committed in workready-deploy and contains 6 real UUIDs (one per company)
- [ ] Each of the 6 company repos has its own commit adding `_chat_widget.html.j2`, the `load_embed_uuid` helper + context keys in `build.py`, and the careers-page include
- [ ] Each company's `dist/careers.html` contains a `data-embed-id="<uuid>"` line with the right UUID
- [ ] Manual verification on at least one company (IronVale) shows the widget loads in a browser and returns company-specific responses
- [ ] All 7 repos (workready-deploy + 6 company sites) have been pushed to their respective remotes
- [ ] Plan 1 (Layer 3 Team Communications) remains as its own separate plan, independently executable

### Task 6.3: Post-completion notes for the operator

Three things the operator should know about after this plan lands:

1. **Re-running `setup-chatbots.py` is safe.** The marker check means the script will never touch a workspace that isn't WorkReady-managed. The full-refresh update means it will re-upload RAG docs if brief.yaml content has changed — preserving embed UUIDs so the widgets on careers pages don't break.

2. **Adding a new role to a company:** edit `brief.yaml`, re-run ensayo's `export-jobs` for that company, then re-run `setup-chatbots.py <company>`. The hiring desk will immediately know about the new role without any widget changes.

3. **Adding a new company:** three separate things to do, in this order:
   - Create the new company repo with brief.yaml + content/employees/*.txt
   - Add the company to the `COMPANIES` dict at the top of `setup-chatbots.py`
   - Run `setup-chatbots.py <new-slug>` to provision the workspace
   - Copy the Block 3 pattern to the new company's site (partial + build.py + careers.html.j2 include)

Done.
