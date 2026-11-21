## Cloudera AI Deployment & Installation Guide

**1. Project Initialization**
Create a new Project in your Cloudera AI (formerly CML) workspace and clone the modernized repository containing the `src/` backend, `packs/` directories, and `ddl/` Iceberg schemas. Ensure your project has adequate compute resources allocated, particularly if you are spinning up a local LLM container within the same session.

**2. Python packages**
Use a Python 3.12 runtime. `mistralai-agents` does not install on 3.11 or earlier. Cloudera installs the project-root `requirements.txt` when a new session or application engine starts. On a session that is already running:

```bash
pip install -r requirements.txt
```

**3. Environment Configuration**
Configure the following environment variables in your Cloudera AI Project Settings to ensure the FastAPI loop and Mistral client bind correctly:

* `CDSW_APP_PORT`: Set by Cloudera automatically (defaults to `8080`), used by FastAPI to serve the split-pane UI.
* `MISTRAL_LOCAL_URL`: Optional. On a workbench session the client uses the Nemotron endpoint `https://ml-64288d82-5dd.go01-dem.ylcu-atmi.cloudera.site/namespaces/serving-default/endpoints/goes---nemotron-3-super-120b/v1` and the model `nvidia/nemotron-3-super-120b-a12b`, with the bearer token from `/tmp/jwt`. Set this variable only to point at a different endpoint.
* `MISTRAL_MODEL`: Optional override for the model id. The workbench default is `nvidia/nemotron-3-super-120b-a12b`.
* `SESSION_DIRECTORY`: Optional. When unset, `claim_{id}_case.json` and letter files are written to the project `workspace/` directory. Set this only to use a different writable folder.
* Impala uses the same variables as the original claims MCP server. `IMPALA_HOST` may be a hostname or a pasted coordinator URL. Defaults match that server: `IMPALA_PORT=443`, `IMPALA_AUTH_MECHANISM=LDAP`, `IMPALA_USE_SSL=true`, `IMPALA_USE_HTTP_TRANSPORT=true`, `IMPALA_HTTP_PATH=cliservice`. Set `IMPALA_USER`, `IMPALA_PASSWORD`, and `IMPALA_DATABASE`.

**4. Inference Server Boot (If Hosting Locally)**
If you are not using a remote API, launch your local inference engine in a background terminal session before starting the application. For Ollama:

```bash
ollama serve &
ollama run mistral:7b

```

**5. Launch the Application**
Navigate to the **Applications** tab in Cloudera AI and create a new Application.

* **Subdomain:** `mistral-agent-ui`
* **Script:** `serve.py`
* Launch the application. Once the status shows as running, click the application card to open the split-pane diagnostic harness in your browser.

---

## Quality Assurance Checklist

Execute these tests through the left pane of the UI harness. Validate the outcomes by inspecting the `meta.debug_cot`, `content.routing_summary`, and `actions` payloads in the right pane's JSON viewer.

### 1. System & Connectivity Checks

* **Domain Pack Mounting:** Select `underwriting` from the UI dropdown and submit a test prompt. Verify `PackManager` mounts the pack without raising an HTTP 404.
* **LLM Chain-of-Thought Extraction:** Submit a prompt and verify the JSON viewer populates `meta.debug_cot` with the Orchestrator's entity ID extraction step and the Specialist Agent's tool calls.
* **MCP Catalog Connectivity:** Verify that executing a read tool correctly queries the Impala lake and surfaces the raw payload inside `content.view_data`.

### 2. Deterministic Routing Validation (Shadow Tests)

Route the following verified seeds to ensure the CEL playbook correctly overrides LLM inference and matches the legacy baseline.

| Domain | Entity ID | Natural Language Prompt | Expected `next_step` | Expected Lane |
| --- | --- | --- | --- | --- |
| Claims | 401 | "Please process claim 401." | `CollectIncidentReportNumber` or `RequestPoliceReport`

 | PD |
| Claims | 402 | "Please process claim 402." | `EscalateDiscovery`

 | LITIGATION |
| Claims | 403 | "Please process claim 403." | `CloseoutAudit`

 | CLOSEOUT |
| Claims | 404 | "Please process claim 404." | `DenyUnlawfulOperation` or `DenyAudit`

 | DENY |
| Claims (FNOL) | 88 | "Please process FNOL 88." | `ConvertFnolToIncidentOnly`

 | FNOL |
| Distributions | 7001 | "Please process claim 7001." | `ProcessDistribution`

 | GENERAL |

### 3. Action Endpoint & Artifact Generation

* **Letter Drafting Flag:** Process a claim that requires a letter (e.g., seed 401 routing to `RequestPoliceReport`). Verify `actions.quick_replies` populates with the letter prompt and `actions.tool_invocations` contains the pre-filled JSON payload.
* **Direct File Write:** Click the generated UI action button to trigger a `POST /api/v1/action` with the intent `save_claim_letter`. Check the Cloudera AI file browser to confirm `claim_401_letter.txt` was successfully written to the `SESSION_DIRECTORY`.