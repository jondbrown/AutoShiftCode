"""Scrape Borderlands 4 SHiFT codes and redeem them on shift.gearboxsoftware.com.

  python autoshift.py --login      one-time: sign in manually, session is saved
  python autoshift.py --dry-run    list new codes without redeeming
  python autoshift.py              redeem new codes (headless; use --headed to watch)
"""
import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "redeemed.json"
PROFILE_DIR = ROOT / "browser_profile"
LOG_DIR = ROOT / "logs"
DEFAULT_SOURCE_URL = "https://mentalmars.com/game-news/borderlands-4-shift-codes/"
REWARDS_URL = "https://shift.gearboxsoftware.com/rewards"
CODE_RE = re.compile(r"\b[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}\b")
TERMINAL = {"redeemed", "already_redeemed", "expired", "invalid"}

_log_file = None


def log(msg):
    global _log_file
    line = f"{datetime.now():%H:%M:%S} {msg}"
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
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    codes = {}
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        m = CODE_RE.search(" ".join(cells))
        if not m:
            continue
        codes.setdefault(m.group(0), {
            "reward": cells[0] if cells else "",
            "expires": cells[-1] if len(cells) > 1 else "",
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
                  r"launch a SHiFT-enabled title|too many).*")
KEYRING_SERVICE = "AutoShiftKey"


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
    page.goto("https://shift.gearboxsoftware.com/home")
    page.locator("input[type=email], #user_email").first.fill(email)
    page.locator("input[type=password], #user_password").first.fill(password)
    page.locator("input[type=submit], button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")
    if logged_in(page):
        log("Automatic sign-in succeeded.")
        return True
    log("Automatic sign-in failed (wrong password, captcha or 2FA?). Run --login manually.")
    return False


BUTTONS = "#code_results input[type=submit], #code_results button, .redeem_button"
MAX_REDEEMS_PER_CODE = 10


def check_code(page, code):
    """Enter a code and return (alert_text, [(label, button_locator), ...])."""
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
            (LOG_DIR / "last_results.txt").write_text("\n".join(lines), encoding="utf-8")
        msg = alerts or msg
        st = classify(alerts) if alerts else "unknown"
        if st == "blocked":
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
        if st in ("rate_limited", "blocked", "unknown"):
            return st, result[:200]
        done[f"{attempt}:{label}"] = st
        if st == "already_redeemed":
            skip += 1
        msg = result[:200]
    return "unknown", "too many redeem buttons"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="sign in manually and save the session")
    ap.add_argument("--set-credentials", action="store_true",
                    help="store SHiFT email/password in Windows Credential Manager")
    ap.add_argument("--source-url", default=DEFAULT_SOURCE_URL,
                    help="page listing SHiFT codes (default: mentalmars.com Borderlands 4 page)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    if args.set_credentials:
        import getpass
        import keyring
        email = input("SHiFT email: ").strip()
        keyring.set_password(KEYRING_SERVICE, "email", email)
        keyring.set_password(KEYRING_SERVICE, email, getpass.getpass("SHiFT password: "))
        print("Saved to Windows Credential Manager.")
        return 0

    if args.login:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://shift.gearboxsoftware.com/home")
            input("Sign in in the browser window, then press Enter here to save and exit...")
            ctx.close()
        return 0

    codes = scrape(args.source_url)
    state = load_state()
    todo = pending(codes, state)
    log(f"Scraped {len(codes)} codes; {len(todo)} new/retryable.")
    for c, i in todo.items():
        log(f"  {c}  {i['reward']}  (expires {i['expires']})")
    if args.dry_run or not todo:
        return 0

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=not args.headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not logged_in(page) and not auto_login(page):
            log("Not logged in. Run: python autoshift.py --login")
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
                state[code] = {**info, "status": status, "message": msg, "redemptions": done,
                               "timestamp": datetime.now().isoformat(timespec="seconds")}
                save_state(state)
            if status in ("rate_limited", "blocked"):
                log("Blocked/rate limited by SHiFT; stopping, will retry next run.")
                break
            time.sleep(5)
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
