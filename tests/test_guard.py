import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("gmail_guard", ROOT / "__init__.py")
g = importlib.util.module_from_spec(spec)
sys.modules["gmail_guard"] = g
spec.loader.exec_module(g)

ALICE = "123456789012345678"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HERMES_HOME"] = self.tmp.name
        Path(self.tmp.name, "gmail-guard.json").write_text(json.dumps({
            "people": {ALICE: "Alice"},
            "inboxes": {"alice": "Alice's inbox"},
        }))
        g._sender_by_session.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ignores_other_tools(self):
        self.assertIsNone(g._on_pre_tool_call("terminal", {"command": "ls"}, "s"))
        self.assertIsNone(g._on_pre_tool_call("mcp_other_gmail_search", {"account": "x"}, "s"))

    def test_search_prompts_with_inbox_query_and_asker(self):
        g._on_pre_llm_call(session_id="s1", sender_id=ALICE)
        r = g._on_pre_tool_call("mcp_gmail_gmail_search", {"account": "alice", "query": "from:@school.org"}, "s1")
        self.assertEqual(r["action"], "approve")
        self.assertEqual(r["rule_key"], "gmail:alice")
        self.assertIn("Search Alice's inbox for `from:@school.org`", r["message"])
        self.assertIn(f"asked by Alice (<@{ALICE}>)", r["message"])

    def test_rule_key_is_per_inbox(self):
        a = g._on_pre_tool_call("mcp_gmail_gmail_list", {"account": "alice"}, "s")
        b = g._on_pre_tool_call("mcp_gmail_gmail_list", {"account": "bob"}, "s")
        self.assertNotEqual(a["rule_key"], b["rule_key"])
        self.assertIn("inbox 'bob'", b["message"])

    def test_unknown_sender_and_cron(self):
        r = g._on_pre_tool_call("mcp_gmail_gmail_read_message", {"account": "alice", "message_id": "abc"}, "nope")
        self.assertIn("non-chat context", r["message"])
        g._on_pre_llm_call(session_id="s2", sender_id="999999999999999999")
        r = g._on_pre_tool_call("mcp_gmail_gmail_grep", {"account": "alice", "pattern": "drum", "query": "newer_than:7d"}, "s2")
        self.assertIn("<@999999999999999999>", r["message"])
        self.assertIn("/drum/", r["message"])

    def test_blocks_write_and_setup_tools(self):
        for t in ("gmail_send", "gmail_trash", "gmail_connect_account", "gmail_configure_oauth_client"):
            r = g._on_pre_tool_call(f"mcp_gmail_{t}", {"account": "alice"}, "s")
            self.assertEqual(r["action"], "block", t)

    def test_double_underscore_naming(self):
        # Live Hermes names MCP tools mcp__<server>__<tool>; both forms must be guarded.
        r = g._on_pre_tool_call("mcp__gmail__gmail_list", {"account": "alice"}, "s")
        self.assertEqual(r["action"], "approve")
        r = g._on_pre_tool_call("mcp__gmail__gmail_send", {"account": "alice"}, "s")
        self.assertEqual(r["action"], "block")
        self.assertIsNone(g._on_pre_tool_call("mcp__gmail__gmail_list_accounts", {}, "s"))
        self.assertIsNone(g._on_pre_tool_call("mcp__gmailx__gmail_list", {"account": "a"}, "s"))

    def test_requires_account(self):
        r = g._on_pre_tool_call("mcp_gmail_gmail_search", {"query": "x"}, "s")
        self.assertEqual(r["action"], "block")

    def test_list_accounts_needs_no_prompt(self):
        self.assertIsNone(g._on_pre_tool_call("mcp_gmail_gmail_list_accounts", {}, "s"))

    def test_backticks_in_query_cannot_break_formatting(self):
        r = g._on_pre_tool_call("mcp_gmail_gmail_search", {"account": "alice", "query": "a` **x**"}, "s")
        self.assertNotIn("a`", r["message"])

    def test_sender_cleared_on_empty(self):
        g._on_pre_llm_call(session_id="s3", sender_id=ALICE)
        g._on_pre_llm_call(session_id="s3", sender_id="")
        r = g._on_pre_tool_call("mcp_gmail_gmail_list", {"account": "alice"}, "s3")
        self.assertIn("non-chat context", r["message"])


if __name__ == "__main__":
    unittest.main()
