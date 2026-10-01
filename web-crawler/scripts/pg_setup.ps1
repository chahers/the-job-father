<#
.SYNOPSIS
    One-time (idempotent) bootstrap of the jobfather role + database on the
    EXISTING local PostgreSQL 16 cluster at %LOCALAPPDATA%\pgsql16.

.DESCRIPTION
    - Verifies the PG16 cluster is running (starts it gracefully if not).
    - Creates the app role (`jobfather`) and database (`jobfather`).
    - Runs CREATE EXTENSION vector (needs the superuser; pgvector 0.8.6 is
      already present in pgsql16, so no download/compile is required).
    - Prints a ready-to-paste DATABASE_URL.

    The superuser password is used ONLY for this bootstrap and is never written
    to disk.  Only the app-role password lands in .env.

.EXAMPLE
    .\scripts\pg_setup.ps1 -SuperuserPassword (Read-Host -AsSecureString 'postgres superuser password')
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [System.Security.SecureString] $SuperuserPassword,

    [string] $Superuser = 'postgres',
    [string] $AppRole   = 'jobfather',
    [string] $AppPassword = 'the-great-job-father',
    [string] $Database  = 'jobfather',
    [string] $DbHost    = '127.0.0.1',
    [int]    $Port      = 5432,

    [string] $PgHome = (Join-Path $env:LOCALAPPDATA 'pgsql16\pgsql'),
    [string] $PgData = (Join-Path $env:LOCALAPPDATA 'pgsql16\data')
)

$ErrorActionPreference = 'Stop'

$psql  = Join-Path $PgHome 'bin\psql.exe'
$pgctl = Join-Path $PgHome 'bin\pg_ctl.exe'
$log   = Join-Path (Split-Path $PgData -Parent) 'server.log'

function Assert-PgPresent {
    foreach ($exe in @($psql, $pgctl)) {
        if (-not (Test-Path $exe)) { throw "PostgreSQL binary not found: $exe" }
    }
    Write-Host "[ok] PostgreSQL 16 binaries : $PgHome"
}

function Ensure-PgRunning {
    & $pgctl status -D $PgData *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[ok] cluster already running (PGDATA=$PgData)"
        return
    }
    Write-Host "[..] cluster not running - starting gracefully"
    & $pgctl start -D $PgData -l $log -o "-p $Port" | Out-Null
    Start-Sleep -Seconds 2
    & $pgctl status -D $PgData *> $null
    if ($LASTEXITCODE -ne 0) { throw "Failed to start cluster. See $log" }
    Write-Host "[ok] cluster started on port $Port"
}

# --- helper: run a scalar SQL query, return trimmed string -------------------
function Invoke-Sql {
    param([string] $Sql, [string] $Db = 'postgres', [string] $User = $Superuser)
    $out = & $psql -h $DbHost -p $Port -U $User -d $Db -tA -v ON_ERROR_STOP=1 -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql failed: $Sql" }
    return ($out | Out-String).Trim()
}

# --- helper: escape a value for a single-quoted SQL literal ------------------
function Format-SqlLiteral {
    param([string] $Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

Assert-PgPresent
Ensure-PgRunning

$plainSuper = [System.Net.NetworkCredential]::new('', $SuperuserPassword).Password
$env:PGPASSWORD = $plainSuper

try {
    # 1) role -----------------------------------------------------------------
    $roleExists = Invoke-Sql "SELECT 1 FROM pg_roles WHERE rolname = $(Format-SqlLiteral $AppRole);"
    if ($roleExists -eq '1') {
        Write-Host "[ok] role '$AppRole' already exists - ensuring password/login"
        Invoke-Sql "ALTER ROLE $AppRole WITH LOGIN PASSWORD $(Format-SqlLiteral $AppPassword);" | Out-Null
    } else {
        Write-Host "[..] creating role '$AppRole'"
        Invoke-Sql "CREATE ROLE $AppRole WITH LOGIN CREATEDB PASSWORD $(Format-SqlLiteral $AppPassword);" | Out-Null
    }

    # 2) database -------------------------------------------------------------
    $dbExists = Invoke-Sql "SELECT 1 FROM pg_database WHERE datname = $(Format-SqlLiteral $Database);"
    if ($dbExists -eq '1') {
        Write-Host "[ok] database '$Database' already exists"
    } else {
        Write-Host "[..] creating database '$Database' owned by '$AppRole'"
        Invoke-Sql "CREATE DATABASE $Database WITH OWNER = $AppRole ENCODING = 'UTF8';" | Out-Null
    }

    # 3) pgvector extension (superuser required - vector.control is not trusted)
    Write-Host "[..] ensuring extension 'vector' in '$Database'"
    Invoke-Sql "CREATE EXTENSION IF NOT EXISTS vector;" -Db $Database | Out-Null
    $ext = Invoke-Sql "SELECT extversion FROM pg_extension WHERE extname = 'vector';" -Db $Database

    Write-Host ""
    Write-Host "================ jobfather database ready ================"
    Write-Host ("  host      : {0}:{1}" -f $DbHost, $Port)
    Write-Host ("  database  : {0}" -f $Database)
    Write-Host ("  app role  : {0}" -f $AppRole)
    Write-Host ("  pgvector  : {0}" -f $ext)
    Write-Host ""
    Write-Host "  DATABASE_URL=postgresql+psycopg://$AppRole`:$AppPassword@$DbHost`:$Port/$Database"
    Write-Host ""
    Write-Host "  Next:  python -m jobfather_crawler db-init"
    Write-Host "=========================================================="
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
