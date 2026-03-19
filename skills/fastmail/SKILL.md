---
name: fastmail
description: >
  Automate Fastmail email workflows via a local browser session. Use this skill
  ONLY when running on Adam's computer with access to his browser (e.g. via
  Claude desktop / Cowork mode with Claude in Chrome). Do NOT use in headless
  environments such as the Claude Code CLI, CI pipelines, or any context without
  an interactive browser available. Trigger when the user wants to: search
  Fastmail for emails by sender, subject, or keyword; read email threads or
  attachment contents (including spreadsheets); compose and send new messages;
  or draft and send replies. Trigger on any mention of "Fastmail", "check my
  email", "search my inbox", "reply to", or similar email-management requests.
compatibility:
  tools:
    - Claude in Chrome (browser automation)
  environment: local-browser-only
---

# Fastmail Skill

Automate email search, reading, replying, and sending on Fastmail (fastmail.com)
using browser automation tools.

## Quick-start checklist

1. Ensure you have a browser tab available (use `tabs_context_mcp` / `tabs_create_mcp`).
2. [Log in if needed](#1-logging-in)
3. Proceed to the relevant section: [Search](#2-searching-for-emails), [Read](#3-reading-email-threads), [Reply / Send](#4-drafting-and-sending-replies).

---

## 1. Logging in

Navigate to `https://www.fastmail.com` and check whether you are already
authenticated (the inbox or a folder list will be visible if you are).

**If not logged in:**

1. Navigate to `https://app.fastmail.com/mail/`.
2. Locate the username/email field and type the user's email address.
3. Click **Continue** (or press Enter), then enter the password when prompted.
   - **Important:** Never auto-fill passwords. Ask the user to type their
     password themselves, then wait for them to confirm it's entered before
     continuing.
4. Complete any two-factor authentication step the user is asked for; wait for
   them to confirm before proceeding.
5. Confirm the inbox is now visible before moving on.

> **Security note:** Credentials must never be stored, logged, or transmitted.
> Always defer password/2FA entry to the user.

---

## 2. Searching for emails

Fastmail has a powerful search bar at the top of every page.

### Using the keyboard shortcut

Press `/` (forward slash) to focus the search box immediately, then type the
query and press Enter.

### Search query syntax

| Goal | Query example |
|---|---|
| From a specific sender | `from:alice@example.com` |
| Subject contains phrase | `subject:"budget report"` |
| Any field contains keyword | `invoice` |
| Combined | `from:bob@example.com subject:Q1` |
| Within a date range | `after:2025-01-01 before:2025-03-01` |
| Has attachment | `hasattachment:true` |

After pressing Enter the results list appears. Use `read_page` or `find` to
locate matching messages in the list.

### Extracting the result list

Use `get_page_text` to read the list of results, or `find` with descriptive
queries like `"email from Alice"` to locate specific items.

---

## 3. Reading email threads

1. Click the target message row in the list.
2. Wait for the message pane to load (a heading with the subject line will
   appear on the right).
3. Use `get_page_text` to extract the full thread text.

### Reading attachments

For **spreadsheet attachments** (.xlsx / .csv):

1. Locate the attachment chip/link using `find "attachment"` or `read_page`.
2. Ask the user for explicit permission before downloading: state the filename
   and source, then wait for "yes" / "confirmed".
3. Once approved, click the **Download** link for the attachment.
4. After download, use the `xlsx` or `pdf` skill (as appropriate) to parse and
   summarise the contents.

For **PDF attachments**, use the `pdf` skill after downloading.

For **inline images**, use `screenshot` + `zoom` to read visible content.

> Only download attachments after explicit user confirmation.

---

## 4. Drafting and sending replies

### Reply to an existing thread

1. With the thread open, locate the **Reply** button (or press `R` if focused).
2. Click **Reply** (or **Reply all** — confirm with the user which they want).
3. Wait for the compose panel to appear.
4. Use `find "compose text area"` or `read_page` to locate the body field, then
   click into it.
5. Type the draft message using `type`.
6. Review the To/CC/Subject fields with the user before sending.
7. **Ask for explicit confirmation:** "Ready to send this reply to [recipient]?" — wait for "yes" / "confirmed".
8. Click **Send**.

### Compose a new message

1. Click the **Compose** button (pencil/pen icon, top-left area) or press `C`.
2. Fill in:
   - **To**: the recipient email address(es)
   - **Subject**: the subject line
   - **Body**: the message content
3. Review all fields with the user.
4. Ask for explicit send confirmation before clicking **Send**.

### Compose panel tips

- Use `read_page filter:"interactive"` to find input refs quickly.
- To attach a file, locate the **Attach** button (paperclip icon) and use
  `file_upload` with the local path.
- To discard a draft, click **Discard** — confirm with the user first.

---

## 5. Common pitfalls and mitigations

| Pitfall | Mitigation |
|---|---|
| Not yet logged in | Always check for inbox UI before assuming auth |
| Fastmail app loading (SPA) | After navigation, wait briefly and re-check page with `read_page` |
| Session expired / 2FA required | Surface to user and wait for them to re-authenticate |
| Search returns no results | Try loosening query (remove date range, try partial sender name) |
| Attachment download blocked | Guide user to download manually if automation is blocked |
| Reply vs Reply-all confusion | Always confirm with user before choosing reply scope |

---

## 6. Example workflows

### "Find emails from Sarah about the Q1 budget"

```
1. Navigate to https://app.fastmail.com/mail/
2. Press / to focus search
3. Type: from:sarah subject:"Q1 budget"  -> Enter
4. Read results list with get_page_text
5. Click the most relevant result
6. Use get_page_text to extract thread
```

### "Reply to the last email from finance@acme.com"

```
1. Search: from:finance@acme.com
2. Click the top (most recent) result
3. Read thread to confirm context
4. Click Reply
5. Draft message, confirm with user
6. Send after explicit confirmation
```

### "Download the spreadsheet from Tom's email and summarise it"

```
1. Search: from:tom hasattachment:true
2. Open the matching thread
3. Ask user: "I found an attachment called 'Q4-data.xlsx'. OK to download?"
4. On confirmation, download the file
5. Use the xlsx skill to parse and summarise
```
