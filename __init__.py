"""gmail-guard: human approval for every Gmail read the agent makes, naming the inbox and the asker.

Pairs with the gmail-multi-mcp server (fork: github.com/eisene/gmail-multi-mcp) registered in
Hermes as MCP server ``gmail``, whose tools surface as ``mcp__gmail__<tool>`` (or ``mcp_gmail_<tool>``).

* ``pre_llm_call`` remembers, per session, the gateway-verified sender ID of the latest turn
  (the same ``sender_id`` the discord-sender-id plugin uses; it comes from the platform event,
  not message text, so it cannot be typed).
* ``pre_tool_call`` on any of that server's tools that takes an ``account``:
  - escalates to Hermes's human-approval gate with a message like
    "Search Alice's inbox for `from:@school.org newer_than:7d` — asked by <@1234>", and an
    allowlist key of ``gmail:<account>`` so "Allow for session" covers further reads of the SAME
    inbox in this session, never a different inbox;
  - blocks any of its tools not on the read-only allowlist (defense in depth: the server
    runs in locked mode and should not expose them anyway).

The approval gate fails closed: if no human can answer (or it times out), the call is blocked.

Optional config ``$HERMES_HOME/gmail-guard.json``::

    {"people": {"123456789012345678": "Alice"},
     "inboxes": {"alice": "Alice's inbox", "bob": "Bob's inbox"},
     "server": "gmail"}

``people`` maps Discord user IDs to names for the prompt; ``inboxes`` maps the account label (or
address) the model passes to a display name. Unknown values are shown verbatim.

What this does NOT do: stop a model with a shell from talking to the mail server directly. The
server's own protections (Google-enforced read-only scope, redaction, untrusted-content fencing)
hold either way; this plugin catches crossed wires and makes every inbox access visible.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

_SNOWFLAKE = re.compile(r"^\d{15,21}$")

# Read-only tools of the gmail-multi-mcp fork. Anything else under the server prefix is refused.
READ_TOOLS = {
    "gmail_list_accounts",
    "gmail_list_labels",
    "gmail_search",
    "gmail_read_message",
    "gmail_read_thread",
    "gmail_list",
    "gmail_grep",
}
# Tools that touch no mailbox content and need no prompt.
NO_PROMPT = {"gmail_list_accounts"}

_lock = threading.Lock()
_sender_by_session: dict[str, str] = {}
_MAX_SESSIONS = 2000


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _config() -> dict[str, Any]:
    try:
        data = json.loads((_home() / "gmail-guard.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _tool_of(tool_name: str, cfg: dict[str, Any]) -> Optional[str]:
    """Bare MCP tool name if ``tool_name`` belongs to our server, else None.

    Hermes has used both ``mcp_<server>_<tool>`` and ``mcp__<server>__<tool>`` for MCP tools, so
    match any run of underscores around the server name.
    """
    server = re.sub(r"[^A-Za-z0-9_]", "_", str(cfg.get("server") or "gmail"))
    m = re.match(rf"^mcp_+{re.escape(server)}_+(.+)$", tool_name or "")
    return m.group(1) if m else None


def _clip(value: Any, n: int = 160) -> str:
    s = " ".join(str(value).split())
    s = s.replace("`", "'")
    return s if len(s) <= n else s[: n - 1] + "…"


def _on_pre_llm_call(session_id: str = "", sender_id: str = "", **_: Any) -> None:
    sid = str(sender_id or "").strip()
    if not session_id:
        return None
    with _lock:
        if sid:
            _sender_by_session[session_id] = sid
        else:
            _sender_by_session.pop(session_id, None)
        if len(_sender_by_session) > _MAX_SESSIONS:
            for key in list(_sender_by_session)[: _MAX_SESSIONS // 2]:
                _sender_by_session.pop(key, None)
    return None


def _asker(session_id: str, cfg: dict[str, Any]) -> str:
    with _lock:
        sid = _sender_by_session.get(session_id or "", "")
    if not sid:
        return "asked from a non-chat context (cron or CLI)"
    name = (cfg.get("people") or {}).get(sid)
    if _SNOWFLAKE.match(sid):
        return f"asked by {name} (<@{sid}>)" if name else f"asked by <@{sid}>"
    return f"asked by {name or sid}"


def _describe(tool: str, args: dict[str, Any], inbox: str) -> str:
    if tool == "gmail_search":
        what = f"Search {inbox} for `{_clip(args.get('query', ''))}`"
        if args.get("include_body"):
            what += " (full bodies)"
        return what
    if tool == "gmail_list":
        q = _clip(args.get("query") or "") or "all mail"
        page = " (next page)" if args.get("page_token") else ""
        return f"List {inbox}: `{q}`{page}"
    if tool == "gmail_grep":
        q = _clip(args.get("query") or "") or "all mail"
        return f"Regex-search {inbox} for /{_clip(args.get('pattern', ''), 80)}/ within `{q}`"
    if tool == "gmail_read_message":
        return f"Read one message in {inbox} (id {_clip(args.get('message_id', ''), 40)})"
    if tool == "gmail_read_thread":
        return f"Read a whole thread in {inbox} (id {_clip(args.get('thread_id', ''), 40)})"
    if tool == "gmail_list_labels":
        return f"List the labels in {inbox}"
    return f"{tool} on {inbox}"


def _on_pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any) -> Optional[dict]:
    cfg = _config()
    tool = _tool_of(tool_name, cfg)
    if tool is None:
        return None
    if tool not in READ_TOOLS:
        return {
            "action": "block",
            "message": f"gmail-guard: '{tool}' is not a read-only Gmail tool and is not allowed.",
        }
    if tool in NO_PROMPT:
        return None
    args = args if isinstance(args, dict) else {}
    account = str(args.get("account") or "").strip()
    if not account:
        return {"action": "block", "message": "gmail-guard: every Gmail read must name an account."}
    inboxes = cfg.get("inboxes") or {}
    inbox = inboxes.get(account) or inboxes.get(account.lower()) or f"inbox '{account}'"
    message = f"{_describe(tool, args, inbox)} — {_asker(session_id, cfg)}"
    # One allowlist key per inbox: "Allow for session" covers later reads of THIS inbox only.
    return {"action": "approve", "message": message, "rule_key": f"gmail:{account.lower()}"}


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
