"""
AI Tool commands and server panel for FreeCAD.
"""

import FreeCAD
import FreeCADGui
import threading
import json
import os
import sys
import base64
import tempfile
import time
from collections import deque
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from PySide import QtCore, QtGui, QtWidgets

# ─── History Logger ──────────────────────────────────────────────────────────

HISTORY_DIR = os.path.expanduser("~/freecad-ai-tool-history")
os.makedirs(HISTORY_DIR, exist_ok=True)

_history_file = None
_history_lock = threading.Lock()


def _get_history_file():
    """One log file per day: 2026-03-18.jsonl"""
    global _history_file
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(HISTORY_DIR, f"{today}.jsonl")
    if _history_file is None or _history_file.name != path:
        if _history_file is not None:
            _history_file.close()
        _history_file = open(path, "a", encoding="utf-8")
    return _history_file


def _get_history_today(limit=100):
    """Read today's history, last N entries."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(HISTORY_DIR, f"{today}.jsonl")
    if not os.path.exists(path):
        return {"entries": [], "file": path}
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return {"entries": entries, "total": len(lines), "file": path}


def history_write(event_type, data):
    """Write an event to history log. Thread-safe."""
    entry = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "type": event_type,
        **data,
    }
    with _history_lock:
        try:
            f = _get_history_file()
            f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
        except Exception:
            pass


# ─── Import the API tools ────────────────────────────────────────────────────

# Add the freecad-api directory to path
API_DIR = os.path.expanduser("~/freecad-api")
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

# We inline the tools here since we're running inside FreeCAD GUI
import Part

_server = None
_server_thread = None
_panel = None

TOOLS = {}


def tool(func):
    TOOLS[func.__name__] = func
    return func


def _get_doc(name=None):
    if name is None:
        if FreeCAD.ActiveDocument is None:
            raise ValueError("No active document. Create one first.")
        return FreeCAD.ActiveDocument
    doc = FreeCAD.getDocument(name)
    if doc is None:
        raise ValueError(f"Document '{name}' not found")
    return doc


# ─── Tools ────────────────────────────────────────────────────────────────────

@tool
def list_tools(**kw):
    """List all available tools."""
    return {name: (f.__doc__ or "").strip() for name, f in TOOLS.items()}

@tool
def new_document(name="Untitled", **kw):
    """Create a new FreeCAD document."""
    doc = FreeCAD.newDocument(name)
    return {"document": doc.Name}

@tool
def list_documents(**kw):
    """List all open documents."""
    return {"documents": [d.Name for d in FreeCAD.listDocuments().values()]}

@tool
def list_objects(document=None, **kw):
    """List all objects in a document with volumes and bboxes."""
    doc = _get_doc(document)
    objs = []
    for obj in doc.Objects:
        info = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
        if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
            info["volume"] = round(obj.Shape.Volume, 4)
            bb = obj.Shape.BoundBox
            info["bbox"] = {
                "xmin": round(bb.XMin, 3), "ymin": round(bb.YMin, 3), "zmin": round(bb.ZMin, 3),
                "xmax": round(bb.XMax, 3), "ymax": round(bb.YMax, 3), "zmax": round(bb.ZMax, 3),
            }
        objs.append(info)
    return {"objects": objs}

@tool
def add_box(length=10, width=10, height=10, label="Box", document=None, **kw):
    """Add a box."""
    doc = _get_doc(document)
    obj = doc.addObject("Part::Box", label)
    obj.Length = length
    obj.Width = width
    obj.Height = height
    doc.recompute()
    return {"name": obj.Name, "label": obj.Label, "volume": round(obj.Shape.Volume, 4)}

@tool
def add_cylinder(radius=5, height=10, label="Cylinder", document=None, **kw):
    """Add a cylinder."""
    doc = _get_doc(document)
    obj = doc.addObject("Part::Cylinder", label)
    obj.Radius = radius
    obj.Height = height
    doc.recompute()
    return {"name": obj.Name, "label": obj.Label, "volume": round(obj.Shape.Volume, 4)}

@tool
def add_sphere(radius=5, label="Sphere", document=None, **kw):
    """Add a sphere."""
    doc = _get_doc(document)
    obj = doc.addObject("Part::Sphere", label)
    obj.Radius = radius
    doc.recompute()
    return {"name": obj.Name, "label": obj.Label, "volume": round(obj.Shape.Volume, 4)}

@tool
def add_cone(radius1=5, radius2=0, height=10, label="Cone", document=None, **kw):
    """Add a cone."""
    doc = _get_doc(document)
    obj = doc.addObject("Part::Cone", label)
    obj.Radius1 = radius1
    obj.Radius2 = radius2
    obj.Height = height
    doc.recompute()
    return {"name": obj.Name, "label": obj.Label, "volume": round(obj.Shape.Volume, 4)}

@tool
def add_torus(radius1=10, radius2=2, label="Torus", document=None, **kw):
    """Add a torus."""
    doc = _get_doc(document)
    obj = doc.addObject("Part::Torus", label)
    obj.Radius1 = radius1
    obj.Radius2 = radius2
    doc.recompute()
    return {"name": obj.Name, "label": obj.Label, "volume": round(obj.Shape.Volume, 4)}

@tool
def move_object(name, x=0, y=0, z=0, document=None, **kw):
    """Move an object to (x, y, z)."""
    doc = _get_doc(document)
    obj = doc.getObject(name)
    if not obj:
        raise ValueError(f"Object '{name}' not found")
    obj.Placement.Base = FreeCAD.Vector(x, y, z)
    doc.recompute()
    return {"name": obj.Name, "position": [x, y, z]}

@tool
def rotate_object(name, axis_x=0, axis_y=0, axis_z=1, angle=0, document=None, **kw):
    """Rotate an object around axis by angle (degrees)."""
    doc = _get_doc(document)
    obj = doc.getObject(name)
    if not obj:
        raise ValueError(f"Object '{name}' not found")
    rot = FreeCAD.Rotation(FreeCAD.Vector(axis_x, axis_y, axis_z), angle)
    obj.Placement.Rotation = rot
    doc.recompute()
    return {"name": obj.Name, "angle": angle}

@tool
def set_property(name, property, value, document=None, **kw):
    """Set a property on an object."""
    doc = _get_doc(document)
    obj = doc.getObject(name)
    if not obj:
        raise ValueError(f"Object '{name}' not found")
    setattr(obj, property, value)
    doc.recompute()
    return {"name": obj.Name, "property": property, "value": value}

@tool
def get_properties(name, document=None, **kw):
    """Get all properties of an object."""
    doc = _get_doc(document)
    obj = doc.getObject(name)
    if not obj:
        raise ValueError(f"Object '{name}' not found")
    props = {}
    for p in obj.PropertiesList:
        try:
            val = getattr(obj, p)
            if isinstance(val, (int, float, str, bool)):
                props[p] = val
            elif isinstance(val, FreeCAD.Vector):
                props[p] = [val.x, val.y, val.z]
            else:
                props[p] = str(val)
        except:
            pass
    return {"name": obj.Name, "properties": props}

@tool
def boolean_union(name1, name2, label="Union", document=None, **kw):
    """Boolean union of two objects."""
    doc = _get_doc(document)
    obj1, obj2 = doc.getObject(name1), doc.getObject(name2)
    if not obj1 or not obj2:
        raise ValueError(f"Objects not found: {name1}, {name2}")
    fuse = doc.addObject("Part::Fuse", label)
    fuse.Shape1 = obj1
    fuse.Shape2 = obj2
    doc.recompute()
    return {"name": fuse.Name, "volume": round(fuse.Shape.Volume, 4)}

@tool
def boolean_cut(name1, name2, label="Cut", document=None, **kw):
    """Boolean cut (subtract name2 from name1)."""
    doc = _get_doc(document)
    obj1, obj2 = doc.getObject(name1), doc.getObject(name2)
    if not obj1 or not obj2:
        raise ValueError(f"Objects not found: {name1}, {name2}")
    cut = doc.addObject("Part::Cut", label)
    cut.Base = obj1
    cut.Tool = obj2
    doc.recompute()
    return {"name": cut.Name, "volume": round(cut.Shape.Volume, 4)}

@tool
def boolean_intersection(name1, name2, label="Intersection", document=None, **kw):
    """Boolean intersection of two objects."""
    doc = _get_doc(document)
    obj1, obj2 = doc.getObject(name1), doc.getObject(name2)
    if not obj1 or not obj2:
        raise ValueError(f"Objects not found: {name1}, {name2}")
    common = doc.addObject("Part::Common", label)
    common.Shape1 = obj1
    common.Shape2 = obj2
    doc.recompute()
    return {"name": common.Name, "volume": round(common.Shape.Volume, 4)}

@tool
def create_sketch_rectangle(width=20, height=10, plane="XY", label="Sketch", document=None, **kw):
    """Create a sketch with a rectangle."""
    doc = _get_doc(document)
    sketch = doc.addObject("Sketcher::SketchObject", label)
    planes = {
        "XY": FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation(0,0,0,1)),
        "XZ": FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation(FreeCAD.Vector(1,0,0), -90)),
        "YZ": FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation(FreeCAD.Vector(0,1,0), 90)),
    }
    sketch.Placement = planes.get(plane, planes["XY"])
    w2, h2 = width/2, height/2
    sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(-w2,-h2,0), FreeCAD.Vector(w2,-h2,0)))
    sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(w2,-h2,0), FreeCAD.Vector(w2,h2,0)))
    sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(w2,h2,0), FreeCAD.Vector(-w2,h2,0)))
    sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(-w2,h2,0), FreeCAD.Vector(-w2,-h2,0)))
    doc.recompute()
    return {"name": sketch.Name, "width": width, "height": height}

@tool
def extrude_sketch(sketch_name, length=10, document=None, **kw):
    """Extrude a sketch."""
    doc = _get_doc(document)
    sketch = doc.getObject(sketch_name)
    if not sketch:
        raise ValueError(f"Sketch '{sketch_name}' not found")
    pad = doc.addObject("Part::Extrusion", sketch_name + "_Extrude")
    pad.Base = sketch
    pad.Dir = FreeCAD.Vector(0, 0, length)
    pad.Solid = True
    doc.recompute()
    return {"name": pad.Name, "length": length}

@tool
def save_document(path, document=None, **kw):
    """Save document to .FCStd file."""
    doc = _get_doc(document)
    path = os.path.expanduser(path)
    if not path.endswith(".FCStd"):
        path += ".FCStd"
    doc.saveAs(path)
    return {"saved": path}

@tool
def export_step(path, objects=None, document=None, **kw):
    """Export to STEP file."""
    doc = _get_doc(document)
    path = os.path.expanduser(path)
    if not path.endswith((".step", ".stp")):
        path += ".step"
    if objects:
        shapes = [doc.getObject(n).Shape for n in objects if doc.getObject(n)]
    else:
        shapes = [o.Shape for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()]
    if not shapes:
        raise ValueError("No shapes to export")
    compound = Part.makeCompound(shapes)
    compound.exportStep(path)
    return {"exported": path, "objects": len(shapes)}

@tool
def export_stl(path, objects=None, document=None, **kw):
    """Export to STL file."""
    doc = _get_doc(document)
    path = os.path.expanduser(path)
    if not path.endswith(".stl"):
        path += ".stl"
    import Mesh
    if objects:
        shapes = [doc.getObject(n).Shape for n in objects if doc.getObject(n)]
    else:
        shapes = [o.Shape for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()]
    if not shapes:
        raise ValueError("No shapes to export")
    mesh_list = [Mesh.Mesh(s.tessellate(0.1)) for s in shapes]
    merged = mesh_list[0]
    for m in mesh_list[1:]:
        merged.addMesh(m)
    merged.write(path)
    return {"exported": path, "objects": len(shapes)}

@tool
def close_document(document=None, **kw):
    """Close a document."""
    doc = _get_doc(document)
    name = doc.Name
    FreeCAD.closeDocument(name)
    return {"closed": name}

@tool
def fit_view(**kw):
    """Fit all objects in the 3D view."""
    FreeCADGui.SendMsgToActiveView("ViewFit")
    return {"ok": True}

@tool
def run_python(code, **kw):
    """Execute arbitrary FreeCAD Python code."""
    local_vars = {"FreeCAD": FreeCAD, "FreeCADGui": FreeCADGui, "Part": Part}
    exec(code, local_vars)
    result = local_vars.get("result", "executed")
    if not isinstance(result, (str, int, float, bool, list, dict, type(None))):
        result = str(result)
    return {"result": result}


# ─── Document Observer (real-time change tracking) ────────────────────────────

_change_log = deque(maxlen=200)
_observer_installed = False


class AIToolDocumentObserver:
    """Tracks all changes in FreeCAD documents in real-time."""

    def _log(self, entry):
        _change_log.append(entry)
        history_write("model", entry)

    def slotCreatedObject(self, obj):
        self._log({
            "time": time.time(),
            "event": "created",
            "object": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
        })

    def slotDeletedObject(self, obj):
        self._log({
            "time": time.time(),
            "event": "deleted",
            "object": obj.Name,
            "label": obj.Label,
        })

    def slotChangedObject(self, obj, prop):
        # Skip noisy internal properties
        if prop in ("ExpressionEngine", "Proxy", "Shape"):
            return
        try:
            val = getattr(obj, prop)
            if isinstance(val, FreeCAD.Vector):
                val = [round(val.x, 3), round(val.y, 3), round(val.z, 3)]
            elif not isinstance(val, (int, float, str, bool)):
                val = str(val)
        except Exception:
            val = "?"
        self._log({
            "time": time.time(),
            "event": "changed",
            "object": obj.Name,
            "label": obj.Label,
            "property": prop,
            "value": val,
        })

    def slotCreatedDocument(self, doc):
        self._log({
            "time": time.time(),
            "event": "document_created",
            "document": doc.Name,
        })

    def slotDeletedDocument(self, doc):
        self._log({
            "time": time.time(),
            "event": "document_deleted",
            "document": doc.Name,
        })


_doc_observer = AIToolDocumentObserver()


def install_observer():
    global _observer_installed
    if not _observer_installed:
        FreeCAD.addDocumentObserver(_doc_observer)
        _observer_installed = True


def uninstall_observer():
    global _observer_installed
    if _observer_installed:
        FreeCAD.removeDocumentObserver(_doc_observer)
        _observer_installed = False


# ─── Vision Tools ─────────────────────────────────────────────────────────────

@tool
def get_screenshot(width=1920, height=1080, **kw):
    """Capture current 3D viewport as base64 PNG image. Returns data:image/png;base64,..."""
    try:
        view = FreeCADGui.ActiveDocument.ActiveView
    except Exception:
        raise ValueError("No active 3D view")

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        view.saveImage(tmp.name, width, height, "Current")
        with open(tmp.name, "rb") as f:
            img_data = f.read()
        img_b64 = base64.b64encode(img_data).decode()
        return {
            "image": f"data:image/png;base64,{img_b64}",
            "width": width,
            "height": height,
            "size_bytes": len(img_data),
        }
    finally:
        os.unlink(tmp.name)


@tool
def get_model_state(document=None, **kw):
    """Get full model state: all objects, properties, volumes, bboxes. For AI vision."""
    doc = _get_doc(document)
    objects = []
    for obj in doc.Objects:
        info = {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "visible": obj.ViewObject.Visibility if hasattr(obj, "ViewObject") else None,
        }
        # Geometry info
        if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
            info["volume"] = round(obj.Shape.Volume, 4)
            info["area"] = round(obj.Shape.Area, 4)
            bb = obj.Shape.BoundBox
            info["bbox"] = {
                "xmin": round(bb.XMin, 3), "ymin": round(bb.YMin, 3), "zmin": round(bb.ZMin, 3),
                "xmax": round(bb.XMax, 3), "ymax": round(bb.YMax, 3), "zmax": round(bb.ZMax, 3),
            }
            info["center"] = [round(bb.Center.x, 3), round(bb.Center.y, 3), round(bb.Center.z, 3)]
            info["edges"] = len(obj.Shape.Edges)
            info["faces"] = len(obj.Shape.Faces)
        # Key properties
        props = {}
        for p in obj.PropertiesList:
            try:
                val = getattr(obj, p)
                if isinstance(val, (int, float, str, bool)):
                    props[p] = val
                elif isinstance(val, FreeCAD.Vector):
                    props[p] = [round(val.x, 3), round(val.y, 3), round(val.z, 3)]
            except Exception:
                pass
        info["properties"] = props
        objects.append(info)

    return {
        "document": doc.Name,
        "label": doc.Label,
        "objects_count": len(objects),
        "objects": objects,
    }


@tool
def get_changes(since=0, limit=50, **kw):
    """Get recent changes since timestamp. Returns list of change events tracked by DocumentObserver."""
    changes = [c for c in _change_log if c["time"] > since]
    if limit:
        changes = changes[-limit:]
    return {
        "changes": changes,
        "total": len(_change_log),
        "server_time": time.time(),
    }


@tool
def get_camera(**kw):
    """Get current camera position and orientation."""
    try:
        view = FreeCADGui.ActiveDocument.ActiveView
        cam = view.getCameraNode()
        pos = cam.position.getValue()
        orient = cam.orientation.getValue()
        return {
            "position": [pos[0], pos[1], pos[2]],
            "orientation": [orient[0], orient[1], orient[2], orient[3]],
            "type": "perspective" if view.getCameraType() == "Perspective" else "orthographic",
        }
    except Exception as e:
        raise ValueError(f"Cannot get camera: {e}")


@tool
def set_camera(position=None, look_at=None, **kw):
    """Set camera position. Optionally look at a point."""
    try:
        view = FreeCADGui.ActiveDocument.ActiveView
        if position:
            cam = view.getCameraNode()
            from pivy import coin
            cam.position.setValue(coin.SbVec3f(position[0], position[1], position[2]))
        if look_at:
            cam = view.getCameraNode()
            from pivy import coin
            cam.pointAt(coin.SbVec3f(look_at[0], look_at[1], look_at[2]))
        FreeCADGui.updateGui()
        return {"ok": True}
    except Exception as e:
        raise ValueError(f"Cannot set camera: {e}")


@tool
def get_selection(**kw):
    """Get currently selected objects."""
    sel = FreeCADGui.Selection.getSelection()
    result = []
    for obj in sel:
        info = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
        if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
            info["volume"] = round(obj.Shape.Volume, 4)
        result.append(info)
    return {"selection": result, "count": len(result)}


@tool
def get_vision(width=1280, height=720, **kw):
    """AI Vision: returns screenshot + model state + recent changes in one call. Most efficient for AI."""
    screenshot = get_screenshot(width=width, height=height)
    state = get_model_state()
    changes = get_changes(since=time.time() - 10)  # last 10 seconds
    selection = get_selection()
    camera = {}
    try:
        camera = get_camera()
    except Exception:
        pass

    return {
        "screenshot": screenshot,
        "model": state,
        "changes": changes,
        "selection": selection,
        "camera": camera,
        "timestamp": time.time(),
    }


# ─── HTTP Server (runs in thread inside FreeCAD GUI) ──────────────────────────

class AIToolHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log_to_panel(f"[HTTP] {format % args}")

    def _send_json(self, data, code=200):
        body = json.dumps(data, default=str, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_png(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path == "/tools":
            self._send_json({"tools": list(TOOLS.keys())})
        elif self.path == "/state":
            self._send_json(get_model_state())
        elif self.path == "/changes":
            self._send_json(get_changes())
        elif self.path == "/selection":
            self._send_json(get_selection())
        elif self.path == "/camera":
            try:
                self._send_json(get_camera())
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        elif self.path.startswith("/screenshot"):
            try:
                result = get_screenshot()
                # Return raw PNG for browser preview
                raw = base64.b64decode(result["image"].split(",")[1])
                self._send_png(raw)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        elif self.path == "/vision":
            try:
                self._send_json(get_vision())
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        elif self.path == "/history":
            self._send_json(_get_history_today())
        elif self.path == "/history/files":
            files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".jsonl"))
            self._send_json({"dir": HISTORY_DIR, "files": files})
        else:
            self._send_json({"error": "Use POST /call or GET /state /screenshot /vision /changes"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, 400)
            return

        tool_name = data.get("tool") or self.path.lstrip("/")
        args = data.get("args", {})

        if tool_name not in TOOLS:
            self._send_json({"error": f"Unknown tool: {tool_name}"}, 404)
            return

        try:
            t0 = time.time()
            result = TOOLS[tool_name](**args)
            dt = round((time.time() - t0) * 1000)
            try:
                FreeCADGui.updateGui()
            except Exception:
                pass
            self._send_json({"ok": True, "result": result})
            log_to_panel(f"OK {tool_name} ({dt}ms)")
            history_write("api", {"tool": tool_name, "args": args, "ms": dt, "ok": True})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)
            log_to_panel(f"ERR {tool_name}: {e}")
            history_write("api", {"tool": tool_name, "args": args, "ok": False, "error": str(e)})


def _kill_port(port):
    """Kill any process using the given port."""
    import subprocess
    try:
        result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        pids = result.stdout.strip().split()
        for pid in pids:
            if pid:
                subprocess.run(["kill", "-9", pid], capture_output=True)
                log_to_panel(f"Killed old process on port {port} (PID {pid})")
    except Exception:
        pass


def start_server(port=8765):
    global _server, _server_thread
    if _server is not None:
        log_to_panel("Server already running!")
        return

    try:
        _server = HTTPServer(("127.0.0.1", port), AIToolHandler)
    except OSError:
        log_to_panel(f"Port {port} busy, killing old process...")
        _kill_port(port)
        import time
        time.sleep(0.5)
        _server = HTTPServer(("127.0.0.1", port), AIToolHandler)

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    install_observer()
    log_to_panel(f"Server started → http://127.0.0.1:{port}")
    log_to_panel(f"Vision: /screenshot /state /vision /changes")
    log_to_panel(f"History: {HISTORY_DIR}")
    FreeCAD.Console.PrintMessage(f"AI Tool: HTTP server running on port {port}\n")
    history_write("server", {"event": "started", "port": port})


def stop_server():
    global _server, _server_thread
    if _server is None:
        log_to_panel("Server not running")
        return
    _server.shutdown()
    _server = None
    _server_thread = None
    log_to_panel("Server stopped")
    FreeCAD.Console.PrintMessage("AI Tool: server stopped\n")
    history_write("server", {"event": "stopped"})


# ─── UI: Status Bar Widget (compact server indicator) ────────────────────────

_log_widget = None
_terminal_input = None


def log_to_panel(msg):
    global _log_widget
    if _log_widget is not None:
        _log_widget.appendPlainText(msg)
    FreeCAD.Console.PrintMessage(f"AI Tool: {msg}\n")


class AIToolStatusWidget(QtWidgets.QWidget):
    """Tiny status bar widget: green dot + port number + toggle button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        # Status dot
        self.dot = QtWidgets.QLabel("●")
        self.dot.setStyleSheet("color: #666; font-size: 14px;")
        layout.addWidget(self.dot)

        # Label
        self.label = QtWidgets.QLabel("AI: off")
        self.label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.label)

        # Toggle button
        self.btn = QtWidgets.QPushButton("Start")
        self.btn.setFixedSize(50, 20)
        self.btn.setStyleSheet("font-size: 10px; padding: 1px 4px;")
        self.btn.clicked.connect(self._toggle)
        layout.addWidget(self.btn)

    def _toggle(self):
        if _server is None:
            start_server(8765)
            self.dot.setStyleSheet("color: #00cc00; font-size: 14px;")
            self.label.setText("AI: 8765")
            self.btn.setText("Stop")
        else:
            stop_server()
            self.dot.setStyleSheet("color: #666; font-size: 14px;")
            self.label.setText("AI: off")
            self.btn.setText("Start")

    def set_running(self, port):
        self.dot.setStyleSheet("color: #00cc00; font-size: 14px;")
        self.label.setText(f"AI: {port}")
        self.btn.setText("Stop")


# ─── UI: Terminal Panel (dock widget with command input + log) ────────────────

TERMINAL_STYLE = """
    QPlainTextEdit {
        font-family: 'Menlo', 'SF Mono', 'Courier New', monospace;
        font-size: 12px;
        background-color: #1e1e1e;
        color: #e0e0e0;
        border: none;
        padding: 6px;
        selection-background-color: #ff660044;
    }
    QPlainTextEdit:disabled {
        color: #e0e0e0;
        background-color: #1e1e1e;
    }
"""

INPUT_STYLE = """
    QLineEdit {
        font-family: 'Menlo', 'SF Mono', 'Courier New', monospace;
        font-size: 12px;
        background-color: #2a2a2a;
        color: #ff6600;
        border: 1px solid #555555;
        border-radius: 3px;
        padding: 4px 8px;
    }
    QLineEdit:focus {
        border: 1px solid #ff6600;
    }
    QLineEdit:disabled, QLineEdit:!active {
        color: #ff6600;
        background-color: #2a2a2a;
        border: 1px solid #555555;
    }
"""


class AIToolTerminal(QtWidgets.QDockWidget):
    """Full-featured FreeCAD Python terminal + AI tool commands + Russian NLP."""

    def __init__(self, parent=None):
        super().__init__("AI Terminal", parent)
        self.setObjectName("AIToolTerminal")
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable |
            QtWidgets.QDockWidget.DockWidgetFloatable
        )

        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tab bar: AI | Python | Log
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: #2a2a2a; color: #8090a0; padding: 4px 12px;
                border: none; border-bottom: 2px solid transparent;
                font-family: 'Menlo', monospace; font-size: 11px;
            }
            QTabBar::tab:selected { color: #ff6600; border-bottom: 2px solid #ff6600; }
            QTabBar::tab:hover { color: #e0e0e0; }
        """)

        # === TAB 1: AI Commands (Russian + tool names) ===
        ai_widget = QtWidgets.QWidget()
        ai_layout = QtWidgets.QVBoxLayout(ai_widget)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_layout.setSpacing(0)

        self.ai_log = QtWidgets.QPlainTextEdit()
        self.ai_log.setReadOnly(True)
        self.ai_log.setMaximumBlockCount(500)
        self.ai_log.setStyleSheet(TERMINAL_STYLE)
        ai_layout.addWidget(self.ai_log)

        ai_input_layout = QtWidgets.QHBoxLayout()
        ai_input_layout.setContentsMargins(4, 2, 4, 4)
        ai_input_layout.setSpacing(4)
        prompt1 = QtWidgets.QLabel("❯")
        prompt1.setStyleSheet("color: #ff6600; font-size: 14px; font-weight: bold; padding: 2px;")
        ai_input_layout.addWidget(prompt1)
        self.ai_input = QtWidgets.QLineEdit()
        self.ai_input.setStyleSheet(INPUT_STYLE)
        self.ai_input.setPlaceholderText("создай коробку 50 30 20  |  добавь цилиндр 10 30  |  помощь")
        self.ai_input.returnPressed.connect(self._execute_ai)
        ai_input_layout.addWidget(self.ai_input)
        ai_layout.addLayout(ai_input_layout)
        self.tabs.addTab(ai_widget, "AI")

        # === TAB 2: Python Console (full FreeCAD Python) ===
        py_widget = QtWidgets.QWidget()
        py_layout = QtWidgets.QVBoxLayout(py_widget)
        py_layout.setContentsMargins(0, 0, 0, 0)
        py_layout.setSpacing(0)

        self.py_log = QtWidgets.QPlainTextEdit()
        self.py_log.setReadOnly(True)
        self.py_log.setMaximumBlockCount(1000)
        self.py_log.setStyleSheet(TERMINAL_STYLE)
        self.py_log.appendPlainText("FreeCAD Python Console")
        self.py_log.appendPlainText(f"Python {sys.version.split()[0]} | FreeCAD {'.'.join(FreeCAD.Version()[:3])}")
        self.py_log.appendPlainText("Type any Python code. FreeCAD, Part, FreeCADGui are available.\n")
        py_layout.addWidget(self.py_log)

        py_input_layout = QtWidgets.QHBoxLayout()
        py_input_layout.setContentsMargins(4, 2, 4, 4)
        py_input_layout.setSpacing(4)
        prompt2 = QtWidgets.QLabel(">>>")
        prompt2.setStyleSheet("color: #ffcc00; font-size: 12px; font-weight: bold; padding: 2px; font-family: 'Menlo', monospace;")
        py_input_layout.addWidget(prompt2)
        self.py_input = QtWidgets.QLineEdit()
        self.py_input.setStyleSheet(INPUT_STYLE.replace("#ff6600", "#ffcc00"))
        self.py_input.setPlaceholderText("FreeCAD.ActiveDocument.Objects  |  Part.makeBox(10,20,30)")
        self.py_input.returnPressed.connect(self._execute_python)
        py_input_layout.addWidget(self.py_input)
        py_layout.addLayout(py_input_layout)
        self.tabs.addTab(py_widget, "Python")

        # === TAB 3: Server Log ===
        log_widget = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)

        global _log_widget
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setStyleSheet(TERMINAL_STYLE)
        _log_widget = self.log
        log_layout.addWidget(self.log)
        self.tabs.addTab(log_widget, "Log")

        layout.addWidget(self.tabs)
        self.setWidget(widget)

        self._ai_history = []
        self._ai_hist_idx = -1
        self._py_history = []
        self._py_hist_idx = -1
        self._py_globals = {
            "FreeCAD": FreeCAD,
            "FreeCADGui": FreeCADGui,
            "Part": Part,
            "print": self._py_print,
        }

    def _py_print(self, *args, **kwargs):
        """Redirect print() to Python tab."""
        text = " ".join(str(a) for a in args)
        self.py_log.appendPlainText(text)

    def _execute_python(self):
        """Execute raw Python code in FreeCAD context."""
        cmd = self.py_input.text().strip()
        if not cmd:
            return
        self._py_history.append(cmd)
        self._py_hist_idx = len(self._py_history)
        self.py_input.clear()
        self.py_log.appendPlainText(f">>> {cmd}")

        import io
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        buf = io.StringIO()
        sys.stdout = buf
        sys.stderr = buf
        try:
            # Try eval first (expression), then exec (statement)
            try:
                result = eval(cmd, self._py_globals)
                if result is not None:
                    buf.write(repr(result) + "\n")
            except SyntaxError:
                exec(cmd, self._py_globals)
            try:
                FreeCADGui.updateGui()
            except Exception:
                pass
        except Exception as e:
            buf.write(f"Error: {e}\n")
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        output = buf.getvalue().strip()
        if output:
            self.py_log.appendPlainText(output)
        history_write("python", {"cmd": cmd})

    def _parse_russian(self, text):
        """Parse natural Russian into tool_name + args."""
        t = text.lower().strip()
        import re

        # Extract all numbers from text
        nums = [float(x) for x in re.findall(r'[\d.]+', t)]
        for i, n in enumerate(nums):
            if n == int(n):
                nums[i] = int(n)

        # --- Greetings ---
        if any(w in t for w in ["привет", "здравствуй", "хай", "hello", "hi"]):
            return None, None, "Привет! Я AI терминал FreeCAD. Скажи что создать, например:\n  'создай коробку 50 30 20' или 'покажи объекты'"

        if any(w in t for w in ["помощь", "help", "что умеешь", "команды"]):
            return None, None, (
                "Говори по-русски, я пойму:\n"
                "  создай коробку 50 30 20\n"
                "  добавь цилиндр радиус 10 высота 30\n"
                "  добавь шар радиус 15\n"
                "  добавь конус 10 5 20\n"
                "  добавь тор 15 3\n"
                "  сдвинь Box на 20 10 0\n"
                "  поверни Box по Z на 45\n"
                "  вырежи Box из Cylinder\n"
                "  объедини Box и Cylinder\n"
                "  покажи всё / впиши\n"
                "  список объектов\n"
                "  сохрани /tmp/model.FCStd\n"
                "  экспорт step /tmp/model.step\n"
                "  экспорт stl /tmp/model.stl\n"
                "  удали документ\n"
                "  новый документ МойПроект\n"
                "  скриншот\n"
                "  py: любой Python код"
            )

        # --- Box ---
        if any(w in t for w in ["коробк", "ящик", "куб", "box", "брусок", "блок", "бук", "бокс", "создай", "добавь"]) and not any(w in t for w in ["цилиндр", "шар", "сфер", "конус", "тор", "кольцо", "труб"]):
            l = nums[0] if len(nums) > 0 else 10
            w = nums[1] if len(nums) > 1 else l
            h = nums[2] if len(nums) > 2 else l
            label = "Box"
            m = re.search(r'назови\s+(\S+)', t)
            if m:
                label = m.group(1)
            return "add_box", {"length": l, "width": w, "height": h, "label": label}, None

        # --- Cylinder ---
        if any(w in t for w in ["цилиндр", "трубу", "труб", "cylinder", "столб"]):
            r = nums[0] if len(nums) > 0 else 5
            h = nums[1] if len(nums) > 1 else 10
            return "add_cylinder", {"radius": r, "height": h}, None

        # --- Sphere ---
        if any(w in t for w in ["сфер", "шар", "sphere", "мяч"]):
            r = nums[0] if len(nums) > 0 else 5
            return "add_sphere", {"radius": r}, None

        # --- Cone ---
        if any(w in t for w in ["конус", "cone"]):
            r1 = nums[0] if len(nums) > 0 else 5
            r2 = nums[1] if len(nums) > 1 else 0
            h = nums[2] if len(nums) > 2 else 10
            return "add_cone", {"radius1": r1, "radius2": r2, "height": h}, None

        # --- Torus ---
        if any(w in t for w in ["тор", "бублик", "кольцо", "torus"]):
            r1 = nums[0] if len(nums) > 0 else 10
            r2 = nums[1] if len(nums) > 1 else 2
            return "add_torus", {"radius1": r1, "radius2": r2}, None

        # --- Move ---
        if any(w in t for w in ["сдвинь", "перемести", "двигай", "move", "подвинь"]):
            # Find object name (first capitalized word or after name/объект)
            words = text.split()
            name = None
            for w in words:
                if w[0].isupper() and w not in ("Box", "Cylinder", "Sphere") or w in ("Box", "Cylinder", "Sphere"):
                    if w[0].isupper():
                        name = w
                        break
            if not name and len(words) > 1:
                name = words[1]
            x = nums[0] if len(nums) > 0 else 0
            y = nums[1] if len(nums) > 1 else 0
            z = nums[2] if len(nums) > 2 else 0
            if name:
                return "move_object", {"name": name, "x": x, "y": y, "z": z}, None

        # --- Rotate ---
        if any(w in t for w in ["поверни", "вращай", "rotate", "крути"]):
            words = text.split()
            name = None
            for w in words:
                if w[0].isupper():
                    name = w
                    break
            angle = nums[0] if len(nums) > 0 else 45
            ax, ay, az = 0, 0, 1
            if "x" in t.lower():
                ax, ay, az = 1, 0, 0
            elif "y" in t.lower():
                ax, ay, az = 0, 1, 0
            if name:
                return "rotate_object", {"name": name, "axis_x": ax, "axis_y": ay, "axis_z": az, "angle": angle}, None

        # --- Boolean cut ---
        if any(w in t for w in ["вырежи", "вычти", "отними", "cut"]):
            words = text.split()
            names = [w for w in words if w[0].isupper() and len(w) > 1]
            if len(names) >= 2:
                return "boolean_cut", {"name1": names[0], "name2": names[1]}, None

        # --- Boolean union ---
        if any(w in t for w in ["объедини", "соедини", "слей", "union", "fuse"]):
            words = text.split()
            names = [w for w in words if w[0].isupper() and len(w) > 1]
            if len(names) >= 2:
                return "boolean_union", {"name1": names[0], "name2": names[1]}, None

        # --- Fit view ---
        if any(w in t for w in ["покажи", "впиши", "fit", "показать", "всё"]):
            return "fit_view", {}, None

        # --- List objects ---
        if any(w in t for w in ["список", "объекты", "objects", "что есть", "что в модели"]):
            return "list_objects", {}, None

        # --- New document ---
        if any(w in t for w in ["новый документ", "new doc", "создай документ", "новый проект"]):
            name = "Untitled"
            words = text.split()
            if len(words) > 2:
                name = words[-1]
            return "new_document", {"name": name}, None

        # --- Save ---
        if any(w in t for w in ["сохрани", "save", "запиши"]):
            path = "~/model.FCStd"
            # Find path in text
            m = re.search(r'(/\S+|~/\S+)', text)
            if m:
                path = m.group(1)
            return "save_document", {"path": path}, None

        # --- Export STEP ---
        if "step" in t:
            path = "~/model.step"
            m = re.search(r'(/\S+|~/\S+)', text)
            if m:
                path = m.group(1)
            return "export_step", {"path": path}, None

        # --- Export STL ---
        if "stl" in t:
            path = "~/model.stl"
            m = re.search(r'(/\S+|~/\S+)', text)
            if m:
                path = m.group(1)
            return "export_stl", {"path": path}, None

        # --- Screenshot ---
        if any(w in t for w in ["скриншот", "screenshot", "снимок", "фото"]):
            return "get_screenshot", {"width": 1280, "height": 720}, None

        # --- Close/delete document ---
        if any(w in t for w in ["удали", "закрой", "close", "delete"]):
            return "close_document", {}, None

        # --- Camera ---
        if any(w in t for w in ["камера", "camera", "вид"]):
            return "get_camera", {}, None

        # --- Selection ---
        if any(w in t for w in ["выделен", "selected", "выбран"]):
            return "get_selection", {}, None

        # --- Sketch rectangle ---
        if any(w in t for w in ["прямоугольник", "rectangle", "скетч"]) and any(w in t for w in ["нарисуй", "создай", "добавь", "sketch"]):
            w = nums[0] if len(nums) > 0 else 20
            h = nums[1] if len(nums) > 1 else 10
            return "create_sketch_rectangle", {"width": w, "height": h}, None

        # --- Extrude ---
        if any(w in t for w in ["выдави", "extrude", "вытяни"]):
            words = text.split()
            name = None
            for w_item in words:
                if w_item[0].isupper():
                    name = w_item
                    break
            length = nums[0] if nums else 10
            if name:
                return "extrude_sketch", {"sketch_name": name, "length": length}, None

        return None, None, None

    def _execute_ai(self):
        cmd = self.ai_input.text().strip()
        if not cmd:
            return

        self._ai_history.append(cmd)
        self._ai_hist_idx = len(self._ai_history)
        self.ai_input.clear()
        self.ai_log.appendPlainText(f"❯ {cmd}")

        # Try Russian natural language first
        tool_name, args, message = self._parse_russian(cmd)
        if message:
            self.ai_log.appendPlainText(f"  {message}")
            return
        if tool_name is None:
            # Try as direct tool call: tool_name key=val key=val
            parts = cmd.split()
            tool_name = parts[0]
            args = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    try:
                        v = float(v)
                        if v == int(v):
                            v = int(v)
                    except ValueError:
                        pass
                    args[k] = v

        if tool_name in TOOLS:
            try:
                t0 = time.time()
                result = TOOLS[tool_name](**args)
                dt = round((time.time() - t0) * 1000)
                try:
                    FreeCADGui.updateGui()
                except Exception:
                    pass
                out = json.dumps(result, default=str)
                if len(out) > 200:
                    out = out[:200] + "..."
                self.ai_log.appendPlainText(f"  OK ({dt}ms): {out}")
                history_write("terminal", {"cmd": cmd, "tool": tool_name, "args": args, "ms": dt, "ok": True})
            except Exception as e:
                self.ai_log.appendPlainText(f"  ERROR: {e}")
                history_write("terminal", {"cmd": cmd, "ok": False, "error": str(e)})
        else:
            # Fallback: send to OpenRouter AI
            self.ai_log.appendPlainText("  🤖 Думаю...")
            self.ai_input.setEnabled(False)
            thread = threading.Thread(target=self._ask_ai, args=(cmd,), daemon=True)
            thread.start()

    def _ask_ai(self, user_msg):
        """Send message to OpenRouter AI, get back FreeCAD commands."""
        import urllib.request
        import urllib.error

        api_key = ""
        env_path = os.path.expanduser("~/aicad/.env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("OPENROUTER_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()

        if not api_key:
            self._ai_respond("Нет API ключа. Положи его в ~/aicad/.env")
            return

        # Build context: available tools + current model state
        tools_desc = ", ".join(sorted(TOOLS.keys()))
        model_info = ""
        try:
            state = get_model_state()
            objs = [f"{o['name']}({o['type'].split('::')[1]})" for o in state.get("objects", [])[:10]]
            model_info = f"Текущий документ: {state.get('document','?')}, объекты: {', '.join(objs) if objs else 'пусто'}"
        except Exception:
            model_info = "Нет открытого документа"

        system_prompt = f"""Ты AI-ассистент CAD системы AICAD (на базе FreeCAD). Отвечай кратко по-русски.

Доступные инструменты: {tools_desc}

{model_info}

Когда пользователь просит создать/изменить 3D модель, отвечай в формате:
TOOL: tool_name arg1=value1 arg2=value2

Примеры:
- "создай куб 50 на 30 на 20" → TOOL: add_box length=50 width=30 height=20
- "добавь цилиндр радиус 10" → TOOL: add_cylinder radius=10 height=20
- "сдвинь Box на 20 0 0" → TOOL: move_object name=Box x=20 y=0 z=0
- "вырежи Cylinder из Box" → TOOL: boolean_cut name1=Box name2=Cylinder
- "покажи всё" → TOOL: fit_view
- "что в модели?" → TOOL: list_objects

Если нужно несколько операций, выведи несколько строк TOOL:
Если вопрос не про CAD — просто ответь текстом."""

        body = json.dumps({
            "model": "anthropic/claude-sonnet-4",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 1024,
        }).encode()

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                reply = data["choices"][0]["message"]["content"]
        except Exception as e:
            self._ai_respond(f"Ошибка AI: {e}")
            return

        # Parse response: execute TOOL: lines, show text
        lines = reply.strip().split("\n")
        text_parts = []
        for line in lines:
            line = line.strip()
            if line.startswith("TOOL:"):
                tool_cmd = line[5:].strip()
                self._ai_respond(f"→ {tool_cmd}")
                self._execute_tool_string(tool_cmd)
            else:
                if line:
                    text_parts.append(line)

        if text_parts:
            self._ai_respond("\n".join(text_parts))

        history_write("ai_chat", {"user": user_msg, "reply": reply})

    def _ai_respond(self, text):
        """Thread-safe append to AI log."""
        # Use QTimer to safely update GUI from thread
        QtCore.QMetaObject.invokeMethod(
            self.ai_log, "appendPlainText",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, f"  {text}")
        )
        QtCore.QMetaObject.invokeMethod(
            self.ai_input, "setEnabled",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(bool, True)
        )

    def _execute_tool_string(self, cmd_str):
        """Parse and execute 'tool_name key=val key=val'."""
        parts = cmd_str.split()
        tool_name = parts[0]
        args = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                try:
                    v = float(v)
                    if v == int(v):
                        v = int(v)
                except ValueError:
                    pass
                args[k] = v

        if tool_name in TOOLS:
            try:
                result = TOOLS[tool_name](**args)
                try:
                    FreeCADGui.updateGui()
                except Exception:
                    pass
                out = json.dumps(result, default=str)
                if len(out) > 150:
                    out = out[:150] + "..."
                self._ai_respond(f"OK: {out}")
            except Exception as e:
                self._ai_respond(f"ERROR: {e}")


# ─── Setup UI ─────────────────────────────────────────────────────────────────

_status_widget = None
_terminal_instance = None
_panel_instance = None  # kept for compat


def setup_ui():
    """Create statusbar widget + terminal panel."""
    global _status_widget, _terminal_instance
    mw = FreeCADGui.getMainWindow()

    # Status bar widget
    _status_widget = AIToolStatusWidget()
    sb = mw.statusBar()
    sb.addPermanentWidget(_status_widget)

    # Step 1: Hide ALL bottom dock widgets (Python console, Report, etc.)
    for dock in mw.findChildren(QtWidgets.QDockWidget):
        title = dock.windowTitle()
        area = mw.dockWidgetArea(dock)
        # Hide built-in bottom panels
        if any(x in title for x in ["Python", "Report", "Selection", "console"]):
            dock.setVisible(False)
            dock.toggleViewAction().setVisible(False)  # hide from View menu too

    # Step 2: Ensure Model/Tasks tree is on the left and visible
    combo_dock = None
    for dock in mw.findChildren(QtWidgets.QDockWidget):
        title = dock.windowTitle()
        if any(x in title for x in ["Model", "Combo", "Task"]):
            mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
            dock.show()
            if combo_dock is None:
                combo_dock = dock

    # Step 3: Add our terminal at the bottom, full width
    _terminal_instance = AIToolTerminal(mw)
    mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, _terminal_instance)
    _terminal_instance.setMinimumHeight(150)

    # Step 4: Make terminal span full width (not tabbed with anything)
    # Force bottom area to not share with left panel
    if combo_dock:
        mw.setCorner(QtCore.Qt.BottomLeftCorner, QtCore.Qt.BottomDockWidgetArea)
        mw.setCorner(QtCore.Qt.BottomRightCorner, QtCore.Qt.BottomDockWidgetArea)


def show_panel():
    """Compat function — shows terminal."""
    global _terminal_instance
    if _terminal_instance:
        _terminal_instance.show()
        _terminal_instance.raise_()


# ─── FreeCAD Commands ─────────────────────────────────────────────────────────

class AITool_StartServer:
    def GetResources(self):
        return {"MenuText": "Start AI Server", "ToolTip": "Start HTTP API server"}

    def Activated(self):
        if _status_widget:
            _status_widget._toggle()

    def IsActive(self):
        return _server is None


class AITool_StopServer:
    def GetResources(self):
        return {"MenuText": "Stop AI Server", "ToolTip": "Stop HTTP API server"}

    def Activated(self):
        if _status_widget:
            _status_widget._toggle()

    def IsActive(self):
        return _server is not None


class AITool_ShowPanel:
    def GetResources(self):
        return {"MenuText": "Show AI Terminal", "ToolTip": "Show AI Tool terminal"}

    def Activated(self):
        show_panel()

    def IsActive(self):
        return True


FreeCADGui.addCommand("AITool_StartServer", AITool_StartServer())
FreeCADGui.addCommand("AITool_StopServer", AITool_StopServer())
FreeCADGui.addCommand("AITool_ShowPanel", AITool_ShowPanel())


# ─── Auto-init ────────────────────────────────────────────────────────────────

def _auto_init():
    """Setup UI and auto-start server."""
    try:
        setup_ui()
        log_to_panel("AI Tool ready")
        start_server(8765)
        if _status_widget:
            _status_widget.set_running(8765)
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"AI Tool auto-init: {e}\n")


QtCore.QTimer.singleShot(500, _auto_init)
