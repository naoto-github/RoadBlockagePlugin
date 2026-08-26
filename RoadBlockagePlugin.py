from UCwinRoadCOM import *
from UCwinRoadCOM import *
from LoggerProxy import LoggerProxy
from UCwinRoadUtils import *
from CallbackHandlers import *
import threading
import time
import traceback
import win32com.client as com

# リボンに一覧として並べて表示する道路障害物の最大件数
# （リボングループの表示領域には限りがあるため、収まる範囲に絞って表示する）
MAX_DISPLAYED_OBSTRUCTIONS = 3

# 障害物として配置する3Dモデルの名前(プロジェクトに存在することを確認済み)。
# Pylonは回転対称な形状のため、向き(YawAngle)の設定は不要
OBSTRUCTION_MODEL_NAME = 'Pylon'

# APIのエントリポイント
winRoadProxy = None
const = None

# ロガー
logProxy = None

# リボンUI
ribbon = None

# 現在配置中の障害物のリスト(車線数だけ配置される)。各要素は {'instance':..., 'model':...}
currentObstructions = []

# AddNewTransientで生成した直後の同フレームでPositionを設定すると、
# ネイティブ側のオブジェクト初期化と競合してUC-win/Road自体がクラッシュすることが
# あるため、生成と位置設定を分離する。生成直後はここに設定待ちの情報を積んでおき、
# メインループが数ティック進んでから実際にPositionを設定する。
# (車線ごとに1個ずつ生成するため、複数件を同時に待たせられるようリストで持つ)
PLACEMENT_SETTLE_TICKS = 20  # ループは5ms間隔なので、20ティック=約100ms待つ
pendingObstructions = []


# UC-win/Roadのリボン(または Script Editor)の [Async] チェックがONのときだけ、
# スクリプトはメインスレッドとは別スレッドで実行される(PDFガイド 3.1.1.1参照)。
# これを使い、非同期になっていない(=メインスレッドで実行されている)ことを検出する。
# 非同期でないままイベントループ(while + time.sleep)に入るとメインスレッドを
# ブロックし続け、UC-win/Road自体が操作不能になってしまうため。
def IsRunningAsync():
    return threading.current_thread() is not threading.main_thread()


# 現在のメインカメラの位置(ローカル座標)を緯度経度に変換して取得する
# ローカル座標は X, Z が水平面、Y が高さなので、水平面変換には X, Z を使う
def GetCameraLatLon():
    mainCamera = winRoadProxy.MainForm.MainCamera
    eye = mainCamera.MainCameraState.eye

    srcVec2 = com.Dispatch('UCwinRoad.F8COMdVec2')
    dstVec2 = com.Dispatch('UCwinRoad.F8COMdVec2')
    convRes = com.Dispatch('UCwinRoad.F8COMHcsConvertResultType')
    srcVec2.X = eye.X
    srcVec2.Y = eye.Z

    hConverter = winRoadProxy.CoordinateConverter.HorizontalCoordinateConvertor
    hConverter.Convert(const._hcLocal_XY, const._hcWGS84_LonLat, srcVec2, dstVec2, convRes)

    if not convRes.isSuccess:
        logProxy.logger.error(
            f"Coordinate conversion failed. isOutOfCS={convRes.isOutOfCS} isBadArray={convRes.isBadArray}")
        return None

    # WGS84 LonLat は X=経度, Y=緯度
    longitude = dstVec2.X
    latitude = dstVec2.Y
    return latitude, longitude


class RibbonButtonHandlerGetCameraPosition(RibbonButtonHandler):
    def OnClick(self):
        try:
            result = GetCameraLatLon()
            if result is None:
                return
            latitude, longitude = result
            ribbon.edit_latitude.Text = str(latitude)
            ribbon.edit_longitude.Text = str(longitude)
            logProxy.logger.info(f"Camera position: lat={latitude}, lon={longitude}")
        except Exception:
            logProxy.logger.error(traceback.format_exc())


# 緯度経度をローカル座標(水平面 X, Z)に変換する(GetCameraLatLonの逆変換)
def LatLonToLocalXZ(latitude, longitude):
    srcVec2 = com.Dispatch('UCwinRoad.F8COMdVec2')
    dstVec2 = com.Dispatch('UCwinRoad.F8COMdVec2')
    convRes = com.Dispatch('UCwinRoad.F8COMHcsConvertResultType')
    srcVec2.X = longitude
    srcVec2.Y = latitude

    hConverter = winRoadProxy.CoordinateConverter.HorizontalCoordinateConvertor
    hConverter.Convert(const._hcWGS84_LonLat, const._hcLocal_XY, srcVec2, dstVec2, convRes)

    if not convRes.isSuccess:
        logProxy.logger.error(
            f"Coordinate conversion failed. isOutOfCS={convRes.isOutOfCS} isBadArray={convRes.isBadArray}")
        return None

    return dstVec2.X, dstVec2.Y


# プロジェクト内の全道路を探索し、ローカル座標(x, z)に最も近い道路中心線上の位置を求める
# 粗い間隔(10m)で探索したのち、最も近かった付近を細かい間隔(0.5m)で再探索して精度を上げる
# 道路中心線上の位置なので、道路の断面方向については中央(道路線分の中央)になる
def FindNearestRoadPoint(x, z):
    prj = winRoadProxy.Project
    roadCount = prj.RoadsCount

    bestRoad = None
    bestDistanceAlong = 0.0
    bestSqDist = None
    coarseStep = 10.0

    for i in range(roadCount):
        road = prj.Road(i)
        if road is None:
            continue
        length = road.Length
        if length <= 0:
            continue
        dist = 0.0
        while True:
            pos = road.GetPositionAt(dist)
            sqDist = (pos.X - x) ** 2 + (pos.Z - z) ** 2
            if bestSqDist is None or sqDist < bestSqDist:
                bestSqDist = sqDist
                bestRoad = road
                bestDistanceAlong = dist
            if dist >= length:
                break
            dist = min(dist + coarseStep, length)

    if bestRoad is None:
        return None

    fineStep = 0.5
    fineStart = max(0.0, bestDistanceAlong - coarseStep)
    fineEnd = min(bestRoad.Length, bestDistanceAlong + coarseStep)
    dist = fineStart
    while dist <= fineEnd:
        pos = bestRoad.GetPositionAt(dist)
        sqDist = (pos.X - x) ** 2 + (pos.Z - z) ** 2
        if sqDist < bestSqDist:
            bestSqDist = sqDist
            bestDistanceAlong = dist
        dist += fineStep

    return bestRoad, bestDistanceAlong


# 入力欄の緯度経度から最も近い道路を探し、その地点にある車線のうち入力座標に最も
# 近い車線1本の中央にのみ障害物(3Dモデル)を配置する。Pylonは回転対称な形状のため、
# 向き(YawAngle)は設定しない(RoadLane.GetPositionにconst._ldRoadを渡すことで、
# 道路側と同じdistanceAlongをそのまま使って各車線の中央位置が得られる。
# Sample_GPSroads.pyのRoadLane走査パターンを踏襲)
# 以前AddNewTransientでUC-win/Roadがクラッシュした際にログに何も残らなかったため、
# 各COM呼び出しの前後で必ずログを残し、万一再発した際に原因箇所を特定できるようにする
def PlaceObstruction():
    logProxy.logger.info('PlaceObstruction: start')

    latitude = float(ribbon.edit_latitude.Text)
    longitude = float(ribbon.edit_longitude.Text)
    logProxy.logger.info(f'PlaceObstruction: lat={latitude}, lon={longitude}')

    local = LatLonToLocalXZ(latitude, longitude)
    if local is None:
        logProxy.logger.error('PlaceObstruction: coordinate conversion failed, aborting')
        return
    x, z = local
    logProxy.logger.info(f'PlaceObstruction: local x={x}, z={z}')

    found = FindNearestRoadPoint(x, z)
    if found is None:
        logProxy.logger.error('PlaceObstruction: no road found in the project, aborting')
        return
    road, distanceAlong = found
    logProxy.logger.info(f"PlaceObstruction: nearest road='{road.Name}' distance={distanceAlong:.1f}m")

    model = FindThreeDModelByName(OBSTRUCTION_MODEL_NAME)
    if model is None:
        logProxy.logger.error(f"PlaceObstruction: model '{OBSTRUCTION_MODEL_NAME}' not found, aborting")
        return
    logProxy.logger.info(f"PlaceObstruction: using model '{model.Name}' (ModelType={model.ModelType})")

    laneCount = road.RoadLanesCount
    if laneCount <= 0:
        logProxy.logger.error('PlaceObstruction: road has no lanes, aborting')
        return

    # 各車線の中央位置を求め、入力座標(x, z)に最も近い車線1本だけを選ぶ
    nearestPosition = None
    nearestSqDist = None
    nearestLaneIndex = None
    for laneIndex in range(laneCount):
        lane = road.RoadLane(laneIndex)
        if lane is None:
            continue
        position = lane.GetPosition(distanceAlong, const._ldRoad)
        sqDist = (position.X - x) ** 2 + (position.Z - z) ** 2
        logProxy.logger.info(
            f'PlaceObstruction: lane[{laneIndex}] position=({position.X}, {position.Y}, {position.Z}) '
            f'sqDist={sqDist:.2f}')
        if nearestSqDist is None or sqDist < nearestSqDist:
            nearestSqDist = sqDist
            nearestPosition = position
            nearestLaneIndex = laneIndex

    if nearestPosition is None:
        logProxy.logger.error('PlaceObstruction: no usable lane found, aborting')
        return
    logProxy.logger.info(f'PlaceObstruction: nearest lane is lane[{nearestLaneIndex}]')
    placements = [nearestPosition]

    global currentObstructions, pendingObstructions

    # 既存の配置が車線数・モデルとも一致していれば、作り直さず位置だけ更新する
    if (len(currentObstructions) == len(placements)
            and all(o['model'].IsSameAs(model) for o in currentObstructions)):
        logProxy.logger.info('PlaceObstruction: reusing existing instances, moving them')
        for obstruction, position in zip(currentObstructions, placements):
            obstruction['instance'].Position = position
        winRoadProxy.MainForm.MainOpenGL.Changed()
        logProxy.logger.info('PlaceObstruction: moved existing instances successfully')
    else:
        if currentObstructions:
            logProxy.logger.info('PlaceObstruction: lane layout changed, removing previous instances first')
            ResetObstruction()

        traffic = winRoadProxy.SimulationCore.TrafficSimulation
        newObstructions = []
        newPending = []
        for laneIndex, position in enumerate(placements):
            logProxy.logger.info(f'PlaceObstruction: lane[{laneIndex}] calling AddNewTransient')
            instance = traffic.AddNewTransient(model)
            if instance is None:
                logProxy.logger.error(f'PlaceObstruction: lane[{laneIndex}] AddNewTransient returned None, skipping')
                continue
            newObstructions.append({'instance': instance, 'model': model})
            # 生成直後は位置を設定せず、メインループ側で数ティック後に設定する(上のコメント参照)
            newPending.append({
                'instance': instance,
                'position': position,
                'ticksRemaining': PLACEMENT_SETTLE_TICKS,
            })

        currentObstructions = newObstructions
        pendingObstructions = newPending
        logProxy.logger.info(
            f'PlaceObstruction: {len(newObstructions)} instance(s) created, Position deferred to next ticks')


# メインループから毎ティック呼ばれる。生成直後で設定待ちの障害物があれば、
# 既定のティック数が経過したものから順にPositionを設定する
def ApplyPendingObstructions():
    global pendingObstructions
    if not pendingObstructions:
        return

    remaining = []
    appliedAny = False
    for pending in pendingObstructions:
        pending['ticksRemaining'] -= 1
        if pending['ticksRemaining'] > 0:
            remaining.append(pending)
            continue
        instance = pending['instance']
        logProxy.logger.info('ApplyPendingObstructions: setting Position')
        instance.Position = pending['position']
        logProxy.logger.info('ApplyPendingObstructions: Position set successfully')
        appliedAny = True
    pendingObstructions = remaining

    if appliedAny:
        winRoadProxy.MainForm.MainOpenGL.Changed()
        logProxy.logger.info('ApplyPendingObstructions: done')


# 設置中のPylonをすべてシミュレーションから削除する
def ResetObstruction():
    global currentObstructions, pendingObstructions
    # 設定待ちのまま削除すると、後でApplyPendingObstructionsが削除済みインスタンスに
    # Positionを設定しようとしてしまうため、先に取り消しておく
    pendingObstructions = []
    if not currentObstructions:
        logProxy.logger.info('ResetObstruction: nothing to remove')
        return
    traffic = winRoadProxy.SimulationCore.TrafficSimulation
    for obstruction in currentObstructions:
        logProxy.logger.info('ResetObstruction: calling DeleteTransientObject')
        traffic.DeleteTransientObject(obstruction['instance'])
    logProxy.logger.info(f'ResetObstruction: removed {len(currentObstructions)} obstruction(s) successfully')
    currentObstructions = []


# プロジェクト内の全ての一時オブジェクト(Transient)を無条件に削除する。
# このスクリプトは実行のたびにcurrentObstructions等をNoneでリセットするため、
# 過去の実行がクラッシュ等で正常終了しなかった場合、その回で配置したPylonが
# 道路上に残ったまま追跡できなくなることがある。それらの残骸を一掃するための
# 強制リセット用ボタン(このスクリプトが把握していないオブジェクトも含め、プロジェクト内の
# 一時オブジェクトを全て削除するため、通常の走行中の交通車両にも影響しうる点に注意)
def ClearAllTransientObjects():
    global currentObstructions, pendingObstructions

    traffic = winRoadProxy.SimulationCore.TrafficSimulation
    logProxy.logger.info('ClearAllTransientObjects: calling DeleteAllTransientObjects')
    traffic.DeleteAllTransientObjects()
    logProxy.logger.info('ClearAllTransientObjects: done')

    currentObstructions = []
    pendingObstructions = []


# 現在スクリプトが認識している配置済みバリケードの情報を、実際にUC-win/Road側の
# インスタンスから読み直して一覧にする(こちらで保持している値ではなく、都度
# instance.Position等を読み直すことで、本当にシーンに存在しているかを確認できる)
def ListPlacedBarricades():
    items = []
    for obstruction in currentObstructions:
        instance = obstruction['instance']
        instanceId = instance.ID
        name = instance.Name
        position = instance.Position
        items.append((instanceId, name, position.X, position.Y, position.Z))

    logProxy.logger.info(f"Found {len(items)} placed obstruction(s).")
    for instanceId, name, x, y, z in items:
        logProxy.logger.info(f"  ID={instanceId} Name={name} Position=({x}, {y}, {z})")

    return items


class RibbonButtonHandlerListBarricades(RibbonButtonHandler):
    def OnClick(self):
        try:
            items = ListPlacedBarricades()
            if not items:
                summary = "Nothing placed."
            else:
                instanceId, name, x, y, z = items[0]
                summary = (
                    f"{len(items)} placed. First: ID={instanceId} Name={name} "
                    f"Pos=({x:.1f}, {y:.1f}, {z:.1f})")
            ribbon.label_barricade_summary.Caption = summary
        except Exception:
            logProxy.logger.error(traceback.format_exc())


class RibbonButtonHandlerPlaceObstruction(RibbonButtonHandler):
    def OnClick(self):
        try:
            PlaceObstruction()
        except Exception:
            logProxy.logger.error(traceback.format_exc())


class RibbonButtonHandlerResetObstruction(RibbonButtonHandler):
    def OnClick(self):
        try:
            ResetObstruction()
        except Exception:
            logProxy.logger.error(traceback.format_exc())


class RibbonButtonHandlerClearAll(RibbonButtonHandler):
    def OnClick(self):
        try:
            ClearAllTransientObjects()
        except Exception:
            logProxy.logger.error(traceback.format_exc())


# プロジェクト内の全道路が持つ道路障害物(F8RoadObstructionProxy)を一覧取得する
# 戻り値は (road.Name, description, distance, length) のタプルのリスト
def ListObstructions():
    prj = winRoadProxy.Project
    roadCount = prj.RoadsCount

    items = []
    for i in range(roadCount):
        road = prj.Road(i)
        if road is None:
            continue
        obstructionCount = road.ObstructionsCount
        for j in range(obstructionCount):
            obstruction = road.Obstruction(j)
            if obstruction is None:
                continue
            items.append((road.Name, obstruction.Description, obstruction.Distance, obstruction.Length))

    logProxy.logger.info(f"Found {len(items)} obstruction(s).")
    for roadName, description, distance, length in items:
        logProxy.logger.info(
            f"  {roadName} @ {distance:.1f}m: {description} (Length={length:.1f}m)")

    return items


class RibbonButtonHandlerListObstructions(RibbonButtonHandler):
    def OnClick(self):
        try:
            items = ListObstructions()
            ribbon.ShowObstructionList(items)
        except Exception:
            logProxy.logger.error(traceback.format_exc())


# プロジェクト内の3Dモデルからnameと完全一致するものを探す(読み取り専用)
def FindThreeDModelByName(name):
    prj = winRoadProxy.Project
    count = prj.ThreeDModelsCount

    for i in range(count):
        model = prj.ThreeDModel(i)
        if model is not None and model.Name == name:
            return model

    logProxy.logger.error(f"3D model '{name}' not found among {count} model(s) in the project.")
    return None


class RibbonUI:
    def __init__(self):
        self.ribbonMenu = None

        # Position タブ: 緯度経度の入力とカメラ位置取得
        # (1タブに機能を詰め込むとリボンの横幅が足りず正しく表示されなかったため、
        #  機能ごとにタブを分けている。各タブにはグループを1つだけ置く)
        self.tabPosition = None
        self.groupPosition = None
        self.label_latitude = None
        self.edit_latitude = None
        self.label_longitude = None
        self.edit_longitude = None
        self.button_get_camera_position = None

        # Placement タブ: 障害物の設置・解除・確認
        self.tabPlacement = None
        self.groupPlacement = None
        self.button_place_obstruction = None
        self.button_reset_obstruction = None
        self.button_clear_all = None
        self.button_list_barricades = None
        self.label_barricade_summary = None

        # Obstructions タブ: 道路に既存の道路障害物の一覧
        self.tabObstructions = None
        self.groupObstructions = None
        self.button_list_obstructions = None
        self.panel_obstructions = None
        self.label_obstruction_summary = None
        self.obstructionItemLabels = []

        self.EventList = []

    def MakeRibbonTab(self, Parent, partsName, caption):
        if Parent is not None:
            tab = Parent.GetTabByName(partsName)
            if tab is None:
                tab = Parent.CreateTab(partsName, 10000)
                tab.Caption = caption
            return tab

    def MakeRibbonGroup(self, Parent, partsName, caption):
        if Parent is not None:
            group = Parent.GetGroupByName(partsName)
            if group is None:
                group = Parent.CreateGroup(partsName, 1000)
                group.Caption = caption
            return group

    def MakeRibbonLabel(self, Parent, partsName, caption):
        if Parent is not None:
            label = Parent.GetControlByName(partsName)
            if label is None:
                label = Parent.CreateLabel(partsName)
                label.Caption = caption
            return label

    def MakeRibbonEdit(self, Parent, partsName, defaultText):
        if Parent is not None:
            edit = Parent.GetControlByName(partsName)
            if edit is None:
                edit = Parent.CreateEdit(partsName)
                edit.Text = defaultText
            return edit

    def MakeRibbonPanel(self, Parent, partsName):
        if Parent is not None:
            panel = Parent.GetControlByName(partsName)
            if panel is None:
                panel = Parent.CreatePanel(partsName)
            return panel

    def SetCallbackEvent(self, button, handler):
        if button is not None:
            isValue = button.IsSetCallbackOnClick()
        if isValue == False:
            Event = com.WithEvents(button, handler)
            Event.SetCOMEventClass(Event)
            button.RegisterEventHandlers()
            self.EventList.append(Event)
            return Event

    def MakeRibbonButton(self, Parent, partsName, caption, handler):
        if Parent is not None:
            button = Parent.GetControlByName(partsName)
            if button is None:
                button = Parent.CreateButton(partsName)
                button.Caption = caption
                self.SetCallbackEvent(button, handler)
            return button

    def CloseCallbackEvent(self):
        if self.EventList is not None:
            for Event in self.EventList:
                Event.close()
            self.EventList.clear()

    # 道路障害物の一覧をパネル内に表示する
    # (Groupに直接ラベルを積むとGroup自身の自動レイアウトと衝突して正しく
    #  表示されないため、位置を自由に指定できるPanelの中にサマリー行＋
    #  最大MAX_DISPLAYED_OBSTRUCTIONS件のラベルを縦に並べる)
    # クリックのたびにラベルを削除・再作成すると再描画が不安定になる
    # (2回目以降に表示が消える)ため、ラベル自体はMakeRibbonUIで作成済みの
    # ものを使い回し、ここではCaption/Visibleだけを書き換える。
    def ShowObstructionList(self, items):
        shown = items[:MAX_DISPLAYED_OBSTRUCTIONS]

        if not items:
            self.label_obstruction_summary.Caption = "No obstructions found."
        elif len(items) > len(shown):
            self.label_obstruction_summary.Caption = (
                f"{len(items)} obstruction(s) found (showing first {len(shown)}):")
        else:
            self.label_obstruction_summary.Caption = f"{len(items)} obstruction(s) found:"

        for idx, label in enumerate(self.obstructionItemLabels):
            if idx < len(shown):
                roadName, description, distance, length = shown[idx]
                label.Caption = f"{roadName} @ {distance:.1f}m: {description} (L={length:.1f}m)"
                label.Visible = True
            else:
                label.Caption = ''
                label.Visible = False

    def MakeRibbonUI(self):
        mainForm = winRoadProxy.MainForm
        self.ribbonMenu = mainForm.MainRibbonMenu

        # === Position タブ: 緯度経度の入力とカメラ位置取得 ===
        self.tabPosition = self.MakeRibbonTab(self.ribbonMenu, 'RoadBlockagePluginPosition', 'Road Blockage: Position')
        self.groupPosition = self.MakeRibbonGroup(self.tabPosition, 'GroupPosition', 'Position')

        self.label_latitude = self.MakeRibbonLabel(self.groupPosition, 'LabelLatitude', 'Latitude')
        self.edit_latitude = self.MakeRibbonEdit(self.groupPosition, 'EditLatitude', '')

        self.label_longitude = self.MakeRibbonLabel(self.groupPosition, 'LabelLongitude', 'Longitude')
        self.edit_longitude = self.MakeRibbonEdit(self.groupPosition, 'EditLongitude', '')

        self.button_get_camera_position = self.MakeRibbonButton(
            self.groupPosition, 'ButtonGetCameraPosition', 'Get Camera Position',
            RibbonButtonHandlerGetCameraPosition)
        # デフォルト幅だとキャプションが収まらないため広げる
        self.button_get_camera_position.Width = 160

        # === Placement タブ: 障害物の設置・解除・確認 ===
        self.tabPlacement = self.MakeRibbonTab(
            self.ribbonMenu, 'RoadBlockagePluginPlacement', 'Road Blockage: Placement')
        self.groupPlacement = self.MakeRibbonGroup(self.tabPlacement, 'GroupPlacement', 'Placement')

        self.button_place_obstruction = self.MakeRibbonButton(
            self.groupPlacement, 'ButtonPlaceObstruction', 'Place Obstruction',
            RibbonButtonHandlerPlaceObstruction)
        self.button_place_obstruction.Width = 160

        self.button_reset_obstruction = self.MakeRibbonButton(
            self.groupPlacement, 'ButtonResetObstruction', 'Reset', RibbonButtonHandlerResetObstruction)
        self.button_reset_obstruction.Width = 160

        # 過去の実行がクラッシュ等で正常終了せず、道路上に残ってしまった一時オブジェクトを
        # 一掃するための強制リセット。通常の走行中の交通車両にも影響しうる点に注意
        self.button_clear_all = self.MakeRibbonButton(
            self.groupPlacement, 'ButtonClearAll', 'Clear All', RibbonButtonHandlerClearAll)
        self.button_clear_all.Width = 160

        # 配置中のオブジェクトをUC-win/Road側から読み直して確認するボタン
        # (見た目に表示されない問題の切り分け用。本当にシーンに存在するかを確認する)
        self.button_list_barricades = self.MakeRibbonButton(
            self.groupPlacement, 'ButtonListBarricades', 'List Placed', RibbonButtonHandlerListBarricades)
        self.button_list_barricades.Width = 160

        self.label_barricade_summary = self.MakeRibbonLabel(
            self.groupPlacement, 'LabelBarricadeSummary', '')
        self.label_barricade_summary.Width = 420

        # === Obstructions タブ: 道路に既存の道路障害物の一覧 ===
        self.tabObstructions = self.MakeRibbonTab(
            self.ribbonMenu, 'RoadBlockagePluginObstructions', 'Road Blockage: Obstructions')
        self.groupObstructions = self.MakeRibbonGroup(
            self.tabObstructions, 'GroupObstructions', 'Road Obstructions')

        self.button_list_obstructions = self.MakeRibbonButton(
            self.groupObstructions, 'ButtonListObstructions', 'List Obstructions',
            RibbonButtonHandlerListObstructions)
        self.button_list_obstructions.Width = 160

        # 一覧の表示先パネル(サマリー行 + 最大MAX_DISPLAYED_OBSTRUCTIONS件のラベル)
        self.panel_obstructions = self.MakeRibbonPanel(self.groupObstructions, 'PanelObstructions')
        self.panel_obstructions.Width = 420
        self.panel_obstructions.Height = 18 * (MAX_DISPLAYED_OBSTRUCTIONS + 1) + 6

        self.label_obstruction_summary = self.MakeRibbonLabel(
            self.panel_obstructions, 'LabelObstructionSummary', '')
        self.label_obstruction_summary.Width = 400
        self.label_obstruction_summary.Top = 0

        # 障害物一覧の表示行は削除・再作成せず使い回すため、ここで作成しておく
        rowHeight = 18
        self.obstructionItemLabels = []
        for idx in range(MAX_DISPLAYED_OBSTRUCTIONS):
            label = self.MakeRibbonLabel(self.panel_obstructions, f'LabelObstructionItem{idx}', '')
            label.Width = 400
            label.Top = rowHeight * (idx + 1)
            label.Visible = False
            self.obstructionItemLabels.append(label)

    def KillRibbonUI(self):
        # MakeRibbonUIが途中で失敗していても後始末できるよう、
        # 各ステップはNoneチェックしてから実行する
        self.CloseCallbackEvent()

        # --- Road Obstructions タブ ---
        if self.panel_obstructions is not None:
            for label in self.obstructionItemLabels:
                self.panel_obstructions.DeleteControl(label)
            self.obstructionItemLabels.clear()
            if self.label_obstruction_summary is not None:
                self.panel_obstructions.DeleteControl(self.label_obstruction_summary)
                self.label_obstruction_summary = None
            if self.groupObstructions is not None:
                self.groupObstructions.DeleteControl(self.panel_obstructions)
            self.panel_obstructions = None

        if self.groupObstructions is not None:
            if self.button_list_obstructions is not None:
                self.button_list_obstructions.UnRegisterEventHandlers()
                self.groupObstructions.DeleteControl(self.button_list_obstructions)
                self.button_list_obstructions = None
            if self.tabObstructions is not None:
                self.tabObstructions.DeleteGroup(self.groupObstructions)
            self.groupObstructions = None

        if self.tabObstructions is not None:
            if self.tabObstructions.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.tabObstructions)
            self.tabObstructions = None

        # --- Placement タブ ---
        if self.groupPlacement is not None:
            for button in (self.button_list_barricades, self.button_clear_all,
                            self.button_reset_obstruction, self.button_place_obstruction):
                if button is not None:
                    button.UnRegisterEventHandlers()
                    self.groupPlacement.DeleteControl(button)
            self.button_list_barricades = None
            self.button_clear_all = None
            self.button_reset_obstruction = None
            self.button_place_obstruction = None
            if self.label_barricade_summary is not None:
                self.groupPlacement.DeleteControl(self.label_barricade_summary)
                self.label_barricade_summary = None
            if self.tabPlacement is not None:
                self.tabPlacement.DeleteGroup(self.groupPlacement)
            self.groupPlacement = None

        if self.tabPlacement is not None:
            if self.tabPlacement.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.tabPlacement)
            self.tabPlacement = None

        # --- Position タブ ---
        if self.groupPosition is not None:
            if self.button_get_camera_position is not None:
                self.button_get_camera_position.UnRegisterEventHandlers()
                self.groupPosition.DeleteControl(self.button_get_camera_position)
                self.button_get_camera_position = None
            for ctrl in (self.edit_longitude, self.label_longitude,
                         self.edit_latitude, self.label_latitude):
                if ctrl is not None:
                    self.groupPosition.DeleteControl(ctrl)
            self.edit_longitude = None
            self.label_longitude = None
            self.edit_latitude = None
            self.label_latitude = None
            if self.tabPosition is not None:
                self.tabPosition.DeleteGroup(self.groupPosition)
            self.groupPosition = None

        if self.tabPosition is not None:
            if self.tabPosition.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.tabPosition)
            self.tabPosition = None

        self.ribbonMenu = None


def main():
    scriptName = 'RoadBlockagePlugin'
    start = time.perf_counter_ns()

    global winRoadProxy
    global const
    global logProxy
    global ribbon
    global currentObstructions
    global pendingObstructions
    winRoadProxy = None
    logProxy = None
    ribbon = None
    currentObstructions = []
    pendingObstructions = []

    try:
        # APIのエントリポイント
        winRoadProxy = UCwinRoadComProxy()
        const = winRoadProxy.const

        # ロガーの設定
        logfilepath = winRoadProxy.PythonPluginDirectory() + scriptName + '.log'
        logProxy = LoggerProxy(scriptName, logfilepath)
        logProxy.logger.info('Start ' + scriptName)

        # [Async]チェックがONになっていない場合、イベントループに入るとUC-win/Road
        # が固まってしまうため、リボンやループを作らずここで安全に終了する。
        # print()は非同期でない場合こそ[Script Editor]の出力欄に表示されるため、
        # ログファイルと併用してユーザにすぐ気付けるようにする。
        if not IsRunningAsync():
            message = (
                "[Async] checkbox is OFF. This script uses a blocking event loop "
                "and must be run with [Async] turned ON in the ribbon or Script "
                "Editor, or it will freeze UC-win/Road. Aborting without starting.")
            logProxy.logger.error(message)
            print(message)
            return

        # リボンUIの作成
        ribbon = RibbonUI()
        ribbon.MakeRibbonUI()
        logProxy.logger.info('Ribbon UI created')

        # 障害物の配置(AddNewTransient)はシミュレーションが実行中でないと正しく
        # 初期化されない(表示されない/クラッシュする)ことが分かっている。プラグイン側で
        # ScriptStatus=_scPlayを自動設定する対応も試したが改善しなかったため撤回し、
        # ユーザが事前に手動でシミュレーションを開始してからプラグインを読み込む運用とする。

        # Event Loop
        loopFlg = True
        winRoadProxy.ApplicationServices.IsPythonScriptRun = loopFlg
        logProxy.logger.info('Loop Start')
        while loopFlg:
            time.sleep(0.005)
            ApplyPendingObstructions()
            loopFlg = winRoadProxy.ApplicationServices.IsPythonScriptRun
            if loopFlg == False:
                logProxy.logger.info("loopFlg={}".format(loopFlg))
                logProxy.logger.info("Script close")
        winRoadProxy.ApplicationServices.IsPythonScriptRun = loopFlg

    except Exception:
        # 原因不明のまま無言で落ちないよう、必ずログにトレースバックを残す
        if logProxy is not None:
            logProxy.logger.error(traceback.format_exc())
        else:
            print(traceback.format_exc())

    finally:
        elapsed_time = time.perf_counter_ns() - start
        if logProxy is not None:
            logProxy.logger.info("Total:{}ms".format(elapsed_time / 1000000))

        # 設置中の障害物とリボンの削除（失敗してもログ後始末は必ず行う）
        try:
            ResetObstruction()
        except Exception:
            if logProxy is not None:
                logProxy.logger.error(traceback.format_exc())

        if ribbon is not None:
            try:
                ribbon.KillRibbonUI()
            except Exception:
                if logProxy is not None:
                    logProxy.logger.error(traceback.format_exc())

        if logProxy is not None:
            logProxy.logger.info('End ' + scriptName)
            logProxy.killLogger()

        if winRoadProxy is not None:
            del winRoadProxy


if __name__ == '__main__':
    main()
