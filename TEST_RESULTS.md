# Version 2.8.0 verification

The complete suite now contains 74 tests, including eight GUI tests and three updater tests covering GitHub asset selection and ZIP path-traversal protection. In the current macOS verification, 66 tests passed and the eight display-dependent GUI tests were skipped. The GUI tests passed in the prior virtual-display run. Native Windows rendering and the EXE build have not been verified here.

The Windows GitHub Actions job runs the complete 74-test suite before building or publishing a Release. The PowerShell install/update swap and native Windows packaging still require their first workflow and disposable-PC acceptance run.

The guided uninstaller passed static structure and required-operation checks.
Its destructive Windows operations must be acceptance-tested on a disposable
Windows installation before office-wide use.

The compact two-row filter layout and persistent dropdown passed syntax and
structural checks. The non-display suite passed; the dropdown's native Windows
event behavior still requires verification in the rebuilt EXE.
