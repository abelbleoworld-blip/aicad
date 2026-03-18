"""
FreeCAD AI Tool — addon that runs the HTTP API server inside FreeCAD GUI
and provides a server control panel as a dock widget.
"""

import FreeCAD
import FreeCADGui


class FreecadAIToolWorkbench(FreeCADGui.Workbench):
    MenuText = "AI Tool"
    ToolTip = "FreeCAD AI Tool — HTTP API server for CLI/AI control"
    # Icon = ""  # TODO: add icon

    def Initialize(self):
        import AIToolCommands
        self.appendToolbar("AI Tool", ["AITool_StartServer", "AITool_StopServer", "AITool_ShowPanel"])
        self.appendMenu("AI Tool", ["AITool_StartServer", "AITool_StopServer", "AITool_ShowPanel"])

    def Activated(self):
        pass

    def Deactivated(self):
        pass


FreeCADGui.addWorkbench(FreecadAIToolWorkbench())

# Auto-start: register the commands so they're available from any workbench
try:
    import AIToolCommands
except Exception as e:
    FreeCAD.Console.PrintWarning(f"AI Tool: Could not load commands: {e}\n")
