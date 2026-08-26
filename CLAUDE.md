# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A collection of Python plugin scripts for **UC-win/Road** (Forum8's driving/road simulator). Scripts run
*inside* the UC-win/Road process, driving the application through its COM automation interface
(`win32com.client`) — they are not standalone programs. There is no build step, package manifest, test
suite, or linter in this repo; correctness is verified by loading the script from UC-win/Road's Python
plugin menu and observing behavior/log output in the simulator itself.

The only offline check available is a syntax/import sanity check:

```bash
python -m py_compile RoadBlockagePlugin.py
```

(Full execution requires `pywin32` and a running UC-win/Road instance registered as the COM server
`UCwinRoad.UCwinRoadCom_1724`, so it cannot be exercised outside the app. `SamplePlugin/Sample_RoadInformation.py`
additionally needs `pandas`, and `UCwinRoadOpenGL.py` needs `PyOpenGL` — neither is declared anywhere, since
these scripts run in whatever Python environment UC-win/Road's plugin host is configured to use.)

## Repository layout

Each plugin lives either at the repo root or in its own folder (`SmartMotionPlugin/`, `SamplePlugin/`).
**Every plugin folder carries its own copies** of the shared helper modules
(`UCwinRoadCOM.py`, `UCwinRoadUtils.py`, `CallbackHandlers.py`, `LoggerProxy.py`, `VehicleInfo.py`,
`UCwinRoadOpenGL.py`) rather than importing a shared package. This is because UC-win/Road loads a plugin by
pointing at a single `.py` file, which then does `from UCwinRoadCOM import *` etc. relative to its own
directory. **When editing shared behavior, check whether the fix needs to be duplicated into each plugin
folder** — changes to the root copies do not affect `SmartMotionPlugin/` or `SamplePlugin/`.

- `RoadBlockagePlugin.py` — the plugin this repo is being built around (see below).
- `SmartMotionPlugin/` — OSC-driven vehicle motion sequencer (multi-phase state machine driving a single
  target car; reference implementation for time-phased `OnBeforeCalculateMovement` logic).
- `SamplePlugin/` — Forum8's official Python API samples, one script per API area (vehicle placement,
  road info export, coordinate conversion, ribbon UI, 3D model commands, OSC/GPS/gaze devices, etc.). Use
  these as the primary reference for "how is X done with this COM API" — they're more varied than the two
  real plugins.
- `Document/{en,ja}/` — `PythonAPIIntroduction.pdf` and `UCwinRoad_PythonAPI.chm`: the authoritative COM API
  reference from Forum8. The PDF's text **can** be extracted with `pdftotext -enc UTF-8` (bundled with Git
  for Windows at `/mingw64/bin/pdftotext` in this environment) — useful for confirming a sample's intended
  usage or a UC-win/Road-side prerequisite (e.g. it's how "a Virtual Display must be manually configured in
  UC-win/Road, this API can't create one" got confirmed), but it's an intro/tutorial doc, not a full method
  reference, and the Japanese copy's CJK text has been unreliable to extract. The `.chm` has not been
  successfully decompiled in this environment (`hh.exe -decompile` failed even after `Unblock-File`). For
  the actual method/property list of any COM interface, regenerate pywin32's full typelib wrapper instead of
  reading either doc — see the COM interface discovery method below.

## Discovering the real COM API surface for an interface

Guessing a COM interface's methods/properties from samples or the docs above is unreliable — regenerate
pywin32's full typelib wrapper instead, which gives ground-truth `_prop_map_get_`/`_prop_map_put_` (so
read-only vs. read-write is unambiguous) and every method's actual parameter list:

```python
from win32com.client import makepy
makepy.GenerateFromTypeLibSpec(('{6784BB6B-7D41-40E0-95BE-700263C49E89}', 0, 19, 0))
```

(GUID/version taken from the existing partial cache directory name under `win32com.__gen_path__`, typically
`...\gen_py\3.11\6784BB6B-7D41-40E0-95BE-700263C49E89x0x19x0\`; the CLSID must be wrapped in `{}` or it fails
with `com_error` "invalid class string". Must run with plain `python`, not `python3`, which is broken in this
environment.) This writes one big file, `<gen_path>\<GUID>x0x19x0.py` (~12k lines), containing **every**
interface as a `class IF8XxxProxy(DispatchBaseClass)` — not just the smaller per-interface files that get
lazily created for objects some script has actually dispatched at runtime. Grep that file, or the
already-materialized per-interface files in the same directory, by `class IF8ThingProxy`.

Confirmed findings from this method worth knowing before re-deriving them: `F8RoadObstructionProxy` has only
read-only properties (`Description`/`Distance`/`Length`) and no road/project interface has a Create/Add
method for obstructions — the Python API cannot author a road obstruction, only read ones already placed in
UC-win/Road's own road editor. `F8TrafficSimulationProxy` does have `DeleteTransientObject`/
`DeleteAllTransientObjects`, so a placed `AddNewVehicle`/`AddNewTransient` instance can be genuinely removed.
`IF8MainRibbonGroupProxy` only exposes `CreateButton`/`CreateCheckBox`/`CreateEdit`/`CreateLabel`/
`CreatePanel` — there is no combo box, list box, dropdown, or multi-line edit control anywhere in the ribbon
API; an index-number `Edit` is the closest thing to a "select box" this API offers.

## Shared module responsibilities (per plugin folder)

- **`UCwinRoadCOM.py`** — `UCwinRoadComProxy`: connects to the running app via
  `com.gencache.EnsureDispatch("UCwinRoad.UCwinRoadCom_1724")` and exposes the main entry points as
  attributes: `ApplicationServices`, `Project`, `MainForm`, `SimulationCore`, `GazeTrackingPlugin`,
  `VirtualDisplaysPlugin`, `CoordinateConverter`, plus `const` (== `win32com.client.constants`, used for
  enum values like `const._VehicleModel`, `const._TransientCar`, `const._MeterPerSecond`).
- **`UCwinRoadUtils.py`** — vector/rect constructors (`AsF8COMdVec3`, `AsF8COMRect`, ...), `Distance()`, and
  the event-registration helpers `SetCallbackHandlers(evlist, instance, handler)` /
  `CloseCallbackEvent(evlist)`, which wrap `win32com.client.WithEvents` + `RegisterEventHandlers()` /
  `UnRegisterEventHandlers()`. Always pair a `SetCallbackHandlers` call with a `CloseCallbackEvent` call in
  `finally`, or the COM event sink leaks and can crash a subsequent script run.
- **`CallbackHandlers.py`** — `HandlerBase` (provides `SetCOMEventClass`/`OnIsExistEventHandler`) plus one
  template class per COM event interface (`ApplicationServicesHandler`, `MainOpenGLHandler`,
  `MainFormHandler`, `RibbonButtonHandler`, `SimulationCoreProxyHandler`, `TrafficSimulationHandler`,
  `TransientInstanceHandler`, `ObjectProxyHandler`, ...), all methods stubbed to `pass`. Subclass these and
  **override only the callbacks you actually need** — the file's own docstring warns that every defined
  method is invoked by UC-win/Road even when it does nothing, adding processing load per frame.
- **`LoggerProxy.py`** — one named `logging.Logger` per script, writing to both console and
  `<PythonPluginDirectory>/<scriptName>.log`. Call `logProxy.killLogger()` in `finally` — it deletes the
  logger from `logging.Logger.manager.loggerDict`, and skipping this causes duplicate-handler log spam (or
  a `KeyError`) the next time the same script is run in the same process.
- **`VehicleInfo.py`** — `DataclassVehicleInfo`, a flat dataclass mirroring the fields UC-win/Road exposes
  per vehicle (position/direction/speed/lane/etc.), used by scripts that log or export vehicle telemetry.

## Plugin script shape (convention used everywhere in this repo)

Every plugin's `main()` follows the same skeleton — new scripts should match it:

```python
def main():
    try:
        winRoadProxy = UCwinRoadComProxy()
        const = winRoadProxy.const
        logProxy = LoggerProxy(scriptName, winRoadProxy.PythonPluginDirectory() + scriptName + '.log')
        # build ribbon UI, register callback handlers, etc.
        winRoadProxy.ApplicationServices.IsPythonScriptRun = True
        while winRoadProxy.ApplicationServices.IsPythonScriptRun:
            time.sleep(0.005)   # or an asyncio loop, for OSC/network-driven plugins
    finally:
        # tear down ribbon UI, CloseCallbackEvent(...), logProxy.killLogger(), del winRoadProxy
```

- `IsPythonScriptRun` is UC-win/Road's stop flag: the host flips it to `False` when the user stops the
  script from the UI, which is what breaks the polling loop. The plugin also sets it `True`/`False` itself
  at start/end.
- Ribbon UI is built with a small `RibbonUI` class (`MakeRibbonTab`/`MakeRibbonGroup`/`MakeRibbonButton`/
  `MakeRibbonEdit`/`MakeRibbonLabel`, each idempotent via `GetXByName` before `CreateX`) so re-running the
  script after a crash reuses existing controls instead of erroring. Tear-down is the mirror image
  (`DeleteControl` for plain labels/edits; `UnRegisterEventHandlers()` + `DeleteControl` for buttons that
  had a click handler registered via `SetCallbackEvent`/`com.WithEvents`).
- Per-vehicle behavior overrides happen in `OnBeforeCalculateMovement(self, dTimeInSeconds, proxy)`, called
  by the traffic simulation once per vehicle per tick; `proxy` is re-wrapped with `com.Dispatch(proxy)` to
  get the live instance. When a handler is registered on one specific vehicle instance, the callback can
  still fire for other vehicles too — always re-check `instance.ID` (or `instance.TransientType`) inside the
  handler rather than assuming scoping, and store per-target state (e.g. a target ID) as an attribute set on
  the `Event` object returned by `SetCallbackHandlers`/`com.WithEvents`, not as a class attribute, if more
  than one instance of the same handler class may be live at once.

## `RoadBlockagePlugin.py`

Loads a prediction CSV (lat/lon/probability grid, see `Prediction/`), places a visual-only **'Barricade'**
3D model (`TrafficSimulation.AddNewTransient`) at the nearest lane for each record whose probability clears a
threshold, and separately lets the user monitor traffic flow/average speed around one placement as a live
HUD — so the effect of manually adding/removing a real (script-uncreatable) `F8RoadObstructionProxy` at the
same spot can be observed. Ribbon UI is five tabs (one group each — a single tab didn't have enough
horizontal space):

- **CSV** — `RibbonButtonHandlerBrowseCsv` pops a native Open-File dialog (`win32gui.GetOpenFileNameW`, since
  the ribbon has no file-picker control) and loads the CSV into module-global `loadedCsvRecords` via
  `LoadCsvRecords` (`csv.DictReader`, expects `latitude`/`longitude`/`probability` columns, `side_m`
  optional/unused for placement).
- **Barricades** — Probability Threshold (default 0.7) and Max Distance to Road (m) (default 5000) edits
  gate which CSV records get placed; "Place Barricades from CSV" (`PlaceBarricadesFromCsv`) always calls
  `ResetObstruction()` first and rebuilds the full set from scratch. For each qualifying record,
  `FindNearestLanePosition` finds the nearest point on the nearest lane, then returns **two**
  `(position, yawAngle)` placements offset `BARRICADE_OFFSET_DISTANCE_METERS` (5m) downstream and upstream
  along the road (clamped to `[0, road.Length]` near a road's ends), each independently oriented
  perpendicular to the road (`YawAngle = atan2(direction.X, -direction.Z) + BARRICADE_YAW_OFFSET_RADIANS`,
  the offset empirically tuned to `0.2` — don't "correct" it back to a theoretical `pi/2`). Barricade Index +
  "Jump to Barricade" moves the main camera to a placed instance, deriving camera position from the
  instance's own `YawAngle` (pattern from `SamplePlugin/Sample_MainCameraOperation.py`).
- **Reset** — "Reset" (`ResetObstruction`, deletes only this script's placed Barricades) / "Clear All"
  (`ClearAllTransientObjects` → `TrafficSimulation.DeleteAllTransientObjects()` — project-wide and blunt,
  a crash-recovery tool, not invoked automatically).
- **Obstructions** — "List Obstructions" enumerates road-authored `F8RoadObstructionProxy` entries (100%
  read-only, no creation/edit API exists anywhere — confirmed via typelib, see above) into a persistent-label
  panel (`ShowObstructionList` — labels are created once and only `Caption`/`Visible` toggled afterward;
  deleting/recreating them on every click made the panel render unreliably). Obstruction Index + "Jump to
  Obstruction" computes a jump position via `road.GetPositionAt(distance)` since the proxy itself has no
  `Position`.
- **Traffic Monitor** — Barricade Index / Measurement Radius (m) (default 50) / Flow Window (s) (default 60)
  edits, Start/Stop Monitoring buttons, and a fallback readout. See the dedicated points below for why this
  tab's design choices aren't obvious from the code alone.

**Deferred-Position-settle pattern** (`ApplyPendingObstructions`, `PLACEMENT_SETTLE_TICKS` = 20 ≈ 100ms at
the 5ms loop interval): setting `Position`/`YawAngle` on an `AddNewTransient` instance in the same frame it
was created can crash UC-win/Road, so creation and placement are split across ticks via the
`pendingObstructions` queue. Separately, placement only works reliably while the simulation is actually in
Play state — auto-starting it via `SimulationCore.ScriptStatus` was tried and doesn't help; the user starts
Play manually before loading the plugin.

**Traffic Monitor measures a frozen snapshot position, not a live Barricade reference — this is deliberate,
not an oversight.** Starting monitoring copies the selected Barricade's `.Position` once into module-global
`monitoredPosition`; every tick thereafter, `UpdateTrafficMetrics` calls
`traffic.GetTransientObjectsArround(radius, monitoredPosition)` regardless of whether a Barricade still
exists there. `ResetObstruction` deliberately does **not** touch monitoring state, precisely so the intended
workflow works: start monitoring, delete the visual Barricade via Reset, manually place/remove a real road
obstruction in the UC-win/Road editor at that spot, and watch the flow/speed numbers respond to the AI
actually reacting to the real obstruction. Flow counts only vehicle IDs newly entering the radius since the
last sample (an idling car isn't double-counted) within a rolling window (`flowEventTimestamps`), converted
to veh/h.

**The HUD is a separate tkinter window (`HudOverlayWindow`), not anything drawn into UC-win/Road's own
rendering.** Two direct-rendering approaches were tried first and both failed for reasons specific to this
host process — don't re-attempt either without new evidence:
1. Drawing in `MainOpenGLHandler.OnOpenGLAfterDrawScene` (fires after UC-win/Road's own scene draw) —
   every GL call, including a bare `glMatrixMode` and even `glGetError()` itself, raised
   `GL_INVALID_OPERATION` every frame, with or without `OpenGL.ERROR_CHECKING = False`. Whatever
   thread/context this COM callback runs on never has a valid OpenGL context current.
2. Drawing via a 2D Overlay Virtual Display's `OnDirectDraw` — the same pattern
   `SamplePlugin/Sample_GPSroads.py`/`Sample_VirtualDisplay.py` use successfully, so it should work
   technically, but there is no known API to *create* a Virtual Display from script (only
   `VirtualDisplaysPlugin.GetVirtualDisplays()` to enumerate existing ones) and neither this session nor the
   Python API PDF could determine the UC-win/Road-side manual setup steps.

`HudOverlayWindow` instead runs its own `tk.Tk()` + `mainloop()` on a dedicated `threading.Thread` — tkinter
requires the thread that owns the widgets to be the one actually running the loop, and this plugin's own
thread is non-main whenever [Async] is ON, which is what broke a cruder first attempt with
`RuntimeError: main thread is not in main loop`. The plugin's main loop only ever talks to that thread via a
`queue.Queue` (`SetText`/`SetAnchorRect`/`Destroy`); the tkinter thread drains it itself via
`root.after(50, ...)` so all widget mutation happens on the thread that owns it. `FindMainWindowHandle` locates
UC-win/Road's own window via `win32gui.EnumWindows` + `win32process.GetWindowThreadProcessId` filtered on
`pid == os.getpid()` (valid since the plugin runs *inside* the UC-win/Road process) and picks the largest
visible window by area, so the overlay can track it to the bottom-right corner.

**The ribbon API has no combo/list/dropdown box and no true multi-line edit control** (see the COM discovery
section above). Every "which placed item" selector here is a plain index-number `Edit`; the Traffic Monitor
detail readout is an `Edit` with `Height` widened and `\r\n` embedded in `Text` as an approximation of a text
area — not confirmed whether the underlying native control actually renders multiple lines or collapses
them, so fall back to the proven persistent-`Label`-panel pattern (`ShowObstructionList`) if it doesn't.

**Vehicle-based traffic-avoidance/queueing (making ambient AI traffic actually stop/lane-change around a
placed object) was extensively attempted and fully abandoned** — six distinct techniques, all dead ends (no
lane-change API exists at all; a real stopped vehicle worked but couldn't be hidden; steering/throttle/brake
overrides on ambient traffic are silently ignored because `AutomaticControl` is read-only; one-shot position
teleports get self-corrected by the AI; a full continuous-kinematic override crashed UC-win/Road). All
vehicle-control code for this was removed from the file at the user's request. Don't reintroduce without the
user explicitly asking again.
