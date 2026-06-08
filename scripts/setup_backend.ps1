$ErrorActionPreference = "Stop"

$python = "py -3.12"
$version = Invoke-Expression "$python --version"
if ($version -notmatch "Python 3\.12\.") {
    throw "Python 3.12 is required. Install it with: winget install -e --id Python.Python.3.12"
}

if (Test-Path "venv") {
    Write-Host "Using existing venv"
} else {
    Invoke-Expression "$python -m venv venv"
}

.\venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip check
.\venv\Scripts\python.exe -m pytest -q

Write-Host "Backend environment is ready."
