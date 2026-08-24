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
  reference from Forum8. Both are binary formats that local search tools cannot grep/render in this
  environment — treat the API surface actually used in the sample/plugin `.py` files as the source of truth
  instead of guessing at undocumented COM methods/properties.

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

Generates a road blockage by placing a stationary vehicle instance on a chosen road/lane/distance via
`TrafficSimulation.AddNewVehicle` (the same primitive `SamplePlugin/Sample_VehiclePlacement.py` uses), then
permanently forcing `Brake=1`/`Throttle=0`/`EngineOn=False` on it every tick through a per-instance
`OnBeforeCalculateMovement` callback (`BlockageInstanceHandler`) so it behaves as a fixed obstacle to
surrounding traffic rather than driving off under normal AI control.

- Ribbon tab "Road Blockage" (group `GroupBlockage`) exposes: road name, lane number, distance (m), forward/
  backward, and an optional 3D model name — "Place" calls `PlaceBlockage(...)`, "Clear All" calls
  `ClearAllBlockages()`.
- If no model name is given, `FindBlockageModel` falls back to the first project model with
  `ModelType == const._VehicleModel`.
- **There is no confirmed COM API in this codebase (or in the unreadable `.chm`/`.pdf` docs) for deleting a
  transient vehicle instance.** `ClearAllBlockages()` therefore does not remove the placed object from the
  scene — it unregisters the blockage's callback handler via `CloseCallbackEvent`, handing the vehicle back
  to normal traffic-simulation AI control so it drives away and the lane reopens. If a real deletion API is
  found later (e.g. from the `.chm` reference, opened directly in a CHM viewer), prefer switching to it.
- `blockageList` / `blockageIdCounter` are module-level state tracking currently-placed blockages so
  multiple can be placed and cleared independently; each carries its own `eventList` from
  `SetCallbackHandlers`.
