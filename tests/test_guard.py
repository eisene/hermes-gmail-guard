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
        g.SYSTEM_CONFIG = Path(self.tmp.name, "no-system-config.json")
        Path(self.tmp.name, "gmail-guard.json").write_text(json.dumps({
            "people": {ALICE: "Alice"},
            "inboxes": {"alice": "Alice's inbox"},
            "cron": {"abc123def456": ["alice"]},
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
        self.assertTrue(r["message"].startswith("📬 **Alice's inbox**: search for `from:@school.org`"), r["message"])
        self.assertIn(f"Asked by Alice (<@{ALICE}>)", r["message"])

    def test_rule_key_is_per_inbox(self):
        a = g._on_pre_tool_call("mcp_gmail_gmail_list", {"account": "alice"}, "s")
        b = g._on_pre_tool_call("mcp_gmail_gmail_list", {"account": "bob"}, "s")
        self.assertNotEqual(a["rule_key"], b["rule_key"])
        self.assertIn("**unknown inbox 'bob'**", b["message"])

    def test_unknown_sender_and_cron(self):
        r = g._on_pre_tool_call("mcp_gmail_gmail_read_message", {"account": "alice", "message_id": "abc"}, "nope")
        self.assertIn("non-chat context", r["message"])
        g._on_pre_llm_call(session_id="s2", sender_id="999999999999999999")
        r = g._on_pre_tool_call("mcp_gmail_gmail_grep", {"account": "alice", "pattern": "drum", "query": "newer_than:7d"}, "s2")
        self.assertIn("<@999999999999999999>", r["message"])
        self.assertIn("regex `drum`", r["message"])

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

    def test_model_supplied_account_cannot_inject_markdown(self):
        r = g._on_pre_tool_call("mcp__gmail__gmail_list", {"account": "**x** <@1>"}, "s")
        self.assertIn("unknown inbox 'x 1'", r["message"])

    def _use_system_config(self):
        sysp = Path(self.tmp.name, "etc.json")
        sysp.write_text(Path(self.tmp.name, "gmail-guard.json").read_text())
        g.SYSTEM_CONFIG = sysp

    def test_cron_allowlist_ignored_in_user_writable_config(self):
        sid = "cron_abc123def456_20261025_220000"
        r = g._on_pre_tool_call("mcp__gmail__gmail_list", {"account": "alice"}, sid)
        self.assertEqual(r["action"], "approve")

    def test_allowlisted_cron_job_skips_prompt_for_its_inbox_only(self):
        self._use_system_config()
        sid = "cron_abc123def456_20261025_220000"
        self.assertIsNone(g._on_pre_tool_call("mcp__gmail__gmail_list", {"account": "alice"}, sid))
        self.assertIsNone(g._on_pre_tool_call("mcp__gmail__gmail_list", {"account": "ALICE"}, sid))
        r = g._on_pre_tool_call("mcp__gmail__gmail_list", {"account": "bob"}, sid)
        self.assertEqual(r["action"], "approve")
        r = g._on_pre_tool_call("mcp__gmail__gmail_send", {"account": "alice"}, sid)
        self.assertEqual(r["action"], "block")

    def test_other_cron_jobs_and_lookalike_sessions_still_prompt(self):
        self._use_system_config()
        for sid in ("cron_ffffffffffff_20261025_220000", "cron_abc123def456", "xcron_abc123def456_20261025_220000",
                    "abc123def456"):
            r = g._on_pre_tool_call("mcp__gmail__gmail_list", {"account": "alice"}, sid)
            self.assertEqual(r["action"], "approve", sid)

    def test_system_config_overrides_user_config(self):
        sysp = Path(self.tmp.name, "etc.json")
        sysp.write_text(json.dumps({"inboxes": {"alice": "SYSTEM"}}))
        g.SYSTEM_CONFIG = sysp
        sid = "cron_abc123def456_20261025_220000"
        r = g._on_pre_tool_call("mcp__gmail__gmail_list", {"account": "alice"}, sid)
        self.assertEqual(r["action"], "approve")  # user file's cron allowlist ignored
        self.assertIn("**SYSTEM**", r["message"])

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
