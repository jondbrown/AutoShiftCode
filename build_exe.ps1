# Builds dist\AutoShiftKey.exe (needs the project .venv).
Set-Location $PSScriptRoot
$py = ".venv\Scripts\python.exe"
& $py -m pip install -q -r requirements.txt pyinstaller
& $py -m PyInstaller --noconfirm --onefile --name AutoShiftKey `
    --collect-all playwright --hidden-import keyring.backends.Windows autoshift.py
