; HandOff uninstall: remove HandOff's own data folder, and nothing else (ADR-043, DEPLOYMENT §14-15).
; The target is a fixed path, %APPDATA%\HandOff. It is never derived from user input.
!macro NSIS_HOOK_POSTUNINSTALL
  MessageBox MB_YESNO|MB_ICONEXCLAMATION \
    "Also delete HandOff's data (transferred files, imported files, trusted devices, history and settings)?$\r$\n$\r$\nThis removes only the HandOff data folder: $APPDATA\HandOff" \
    /SD IDYES IDNO handoff_keep_data
  RMDir /r "$APPDATA\HandOff"
  handoff_keep_data:
!macroend
