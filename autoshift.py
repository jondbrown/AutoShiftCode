"""Scrape Borderlands 4 SHiFT codes and redeem them on shift.gearboxsoftware.com.

  python autoshift.py --login      one-time: sign in manually, session is saved
  python autoshift.py --dry-run    list new codes without redeeming
  python autoshift.py --run        redeem new codes (minimized window; --headed shows it)
  python autoshift.py --schedule   register a daily Windows scheduled task
  AutoShiftCode.exe                 (packaged build) opens an interactive menu
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

FROZEN = getattr(sys, "frozen", False)  # running as a PyInstaller .exe
# The .exe keeps its data per user; running from source keeps it beside the script.
ROOT = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AutoShiftCode") if FROZEN \
    else Path(__file__).parent
ROOT.mkdir(parents=True, exist_ok=True)
TASK_NAME = "AutoShiftCode"
STATE_FILE = ROOT / "redeemed.json"
PROFILE_DIR = ROOT / "browser_profile"
LOG_DIR = ROOT / "logs"
DEFAULT_SOURCE_URL = "https://mentalmars.com/game-news/borderlands-4-shift-codes/"
REWARDS_URL = "https://shift.gearboxsoftware.com/rewards"
CODE_RE = re.compile(r"\b[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}\b")
TERMINAL = {"redeemed", "already_redeemed", "expired", "invalid"}

MAX_PAGE_BYTES = 5_000_000  # source page size cap (the real page is ~0.5 MB)
MAX_CODES_PER_RUN = 50      # a hostile/broken source can't make us hammer SHiFT

_log_file = None


def clean(text, limit=120):
    """Untrusted text (from web pages): drop control characters, e.g. terminal escape
    sequences, and cap the length before it is printed, logged or stored."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(text))[:limit]


def log(msg):
    global _log_file
    line = f"{datetime.now():%H:%M:%S} {clean(msg, 400)}"
    print(line, flush=True)
    if _log_file is None:
        LOG_DIR.mkdir(exist_ok=True)
        _log_file = open(LOG_DIR / f"{date.today():%Y-%m-%d}.log", "a", encoding="utf-8")
    _log_file.write(line + "\n")
    _log_file.flush()


def parse_date(text):
    text = text.strip().replace("Sept ", "Sep ").replace(".", "")
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None  # "Unknown", "Never", etc.


def scrape(url=DEFAULT_SOURCE_URL):
    """Return {code: {reward, expires}} from every table row containing a code."""
    body = b""
    with requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
                      stream=True) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(65536):
            body += chunk
            if len(body) > MAX_PAGE_BYTES:
                raise ValueError(f"Source page larger than {MAX_PAGE_BYTES} bytes; refusing.")
    soup = BeautifulSoup(body.decode("utf-8", errors="replace"), "html.parser")
    codes = {}
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        m = CODE_RE.search(" ".join(cells))
        if not m:
            continue
        codes.setdefault(m.group(0), {
            "reward": clean(cells[0]) if cells else "",
            "expires": clean(cells[-1]) if len(cells) > 1 else "",
        })
    if not codes:  # table layout changed: fall back to any code in the page text
        for c in CODE_RE.findall(soup.get_text(" ")):
            codes.setdefault(c, {"reward": "", "expires": ""})
    return codes


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def pending(codes, state):
    out = {}
    for code, info in codes.items():
        entry = state.get(code, {})
        # Entries from before per-game tracking ("redemptions" missing) only covered the
        # first game button, so a "redeemed" one is rechecked for the remaining games.
        legacy = entry.get("status") == "redeemed" and "redemptions" not in entry
        if entry.get("status") in TERMINAL and not legacy:
            continue
        exp = parse_date(info["expires"])
        if exp and exp < date.today():
            continue
        out[code] = info
    return out


def classify(text):
    t = text.lower()
    if "launch a shift-enabled title" in t or "forbidden" in t:
        return "blocked"
    if "unexpected error" in t:
        return "error"  # transient SHiFT-side failure: not recorded as final, retried next run
    if "not a valid" in t or "invalid" in t or "does not exist" in t:
        return "invalid"
    if "already been redeemed" in t or "already redeemed" in t:
        return "already_redeemed"
    if "expired" in t:
        return "expired"
    if "too many" in t or "try again later" in t or "rate" in t and "limit" in t:
        return "rate_limited"
    if "success" in t or "redeemed" in t or "check your in-game mail" in t:
        return "redeemed"
    return "unknown"


def logged_in(page):
    page.goto(REWARDS_URL)
    page.wait_for_load_state("domcontentloaded")
    return page.locator("#shift_code_input").count() > 0


# Result messages SHiFT may show outside an .alert element; the match is the message.
RESULT_TEXT_RE = (r".*(?:has expired|already been redeemed|already redeemed|not a valid|"
                  r"launch a SHiFT-enabled title|too many|unexpected error).*")
KEYRING_SERVICE = "AutoShiftCode"


def launch(p, minimized=False):
    """Open the persistent browser profile in the system's Microsoft Edge.

    Always a real, windowed browser: SHiFT answers headless browsers with 403 Forbidden.
    `minimized` minimizes the window after launch so unattended runs stay out of the way
    (the page keeps working while minimized). Edge ships with Windows, so nothing has to
    be downloaded or bundled. From source without Edge, fall back to Playwright's Chromium.
    """
    try:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), channel="msedge",
                                                   headless=False)
    except Exception:  # noqa: BLE001
        if FROZEN:
            raise
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
    if minimized:
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            cdp = ctx.new_cdp_session(page)
            win = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds",
                     {"windowId": win, "bounds": {"windowState": "minimized"}})
        except Exception:  # noqa: BLE001
            pass  # cosmetic only: a visible window is fine
    return ctx


def command_line(*flags):
    """Command (as a list) that re-runs this program with the given flags."""
    base = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
    return base + list(flags)


def schedule_daily(at="09:00"):
    """Register a daily task that runs while this user is logged on."""
    tr = subprocess.list2cmdline(command_line("--run"))
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", at,
                        "/TN", TASK_NAME, "/TR", tr], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode


def unschedule():
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode


def set_credentials():
    import getpass
    import keyring
    email = input("SHiFT email: ").strip()
    keyring.set_password(KEYRING_SERVICE, "email", email)
    keyring.set_password(KEYRING_SERVICE, email, getpass.getpass("SHiFT password: "))
    print("Saved to Windows Credential Manager.")


def manual_login():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://shift.gearboxsoftware.com/home")
        input("Sign in in the browser window, then press Enter here to save and exit...")
        ctx.close()


def menu():
    """Interactive menu for the packaged build."""
    actions = {
        "1": ("Sign in to SHiFT (do this first)", manual_login),
        "2": ("Redeem new codes now", lambda: run(argparse.Namespace(
            source_url=DEFAULT_SOURCE_URL, dry_run=False, headed=True))),
        "3": ("Show new codes without redeeming", lambda: run(argparse.Namespace(
            source_url=DEFAULT_SOURCE_URL, dry_run=True, headed=False))),
        "4": ("Schedule a daily run (9:00 AM)", schedule_daily),
        "5": ("Remove the daily schedule", unschedule),
        "6": ("Store password for automatic sign-in", set_credentials),
        "q": ("Quit", None),
    }
    # A heading is printed above the option with the matching key.
    headings = {"1": "Redeem codes", "4": "Automation (optional)", "q": ""}
    while True:
        print()
        print("AutoShiftCode - unofficial tool, use at your own risk")
        for k, (label, _) in actions.items():
            if k in headings:
                print()
                if headings[k]:
                    print(headings[k])
            print(f"  {k}) {label}")
        choice = input("> ").strip().lower()
        if choice == "q":
            return 0
        if choice in actions:
            try:
                actions[choice][1]()
            except Exception as e:  # noqa: BLE001
                print(f"Failed: {e}")
        else:
            print("Pick one of the listed options.")


def auto_login(page):
    """Try one automatic sign-in with credentials from Windows Credential Manager.

    Store them once (you type the password yourself):
      python autoshift.py --set-credentials
    Fails (returns False) if none are stored, or if SHiFT asks for a captcha/2FA.
    Only one attempt is made per run, to avoid locking the account.
    """
    try:
        import keyring
        email = keyring.get_password(KEYRING_SERVICE, "email")
        password = keyring.get_password(KEYRING_SERVICE, email) if email else None
    except Exception as e:  # noqa: BLE001
        log(f"Credential lookup failed: {e}")
        return False
    if not (email and password):
        log("No stored credentials. Run: python autoshift.py --set-credentials")
        return False
    log("Session expired; attempting automatic sign-in.")
    try:
        page.goto("https://shift.gearboxsoftware.com/home")
        page.locator("input[type=email], #user_email").first.fill(email, timeout=10000)
        page.locator("input[type=password], #user_password").first.fill(password, timeout=10000)
        page.locator("input[type=submit], button[type=submit]").first.click()
        page.wait_for_load_state("networkidle")
        if logged_in(page):
            log("Automatic sign-in succeeded.")
            return True
    except Exception as e:  # noqa: BLE001
        log(f"Automatic sign-in error at {page.url!r} (title {page.title()!r}): {str(e)[:120]}")
    log("Automatic sign-in failed (wrong password, captcha or 2FA?). Run --login manually.")
    return False


BUTTONS = "#code_results input[type=submit], #code_results button, .redeem_button"
MAX_REDEEMS_PER_CODE = 10
CHECK_INTERVAL = 3  # minimum seconds between two code checks (SHiFT throttles bursts)
_last_check = 0.0


def check_code(page, code):
    """Enter a code and return (alert_text, [(label, button_locator), ...])."""
    global _last_check
    wait = CHECK_INTERVAL - (time.monotonic() - _last_check)
    if wait > 0:
        time.sleep(wait)
    _last_check = time.monotonic()
    page.goto(REWARDS_URL)
    page.locator("#shift_code_input").fill(code)
    page.locator("#shift_code_check").click()
    # The result may be an .alert, a message elsewhere on the page, or redeem buttons.
    page.wait_for_function(
        """([sel, pat]) => document.querySelector(sel) ||
               new RegExp(pat, 'i').test(document.body.innerText)""",
        arg=[f".alert.notice, .alert.error, {BUTTONS}", RESULT_TEXT_RE], timeout=20000)
    alerts = " ".join(page.locator(".alert").all_inner_texts()).strip()
    if not alerts:
        m = re.search(RESULT_TEXT_RE, page.locator("body").inner_text(), re.I)
        alerts = m.group(0) if m else ""
    buttons = []
    for k in range(page.locator(BUTTONS).count()):
        btn = page.locator(BUTTONS).nth(k)
        # Label = text of the enclosing form/row (names the game and platform).
        ctx = btn.locator("xpath=ancestor::*[self::form or self::tr or self::li][1]")
        label = " ".join((ctx.inner_text() if ctx.count() else btn.inner_text()).split())[:80]
        buttons.append((label or f"button{k}", btn))
    return alerts, buttons


def redeem_code(page, code, done):
    """Redeem a code once for every game/platform button offered.

    Buttons are positional, so rather than tracking them by label: click the first
    button; a redeemed button disappears, while an already-redeemed one stays and is
    skipped on the next pass. `done` collects log entries. Returns (status, message);
    status is terminal ("redeemed") only once every offered button has been handled.
    """
    msg, skip = "", 0
    for attempt in range(MAX_REDEEMS_PER_CODE):
        alerts, buttons = check_code(page, code)
        if attempt == 0:
            LOG_DIR.mkdir(exist_ok=True)
            lines = [code, alerts] + [l for l, _ in buttons]
            (LOG_DIR / "last_results.txt").write_text(
                "\n".join(clean(x, 400) for x in lines), encoding="utf-8")
        msg = alerts or msg
        st = classify(alerts) if alerts else "unknown"
        if st in ("blocked", "error"):
            # An error on any check means the code's state is unknown: never record it as
            # finished, even if some buttons were handled; the next run starts it over.
            return st, alerts[:200]
        if skip >= len(buttons):
            if buttons or done:
                return "redeemed", msg
            return (st if st != "redeemed" else "unknown"), msg
        label, btn = buttons[skip]
        btn.click()
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        result = " ".join(page.locator(".alert").all_inner_texts()).strip() or page.locator("body").inner_text()
        st = classify(result)
        log(f"  {code} [{label}]: {st}")
        if st in ("rate_limited", "blocked", "unknown", "error"):
            return st, result[:200]
        done[f"{attempt}:{label}"] = st
        if st == "already_redeemed":
            skip += 1
        msg = result[:200]
    return "unknown", "too many redeem buttons"


def run(args):
    codes = scrape(args.source_url)
    state = load_state()
    todo = pending(codes, state)
    if len(todo) > MAX_CODES_PER_RUN:
        log(f"{len(todo)} codes pending; only the first {MAX_CODES_PER_RUN} are attempted per run.")
        todo = dict(list(todo.items())[:MAX_CODES_PER_RUN])
    log(f"Scraped {len(codes)} codes; {len(todo)} new/retryable.")
    for c, i in todo.items():
        log(f"  {c}  {i['reward']}  (expires {i['expires']})")
    if args.dry_run or not todo:
        return 0

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = launch(p, minimized=not args.headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not logged_in(page) and not auto_login(page):
            log("Not logged in. Choose 'Sign in to SHiFT' in the menu (or run with --login).")
            ctx.close()
            return 2
        for code, info in todo.items():
            done = {}
            try:
                status, msg = redeem_code(page, code, done)
            except Exception as e:  # noqa: BLE001
                status, msg = "error", str(e)[:200]
            log(f"{code}: {status} - {msg[:100]!r}")
            if status not in ("rate_limited", "blocked"):
                state[code] = {**info, "status": status, "message": clean(msg, 200),
                               "redemptions": done,
                               "timestamp": datetime.now().isoformat(timespec="seconds")}
                save_state(state)
            if status in ("rate_limited", "blocked"):
                log("Blocked/rate limited by SHiFT; stopping, will retry next run.")
                break
            time.sleep(5)
        ctx.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="redeem new codes (what the schedule runs)")
    ap.add_argument("--login", action="store_true", help="sign in manually and save the session")
    ap.add_argument("--set-credentials", action="store_true",
                    help="store SHiFT email/password in Windows Credential Manager")
    ap.add_argument("--schedule", action="store_true", help="register a daily scheduled task")
    ap.add_argument("--unschedule", action="store_true", help="remove the daily scheduled task")
    ap.add_argument("--source-url", default=DEFAULT_SOURCE_URL,
                    help="page listing SHiFT codes (default: mentalmars.com Borderlands 4 page)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser window normally instead of starting it minimized")
    args = ap.parse_args()

    if args.set_credentials:
        set_credentials()
        return 0
    if args.login:
        manual_login()
        return 0
    if args.schedule:
        return schedule_daily()
    if args.unschedule:
        return unschedule()
    if FROZEN and len(sys.argv) == 1:  # double-clicked .exe
        return menu()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
