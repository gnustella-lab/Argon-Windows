; SPDX-License-Identifier: MPL-2.0
; Argon Browser Windows NSIS Installer
;
; Leverages Mozilla's NSIS branding layout (browser/branding/argon/branding.nsi,
; wizHeader.bmp, wizHeaderRTL.bmp, wizWatermark.bmp, and Argon .ico assets) with:
;   - Dual per-machine (HKLM / Program Files) or per-user (HKCU / LocalAppData) install
;   - Argon Start Menu, Private Browsing, and optional Desktop shortcuts
;   - Polite Windows Capabilities / ProgID registration with NO default browser hijacking
;   - Zero Mozilla Maintenance Service, Default Browser Agent, telemetry, or uninstall survey
;   - Full preservation of user profiles (%APPDATA%\argon & %LOCALAPPDATA%\argon) on upgrade & uninstall

!verbose 3

Unicode true
ManifestSupportedOS all
ManifestDPIAware true
RequestExecutionLevel user
CRCCheck on
SetDatablockOptimize on
SetCompressor /SOLID lzma

!ifndef ARGON_APP_VERSION
  !define ARGON_APP_VERSION "0.1.3"
!endif
!ifndef ARGON_VI_VERSION
  !define ARGON_VI_VERSION "0.1.3.0"
!endif
!ifndef ARGON_ARCH
  !define ARGON_ARCH "x64"
!endif
!ifndef ARGON_OUT_FILE
  !define ARGON_OUT_FILE "argon-installer.exe"
!endif
!ifndef ARGON_OUT_BASENAME
  !define ARGON_OUT_BASENAME "argon-installer.exe"
!endif

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "WinVer.nsh"
!include "x64.nsh"

!insertmacro GetParameters
!insertmacro GetOptions

!include "${ARGON_BRANDING_DIR}\branding.nsi"

!define AppName        "Argon"
!define AppVersion     "${ARGON_APP_VERSION}"
!define FileMainEXE    "argon.exe"
!define AppRegName     "Argon"
!define BrandShortName "Argon"
!define UninstallKey   "Software\Microsoft\Windows\CurrentVersion\Uninstall\Argon"
!define InstallMetaKey "Software\Argon\Argon\Install"
!define CapabilitiesKey "Software\Clients\StartMenuInternet\Argon\Capabilities"
!define StartMenuKey   "Software\Clients\StartMenuInternet\Argon"

; Zero-based resource icon indices in argon.exe (matching browser/app/ and defines.nsi.in)
!define IDI_APPICON_ZERO_BASED      "0"
!define IDI_DOCUMENT_ZERO_BASED     "1"
!define IDI_PBICON_ZERO_BASED       "4"
!define IDI_DOCUMENT_PDF_ZERO_BASED "5"

Name "${BrandFullName}"
OutFile "${ARGON_OUT_FILE}"
BrandingText " "
InstallDir "$LOCALAPPDATA\Programs\${BrandShortName}"

VIProductVersion "${ARGON_VI_VERSION}"
VIAddVersionKey "ProductName" "${BrandFullName}"
VIAddVersionKey "CompanyName" "${CompanyName}"
VIAddVersionKey "FileDescription" "${BrandShortName} Installer"
VIAddVersionKey "FileVersion" "${ARGON_VI_VERSION}"
VIAddVersionKey "ProductVersion" "${AppVersion}"
VIAddVersionKey "LegalCopyright" "Argon Contributors (MPL-2.0)"
VIAddVersionKey "OriginalFilename" "${ARGON_OUT_BASENAME}"

Var RegHive
Var IsAdminUser
Var InstallStartMenuSC
Var InstallDesktopSC
Var ExistingDir

; Registry dispatch helpers (never rely on Wow6432Node or silent default hijacking).
!macro WriteHiveStr Key Name Value
  ${If} $RegHive == "HKLM"
    WriteRegStr HKLM "${Key}" "${Name}" "${Value}"
  ${Else}
    WriteRegStr HKCU "${Key}" "${Name}" "${Value}"
  ${EndIf}
!macroend

!macro WriteHiveDWORD Key Name Value
  ${If} $RegHive == "HKLM"
    WriteRegDWORD HKLM "${Key}" "${Name}" ${Value}
  ${Else}
    WriteRegDWORD HKCU "${Key}" "${Name}" ${Value}
  ${EndIf}
!macroend

!macro un_DeleteHiveKey Key
  ${If} $RegHive == "HKLM"
    DeleteRegKey HKLM "${Key}"
  ${Else}
    DeleteRegKey HKCU "${Key}"
  ${EndIf}
!macroend

!macro un_DeleteHiveValue Key Name
  ${If} $RegHive == "HKLM"
    DeleteRegValue HKLM "${Key}" "${Name}"
  ${Else}
    DeleteRegValue HKCU "${Key}" "${Name}"
  ${EndIf}
!macroend

; Modern UI branding configuration
!define MUI_ABORTWARNING
!define MUI_ICON "${ARGON_BRANDING_DIR}\firefox.ico"
!define MUI_UNICON "${ARGON_BRANDING_DIR}\firefox.ico"

!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "${ARGON_BRANDING_DIR}\wizHeader.bmp"
!define MUI_HEADERIMAGE_BITMAP_RTL "${ARGON_BRANDING_DIR}\wizHeaderRTL.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "${ARGON_BRANDING_DIR}\wizHeader.bmp"
!define MUI_HEADERIMAGE_UNBITMAP_RTL "${ARGON_BRANDING_DIR}\wizHeaderRTL.bmp"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${ARGON_BRANDING_DIR}\wizWatermark.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${ARGON_BRANDING_DIR}\wizWatermark.bmp"

!define MUI_FINISHPAGE_NOAUTOCLOSE
!define MUI_FINISHPAGE_RUN "$INSTDIR\${FileMainEXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${BrandShortName}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${ARGON_LICENSE_FILE}"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

Section "${BrandFullName} (Required)" SecMain
  SectionIn RO

  ; Refuse to half-overwrite an actively running Argon installation on upgrade.
  ; The main executable is not the only payload file held open by a live
  ; browser process: AccessibleMarshal.dll and the core runtime DLLs are
  ; memory-mapped too, which produced "Error opening file for writing"
  ; dialogs mid-install. Probe every critical file and close Argon politely.
  StrCpy $3 0
  argon_lock_check:
    StrCpy $2 0
    ${If} ${FileExists} "$INSTDIR\${FileMainEXE}"
      ClearErrors
      FileOpen $0 "$INSTDIR\${FileMainEXE}" a
      ${If} ${Errors}
        StrCpy $2 1
      ${Else}
        FileClose $0
      ${EndIf}
    ${EndIf}
    ${If} $2 == 0
    ${AndIf} ${FileExists} "$INSTDIR\AccessibleMarshal.dll"
      ClearErrors
      FileOpen $0 "$INSTDIR\AccessibleMarshal.dll" a
      ${If} ${Errors}
        StrCpy $2 1
      ${Else}
        FileClose $0
      ${EndIf}
    ${EndIf}
    ${If} $2 == 0
      goto argon_lock_clear
    ${EndIf}
    ${If} ${Silent}
      Abort "Target files are in use under $INSTDIR. Close ${BrandShortName} and retry the installation."
    ${EndIf}
    IntOp $3 $3 + 1
    ; Ask any running ${BrandShortName} main window to close (WM_CLOSE).
    System::Call 'user32::FindWindow(t "MozillaWindowClass", p 0) p .r0'
    ${If} $0 != 0
      System::Call 'user32::PostMessage(p $0, i 16, p 0, p 0)'
    ${EndIf}
    ${If} $3 >= 3
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${BrandFullName} appears to be running in $INSTDIR. Please close ${BrandShortName} and retry the installation." IDRETRY argon_lock_retry
      Abort "Target files are in use: $INSTDIR\${FileMainEXE}"
    ${EndIf}
    argon_lock_retry:
    Sleep 1000
    goto argon_lock_check
  argon_lock_clear:

  ; Ensure target directory exists and install payload without touching user profiles.
  SetOutPath "$INSTDIR"
  !include "${ARGON_INSTALL_FILES_NSH}"

  CreateDirectory "$INSTDIR\uninstall"
  WriteUninstaller "$INSTDIR\uninstall\helper.exe"

  ; Standard Add/Remove Programs metadata.
  !insertmacro WriteHiveStr "${UninstallKey}" "DisplayName" "${BrandFullName}"
  !insertmacro WriteHiveStr "${UninstallKey}" "DisplayVersion" "${AppVersion}"
  !insertmacro WriteHiveStr "${UninstallKey}" "Publisher" "${CompanyName}"
  !insertmacro WriteHiveStr "${UninstallKey}" "URLInfoAbout" "${URLInfoAbout}"
  !insertmacro WriteHiveStr "${UninstallKey}" "HelpLink" "${HelpLink}"
  !insertmacro WriteHiveStr "${UninstallKey}" "DisplayIcon" "$INSTDIR\${FileMainEXE},${IDI_APPICON_ZERO_BASED}"
  !insertmacro WriteHiveStr "${UninstallKey}" "InstallLocation" "$INSTDIR"
  !insertmacro WriteHiveStr "${UninstallKey}" "UninstallString" "$\"$INSTDIR\uninstall\helper.exe$\""
  !insertmacro WriteHiveStr "${UninstallKey}" "QuietUninstallString" "$\"$INSTDIR\uninstall\helper.exe$\" /S"
  !insertmacro WriteHiveDWORD "${UninstallKey}" "NoModify" 1
  !insertmacro WriteHiveDWORD "${UninstallKey}" "NoRepair" 1
!ifdef ARGON_ESTIMATED_SIZE_KB
  !insertmacro WriteHiveDWORD "${UninstallKey}" "EstimatedSize" ${ARGON_ESTIMATED_SIZE_KB}
!endif

  !insertmacro WriteHiveStr "${InstallMetaKey}" "InstallMode" "$RegHive"
  !insertmacro WriteHiveStr "${InstallMetaKey}" "InstallDir" "$INSTDIR"
  !insertmacro WriteHiveStr "${InstallMetaKey}" "Version" "${AppVersion}"
  !insertmacro WriteHiveDWORD "${InstallMetaKey}" "StartMenuShortcut" 0
  !insertmacro WriteHiveDWORD "${InstallMetaKey}" "DesktopShortcut" 0

  ; App Paths entry so Windows Run/Search locates argon.exe cleanly.
  !insertmacro WriteHiveStr "Software\Microsoft\Windows\CurrentVersion\App Paths\${FileMainEXE}" "" "$INSTDIR\${FileMainEXE}"
  !insertmacro WriteHiveStr "Software\Microsoft\Windows\CurrentVersion\App Paths\${FileMainEXE}" "Path" "$INSTDIR"

  ; Polite Windows Capabilities / ProgID registration for Settings -> Default Apps.
  ; Intentionally NEVER modifies UserChoice or existing HTTP/HTTPS/HTML defaults.
  !insertmacro WriteHiveStr "${StartMenuKey}" "" "${BrandShortName}"
  !insertmacro WriteHiveStr "${StartMenuKey}\DefaultIcon" "" "$INSTDIR\${FileMainEXE},${IDI_APPICON_ZERO_BASED}"
  !insertmacro WriteHiveStr "${StartMenuKey}\shell\open\command" "" "$\"$INSTDIR\${FileMainEXE}$\""
  !insertmacro WriteHiveDWORD "${StartMenuKey}\InstallInfo" "IconsVisible" 1

  !insertmacro WriteHiveStr "${CapabilitiesKey}" "ApplicationDescription" "${BrandFullName}"
  !insertmacro WriteHiveStr "${CapabilitiesKey}" "ApplicationIcon" "$INSTDIR\${FileMainEXE},${IDI_APPICON_ZERO_BASED}"
  !insertmacro WriteHiveStr "${CapabilitiesKey}" "ApplicationName" "${BrandShortName}"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\FileAssociations" ".htm" "ArgonHTML"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\FileAssociations" ".html" "ArgonHTML"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\FileAssociations" ".shtml" "ArgonHTML"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\FileAssociations" ".xht" "ArgonHTML"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\FileAssociations" ".xhtml" "ArgonHTML"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\FileAssociations" ".svg" "ArgonHTML"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\FileAssociations" ".webp" "ArgonHTML"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\FileAssociations" ".pdf" "ArgonPDF"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\URLAssociations" "http" "ArgonURL"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\URLAssociations" "https" "ArgonURL"
  !insertmacro WriteHiveStr "${CapabilitiesKey}\Startmenu" "StartMenuInternet" "${AppRegName}"

  !insertmacro WriteHiveStr "Software\RegisteredApplications" "${AppRegName}" "${CapabilitiesKey}"

  !insertmacro WriteHiveStr "Software\Classes\ArgonHTML" "" "${AppRegName} HTML Document"
  !insertmacro WriteHiveStr "Software\Classes\ArgonHTML" "FriendlyTypeName" "${AppRegName} HTML Document"
  !insertmacro WriteHiveStr "Software\Classes\ArgonHTML\DefaultIcon" "" "$INSTDIR\${FileMainEXE},${IDI_DOCUMENT_ZERO_BASED}"
  !insertmacro WriteHiveStr "Software\Classes\ArgonHTML\shell\open\command" "" "$\"$INSTDIR\${FileMainEXE}$\" -osint -url $\"%1$\""

  !insertmacro WriteHiveStr "Software\Classes\ArgonPDF" "" "${AppRegName} PDF Document"
  !insertmacro WriteHiveStr "Software\Classes\ArgonPDF" "FriendlyTypeName" "${AppRegName} PDF Document"
  !insertmacro WriteHiveStr "Software\Classes\ArgonPDF\DefaultIcon" "" "$INSTDIR\${FileMainEXE},${IDI_DOCUMENT_PDF_ZERO_BASED}"
  !insertmacro WriteHiveStr "Software\Classes\ArgonPDF\shell\open\command" "" "$\"$INSTDIR\${FileMainEXE}$\" -osint -url $\"%1$\""

  !insertmacro WriteHiveStr "Software\Classes\ArgonURL" "" "${AppRegName} URL"
  !insertmacro WriteHiveStr "Software\Classes\ArgonURL" "FriendlyTypeName" "${AppRegName} URL"
  !insertmacro WriteHiveStr "Software\Classes\ArgonURL" "URL Protocol" ""
  !insertmacro WriteHiveStr "Software\Classes\ArgonURL\DefaultIcon" "" "$INSTDIR\${FileMainEXE},${IDI_APPICON_ZERO_BASED}"
  !insertmacro WriteHiveStr "Software\Classes\ArgonURL\shell\open\command" "" "$\"$INSTDIR\${FileMainEXE}$\" -osint -url $\"%1$\""

  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd

Section "Start Menu Shortcuts" SecStartMenu
  ${If} $InstallStartMenuSC == "1"
    SetOutPath "$INSTDIR"
    CreateShortcut "$SMPROGRAMS\${BrandShortName}.lnk" "$INSTDIR\${FileMainEXE}" "" "$INSTDIR\${FileMainEXE}" ${IDI_APPICON_ZERO_BASED} SW_SHOWNORMAL "" "${BrandFullName}"
    CreateShortcut "$SMPROGRAMS\${BrandShortName} Private Browsing.lnk" "$INSTDIR\${FileMainEXE}" "-private-window" "$INSTDIR\${FileMainEXE}" ${IDI_PBICON_ZERO_BASED} SW_SHOWNORMAL "" "${BrandShortName} Private Browsing"
    !insertmacro WriteHiveDWORD "${InstallMetaKey}" "StartMenuShortcut" 1
  ${Else}
    !insertmacro WriteHiveDWORD "${InstallMetaKey}" "StartMenuShortcut" 0
  ${EndIf}
SectionEnd

Section "Desktop Shortcut" SecDesktop
  ${If} $InstallDesktopSC == "1"
    SetOutPath "$INSTDIR"
    CreateShortcut "$DESKTOP\${BrandShortName}.lnk" "$INSTDIR\${FileMainEXE}" "" "$INSTDIR\${FileMainEXE}" ${IDI_APPICON_ZERO_BASED} SW_SHOWNORMAL "" "${BrandFullName}"
    !insertmacro WriteHiveDWORD "${InstallMetaKey}" "DesktopShortcut" 1
  ${Else}
    !insertmacro WriteHiveDWORD "${InstallMetaKey}" "DesktopShortcut" 0
  ${EndIf}
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "Install ${BrandFullName} core files and Windows application registration."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecStartMenu} "Create Start Menu shortcuts for ${BrandShortName} and Private Browsing."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Create a Desktop shortcut for ${BrandShortName}."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function .onInit
  SetRegView 64

  ${IfNot} ${AtLeastWin10}
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP "${BrandFullName} requires Microsoft Windows 10 (64-bit) or later."
    ${EndIf}
    Abort
  ${EndIf}

  ${IfNot} ${RunningX64}
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP "${BrandFullName} requires a 64-bit version of Windows."
    ${EndIf}
    Abort
  ${EndIf}

  UserInfo::GetAccountType
  Pop $0
  ${If} $0 == "Admin"
    StrCpy $IsAdminUser "1"
    StrCpy $RegHive "HKLM"
    SetShellVarContext all
  ${Else}
    StrCpy $IsAdminUser "0"
    StrCpy $RegHive "HKCU"
    SetShellVarContext current
  ${EndIf}

  ; Preserve install location across upgrades unless overridden via /D=...
  StrCpy $ExistingDir ""
  ReadRegStr $ExistingDir HKLM "${UninstallKey}" "InstallLocation"
  ${If} $ExistingDir != ""
  ${AndIf} $IsAdminUser == "1"
    StrCpy $RegHive "HKLM"
    SetShellVarContext all
    ${If} $INSTDIR == "$LOCALAPPDATA\Programs\${BrandShortName}"
      StrCpy $INSTDIR $ExistingDir
    ${EndIf}
  ${Else}
    ReadRegStr $ExistingDir HKCU "${UninstallKey}" "InstallLocation"
    ${If} $ExistingDir != ""
      StrCpy $RegHive "HKCU"
      SetShellVarContext current
      ${If} $INSTDIR == "$LOCALAPPDATA\Programs\${BrandShortName}"
        StrCpy $INSTDIR $ExistingDir
      ${EndIf}
    ${ElseIf} $IsAdminUser == "1"
    ${AndIf} $INSTDIR == "$LOCALAPPDATA\Programs\${BrandShortName}"
      StrCpy $INSTDIR "$PROGRAMFILES64\${BrandShortName}"
    ${EndIf}
  ${EndIf}

  StrCpy $InstallStartMenuSC "1"
  StrCpy $InstallDesktopSC "1"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/NOSTARTMENU" $R1
  ${IfNot} ${Errors}
    StrCpy $InstallStartMenuSC "0"
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/NODESKTOP" $R1
  ${IfNot} ${Errors}
    StrCpy $InstallDesktopSC "0"
  ${EndIf}
FunctionEnd

Section "Uninstall"
  ; Remove shortcuts created by the installer without deleting unrelated user shortcuts.
  ClearErrors
  ${If} $RegHive == "HKLM"
    ReadRegDWORD $0 HKLM "${InstallMetaKey}" "StartMenuShortcut"
  ${Else}
    ReadRegDWORD $0 HKCU "${InstallMetaKey}" "StartMenuShortcut"
  ${EndIf}
  ${If} ${Errors}
  ${OrIf} $0 == 1
    Delete "$SMPROGRAMS\${BrandShortName}.lnk"
    Delete "$SMPROGRAMS\${BrandShortName} Private Browsing.lnk"
  ${EndIf}

  ClearErrors
  ${If} $RegHive == "HKLM"
    ReadRegDWORD $0 HKLM "${InstallMetaKey}" "DesktopShortcut"
  ${Else}
    ReadRegDWORD $0 HKCU "${InstallMetaKey}" "DesktopShortcut"
  ${EndIf}
  ${If} ${Errors}
  ${OrIf} $0 == 1
    Delete "$DESKTOP\${BrandShortName}.lnk"
  ${EndIf}

  ; Delete only files and subdirs packaged by Argon; never touch user profiles
  ; (%APPDATA%\argon, %LOCALAPPDATA%\argon, Mozilla, or LibreWolf).
  !include "${ARGON_UNINSTALL_FILES_NSH}"
  Delete "$INSTDIR\uninstall\helper.exe"
  RMDir "$INSTDIR\uninstall"
  RMDir "$INSTDIR"

  ; Remove Argon Windows registration keys without touching UserChoice.
  !insertmacro un_DeleteHiveKey "${UninstallKey}"
  !insertmacro un_DeleteHiveKey "Software\Microsoft\Windows\CurrentVersion\App Paths\${FileMainEXE}"
  !insertmacro un_DeleteHiveValue "Software\RegisteredApplications" "${AppRegName}"
  !insertmacro un_DeleteHiveKey "${StartMenuKey}"
  !insertmacro un_DeleteHiveKey "Software\Classes\ArgonHTML"
  !insertmacro un_DeleteHiveKey "Software\Classes\ArgonPDF"
  !insertmacro un_DeleteHiveKey "Software\Classes\ArgonURL"
  !insertmacro un_DeleteHiveKey "Software\Argon"

  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd

Function un.onInit
  SetRegView 64

  UserInfo::GetAccountType
  Pop $0
  StrCpy $1 ""
  ReadRegStr $1 HKLM "${UninstallKey}" "InstallLocation"
  ${If} $1 != ""
  ${AndIf} $0 == "Admin"
    StrCpy $RegHive "HKLM"
    SetShellVarContext all
  ${Else}
    StrCpy $RegHive "HKCU"
    SetShellVarContext current
  ${EndIf}
FunctionEnd
