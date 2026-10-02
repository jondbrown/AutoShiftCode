# AGENTS.md

Guidance for AI coding agents working in this repository. Read `README.md` first: it covers setup, commands, data locations, scheduling and the disclaimer. This file holds only what is recorded neither there nor in the code, and points to the source of truth for everything else. Do not copy values or behavior descriptions into this file; reference them.

## Orientation
- All logic is in `autoshift.py`; start at `run()`. Function docstrings and comments there describe behavior (browser launch, button handling, status handling).
- No tests or linter. Verify with `--dry-run`, and for logic changes stub `check_code` or the page object.
- Build with `build_exe.ps1` (README, "Using the packaged .exe"). It fails if the exe is running, but still rewrites the `.sha256`, so confirm the hash matches the exe.

## Rules that aren't obvious from the code
- **Never headless.** SHiFT returns 403 to headless browsers (see `launch()`). Don't add user-agent spoofing or other ways to hide automation.
- **Status handling** (`TERMINAL`, `classify`, `redeem_code`, `run`): a `blocked` result stops the run and isn't saved; an `error` on any check must never be recorded as finished.
- **Keep `RESULT_TEXT_RE` to outcome wording only.** A broader pattern once matched the page's description text and caused endless `unknown` retries.
- **Don't remove throttling** (`CHECK_INTERVAL`, `MAX_CODES_PER_RUN`, the sleeps in `run()`). Bursts escalate from "Unexpected error" to a server error page to an account block; tune the values, don't bypass them.
- **Web-sourced text is untrusted.** Pass it through `clean()`; codes must match `CODE_RE`.
- **Exe and source runs keep separate state** (`ROOT`), so a fresh exe re-checks codes the source install already handled.
- **Login form selectors in `auto_login()` are unverified guesses.**

## Repo hygiene
- Never commit anything in `.gitignore`. The browser profile holds live SHiFT session cookies.
- Commits use author email `24534348+jondbrown@users.noreply.github.com` and no `Co-Authored-By` trailer.
- Keep the README disclaimer; the repo is public.
- Writing Python through Bash heredocs turns `\n` inside string literals into real newlines. Write the patch to a script file and run that.
- When behavior changes, update the code comment or README that owns it, not this file.
