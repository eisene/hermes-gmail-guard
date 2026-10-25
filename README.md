# hermes-gmail-guard

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that puts a human approval in
front of every Gmail read made through the
[gmail-multi-mcp](https://github.com/eisene/gmail-multi-mcp) MCP server. It's built for a shared
family or household bot where several people's inboxes are connected.

## What it does

- **Asks before every inbox read.** It uses Hermes's own approval gate (CLI prompt or the
  Discord/Telegram/Slack buttons). The prompt names the inbox, the query and who asked:
  > Search Alice's inbox for `from:@school.org newer_than:7d`, asked by Bob (<@1234…>)
- **One approval key per inbox.** "Allow for session" covers further reads of the *same* inbox in
  that session, never a different one.
- **Shows the gateway-verified sender.** The asker comes from the platform event's user ID, not
  from message text, so it can't be faked by typing.
- **Refuses anything that isn't a read tool** (send, trash, connect account, …), as a second layer
  on top of the server's locked mode.
- **Fails closed.** If no human can answer, or the prompt times out, the call is blocked.

## Install

```bash
hermes plugins install eisene/hermes-gmail-guard --enable
```

It expects the MCP server to be registered as `gmail`, so tools appear as `mcp_gmail_*`.

Optional `$HERMES_HOME/gmail-guard.json`:

```json
{
  "server": "gmail",
  "people":  {"123456789012345678": "Alice"},
  "inboxes": {"alice": "Alice's inbox", "bob": "Bob's inbox"}
}
```

## What it does not do

It can't stop an agent that has a shell from talking to the MCP server directly. The real
boundaries are on the server side:
- the OAuth `gmail.readonly` scope, which Google enforces;
- a separate Unix user that holds the tokens;
- locked mode (read tools only);
- redaction of codes and links;
- fencing of untrusted content.

This plugin catches crossed wires and makes every inbox access visible.

## Tests

```bash
python3 -m unittest discover -s tests
```

Part of [eisene/housebot](https://github.com/eisene/housebot). MIT licensed.
