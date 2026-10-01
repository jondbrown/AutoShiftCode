# AutoShiftKey

Scrapes Borderlands 4 SHiFT codes from mentalmars.com and redeems them on shift.gearboxsoftware.com.
Handled codes are recorded in `redeemed.json` so each is only attempted once.

> **Disclaimer.** Unofficial hobby tool, not affiliated with or endorsed by Gearbox, 2K or
> mentalmars.com. Automating the SHiFT website may conflict with its terms of service, and
> Gearbox can rate-limit or block accounts (e.g. "launch a SHiFT-enabled title first" or 403
> errors). Use at your own risk. Run it at most once a day, to be kind to both sites.

The code list defaults to the mentalmars.com Borderlands 4 page; use `--source-url <page>`
to read a different page that lists codes in table rows.

## Using the packaged .exe (Windows)
Download `AutoShiftKey.exe` from the Releases page and double-click it (Windows SmartScreen
may warn because the exe is unsigned: More info, then Run anyway). Needs Microsoft Edge,
which ships with Windows. In the menu: **1** sign in to SHiFT once, **3** preview new codes,
**2** redeem now, **4** schedule a daily run. Data and logs live in
`%LOCALAPPDATA%\AutoShiftKey`.

Build it yourself with `powershell -ExecutionPolicy Bypass -File build_exe.ps1`
(output: `dist\AutoShiftKey.exe`).

## Setup (from source)
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python autoshift.py --login     # sign in once in the window, press Enter
.venv\Scripts\python autoshift.py --dry-run   # list new codes
.venv\Scripts\python autoshift.py --headed    # first real run, watch it work
```

## Schedule daily (run only while logged on, so the saved session works)
```powershell
$a = New-ScheduledTaskAction -Execute powershell.exe -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\run_daily.ps1`""
$t = New-ScheduledTaskTrigger -Daily -At 9am
Register-ScheduledTask -TaskName AutoShiftKey -Action $a -Trigger $t
```
## Auto sign-in (optional)
```powershell
.venv\Scripts\python autoshift.py --set-credentials   # you type email + password; stored in Windows Credential Manager
```
If the saved session is lost, the script makes one sign-in attempt with these. Captcha/2FA will defeat it; then use `--login`.

Exit code 2 / a "Not logged in" log line means rerun `--login`. Logs are in `logs/`.
