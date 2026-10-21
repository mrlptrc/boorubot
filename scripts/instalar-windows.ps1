# boorubot · instalação no Windows
#
# Rodar uma vez, de dentro da pasta do boorubot (ex.: C:\boorubot), no PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\instalar-windows.ps1
#
# O que faz: instala o uv (se faltar), as dependências e o navegador do robô, deixa o painel abrir
# escondido quando o Windows liga e abre o painel agora. Pode rodar de novo sem problema.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Passo($texto) { Write-Host "`n==> $texto" -ForegroundColor Magenta }

Passo "uv (gerenciador do Python)"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv --version

Passo "dependências (baixa o Python se precisar)"
uv sync

Passo "navegador do robô (Chromium do Playwright)"
uv run playwright install chromium

Passo "painel abrindo junto com o Windows (escondido)"
uv run boorubot startup install

if (Test-Path "data\boorubot.db") {
    Write-Host "`nDados encontrados em data\: a fila, o histórico e as contas continuam como estavam." -ForegroundColor Green
} else {
    Write-Host "`nSem pasta data\: o painel começa vazio, no modo teste. Siga o Guia do painel." -ForegroundColor Yellow
}

Passo "abrindo o painel em http://127.0.0.1:8765"
uv run boorubot panel
