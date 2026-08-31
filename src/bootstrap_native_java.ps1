$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runtime = Join-Path $Root "data\native_abba_runtime"
$JavaHome = Join-Path $Runtime "java"
$Marker = Join-Path $JavaHome "runtime-manifest.json"
$PinnedVersion = "17.0.14+7"
$ApiVersion = [uri]::EscapeDataString($PinnedVersion)
$Api = "https://api.adoptium.net/v3/assets/version/$ApiVersion?architecture=x64&heap_size=normal&image_type=jdk&jvm_impl=hotspot&os=windows&project=jdk&vendor=eclipse"

if (Test-Path $Marker) {
    $Existing = Get-Content $Marker -Raw | ConvertFrom-Json
    if ($Existing.pinned_version -eq $PinnedVersion -and (Test-Path (Join-Path $JavaHome "bin\java.exe"))) {
        Write-Host "Builder-local Java $PinnedVersion is ready."
        exit 0
    }
    throw "JAVA_CACHE_CORRUPT: runtime marker/version does not match $PinnedVersion"
}

New-Item -ItemType Directory -Force -Path $Runtime, (Join-Path $Runtime "downloads") | Out-Null
try {
    $Assets = Invoke-RestMethod -Uri $Api -Headers @{"User-Agent"="rat-paxinos-builder/0.3.1"}
} catch {
    throw "JAVA_NETWORK: Adoptium metadata request failed: $($_.Exception.Message)"
}
$Asset = @($Assets) | Where-Object {
    $_.version.semver -like "17.0.14*" -and $_.binary.os -eq "windows" -and $_.binary.architecture -eq "x64" -and $_.binary.image_type -eq "jdk"
} | Select-Object -First 1
if ($null -eq $Asset) { throw "JAVA_VERSION: pinned Adoptium JDK $PinnedVersion was not returned" }
$Package = $Asset.binary.package
if ([string]::IsNullOrWhiteSpace($Package.checksum) -or [string]::IsNullOrWhiteSpace($Package.link)) {
    throw "JAVA_METADATA: download URL or publisher SHA-256 is missing"
}
$Archive = Join-Path $Runtime "downloads\temurin-jdk-$($PinnedVersion.Replace('+','_')).zip"
try { Invoke-WebRequest -Uri $Package.link -OutFile $Archive -UseBasicParsing }
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
$Top = Get-ChildItem $Stage -Directory | Select-Object -First 1
if ($null -eq $Top -or -not (Test-Path (Join-Path $Top.FullName "bin\java.exe"))) {
    throw "JAVA_COMPONENT_MISSING: extracted JDK has no bin\java.exe"
}
Remove-Item $JavaHome -Recurse -Force -ErrorAction SilentlyContinue
Move-Item $Top.FullName $JavaHome
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
@{
    pinned_version = $PinnedVersion
    distribution = "Eclipse Temurin"
    metadata_url = $Api
    download_url = $Package.link
    archive_sha256 = $Observed
    installed_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json | Set-Content -Encoding UTF8 $Marker
& (Join-Path $JavaHome "bin\java.exe") -version
