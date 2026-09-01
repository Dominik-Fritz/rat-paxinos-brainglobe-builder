$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runtime = Join-Path $Root "data\native_abba_runtime"
$JavaHome = Join-Path $Runtime "java"
$Marker = Join-Path $JavaHome "runtime-manifest.json"
$PinnedVersion = "17.0.14+7"
$PinnedRelease = "jdk-$PinnedVersion"
$ApiVersion = [uri]::EscapeDataString($PinnedVersion)
$VersionApi = "https://api.adoptium.net/v3/assets/version/${ApiVersion}?architecture=x64&heap_size=normal&image_type=jdk&jvm_impl=hotspot&os=windows"
$FeatureApi = "https://api.adoptium.net/v3/assets/feature_releases/17/ga?architecture=x64&heap_size=normal&image_type=jdk&jvm_impl=hotspot&os=windows&page=0&page_size=50"

if (Test-Path $Marker) {
    $Existing = Get-Content $Marker -Raw | ConvertFrom-Json
    if ($Existing.pinned_version -eq $PinnedVersion -and (Test-Path (Join-Path $JavaHome "bin\java.exe"))) {
        Write-Host "Builder-local Java $PinnedVersion is ready."
        exit 0
    }
    throw "JAVA_CACHE_CORRUPT: runtime marker/version does not match $PinnedVersion"
}

New-Item -ItemType Directory -Force -Path $Runtime, (Join-Path $Runtime "downloads") | Out-Null
$Assets = $null
$MetadataUrl = $null
$MetadataErrors = @()
foreach ($CandidateApi in @($VersionApi, $FeatureApi)) {
    try {
        Write-Host "Requesting Adoptium metadata: $CandidateApi"
        $Response = Invoke-RestMethod -Uri $CandidateApi -Headers @{
            "User-Agent"="rat-paxinos-builder/0.3.1"
            "Accept"="application/json"
        } -TimeoutSec 60
        if (@($Response).Count -gt 0) {
            $Assets = @($Response)
            $MetadataUrl = $CandidateApi
            break
        }
        $MetadataErrors += "${CandidateApi}: empty response"
    } catch {
        $MetadataErrors += "${CandidateApi}: $($_.Exception.Message)"
    }
}
if ($null -eq $Assets) {
    throw "JAVA_NETWORK: all Adoptium metadata requests failed: $($MetadataErrors -join ' | ')"
}
$Asset = $Assets | Where-Object {
    ($_.release_name -eq $PinnedRelease -or $_.version_data.semver -eq $PinnedVersion) -and
    $_.binary.os -eq "windows" -and $_.binary.architecture -eq "x64" -and
    $_.binary.image_type -eq "jdk" -and $_.binary.jvm_impl -eq "hotspot"
} | Select-Object -First 1
if ($null -eq $Asset) {
    $Available = ($Assets | ForEach-Object { $_.release_name } | Where-Object { $_ } | Select-Object -First 10) -join ", "
    throw "JAVA_VERSION: pinned Adoptium JDK $PinnedRelease was not returned; available: $Available"
}
$Package = $Asset.binary.package
if ([string]::IsNullOrWhiteSpace($Package.checksum) -or [string]::IsNullOrWhiteSpace($Package.link)) {
    throw "JAVA_METADATA: download URL or publisher SHA-256 is missing"
}
$Archive = Join-Path $Runtime "downloads\temurin-jdk-$($PinnedVersion.Replace('+','_')).zip"
try { Invoke-WebRequest -Uri $Package.link -OutFile $Archive -UseBasicParsing -TimeoutSec 1800 }
catch { throw "JAVA_NETWORK: JDK download failed: $($_.Exception.Message)" }
$Observed = (Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant()
if ($Observed -ne $Package.checksum.ToLowerInvariant()) {
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    throw "JAVA_HASH: expected $($Package.checksum), got $Observed"
}
$Stage = Join-Path $Runtime "java.partial"
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Stage | Out-Null
try { Expand-Archive -Path $Archive -DestinationPath $Stage -Force }
catch { throw "JAVA_CACHE_CORRUPT: JDK archive cannot be extracted: $($_.Exception.Message)" }
$Top = Get-ChildItem $Stage -Directory | Where-Object { Test-Path (Join-Path $_.FullName "bin\java.exe") } | Select-Object -First 1
if ($null -eq $Top) { throw "JAVA_COMPONENT_MISSING: extracted JDK has no bin\java.exe" }
Remove-Item $JavaHome -Recurse -Force -ErrorAction SilentlyContinue
Move-Item $Top.FullName $JavaHome
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
@{
    pinned_version = $PinnedVersion
    release_name = $PinnedRelease
    distribution = "Eclipse Temurin"
    metadata_url = $MetadataUrl
    metadata_errors_before_success = $MetadataErrors
    download_url = $Package.link
    archive_sha256 = $Observed
    installed_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json | Set-Content -Encoding UTF8 $Marker
& (Join-Path $JavaHome "bin\java.exe") -version
