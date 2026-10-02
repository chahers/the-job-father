<#
.SYNOPSIS
    Lifecycle control for the local jobfather PostgreSQL 16 cluster.

.DESCRIPTION
    ALWAYS use this for stopping the server.  A previous force-kill (^C) left
    the cluster to perform crash recovery, as seen in server.log.

.EXAMPLE
    .\database\scripts\pg.ps1 status
    .\database\scripts\pg.ps1 start
    .\database\scripts\pg.ps1 stop
    .\database\scripts\pg.ps1 log
    .\database\scripts\pg.ps1 shell jobfather
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'log', 'shell')]
    [string] $Action,

    [string] $Database = 'jobfather',
    [string] $DbUser   = 'jobfather',
    [string] $DbHost   = '127.0.0.1',
    [int]    $Port     = 5432,

    [string] $PgHome = (Join-Path $env:LOCALAPPDATA 'pgsql16\pgsql'),
    [string] $PgData = (Join-Path $env:LOCALAPPDATA 'pgsql16\data')
)

$ErrorActionPreference = 'Stop'
$pgctl = Join-Path $PgHome 'bin\pg_ctl.exe'
$psql  = Join-Path $PgHome 'bin\psql.exe'
$log   = Join-Path (Split-Path $PgData -Parent) 'server.log'

switch ($Action) {
    'status' {
        & $pgctl status -D $PgData
        Write-Host ("log: {0}" -f $log)
    }
    'start' {
        & $pgctl start -D $PgData -l $log -o "-p $Port"
    }
    'stop' {
        # graceful: waits for active transactions, no crash recovery
        & $pgctl stop -D $PgData -m fast
    }
    'restart' {
        & $pgctl restart -D $PgData -m fast -l $log -o "-p $Port"
    }
    'log' {
        Get-Content $log -Tail 40
    }
    'shell' {
        & $psql -h $DbHost -p $Port -U $DbUser -d $Database
    }
}