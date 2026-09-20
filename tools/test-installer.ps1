$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$installer = Join-Path $projectRoot 'dist\0.1.5\克克咪 火花搭子-0.1.5-windows-x64-setup.exe'
$registration = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7782606-4C63-4AFD-B8C2-68A955E05BF8}_is1'
if (Test-Path -LiteralPath $registration) { throw 'Spark Mate is already installed; refusing to replace its registration during this test.' }
$testFolder = [IO.Path]::GetFullPath((Join-Path $projectRoot ('outputs\installed-test-' + [Guid]::NewGuid().ToString('N'))))
$expectedPrefix = [IO.Path]::GetFullPath((Join-Path $projectRoot 'outputs')) + [IO.Path]::DirectorySeparatorChar
if (-not $testFolder.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Test directory is outside the project outputs.' }
$receiptPath = Join-Path $projectRoot 'outputs\installed-smoke.json'
$logPath = Join-Path $projectRoot 'outputs\installer-test.log'
$installArguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', '/TASKS=""', ('/DIR="' + $testFolder + '"'), ('/LOG="' + $logPath + '"'))
$installProcess = Start-Process -FilePath $installer -ArgumentList $installArguments -WindowStyle Hidden -Wait -PassThru
if ($installProcess.ExitCode -ne 0) { throw ('Installation failed: ' + $installProcess.ExitCode) }
$installedExe = Join-Path $testFolder '克克咪 火花搭子.exe'
$appHash = (Get-FileHash -LiteralPath $installedExe -Algorithm SHA256).Hash
$testProcess = Start-Process -FilePath $installedExe -ArgumentList @('--self-test', ('"' + $receiptPath + '"')) -WindowStyle Hidden -Wait -PassThru
$smoke = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
if ($testProcess.ExitCode -ne 0 -or -not $smoke.ok -or -not $smoke.frozen) { throw 'Installed application failed its smoke test; leaving the test installation for diagnosis.' }
$registeredFolder = [IO.Path]::GetFullPath((Get-ItemProperty -LiteralPath $registration).InstallLocation).TrimEnd('\')
if ($registeredFolder -ne $testFolder.TrimEnd('\')) { throw 'Installation registration does not match the test directory; refusing to uninstall.' }
$uninstaller = (Resolve-Path -LiteralPath (Join-Path $testFolder 'unins000.exe')).Path
if (-not $uninstaller.StartsWith($testFolder + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Uninstaller is outside the test directory.' }
$uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -WindowStyle Hidden -Wait -PassThru
$receipt = [ordered]@{
    tested_at = [DateTime]::UtcNow.ToString('o')
    installer_sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    application_sha256 = $appHash.ToLowerInvariant()
    install_exit = $installProcess.ExitCode
    application_smoke = $smoke
    uninstall_exit = $uninstallProcess.ExitCode
    application_removed = -not (Test-Path -LiteralPath $installedExe)
    registration_removed = -not (Test-Path -LiteralPath $registration)
    test_directory = $testFolder
}
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'outputs\installer-verification.json') -Encoding UTF8
$receipt | ConvertTo-Json -Depth 5
if ($uninstallProcess.ExitCode -ne 0 -or -not $receipt.application_removed -or -not $receipt.registration_removed) { throw 'Uninstall verification failed.' }
