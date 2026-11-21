Phase 11: Local LLM Output Sanitization & Data Pipeline Debugging
To: Grok (Cursor)From: Implementation Architect

The new local inference server is successfully generating responses, but it is hallucinating raw XML <think> and <tool_call> tags instead of adhering to the Mistral SDK's JSON tool-calling specification. Furthermore, the build_session_graph tool is generating empty JSON graphs, causing the CEL playbook to route everything to FixDataQuality.

In this phase, you will sanitize the UI output and debug the MCP pipeline.

Task 1: Sanitize chat_text (Regex Cleanup)
Since open-weight models frequently leak Chain-of-Thought (CoT) and XML tokens, we must strip these from the final UI payload to prevent breaking the frontend display.

Open src/core/agent_loop.py.

At the end of run_specialist (and anywhere else the agent's conversational text is returned), import re.

Apply a regex substitution to strip out <think> blocks and <tool_call> blocks before returning the text.

Example:

Python
import re
clean_text = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL)
clean_text = re.sub(r'<tool_call>.*?</tool_call>', '', clean_text, flags=re.DOTALL).strip()
The raw string will still exist in the meta.debug_cot array for debugging, but content.chat_text will now be clean.

Task 2: Force Tool Execution on XML Fallback (Optional but Recommended)
Because the Mistral SDK expects a specific tool_calls object, it ignores the XML <tool_call> the model just wrote.

In run_specialist, if the Mistral client fails to trigger the run_named_write tool natively (because the model output XML instead), manually parse the label from the XML block and execute the run_named_write(label) tool in Python.

Task 3: Debug the get_claim_spine MCP Pipeline
The case JSON is being written, but it is empty. We need to find out why the MCP server isn't returning data.

Open mcp_forks/iceberg-mcp-server/ and add explicit logging to the run_named_query handler.

Log the exact SQL being executed and the raw rows returned by the Impala connection.

Verify that the SQL placeholder ${var:claim_id} is being correctly replaced with '401' before the query is sent to Impala. (If the quotes are missing, or the variable replacement fails, Impala will return 0 rows).

Phase 11 Acceptance Criteria:

Running the same prompt for claim 401 returns a clean content.chat_text without any XML or <think> blocks.

The stdout logs from the FastAPI server (which capture the MCP stdio) explicitly print the SQL being sent to Impala for get_claim_spine, allowing us to see why 0 rows are being returned.