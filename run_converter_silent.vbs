Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
currentDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentDir

pythonExe = currentDir & "\.venv\Scripts\pythonw.exe"
if Not FSO.FileExists(pythonExe) Then
    pythonExe = currentDir & "\.venv\Scripts\python.exe"
End If

appPy = currentDir & "\app.py"

WshShell.Run Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & appPy & Chr(34), 0, False
