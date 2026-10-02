# Builds dist\AutoShiftCode.exe (needs the project .venv) and writes its SHA-256 checksum.
Set-Location $PSScriptRoot
$py = ".venv\Scripts\python.exe"
& $py -m pip install -q -r requirements.txt pyinstaller
& $py -m PyInstaller --noconfirm --onefile --name AutoShiftCode `
    --collect-all playwright --hidden-import keyring.backends.Windows autoshift.py
$hash = (Get-FileHash dist\AutoShiftCode.exe -Algorithm SHA256).Hash.ToLower()
"$hash  AutoShiftCode.exe" | Set-Content -Encoding ascii dist\AutoShiftCode.exe.sha256
Write-Host "SHA-256: $hash"
