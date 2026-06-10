$ErrorActionPreference = "Stop"

function Resolve-Python312 {
    try {
        $version = py -3.12 --version 2>$null
        if ($version -match "Python 3\.12\.") {
            return "py"
        }
    } catch {}

    $candidates = @(
        $env:SSLAGO_PYTHON312,
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python312\python.exe"
    ) | Where-Object { $_ -and $_.Trim() }

    foreach ($candidate in $candidates) {
        try {
            if (-not (Test-Path $candidate)) {
                continue
            }
            $version = & $candidate --version 2>$null
            if ($version -match "Python 3\.12\.") {
                return $candidate
            }
        } catch {
            continue
        }
    }
    throw "Python 3.12 is required. Install it with: winget install -e --id Python.Python.3.12"
}

$python = Resolve-Python312

function Invoke-Python312 {
    if ($python -eq "py") {
        py -3.12 @args
    } else {
        & $python @args
    }
}

if (Test-Path "venv") {
    try {
        $venvVersion = .\venv\Scripts\python.exe --version
        if ($venvVersion -notmatch "Python 3\.12\.") {
            throw "Existing venv is not Python 3.12"
        }
        Write-Host "Using existing venv"
    } catch {
        Write-Host "Existing venv is broken or incompatible. Recreating local backend venv..."
        Remove-Item -Recurse -Force "venv"
        Invoke-Python312 -m venv venv
    }
} else {
    Invoke-Python312 -m venv venv
}

.\venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip check
.\venv\Scripts\python.exe -m pytest -q

Write-Host "Backend environment is ready."
