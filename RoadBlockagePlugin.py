from UCwinRoadCOM import *
from UCwinRoadCOM import *
from LoggerProxy import LoggerProxy
from UCwinRoadUtils import *
from CallbackHandlers import *
import time
import traceback
import win32com.client as com

# APIのエントリポイント
winRoadProxy = None
const = None

# ロガー
logProxy = None

# リボンUI
ribbon = None


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


class RibbonUI:
    def __init__(self):
        self.ribbonMenu = None
        self.ribbonTab = None
        self.ribbonGroup = None
        self.label_latitude = None
        self.edit_latitude = None
        self.label_longitude = None
        self.edit_longitude = None
        self.button_get_camera_position = None
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

    def CloseCallbackEvent(self):
        if self.EventList is not None:
            for Event in self.EventList:
                Event.close()
            self.EventList.clear()

    def MakeRibbonUI(self):
        mainForm = winRoadProxy.MainForm
        # Menu
        self.ribbonMenu = mainForm.MainRibbonMenu
        # Tab
        self.ribbonTab = self.MakeRibbonTab(self.ribbonMenu, 'RoadBlockagePlugin', 'Road Blockage')
        # Group
        self.ribbonGroup = self.MakeRibbonGroup(self.ribbonTab, 'GroupPosition', 'Position')

        # 緯度
        self.label_latitude = self.MakeRibbonLabel(self.ribbonGroup, 'LabelLatitude', 'Latitude')
        self.edit_latitude = self.MakeRibbonEdit(self.ribbonGroup, 'EditLatitude', '')

        # 経度
        self.label_longitude = self.MakeRibbonLabel(self.ribbonGroup, 'LabelLongitude', 'Longitude')
        self.edit_longitude = self.MakeRibbonEdit(self.ribbonGroup, 'EditLongitude', '')

        # 現在のカメラ位置を緯度経度に変換して入力欄に反映するボタン
        self.button_get_camera_position = self.MakeRibbonButton(
            self.ribbonGroup, 'ButtonGetCameraPosition', 'Get Camera Position',
            RibbonButtonHandlerGetCameraPosition)
        # デフォルト幅だとキャプションが収まらないため広げる
        self.button_get_camera_position.Width = 160

    def KillRibbonUI(self):
        # MakeRibbonUIが途中で失敗していても後始末できるよう、
        # 各ステップはNoneチェックしてから実行する
        self.CloseCallbackEvent()

        if self.ribbonGroup is not None:
            if self.button_get_camera_position is not None:
                self.button_get_camera_position.UnRegisterEventHandlers()
                self.ribbonGroup.DeleteControl(self.button_get_camera_position)
                self.button_get_camera_position = None
            for ctrl in (self.edit_longitude, self.label_longitude,
                         self.edit_latitude, self.label_latitude):
                if ctrl is not None:
                    self.ribbonGroup.DeleteControl(ctrl)
            if self.ribbonTab is not None:
                self.ribbonTab.DeleteGroup(self.ribbonGroup)
            self.ribbonGroup = None

        if self.ribbonTab is not None:
            if self.ribbonTab.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.ribbonTab)
            self.ribbonTab = None

        self.ribbonMenu = None


def main():
    scriptName = 'RoadBlockagePlugin'
    start = time.perf_counter_ns()

    global winRoadProxy
    global const
    global logProxy
    global ribbon
    winRoadProxy = None
    logProxy = None
    ribbon = None

    try:
        # APIのエントリポイント
        winRoadProxy = UCwinRoadComProxy()
        const = winRoadProxy.const

        # ロガーの設定
        logfilepath = winRoadProxy.PythonPluginDirectory() + scriptName + '.log'
        logProxy = LoggerProxy(scriptName, logfilepath)
        logProxy.logger.info('Start ' + scriptName)

        # リボンUIの作成
        ribbon = RibbonUI()
        ribbon.MakeRibbonUI()
        logProxy.logger.info('Ribbon UI created')

        # Event Loop
        loopFlg = True
        winRoadProxy.ApplicationServices.IsPythonScriptRun = loopFlg
        logProxy.logger.info('Loop Start')
        while loopFlg:
            time.sleep(0.005)
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

        # リボンの削除（失敗してもログ後始末は必ず行う）
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
