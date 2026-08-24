from UCwinRoadCOM import *
from UCwinRoadCOM import *
from LoggerProxy import LoggerProxy
from UCwinRoadUtils import *
from CallbackHandlers import *
import time
import win32com.client as com

# APIのエントリポイント
winRoadProxy = None
const = None

# ロガー
logProxy = None

# リボンUI
ribbon = None

# 設置済みの閉塞（ブロッキング車両）のリスト
blockageList = []
blockageIdCounter = 0


# 道路閉塞として設置した車両を停止させ続けるハンドラ
# （自身が生成した閉塞インスタンスの ID のときだけ制御する）
class BlockageInstanceHandler(HandlerBase):
    targetID = None

    def OnBeforeCalculateMovement(self, dTimeInSeconds, proxy):
        if proxy is None:
            return
        instance = com.Dispatch(proxy)
        if self.targetID is not None and instance.ID != self.targetID:
            return
        instance.EngineOn = False
        instance.Throttle = 0.0
        instance.Brake = 1.0
        instance.Clutch = 0.0
        instance.Steering = 0.0
        instance.SetSpeed(const._MeterPerSecond, 0)
        instance.PositionInTraffic = instance.Position


class RibbonButtonHandlerPlace(RibbonButtonHandler):
    def OnClick(self):
        try:
            roadName = ribbon.edit_road.Text
            lane = int(ribbon.edit_lane.Text)
            distance = float(ribbon.edit_distance.Text)
            isForward = ribbon.edit_forward.Text.strip() not in ('0', 'False', 'false')
            modelName = ribbon.edit_model.Text
            PlaceBlockage(roadName, lane, distance, isForward, modelName)
        except Exception as e:
            logProxy.logger.error(f"[PlaceBlockage error] {e}")
        winRoadProxy.MainForm.MainOpenGL.Changed()


class RibbonButtonHandlerClear(RibbonButtonHandler):
    def OnClick(self):
        try:
            ClearAllBlockages()
        except Exception as e:
            logProxy.logger.error(f"[ClearAllBlockages error] {e}")
        winRoadProxy.MainForm.MainOpenGL.Changed()


class RibbonUI:
    def __init__(self):
        self.ribbonMenu = None
        self.ribbonTab = None
        self.ribbonGroup = None
        self.label_road = None
        self.edit_road = None
        self.label_lane = None
        self.edit_lane = None
        self.label_distance = None
        self.edit_distance = None
        self.label_forward = None
        self.edit_forward = None
        self.label_model = None
        self.edit_model = None
        self.button_place = None
        self.button_clear = None
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

    def DeleteControlFromParent(self, child, Parent):
        if child is not None:
            child.UnRegisterEventHandlers()
            if Parent is not None:
                Parent.DeleteControl(child)
            child = None

    def CloseCallbackEvent(self):
        if self.EventList is not None:
            for Event in self.EventList:
                Event.close()

    def MakeRibbonUI(self):
        mainForm = winRoadProxy.MainForm
        # Menu
        self.ribbonMenu = mainForm.MainRibbonMenu
        # Tab
        self.ribbonTab = self.MakeRibbonTab(self.ribbonMenu, 'RoadBlockagePlugin', 'Road Blockage')
        # Group
        self.ribbonGroup = self.MakeRibbonGroup(self.ribbonTab, 'GroupBlockage', 'Blockage')

        defaultRoad = FirstRoadName()

        # Road name
        self.label_road = self.MakeRibbonLabel(self.ribbonGroup, 'LabelRoadName', 'Road')
        self.edit_road = self.MakeRibbonEdit(self.ribbonGroup, 'EditRoadName', defaultRoad)

        # Lane number
        self.label_lane = self.MakeRibbonLabel(self.ribbonGroup, 'LabelLane', 'Lane')
        self.edit_lane = self.MakeRibbonEdit(self.ribbonGroup, 'EditLane', '1')

        # Distance along the road (m)
        self.label_distance = self.MakeRibbonLabel(self.ribbonGroup, 'LabelDistance', 'Distance(m)')
        self.edit_distance = self.MakeRibbonEdit(self.ribbonGroup, 'EditDistance', '0')

        # Lane direction (1=forward / 0=backward)
        self.label_forward = self.MakeRibbonLabel(self.ribbonGroup, 'LabelForward', 'Forward(1/0)')
        self.edit_forward = self.MakeRibbonEdit(self.ribbonGroup, 'EditForward', '1')

        # 3D model name used as the blockage marker (blank = first vehicle model)
        self.label_model = self.MakeRibbonLabel(self.ribbonGroup, 'LabelModel', 'Model')
        self.edit_model = self.MakeRibbonEdit(self.ribbonGroup, 'EditModel', '')

        self.button_place = self.MakeRibbonButton(self.ribbonGroup, 'ButtonPlaceBlockage', 'Place', RibbonButtonHandlerPlace)
        self.button_clear = self.MakeRibbonButton(self.ribbonGroup, 'ButtonClearBlockage', 'Clear All', RibbonButtonHandlerClear)

    def KillRibbonUI(self):
        self.CloseCallbackEvent()
        self.DeleteControlFromParent(self.button_place, self.ribbonGroup)
        self.DeleteControlFromParent(self.button_clear, self.ribbonGroup)
        self.ribbonGroup.DeleteControl(self.edit_model)
        self.ribbonGroup.DeleteControl(self.label_model)
        self.ribbonGroup.DeleteControl(self.edit_forward)
        self.ribbonGroup.DeleteControl(self.label_forward)
        self.ribbonGroup.DeleteControl(self.edit_distance)
        self.ribbonGroup.DeleteControl(self.label_distance)
        self.ribbonGroup.DeleteControl(self.edit_lane)
        self.ribbonGroup.DeleteControl(self.label_lane)
        self.ribbonGroup.DeleteControl(self.edit_road)
        self.ribbonGroup.DeleteControl(self.label_road)
        self.ribbonTab.DeleteGroup(self.ribbonGroup)
        self.ribbonGroup = None
        if self.ribbonTab.RibbonGroupsCount == 0:
            self.ribbonMenu.DeleteTab(self.ribbonTab)
        self.ribbonTab = None
        self.ribbonMenu = None


# プロジェクト内の最初の道路名を取得（初期値表示用）
def FirstRoadName():
    prj = winRoadProxy.Project
    if prj.RoadsCount > 0:
        road = prj.Road(0)
        if road is not None:
            return road.Name
    return ''


# 名前で道路を検索
def FindRoadByName(name):
    prj = winRoadProxy.Project
    count = prj.RoadsCount
    for i in range(count):
        road = prj.Road(i)
        if road is not None and road.Name == name:
            return road
    return None


# 閉塞マーカーに使う3Dモデルを検索
# modelName が指定されていればその名前のモデル、なければ最初に見つかった車両モデルを使う
def FindBlockageModel(modelName):
    prj = winRoadProxy.Project
    count = prj.ThreeDModelsCount
    if modelName:
        for i in range(count):
            model = prj.ThreeDModel(i)
            if model is not None and model.Name == modelName:
                return model
        logProxy.logger.error(f"3D model not found: {modelName}")
        return None
    for i in range(count):
        model = prj.ThreeDModel(i)
        if model is not None and model.ModelType == const._VehicleModel:
            return model
    return None


# 指定した道路・車線・距離に閉塞を設置する
def PlaceBlockage(roadName, lane, distance, isForward, modelName):
    global blockageIdCounter

    road = FindRoadByName(roadName)
    if road is None:
        logProxy.logger.error(f"Road not found: {roadName}")
        return None

    model = FindBlockageModel(modelName)
    if model is None:
        logProxy.logger.error("No 3D model available to use as a blockage marker.")
        return None

    traffic = winRoadProxy.SimulationCore.TrafficSimulation

    vptype = com.DispatchEx('UCwinRoad.F8COMVehiclePlacementType')
    vptype.IsForward = isForward
    vptype.Lane = lane
    vptype.Distance = distance

    instance = traffic.AddNewVehicle(model, road, vptype)
    if instance is None:
        logProxy.logger.error("Failed to place blockage.")
        return None

    eventList = []
    SetCallbackHandlers(eventList, instance, BlockageInstanceHandler)
    for entry in eventList:
        entry[1].targetID = instance.ID

    blockageIdCounter += 1
    record = {
        'blockageID': blockageIdCounter,
        'instance': instance,
        'eventList': eventList,
        'roadName': roadName,
        'lane': lane,
        'distance': distance,
    }
    blockageList.append(record)
    logProxy.logger.info(
        f"Placed blockage #{blockageIdCounter} on '{roadName}' lane {lane} at {distance}m (ID={instance.ID})")
    return record


# 閉塞を1件解除する（コールバックを解除し、車両の制御をシミュレーションに戻す）
def ClearBlockage(record):
    CloseCallbackEvent(record['eventList'])
    logProxy.logger.info(f"Cleared blockage #{record['blockageID']}")


# 設置済みの閉塞をすべて解除する
def ClearAllBlockages():
    for record in blockageList:
        ClearBlockage(record)
    blockageList.clear()


def main():
    scriptName = 'RoadBlockagePlugin'
    try:
        start = time.perf_counter_ns()

        # APIのエントリポイント
        global winRoadProxy
        global const
        winRoadProxy = UCwinRoadComProxy()
        const = winRoadProxy.const

        # ロガーの設定
        global logProxy
        logfilepath = winRoadProxy.PythonPluginDirectory() + scriptName + '.log'
        logProxy = LoggerProxy(scriptName, logfilepath)
        logProxy.logger.info('Start ' + scriptName)

        global blockageList
        global blockageIdCounter
        blockageList = []
        blockageIdCounter = 0

        # リボンUIの作成
        global ribbon
        ribbon = RibbonUI()
        ribbon.MakeRibbonUI()

        # Event Loop
        loopFlg = True
        winRoadProxy.ApplicationServices.IsPythonScriptRun = loopFlg
        while loopFlg:
            time.sleep(0.005)
            loopFlg = winRoadProxy.ApplicationServices.IsPythonScriptRun
            if loopFlg == False:
                logProxy.logger.info("loopFlg={}".format(loopFlg))
                logProxy.logger.info("Script close")
        winRoadProxy.ApplicationServices.IsPythonScriptRun = loopFlg

    finally:
        elapsed_time = time.perf_counter_ns() - start
        logProxy.logger.info("Total:{}ms".format(elapsed_time / 1000000))

        # 閉塞の解除とリボンの削除
        ClearAllBlockages()
        ribbon.KillRibbonUI()

        logProxy.logger.info('End ' + scriptName)
        logProxy.killLogger()
        del winRoadProxy


if __name__ == '__main__':
    main()
