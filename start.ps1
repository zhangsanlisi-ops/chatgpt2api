$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dockerConfig = Join-Path $root '.docker-config'
New-Item -ItemType Directory -Force $dockerConfig | Out-Null
@{ cliPluginsExtraDirs = @("$env:LOCALAPPDATA\Programs\DockerDesktop\resources\cli-plugins") } |
  ConvertTo-Json | Set-Content (Join-Path $dockerConfig 'config.json')
$env:DOCKER_CONFIG = $dockerConfig
Set-Location $root
docker compose up -d
$cloudflared = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$tunnelId = '488cc397-4db4-485a-8091-51f206a86f17'
if (-not (Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object { $_.CommandLine -match $tunnelId })) {
  Start-Process -FilePath $cloudflared -ArgumentList "--config `"$root\cloudflared.yml`" tunnel run $tunnelId" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput "$root\cloudflared.out.log" -RedirectStandardError "$root\cloudflared.err.log"
}
docker compose ps
Write-Host 'Console: https://api.zhangbinbin.edu.kg'
