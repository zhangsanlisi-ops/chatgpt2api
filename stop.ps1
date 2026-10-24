$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tunnelId = '488cc397-4db4-485a-8091-51f206a86f17'
Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" |
  Where-Object { $_.CommandLine -match $tunnelId } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
$env:DOCKER_CONFIG = Join-Path $root '.docker-config'
Set-Location $root
docker compose down
