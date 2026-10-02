# Builds dist\AutoShiftKey.exe (needs the project .venv) and writes its SHA-256 checksum.
Set-Location $PSScriptRoot
$py = ".venv\Scripts\python.exe"
& $py -m pip install -q -r requirements.txt pyinstaller
& $py -m PyInstaller --noconfirm --onefile --name AutoShiftKey `
    --collect-all playwright --hidden-import keyring.backends.Windows autoshift.py
$hash = (Get-FileHash dist\AutoShiftKey.exe -Algorithm SHA256).Hash.ToLower()
"$hash  AutoShiftKey.exe" | Set-Content -Encoding ascii dist\AutoShiftKey.exe.sha256
Write-Host "SHA-256: $hash"
