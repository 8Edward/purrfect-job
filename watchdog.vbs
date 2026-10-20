' Runs watchdog.ps1 with no visible window (called by the scheduled task "career-ops bot watchdog").
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & here & "\watchdog.ps1""", 0, True
