# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# NSIS branding defines for Argon Browser.
# Compatible with both packaging/windows/argon-installer.nsi and Mozilla's
# browser/installer/windows/nsis/ build pipeline.

!define BrandFullNameInternal "Argon"
!define BrandFullName         "Argon Browser"
!define CompanyName           "Argon"
!define URLInfoAbout          "https://github.com/Argon-Browser/Argon"
!define HelpLink              "https://github.com/Argon-Browser/Argon/issues"

!define URLStubDownloadX86     "https://github.com/Argon-Browser/Argon/releases"
!define URLStubDownloadAMD64   "https://github.com/Argon-Browser/Argon/releases"
!define URLStubDownloadAArch64 "https://github.com/Argon-Browser/Argon/releases"
!define URLManualDownload      "https://github.com/Argon-Browser/Argon/releases"
!define URLSystemRequirements  "https://github.com/Argon-Browser/Argon"
!define Channel                "argon"

!define CertNameDownload   "Argon"
!define CertIssuerDownload "Argon"

; Dialog units so the UI scales cleanly with Windows DPI settings.
!define PROFILE_CLEANUP_LABEL_TOP "35u"
!define PROFILE_CLEANUP_LABEL_LEFT "0"
!define PROFILE_CLEANUP_LABEL_WIDTH "100%"
!define PROFILE_CLEANUP_LABEL_HEIGHT "80u"
!define PROFILE_CLEANUP_LABEL_ALIGN "center"
!define PROFILE_CLEANUP_CHECKBOX_LEFT "center"
!define PROFILE_CLEANUP_CHECKBOX_WIDTH "100%"
!define PROFILE_CLEANUP_BUTTON_LEFT "center"
!define INSTALL_BLURB_TOP "137u"
!define INSTALL_BLURB_WIDTH "60u"
!define INSTALL_FOOTER_TOP "-48u"
!define INSTALL_FOOTER_WIDTH "250u"
!define INSTALL_INSTALLING_TOP "70u"
!define INSTALL_INSTALLING_LEFT "0"
!define INSTALL_INSTALLING_WIDTH "100%"
!define INSTALL_PROGRESS_BAR_TOP "112u"
!define INSTALL_PROGRESS_BAR_LEFT "20%"
!define INSTALL_PROGRESS_BAR_WIDTH "60%"
!define INSTALL_PROGRESS_BAR_HEIGHT "12u"

!define PROFILE_CLEANUP_CHECKBOX_TOP_MARGIN "20u"
!define PROFILE_CLEANUP_BUTTON_TOP_MARGIN "20u"
!define PROFILE_CLEANUP_BUTTON_X_PADDING "40u"
!define PROFILE_CLEANUP_BUTTON_Y_PADDING "4u"

!define INSTALL_HEADER_FONT_SIZE 28
!define INSTALL_HEADER_FONT_WEIGHT 400
!define INSTALL_INSTALLING_FONT_SIZE 28
!define INSTALL_INSTALLING_FONT_WEIGHT 400

; Argon dark chrome palette (#FBF8F4 Ivory text on #15171B Dark Chrome)
!define COMMON_TEXT_COLOR 0xFBF8F4
!define COMMON_BACKGROUND_COLOR 0x15171B
!define INSTALL_INSTALLING_TEXT_COLOR 0xFBF8F4
