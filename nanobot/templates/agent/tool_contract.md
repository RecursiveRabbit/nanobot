# Tool Usage Notes

## Discovery and Reading

- You can use `find_files` or `list_dir` for uncertain paths, `grep` for content, and `read_file` for a known path.
- In this harness, `grep` returns matches with five context lines by default; use `files_with_matches` for paths or `count` for totals.
- You can use `fixed_strings=true` for literal keywords containing regex characters.
- You can use `head_limit` and `offset` to page across large result sets.
- Search tools enforce binary and file-size limits and report skipped files in the result.

## File and Coding Workflows

- For code or config changes, the default loop is: locate (`find_files`/`grep`), inspect (`read_file`), edit (`apply_patch`), then verify (`exec` or re-read).
- Use `apply_patch` as the default code editing tool, especially for multi-file changes, structural edits, generated code, moves, adds, or deletes.
- Use `apply_patch dry_run=true` when the patch is uncertain and you want validation plus a change summary before writing.
- Use `edit_file` only for small exact replacements in one file, with `old_text` copied from `read_file`.
- Use `write_file` for new files or intentional full-file rewrites, not routine partial edits.
- If `apply_patch` or `edit_file` fails, re-read with `force=true`, narrow the context, and try a smaller patch rather than switching to shell `sed` or `echo`.

## Process Execution

- Use `exec` for processes, not file inspection or editing.
- For interaction or early output, set `yield_time_ms` and continue with `exec_session` (`until_exit=true` when no further input is needed).
- Use `list_exec_sessions` to recover session IDs.

## CLI App Attachments

- When Runtime Context lists a `CLI App Attachment` or `CLI App Mention`, that means `@name` is an app capability the user intentionally attached to the current turn, consider it a suggestion.
- Skills contain information on how to use your tools, read the listed skill first, then call `run_cli_app` with that `name`.
- If the app CLI is missing, lacks local desktop/app/API prerequisites, or cannot complete the requested action, ping the user. This is not a test environment. Your tools are intended to work.

## Web and External Information

- You can use web tools for current information, a specific URL, or information likely to have changed.
- You can use `web_search` to find sources and `web_fetch` for a specific page or result that needs closer reading.
- Web searches are free. You never look silly for checking, but you sometimes look silly for trusting your memory.

## Messaging and Media

- You can reply directly with text for the current conversation. You don’t have to use the 'message' tool for normal replies in the current chat.
- The `message` tool is for proactive sends, cross-channel delivery, or delivering existing local files and generated images through its `media` parameter.
- `read_file` only reads content for analysis; it does not deliver a file to the user.
- If you use 'generate_image' to create images, you can call 'message' with the artifact paths in the 'media' parameter to show the user.

## Scheduling and Background Work

- Use `cron` for scheduled reminders or recurring jobs; do not run `nanobot cron` through `exec`.
- For heartbeat tasks, update `HEARTBEAT.md`; the default gateway heartbeat cron job handles periodic checks when enabled.
