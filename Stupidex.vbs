' Stupidex launcher - runs the .exe with no console window.
' Double-click this file to start the server hidden in the background.
' To stop: open Task Manager and end the Stupidex.exe process.
Option Explicit
Dim shell, fso, exePath, batPath

Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

exePath = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "Stupidex.exe")
batPath = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "Stupidex.bat")

If Not fso.FileExists(exePath) Then
    MsgBox "Stupidex.exe not found at:" & vbCrLf & exePath, vbExclamation, "Stupidex"
    WScript.Quit 1
End If

If Not fso.FileExists(batPath) Then
    shell.Run """" & exePath & """", 0, False
Else
    shell.Run """" & batPath & """", 0, False
End If
