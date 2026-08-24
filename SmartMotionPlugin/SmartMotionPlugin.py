from UCwinRoadCOM import *
from UCwinRoadCOM import *
from LoggerProxy import LoggerProxy
from UCwinRoadUtils import *
from CallbackHandlers import *
import time
import win32com.client as com

# 列挙型
from enum import Enum

# Socket
import socket

# OSC
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer

# 非同期処理
import asyncio

# APIのエントリポイント
winRoadProxy = None
const = None

# ロガー
logProxy = None

# イベントリスト
eventList = None

# ユーザの位置
userPosition = None

# ターゲットの車両
targetCar = None

# 他の車両
blueCar = None
whiteCar = None

# 駐車場の座標
park_Position = None
park_Direction = None

# IPアドレスとポート
ip_address = None
port_number = None

# リボン
ribbonMenu = None
tab = None
group = None
label_ip = None
label_port = None
edit_ip = None
edit_port = None

class State(Enum):
    STOP = 0
    CALL = 1
    RETURN = 2
    ACCEL = 3
    BACK = 4
    TURN_LEFT = 5
    TURN_RIGHT = 6
    RESET = 7

class CallPhase(Enum):
    IDLE = 0
    STRAIGHT = 1   # 直進 1秒
    RIGHT = 2      # 右折 3秒
    BACK = 3       # バック 3秒
    DONE = 4

#追加
class ReturnPhase(Enum):
    IDLE = 0
    LEFT1 = 1 # 左回転1
    BACK1 = 2    # バック1
    LEFT2 = 3    # 左回転2
    BACK2 = 4    # バック2
    DONE = 5

# 初期状態
state = State.STOP

# CALLシーケンス管理
call_phase = CallPhase.IDLE
call_phase_start = 0.0

# RETURNシーケンス管理
return_phase = ReturnPhase.IDLE
return_phase_start = 0.0

#追加
# 時間プロファイル（必要に応じて変更）
RETURN_T_BACK1 = 3.0
RETURN_T_RIGHT = 3.0
RETURN_T_BACK2 = 1.0

def start_call_sequence():
    global call_phase, call_phase_start
    call_phase = CallPhase.STRAIGHT
    call_phase_start = time.monotonic()

def reset_call_sequence():
    global call_phase, call_phase_start
    call_phase = CallPhase.IDLE
    call_phase_start = 0.0

def start_return_sequence():
    global return_phase, return_phase_start
    return_phase = ReturnPhase.LEFT1
    return_phase_start = time.monotonic()

def reset_return_sequence():
    global return_phase, return_phase_start
    return_phase = ReturnPhase.IDLE
    return_phase_start = 0.0

# OSCの処理
def osc_handler(address, *args):
    global state
    message = args[0]
    logProxy.logger.info(f"OSC={message}")

    if message == "CALL":
        state = State.CALL
        start_call_sequence()
        reset_return_sequence()
    elif message == "RETURN":
        state = State.RETURN
        reset_call_sequence()
        start_return_sequence()
    elif message == "STOP":
        state = State.STOP
        reset_call_sequence()
        reset_return_sequence()
    elif message == "ACCEL":
        state = State.ACCEL
        reset_call_sequence()
        reset_return_sequence()
    elif message == "BACK":
        state = State.BACK
        reset_call_sequence()
        reset_return_sequence()        
    elif message == "TURN LEFT":
        state = State.TURN_LEFT
        reset_call_sequence()
        reset_return_sequence()
    elif message == "TURN RIGHT":
        state = State.TURN_RIGHT
        reset_call_sequence()
        reset_return_sequence()
    elif message == "RESET":
        state = State.RESET
        reset_call_sequence()
        reset_return_sequence()        
    

# ループ処理
async def loop(winRoadProxy):
    
    sleep_time = 0.1
    winRoadProxy.ApplicationServices.IsPythonScriptRun = True
    
    try:
        while winRoadProxy.ApplicationServices.IsPythonScriptRun:
            await asyncio.sleep(sleep_time)
    except Exception as e:
        logProxy.logger.error(f"[loop error] {e}")
    finally:
        logProxy.logger.info("Script loop terminated.")
        
# OSCサーバ
async def init(winRoadProxy):
    dispatcher = Dispatcher()
    dispatcher.map("/motion", osc_handler)
    server = AsyncIOOSCUDPServer((ip_address, port_number), dispatcher, asyncio.get_event_loop())

    try:
        transport, protocol = await server.create_serve_endpoint()
        await loop(winRoadProxy)
    except Exception as e:
        logProxy.logger.error(f"[Error] {e}")
    finally:
        transport.close()
        logProxy.logger.info("OSC Server close")

class TransientInstanceHandler:

    def SetCOMEventClass(self, events):
        self.events = events

    def OnIsExistEventHandler(self, funcname):
        try:
            func = getattr(self.events, funcname)
        except AttributeError:
            return False
        return True
    
    def ResetCarPosition(self, instance):
        global park_Position
        global park_Direction        
        instance.Position = park_Position
        instance.Direction = park_Direction
        instance.PositionInTraffic = instance.Position

    def OnBeforeCalculateMovement(self, dTimeInSeconds, proxy):
        try:
            global state
            global call_phase
            global call_phase_start
            global return_phase
            global return_phase_start
            global park_Position
            global park_Direction
            instance = com.Dispatch(proxy)
            if instance.ID == targetCar.ID:
                if instance.TransientType == const._TransientCar:

                    if not(instance.EngineOn):            
                        instance.EngineOn = True
                        park_Position = instance.Position
                        park_Direction = instance.Direction

                    #logProxy.logger.info(f"Car Position: {instance.Position.X} {instance.Position.Y} {instance.Position.Z}")
                    #logProxy.logger.info(f"Car Direction: {instance.Direction.X} {instance.Direction.Y} {instance.Direction.Z}")


                    if state == State.CALL:

                        # 経過時間でフェーズを遷移
                        now = time.monotonic()
                        elapsed = now - call_phase_start

                        # フェーズごとの遷移条件
                        if call_phase == CallPhase.STRAIGHT:
                            # 直進 1秒
                            instance.Throttle = 0.08
                            instance.Brake = 0.0
                            instance.Clutch = 0.0
                            instance.Steering = 0.0
                            if elapsed >= 3.5:
                                call_phase = CallPhase.RIGHT
                                call_phase_start = now

                        elif call_phase == CallPhase.RIGHT:
                            #右折 3秒（低速でステア右．必要ならスピードやステア角は調整）
                            instance.Throttle = 0.05
                            instance.Brake = 0.0
                            instance.Clutch = 0.0
                            instance.Steering = -1.0
                            if elapsed >= 2.8:
                                call_phase = CallPhase.BACK
                                call_phase_start = now
                                # バックに移る前に一旦停止したい場合は以下を有効化
                                # instance.Throttle = 0.0
                                # instance.Brake = 1.0

                        elif call_phase == CallPhase.BACK:
                            # バック 3秒（一定の後退速度）
                            instance.Throttle = 0.0
                            instance.Brake = 0.0
                            instance.Clutch = 0.0
                            instance.Steering = 0.0
                            instance.SetSpeed(const._MeterPerSecond, -1)
                            if elapsed >= 5.7:
                                call_phase = CallPhase.DONE
                                call_phase_start = now

                        elif call_phase == CallPhase.DONE:
                            # シーケンス完了後は停止へ
                            state = State.STOP
                            reset_call_sequence()

                        print(f"{state}/{call_phase} Throttle:{instance.Throttle} Brake:{instance.Brake} Steering:{instance.Steering}")

                        
                    elif state == State.RETURN:

                        # 経過時間でフェーズを遷移
                        now = time.monotonic()
                        elapsed = now - return_phase_start

                        # フェーズごとの遷移条件
                        if return_phase == ReturnPhase.LEFT1:
                            # 左折 5秒
                            instance.Throttle = 0.2
                            instance.Brake = 0.0
                            instance.Clutch = 0.0
                            instance.Steering = -1.0
                            if elapsed >= 7.0:
                                return_phase = ReturnPhase.BACK1
                                return_phase_start = now

                        elif return_phase == ReturnPhase.BACK1:
                            # バック 3秒（一定の後退速度）
                            instance.Throttle = 0.0
                            instance.Brake = 0.0
                            instance.Clutch = 0.0
                            instance.Steering = 0.0
                            instance.SetSpeed(const._MeterPerSecond, -1)
                            
                            if elapsed >= 6.5:
                                return_phase = ReturnPhase.LEFT2
                                return_phase_start = now

                        elif return_phase == ReturnPhase.LEFT2:
                            # 左折 1秒
                            instance.Throttle = 0.2
                            instance.Brake = 0.0
                            instance.Clutch = 0.0
                            instance.Steering = -1.0

                            if((instance.Direction.X - park_Direction.X) < 0.01 and (instance.Direction.Z - park_Direction.Z) < 0.01):
                                logProxy.logger.info(f"{instance.Direction}")
                                return_phase = ReturnPhase.BACK2
                                return_phase_start = now

                            #if elapsed >= 2.5:
                            #    return_phase = ReturnPhase.BACK2
                            #    return_phase_start = now

                        elif return_phase == ReturnPhase.BACK2:
                            # バック 3秒（一定の後退速度）
                            instance.Throttle = 0.0
                            instance.Brake = 0.0
                            instance.Clutch = 0.0
                            instance.Steering = 0.0
                            instance.SetSpeed(const._MeterPerSecond, -1)
                            if elapsed >= 4.5:
                                return_phase = ReturnPhase.DONE
                                return_phase_start = now

                        elif return_phase == ReturnPhase.DONE:
                            # シーケンス完了後は停止へ
                            state = State.STOP
                            reset_return_sequence()

                        print(f"{state}/{return_phase} Throttle:{instance.Throttle} Brake:{instance.Brake} Steering:{instance.Steering}")

                        
                    elif state== State.ACCEL:
                        instance.Throttle = 0.01
                        instance.Brake = 0.0
                        instance.Clutch = 0.0
                        instance.Steering = 0.0
                        print(f"{state} Throttle:{instance.Throttle} Brake:{instance.Brake} Steering:{instance.Steering}")                           
                    elif state== State.BACK:
                        instance.Throttle = 0.0
                        instance.Brake = 0.0
                        instance.Clutch = 0.0
                        instance.Steering = 0.0
                        instance.SetSpeed(const._MeterPerSecond, -0.5)
                        print(f"{state} Throttle:{instance.Throttle} Brake:{instance.Brake} Steering:{instance.Steering}")                                                   
                    elif state == State.STOP:
                        instance.Throttle = 0.0
                        instance.Brake = 1.0
                        instance.Clutch = 0.0
                        instance.Steering = 0.0
                        print(f"{state} Throttle:{instance.Throttle} Brake:{instance.Brake} Steering:{instance.Steering}")   
                    elif state == State.TURN_LEFT:
                        instance.Throttle = 0.01
                        instance.Brake = 0.0
                        instance.Clutch = 0.0
                        instance.Steering = -1.0
                        print(f"{state} Throttle:{instance.Throttle} Brake:{instance.Brake} Steering:{instance.Steering}")                           
                    elif state == State.TURN_RIGHT:
                        instance.Throttle = 0.01
                        instance.Brake = 0.0
                        instance.Clutch = 0.0
                        instance.Steering = 1.0
                        print(f"{state} Throttle:{instance.Throttle} Brake:{instance.Brake} Steering:{instance.Steering}")
                    elif state == State.RESET:
                        self.ResetCarPosition(instance)
                        instance.Throttle = 0.0
                        instance.Brake = 0.0
                        instance.Clutch = 0.0
                        instance.Steering = 0.0
                        print(f"{state} Throttle:{instance.Throttle} Brake:{instance.Brake} Steering:{instance.Steering}")   
                        state = State.STOP                     

                    Position = instance.Position
                    instance.PositionInTraffic = Position
        except Exception as e:
            logProxy.logger.error(f"[Error] {e}")

# IPアドレスとポート番号
def setIP():
    global ip_address, port_number  
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip_address = s.getsockname()[0]
    port_number = 5005
    s.close()
    print(f"IP:{ip_address} PORT:{port_number}")

# リボンの作成
def makeRibbon():
    global ribbonMenu, tab, group, label_ip, label_port, edit_ip, edit_port
    mainForm = winRoadProxy.MainForm
    ribbonMenu = mainForm.MainRibbonMenu

    tabname = "SmartMotionPlugin"
    tab = ribbonMenu.GetTabByName(tabname)
    if tab is None:
        tab = ribbonMenu.CreateTab(tabname, 10000)
        tab.Caption = tabname

    groupname = "OSC"
    group = tab.GetGroupByName(groupname)
    if group is None:
        group = tab.CreateGroup(groupname, 1000)
        group.Caption = groupname

    label_ip_name = "IP"
    label_ip = group.GetControlByName(label_ip_name)
    if label_ip is None:
        label_ip = group.CreateLabel(label_ip_name)
        label_ip.Caption = label_ip_name

    edit_ip_name = "ip_address"
    edit_ip = group.GetControlByName(edit_ip_name)
    if edit_ip is None:
        edit_ip = group.CreateEdit(edit_ip_name)
    edit_ip.Text = ip_address

    label_port_name = "PORT"
    label_port = group.GetControlByName(label_port_name)
    if label_port is None:
        label_port = group.CreateLabel(label_port_name)
        label_port.Caption = label_port_name
    edit_port_name = "port_number"
    edit_port = group.GetControlByName(edit_port_name)
    if edit_port is None:
        edit_port = group.CreateEdit(edit_port_name)
    edit_port.Text = str(port_number)

# リボンの削除
def killRibbon():    
    global ribbonMenu, tab, group, label_ip, label_port, edit_ip, edit_port
    group.DeleteControl(label_ip)
    group.DeleteControl(edit_ip)
    group.DeleteControl(label_port)
    group.DeleteControl(edit_port)    
    tab.DeleteGroup(group)
    ribbonMenu.DeleteTab(tab)

# 駐車車両の取得
def setParkingCar():
    global blueCar
    global whiteCar

    proj = winRoadProxy.Project
    count = proj.ThreeDModelInstancesCount
    for i in range(0, count):
        instance = proj.ThreeDModelInstance(i)
        if instance.Name == "BlueCar":
            blueCar = instance
            position = blueCar.Position
            print(f"BlueCar=({position.X} {position.Y} {position.Z})")
        elif instance.Name == "WhiteCar":
            whiteCar = instance
            position = whiteCar.Position
            print(f"WhiteCar=({position.X} {position.Y} {position.Z})")

# ユーザ位置の設定
def setUserPosition():
    global userPosition
    userPosition = AsF8COMdVec3(5038.60, 550.0, 5056.40)
    print(f"User=({userPosition.X} {userPosition.Y} {userPosition.Z})")   

# ターゲット車両の設定
def setTargetCar(traffic, distance):
    global targetCar
   
    # 車両の探索
    car_list = traffic.GetTransientVehiclesArround(distance, userPosition)    

    # 最初の車両を選択    
    if car_list.Count > 0:
        targetCar = car_list.Items(0)
        position = targetCar.Position
        print(f"TargetCar=({position.X} {position.Y} {position.Z})") 

def main():
    try:

        # APIのエントリポイント
        global winRoadProxy
        global const
        winRoadProxy = UCwinRoadComProxy()        
        const = winRoadProxy.const        

        # スクリプト名
        scriptName = "SmartMotionPlugin"
        
        # ロガーの設定
        global logProxy
        logfilepath = winRoadProxy.PythonPluginDirectory() + scriptName + '.log'
        logProxy = LoggerProxy(scriptName, logfilepath)
        logProxy.logger.info('Start '+ scriptName)

        # トラフィックの取得
        traffic = winRoadProxy.ApplicationServices.SimulationCore.TrafficSimulation

        # IPアドレスとポート番号の設定
        setIP()

        # リボンの作成
        makeRibbon()

        # ユーザ位置の設定
        setUserPosition()

        # 駐車車両の設定
        setParkingCar()

        # ターゲット車両の設定
        setTargetCar(traffic, 1000)

        if(targetCar):
            # イベントの登録
            global eventList
            eventList = []            
            SetCallbackHandlers(eventList, targetCar, TransientInstanceHandler)            

            # ループ開始
            logProxy.logger.info("Loop Start")
            loopFlg = True
            winRoadProxy.ApplicationServices.IsPythonScriptRun = loopFlg
        else:            
            # ループ停止            
            loopFlg = False

        try:
            # ループ制御
            asyncio.run(init(winRoadProxy))
        except Exception as e:
            logProxy.logger.error(f"[Error] {e}")

    finally:
        # リボンの削除
        killRibbon()

        # スクリプト終了
        logProxy.logger.info('End '+ scriptName)
        if eventList is not None:
            CloseCallbackEvent(eventList)
        logProxy.killLogger()        
        del winRoadProxy

if __name__ == '__main__':
    main()

