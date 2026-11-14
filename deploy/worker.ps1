<#
.SYNOPSIS
  One command to join a Windows device to the Campus Edge Fabric pool.
.EXAMPLE
  .\deploy\worker.ps1 -Server http://192.168.1.10:8080 -Token abc123 -Name my-pc -Slots 2
#>
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [Parameter(Mandatory = $true)][string]$Token,
  [string]$Name = $env:COMPUTERNAME,
  [int]$Slots = 2,
  [string]$Workloads = "thumbnail,infer,blender",
  [string]$Tags = ""
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "installing uv ..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# worker + CLI only (a teammate's laptop both computes and submits)
uv sync --package cef-worker --package cef-client --extra all | Out-Null
if ($Tags) {
  uv run cef-worker run --server $Server --token $Token --name $Name --slots $Slots --workloads $Workloads --tags $Tags
} else {
  uv run cef-worker run --server $Server --token $Token --name $Name --slots $Slots --workloads $Workloads
}
