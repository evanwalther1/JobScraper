# Refresh jobs into the SERVER's database, from this laptop's internet connection.
# (Workday rate-limits the server's AWS IP, but not home/campus connections.)
#   ./refresh                    all companies
#   ./refresh --company micron   just companies whose name contains "micron"
# Details: app/remote_refresh.py
Push-Location $PSScriptRoot
try {
    & "$PSScriptRoot\.venv\Scripts\python.exe" -m app.remote_refresh @args
} finally {
    Pop-Location
}
exit $LASTEXITCODE
