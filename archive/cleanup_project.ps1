# =====================================================================
# CARA project cleanup script
# =====================================================================
# WHAT THIS DOES:
#   1) Creates two new folders: backend\scripts  and  archive\
#   2) MOVES useful-but-messy files into backend\scripts (diagnostics
#      and one-time import/setup scripts) so they're organized instead
#      of scattered across the project root and backend folder.
#   3) MOVES already-applied, historical files into archive\ (old .bak
#      snapshots + patch scripts whose changes are already baked into
#      the current app.py / database.py).
#   4) DELETES files that are confirmed leftover from momentary testing
#      and have zero ongoing value (superseded smoke tests, a stray old
#      duplicate database, an exploratory output file).
#
# HOW THIS SCRIPT WORKS (so nothing breaks if a file isn't exactly where
# expected): instead of assuming a fixed path for every file, it first
# scans the whole project once and finds the ACTUAL current location of
# each file by name. If a file can't be found, it prints a clear
# "NOT FOUND - skipped" line instead of guessing or failing. If a name
# matches more than one file, it prints all matches and skips it so you
# can move that one by hand. Nothing is forced through silently.
#
# HOW TO RUN THIS:
#   1. Download this file - it will land as cleanup_project.txt in your
#      Downloads folder. Move it into your project root folder itself
#      (C:\Users\Markazi.co\Desktop\Thesis) and rename it there from
#      cleanup_project.txt to cleanup_project.ps1 (right-click -> Rename,
#      change the ".txt" at the end to ".ps1", press Enter, click "Yes"
#      if Windows asks about changing the file type).
#   2. Open PowerShell.
#   3. Run:  cd "C:\Users\Markazi.co\Desktop\Thesis"
#   4. Run:  powershell -ExecutionPolicy Bypass -File .\cleanup_project.ps1
#   5. Read the printed report carefully BEFORE trusting it - it lists
#      every action taken (moved / deleted / not found / ambiguous).
#   6. This script can be run again safely - if a file was already
#      moved/deleted, the second run will just report "NOT FOUND".
# =====================================================================

$ErrorActionPreference = "Stop"
$root = Get-Location

Write-Host "=== CARA cleanup starting in: $root ===" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------
# Step 1: index every file in the project once (excluding heavy/irrelevant
# folders so this stays fast and never touches datasets or the vector DB).
# ---------------------------------------------------------------------
$excludeDirs = @("chroma_db", "docs", "MedQA", "Patient Dataset", "__pycache__", ".venv", ".git", "archive")
$allFiles = Get-ChildItem -Recurse -File | Where-Object {
    $path = $_.FullName
    -not ($excludeDirs | Where-Object { $path -match [regex]::Escape("\$_\") })
}
$byName = $allFiles | Group-Object Name

function Find-One($name) {
    $group = $byName | Where-Object { $_.Name -ieq $name }
    if (-not $group -or $group.Count -eq 0 -or $group.Group.Count -eq 0) {
        Write-Host "  NOT FOUND - skipped: $name" -ForegroundColor DarkYellow
        return $null
    }
    if ($group.Group.Count -gt 1) {
        Write-Host "  AMBIGUOUS (multiple matches) - skipped, move by hand: $name" -ForegroundColor Yellow
        foreach ($f in $group.Group) { Write-Host "      -> $($f.FullName)" }
        return $null
    }
    return $group.Group[0].FullName
}

function Move-Tracked($name, $destFolder) {
    $src = Find-One $name
    if ($null -eq $src) { return }
    New-Item -ItemType Directory -Force -Path $destFolder | Out-Null
    $dest = Join-Path $destFolder (Split-Path $src -Leaf)
    if (Test-Path $dest) {
        Write-Host "  SKIPPED (already exists at destination): $name" -ForegroundColor Yellow
        return
    }
    Move-Item -Path $src -Destination $dest
    Write-Host "  MOVED: $name  ->  $destFolder" -ForegroundColor Green
}

function Delete-Tracked($name) {
    $src = Find-One $name
    if ($null -eq $src) { return }
    Remove-Item -Path $src -Force
    Write-Host "  DELETED: $name" -ForegroundColor Red
}

# ---------------------------------------------------------------------
# Step 2: organize useful scripts into backend\scripts
# (diagnostics you may still want later + the import/setup scripts
#  that document how the demo data was built - real thesis methodology)
# ---------------------------------------------------------------------
Write-Host "--- Organizing into backend\scripts ---" -ForegroundColor Cyan
$scriptsDest = Join-Path $root "backend\scripts"

$toScripts = @(
    "check_login.py",
    "check_clinic_login.py",
    "check_escalations.py",
    "find_test_patients.py",
    "find_patient.py",
    "import_test_patient.py",
    "import_200_patients.py",
    "import_200_patients_log.csv",
    "import_riverside_patients.py",
    "import_dental_patients.py",
    "ingest.py"          # NOT old-prototype leftover - this is what builds the ChromaDB
                         # knowledge base app.py's load_query_engine() actually queries.
                         # Keep it, just organized alongside the other one-time setup scripts.
)
foreach ($name in $toScripts) { Move-Tracked $name $scriptsDest }
Write-Host ""

# ---------------------------------------------------------------------
# Step 3: archive already-applied historical snapshots and patch scripts
# (their changes are already fully present in the current app.py /
#  database.py - kept only for historical reference, not active code)
# ---------------------------------------------------------------------
Write-Host "--- Archiving old snapshots and applied patches ---" -ForegroundColor Cyan
$archiveDest = Join-Path $root "archive\old_snapshots_and_patches"

$toArchive = @(
    "app.py.bak",
    "app_before_backlog_features.py.bak",
    "app_before_clinic_features.py.bak",
    "database.py.bak",
    "database_before_backlog_features.py.bak",
    "database_before_clinic_features.py.bak",
    "add_clinic_features.py",
    "fix_doctor_reply_bug.py"
)
foreach ($name in $toArchive) { Move-Tracked $name $archiveDest }
Write-Host ""

# ---------------------------------------------------------------------
# Step 4: delete confirmed leftover test/exploratory files with zero
# ongoing value (all superseded by real, working code elsewhere)
# ---------------------------------------------------------------------
Write-Host "--- Deleting confirmed leftover test files ---" -ForegroundColor Cyan

$toDelete = @(
    "candidate_patient_profile.txt",   # exploratory output of pick_test_patient.py; superseded by DATA.md
    "pick_test_patient.py",            # one-off script that produced the file above; superseded by DATA.md
    "show_patient_data.py",            # old diagnostic using the pre-fix relative DB path bug; superseded by check_login.py
    "cara_old_root_backup.db",         # stray duplicate DB from the old relative-path bug, not the live database
    "test_gemma.py",                   # one-off connectivity smoke test; superseded by evaluate.py benchmark
    "test_gemma_cerebras.py",          # one-off connectivity smoke test; superseded by evaluate.py benchmark
    "test_groq.py",                    # one-off connectivity smoke test; superseded by evaluate.py benchmark
    "test_medgemma.py",                # one-off connectivity smoke test; superseded by evaluate.py benchmark
    "test_qwen38.py",                  # one-off connectivity smoke test; superseded by evaluate.py benchmark
    "test_vision.py",                  # one-off vision smoke test; superseded by the working image-upload feature in app.py
    "test_vision2.py"                  # one-off vision smoke test; superseded by the working image-upload feature in app.py
)
foreach ($name in $toDelete) { Delete-Tracked $name }
Write-Host ""

# ---------------------------------------------------------------------
# Step 5: delete the truly-retired old interface layer.
#
# IMPORTANT CORRECTION vs. the earlier chat discussion: ingest.py is NOT
# in this list - it is handled above and MOVED (not deleted), because the
# current app.py still queries the exact ChromaDB knowledge base that
# ingest.py builds. Only the old FastAPI backend, the old terminal Q&A
# loop, and the old HTML chat frontend are actually dead code - all three
# were fully replaced by the Streamlit app (app.py) and talk to nothing
# the current platform still uses.
# ---------------------------------------------------------------------
Write-Host "--- Deleting the retired old FastAPI/terminal interface layer ---" -ForegroundColor Cyan

$toDeleteOldPrototype = @(
    "api.py",     # old FastAPI backend - fully replaced by app.py
    "query.py"    # old terminal Q&A loop - fully replaced by app.py
)
foreach ($name in $toDeleteOldPrototype) { Delete-Tracked $name }

$indexHtmlSrc = Find-One "index.html"
if ($null -ne $indexHtmlSrc) {
    Remove-Item -Path $indexHtmlSrc -Force
    Write-Host "  DELETED: index.html" -ForegroundColor Red
    # Clean up the now-empty frontend\static and frontend folders, if nothing else is in them.
    $staticDir = Split-Path $indexHtmlSrc -Parent
    if ((Get-ChildItem -Path $staticDir -Force | Measure-Object).Count -eq 0) {
        Remove-Item -Path $staticDir -Force
        Write-Host "  Removed now-empty folder: $staticDir" -ForegroundColor Red
        $frontendDir = Split-Path $staticDir -Parent
        if ((Get-ChildItem -Path $frontendDir -Force | Measure-Object).Count -eq 0) {
            Remove-Item -Path $frontendDir -Force
            Write-Host "  Removed now-empty folder: $frontendDir" -ForegroundColor Red
        }
    }
}
Write-Host ""

Write-Host "=== Done. Review the report above. ===" -ForegroundColor Cyan
Write-Host "NOTE: the two loose chroma.sqlite3 files were deliberately NOT touched" -ForegroundColor Cyan
Write-Host "by this script - they weren't inside your excluded chroma_db\ folder," -ForegroundColor Cyan
Write-Host "so before deleting them by hand, just confirm they're stray duplicate" -ForegroundColor Cyan
Write-Host "copies sitting OUTSIDE chroma_db\, not something inside it." -ForegroundColor Cyan