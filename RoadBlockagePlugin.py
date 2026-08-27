from UCwinRoadCOM import *
from UCwinRoadCOM import *
from LoggerProxy import LoggerProxy
from UCwinRoadUtils import *
from CallbackHandlers import *
import collections
import csv
import math
import os
import queue
import threading
import time
import tkinter as tk
import traceback
import win32com.client as com
import win32con
import win32gui
import win32process

# リボンに一覧として並べて表示する道路障害物の最大件数
# （リボングループの表示領域には限りがあるため、収まる範囲に絞って表示する）
MAX_DISPLAYED_OBSTRUCTIONS = 3

# 障害物として配置する3Dモデルの名前(プロジェクトに存在することを確認済み)。
# Barricadeは回転対称ではないため、道路の進行方向に対して垂直(車線をふさぐ向き)に
# なるようYawAngleを設定する(BARRICADE_YAW_OFFSET_RADIANS参照)
OBSTRUCTION_MODEL_NAME = 'Barricade'

# 道路の進行方向(road.GetDirectionAtから求めた角度)に対して、Barricadeをどれだけ
# 回転させて配置するかのオフセット(ラジアン)を足すことで、道路の
# 進行方向に対して垂直になり、車線をふさぐ向きになる
BARRICADE_YAW_OFFSET_RADIANS = 0.2

# CSVレコードの緯度経度に最も近い道路上の地点(中心)から、道路に沿って上流・下流
# それぞれこの距離(m)だけずらした2箇所にBarricadeを1本ずつ配置する
BARRICADE_OFFSET_DISTANCE_METERS = 5.0

# APIのエントリポイント
winRoadProxy = None
const = None

# ロガー
logProxy = None

# リボンUI
ribbon = None

# 現在配置中の障害物のリスト(車線数だけ配置される)。各要素は {'instance':..., 'model':...}
currentObstructions = []

# List Obstructionsで最後に取得した、道路データ既存の道路障害物(F8RoadObstructionProxy)の
# 一覧。Jump to Obstructionがインデックス指定で参照するため保持しておく。
# 各要素は ListObstructions() が返すdict(road/roadName/description/distance/length)
currentRoadObstructionItems = []

# AddNewTransientで生成した直後の同フレームでPositionを設定すると、
# ネイティブ側のオブジェクト初期化と競合してUC-win/Road自体がクラッシュすることが
# あるため、生成と位置設定を分離する。生成直後はここに設定待ちの情報を積んでおき、
# メインループが数ティック進んでから実際にPositionを設定する。
# (車線ごとに1個ずつ生成するため、複数件を同時に待たせられるようリストで持つ)
PLACEMENT_SETTLE_TICKS = 20  # ループは5ms間隔なので、20ティック=約100ms待つ
pendingObstructions = []

# CSVタブで読み込んだ予測結果。パスと、レコード(dict: latitude/longitude/side_m/probability)の
# リストを保持する。未読み込みならNone/空リスト
loadedCsvPath = None
loadedCsvRecords = []

# --- 交通流計測(Traffic Monitor) ---
# CSVタブで読み込み済みのレコードのうち、ユーザーが1件選んだ緯度経度に最も近い道路上の
# 位置を中心に、周辺の車両(TransientCar)を定期的に取得して「平均速度」「現在の車両数」を
# 計測し、HUD・グラフで表示する。バリケードの配置は前提とせず、CSVレコードの位置そのもの
# を直接監視できる(バリケードが無くても計測できるようにするため、Barricadeの配置有無から
# 独立させている)。UC-win/Roadのエディタ上で同じ場所に手動で道路障害物
# (F8RoadObstructionProxy)を追加/削除しながら変化を確認する使い方を想定している。
# (流量(単位時間あたりの新規進入台数)も一度実装したが、値の意味が分かりにくく
# あまり有用でないとユーザーからフィードバックがあったため削除した。再度必要になった
# 場合は、車両IDの集合を前回分と比較して新規進入を検出するロジックから再実装できる)

# HUD描画の間引き間隔(秒)。毎ティック(5ms)計測すると重いため、この間隔でのみ更新する
TRAFFIC_METRICS_UPDATE_INTERVAL_SECONDS = 0.5

# 計測中かどうか(Start/Stopボタンで切り替え)
trafficMonitoring = False

# 計測開始時にどのCSVレコードを選んだか(loadedCsvRecords内のインデックス)。表示・ログ用に
# 保持するだけで、計測そのものはmonitoredPosition(下記)という固定座標を使う。
# 未選択(計測していない)場合はNone
monitoredRecordIndex = None

# 計測の基準となる固定座標(F8COMdVec3)。Start Monitoring時点で選んだCSVレコードの
# 緯度経度から最も近い道路上の位置を求め、その座標をコピーして保持する。道路自体は
# 動かないため毎回緯度経度から引き直す必要はなく、またBarricadeの配置有無にも
# 依存しない。未選択(計測していない)場合はNone
monitoredPosition = None

# Avg Speed/Vehicles in Zoneの推移をグラフ表示するために保持する履歴。要素は
# {'time':, 'avgSpeedKmh':, 'vehicleCount':} のdict。最大件数を超えたら古いものから
# 自動的に捨てる(collections.dequeのmaxlen)
METRICS_HISTORY_MAX_POINTS = 120
metricsHistory = collections.deque(maxlen=METRICS_HISTORY_MAX_POINTS)

# HUD用の独立ウィンドウ(main()で生成、HudOverlayWindowのインスタンス)。
# UC-win/Roadのメイン3DビューへのOpenGL直接描画は2通り試して(OnOpenGLAfterDrawScene
# への直接描画、2D Overlay Virtual DisplayのOnDirectDraw)いずれも実用にならなかった
# ため(前者はGL_INVALID_OPERATIONが解消不能、後者はVirtual Display自体をUC-win/Road側
# でどう作成するか不明で前提条件を満たせなかった)、Pythonの標準GUIであるtkinterで
# 枠なし・最前面・背景透過のウィンドウを別途作り、UC-win/Roadのメインウィンドウの位置に
# 追従させることで「画面に重ねて見える」ようにする方式に切り替えた
hudOverlay = None

# UC-win/Roadのメインウィンドウの位置取得を間引く間隔(秒)。GetWindowRectは軽い呼び出し
# だが、毎ティック(5ms)呼ぶ必要はないため計測と同じ間隔で更新する
HUD_POSITION_UPDATE_INTERVAL_SECONDS = 0.5
lastHudPositionUpdateTime = 0.0

# 直近の計測結果。{'index':, 'position':, 'vehicleCount':, 'avgSpeedKmh':} のdict、
# または未計測ならNone。HUD/リボンの両方で参照する
currentTrafficMetric = None

# UpdateTrafficMetrics()の間引き用に前回更新時刻(time.time())を保持する
lastTrafficMetricsUpdateTime = 0.0


# UC-win/Roadのリボン(または Script Editor)の [Async] チェックがONのときだけ、
# スクリプトはメインスレッドとは別スレッドで実行される(PDFガイド 3.1.1.1参照)。
# これを使い、非同期になっていない(=メインスレッドで実行されている)ことを検出する。
# 非同期でないままイベントループ(while + time.sleep)に入るとメインスレッドを
# ブロックし続け、UC-win/Road自体が操作不能になってしまうため。
def IsRunningAsync():
    return threading.current_thread() is not threading.main_thread()


# Windowsのネイティブなファイルを開くダイアログを表示し、選択されたパスを返す
# (キャンセル、またはダイアログ表示に失敗した場合はNone)。リボンにはファイル選択用の
# 部品が無いため、ここだけpywin32のcommon dialogを直接使う
def BrowseForCsvFile(initialDir):
    try:
        filename, _customFilter, _flags = win32gui.GetOpenFileNameW(
            InitialDir=initialDir,
            Filter='CSV Files\0*.csv\0All Files\0*.*\0\0',
            Flags=win32con.OFN_EXPLORER | win32con.OFN_FILEMUSTEXIST | win32con.OFN_HIDEREADONLY,
            Title='Select Prediction CSV',
        )
        return filename
    except Exception:
        # ユーザがキャンセルした場合もここに来る(pywintypes.error)。エラー扱いにはしない
        return None


# CSVファイルを読み込み、latitude/longitude/side_m/probabilityを持つdictのリストにする。
# 値が数値に変換できない行は読み飛ばす
def LoadCsvRecords(path):
    records = []
    with open(path, 'r', newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                records.append({
                    'latitude': float(row['latitude']),
                    'longitude': float(row['longitude']),
                    'side_m': float(row.get('side_m', 0.0) or 0.0),
                    'probability': float(row['probability']),
                })
            except (KeyError, ValueError, TypeError):
                continue
    return records


class RibbonButtonHandlerBrowseCsv(RibbonButtonHandler):
    def OnClick(self):
        global loadedCsvPath, loadedCsvRecords
        try:
            initialDir = winRoadProxy.PythonPluginDirectory() + 'Prediction'
            if not os.path.isdir(initialDir):
                initialDir = winRoadProxy.PythonPluginDirectory()
            path = BrowseForCsvFile(initialDir)
            if not path:
                logProxy.logger.info('RibbonButtonHandlerBrowseCsv: cancelled, no file selected')
                return

            logProxy.logger.info(f'RibbonButtonHandlerBrowseCsv: loading {path}')
            records = LoadCsvRecords(path)
            loadedCsvPath = path
            loadedCsvRecords = records
            logProxy.logger.info(f'RibbonButtonHandlerBrowseCsv: loaded {len(records)} record(s)')

            fileName = os.path.basename(path)
            if not records:
                summary = f"Loaded {fileName}: 0 records."
            else:
                probabilities = [r['probability'] for r in records]
                summary = (
                    f"Loaded {fileName}: {len(records)} records, "
                    f"probability {min(probabilities):.4f}-{max(probabilities):.4f}")
            ribbon.label_csv_summary.Caption = summary
        except Exception:
            logProxy.logger.error(traceback.format_exc())


# 緯度経度をローカル座標(水平面 X, Z)に変換する
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


# 緯度経度に最も近い道路の中心線上の位置(F8COMdVec3)を返す。Traffic Monitorの
# 計測基準点算出専用のヘルパーで、FindNearestLanePositionと違い車線・向き・
# 前後オフセットは考慮しない(Barricadeの配置有無に関係なく、CSVレコードの緯度経度が
# 指す地点そのものを直接監視できるようにするための、より単純な位置決め)。
# 道路が見つからない場合はNone
def FindNearestRoadPositionForMonitoring(latitude, longitude):
    local = LatLonToLocalXZ(latitude, longitude)
    if local is None:
        return None
    x, z = local

    found = FindNearestRoadPoint(x, z)
    if found is None:
        return None
    road, distanceAlong = found
    return road.GetPositionAt(distanceAlong)


# 緯度経度1件分について、最も近い道路・車線を求め、その地点(中心)から道路に沿って
# 上流・下流にBARRICADE_OFFSET_DISTANCE_METERSずつずらした2箇所の位置(F8COMdVec3)と
# Barricadeの向き(YawAngle、ラジアン)を求める。見つからなければNone
# (RoadLane.GetPositionにconst._ldRoadを渡すことで、道路側と同じdistanceAlongを
# そのまま使って各車線上の位置が得られる。Sample_GPSroads.pyのRoadLane走査パターンを踏襲。
# 向きはSample_RoadInformation.pyのroad.GetDirectionAt()と同じ式で道路方向の角度を求め、
# BARRICADE_YAW_OFFSET_RADIANS分だけ回転させて車線をふさぐ向きにする)
def FindNearestLanePosition(latitude, longitude):
    """見つかった場合は (placements, distanceMeters) を返す。
    placementsは [(下流側position, yawAngle), (上流側position, yawAngle)] の2件。
    distanceMetersは入力座標から中心(道路上の最近傍点)までの水平距離で、CSVの点が
    実際に道路の近くにあるかの判定に使う。道路自体が見つからない場合はNone"""
    local = LatLonToLocalXZ(latitude, longitude)
    if local is None:
        return None
    x, z = local

    found = FindNearestRoadPoint(x, z)
    if found is None:
        return None
    road, distanceAlong = found

    laneCount = road.RoadLanesCount
    if laneCount <= 0:
        return None

    nearestLane = None
    nearestSqDist = None
    for laneIndex in range(laneCount):
        lane = road.RoadLane(laneIndex)
        if lane is None:
            continue
        position = lane.GetPosition(distanceAlong, const._ldRoad)
        sqDist = (position.X - x) ** 2 + (position.Z - z) ** 2
        if nearestSqDist is None or sqDist < nearestSqDist:
            nearestSqDist = sqDist
            nearestLane = lane

    if nearestLane is None:
        return None

    # 中心(distanceAlong)から道路に沿って下流・上流にBARRICADE_OFFSET_DISTANCE_METERS
    # ずらした2箇所の位置・向きを求める。道路の始点・終点をはみ出す場合は範囲内に丸める
    placements = []
    for offset in (BARRICADE_OFFSET_DISTANCE_METERS, -BARRICADE_OFFSET_DISTANCE_METERS):
        targetDistance = max(0.0, min(road.Length, distanceAlong + offset))
        position = nearestLane.GetPosition(targetDistance, const._ldRoad)
        roadDirection = road.GetDirectionAt(targetDistance)
        roadYaw = math.atan2(roadDirection.X, -roadDirection.Z)
        yawAngle = roadYaw + BARRICADE_YAW_OFFSET_RADIANS
        placements.append((position, yawAngle))

    return placements, math.sqrt(nearestSqDist)


# CSVタブで読み込み済みのレコードのうち、確率がしきい値以上のものについて、
# それぞれ最も近い道路・車線の位置を中心に、道路に沿って下流・上流に
# BARRICADE_OFFSET_DISTANCE_METERSずらした2箇所にBarricadeを1本ずつ(1レコードあたり
# 計2本)配置する。既存の配置は複数件を個別に差分更新すると複雑になるため、
# クリックのたびに全て作り直す。以前AddNewTransientでUC-win/Roadがクラッシュした際に
# ログに何も残らなかったため、各COM呼び出しの前後で必ずログを残し、
# 万一再発した際に原因箇所を特定できるようにする
def PlaceBarricadesFromCsv():
    logProxy.logger.info('PlaceBarricadesFromCsv: start')

    if not loadedCsvRecords:
        logProxy.logger.error('PlaceBarricadesFromCsv: no CSV loaded, aborting')
        ribbon.label_barricades_summary.Caption = "No CSV loaded. Use the CSV tab first."
        return

    try:
        threshold = float(ribbon.edit_probability_threshold.Text)
    except ValueError:
        logProxy.logger.error('PlaceBarricadesFromCsv: invalid probability threshold, aborting')
        ribbon.label_barricades_summary.Caption = "Invalid probability threshold."
        return

    try:
        maxRoadDistance = float(ribbon.edit_max_road_distance.Text)
    except ValueError:
        logProxy.logger.error('PlaceBarricadesFromCsv: invalid max road distance, aborting')
        ribbon.label_barricades_summary.Caption = "Invalid max distance to road."
        return
    logProxy.logger.info(f'PlaceBarricadesFromCsv: threshold={threshold} maxRoadDistance={maxRoadDistance}')

    targetRecords = [r for r in loadedCsvRecords if r['probability'] >= threshold]
    logProxy.logger.info(
        f'PlaceBarricadesFromCsv: {len(targetRecords)}/{len(loadedCsvRecords)} record(s) meet the threshold')

    model = FindThreeDModelByName(OBSTRUCTION_MODEL_NAME)
    if model is None:
        logProxy.logger.error(f"PlaceBarricadesFromCsv: model '{OBSTRUCTION_MODEL_NAME}' not found, aborting")
        ribbon.label_barricades_summary.Caption = f"Model '{OBSTRUCTION_MODEL_NAME}' not found."
        return

    # CSVからの配置は毎回作り直す(個々のBarricadeを差分更新する仕組みは持たない)
    ResetObstruction()

    if not targetRecords:
        ribbon.label_barricades_summary.Caption = f"0 record(s) >= threshold {threshold}."
        return

    # 道路が全く見つからない場合と、道路は見つかったが遠すぎて「道路上」とは
    # 言えない場合を分けて数える(サマリー表示・ログ調査のため)
    placements = []
    skippedNoRoad = 0
    skippedTooFar = 0
    for record in targetRecords:
        result = FindNearestLanePosition(record['latitude'], record['longitude'])
        if result is None:
            logProxy.logger.error(
                f"PlaceBarricadesFromCsv: no usable road/lane for "
                f"({record['latitude']}, {record['longitude']}), skipping")
            skippedNoRoad += 1
            continue
        recordPlacements, distance = result
        if distance > maxRoadDistance:
            logProxy.logger.info(
                f"PlaceBarricadesFromCsv: ({record['latitude']}, {record['longitude']}) is {distance:.1f}m "
                f"from the nearest road (> {maxRoadDistance}m), skipping")
            skippedTooFar += 1
            continue
        # 中心の前後2箇所(下流・上流)を両方とも配置対象に追加する
        placements.extend(recordPlacements)

    logProxy.logger.info(
        f'PlaceBarricadesFromCsv: resolved {len(placements)} placement(s) '
        f'(skipped {skippedNoRoad} no-road, {skippedTooFar} too-far)')

    global currentObstructions, pendingObstructions
    traffic = winRoadProxy.SimulationCore.TrafficSimulation
    newObstructions = []
    newPending = []
    for idx, (position, yawAngle) in enumerate(placements):
        logProxy.logger.info(f'PlaceBarricadesFromCsv: [{idx}] calling AddNewTransient')
        instance = traffic.AddNewTransient(model)
        if instance is None:
            logProxy.logger.error(f'PlaceBarricadesFromCsv: [{idx}] AddNewTransient returned None, skipping')
            continue
        newObstructions.append({'instance': instance, 'model': model})
        # 生成直後は位置・向きを設定せず、メインループ側で数ティック後に設定する
        # (同フレームでPosition/YawAngleを設定するとクラッシュにつながることがあるため)
        newPending.append({
            'instance': instance,
            'position': position,
            'yawAngle': yawAngle,
            'ticksRemaining': PLACEMENT_SETTLE_TICKS,
        })

    currentObstructions = newObstructions
    pendingObstructions = newPending
    logProxy.logger.info(
        f'PlaceBarricadesFromCsv: {len(newObstructions)} Barricade(s) created, '
        f'Position/YawAngle deferred to next ticks')
    ribbon.label_barricades_summary.Caption = (
        f"Placed {len(newObstructions)} Barricade(s) from {len(targetRecords)} record(s) "
        f"(threshold={threshold}, max road dist={maxRoadDistance}m; "
        f"skipped {skippedNoRoad} no-road, {skippedTooFar} too-far).")


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
        logProxy.logger.info('ApplyPendingObstructions: setting Position/YawAngle')
        instance.Position = pending['position']
        instance.YawAngle = pending['yawAngle']
        logProxy.logger.info('ApplyPendingObstructions: Position/YawAngle set successfully')
        appliedAny = True
    pendingObstructions = remaining

    if appliedAny:
        winRoadProxy.MainForm.MainOpenGL.Changed()
        logProxy.logger.info('ApplyPendingObstructions: done')


# UC-win/Roadのメインウィンドウに重ねて表示する、枠なし・最前面・背景透過のtkinter
# ウィンドウ。メイン3DビューへのOpenGL直接描画を2通り試して(OnOpenGLAfterDrawScene
# への直接描画、2D Overlay Virtual DisplayのOnDirectDraw)いずれも実用にならなかった
# ため(前者は原因不明のGL_INVALID_OPERATIONが解消不能、後者はVirtual Display自体を
# UC-win/Road側でどう作成するか分からず前提を満たせなかった)、Pythonの標準GUIである
# tkinterで独立ウィンドウを別途作り、UC-win/Roadのメインウィンドウの位置に追従させる
# ことで「画面に重ねて見える」ようにしている。
#
# tkinterはウィジェットを作成したスレッドで実際に mainloop() を回し続けている
# ことを前提にしており、それ以外のスレッドから configure() 等を呼ぶと
# 「RuntimeError: main thread is not in main loop」になる(実機で確認済み。
# UC-win/RoadのPythonプラグインは[Async]チェックON時、メインスレッドとは別の
# スレッドで動くため、そのままではこの制約に引っかかる)。そのためtkinter専用の
# スレッドを新たに立て、Tkの生成からmainloop()の呼び出しまで全てそのスレッド内で
# 完結させ、プラグイン側(メインループのスレッド)とはqueue.Queueだけでやり取りする
class HudOverlayWindow:
    def __init__(self):
        self._queue = queue.Queue()
        self._readyEvent = threading.Event()
        self._thread = threading.Thread(target=self._Run, daemon=True)
        self._thread.start()
        self._readyEvent.wait(timeout=5.0)

    def _Run(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)  # タイトルバー・枠を消す
        self.root.attributes('-topmost', True)  # 常に最前面
        self.root.configure(bg='#1a1a1a')
        self.label = tk.Label(
            self.root, text='', fg='#FFFF00', bg='#1a1a1a',
            font=('Consolas', 18, 'bold'), justify='left', anchor='w')
        self.label.pack(padx=14, pady=(10, 4), anchor='w')

        # Avg Speed/Vehicles in Zoneの推移を表示する2つの小さな折れ線グラフ。数値だけ
        # だと「今どちらに向かっているか」が分かりにくいため、CSVレコードを切り替える
        # 度に履歴はリセットされる前提でシンプルな折れ線を描く(軸目盛りは付けず、
        # 現在値と直近の最小/最大だけラベルで添える)。Flowの推移も一度表示したが、
        # 値の意味が分かりにくくあまり有用でないとのフィードバックがあり削除した
        self.speedGraphLabel = tk.Label(
            self.root, text='Avg Speed (km/h)', fg='#66FF99', bg='#1a1a1a',
            font=('Consolas', 10, 'bold'), justify='left', anchor='w')
        self.speedGraphLabel.pack(padx=14, pady=(6, 0), anchor='w')
        self.speedCanvas = tk.Canvas(
            self.root, width=320, height=70, bg='#000000', highlightthickness=0)
        self.speedCanvas.pack(padx=14, pady=(0, 4))

        self.vehicleCountGraphLabel = tk.Label(
            self.root, text='Vehicles in Zone', fg='#FF9966', bg='#1a1a1a',
            font=('Consolas', 10, 'bold'), justify='left', anchor='w')
        self.vehicleCountGraphLabel.pack(padx=14, pady=(2, 0), anchor='w')
        self.vehicleCountCanvas = tk.Canvas(
            self.root, width=320, height=70, bg='#000000', highlightthickness=0)
        self.vehicleCountCanvas.pack(padx=14, pady=(0, 10))

        self.root.geometry('+20+20')
        self._readyEvent.set()
        self._PollQueue()
        self.root.mainloop()

    # プラグイン側スレッドからqueueに積まれた指示を、tkinter自身のスレッド上で処理する。
    # ウィジェット操作は必ずこのスレッド(root.after経由)からのみ行う
    def _PollQueue(self):
        try:
            while True:
                action, payload = self._queue.get_nowait()
                if action == 'text':
                    self.label.config(text=payload)
                elif action == 'history':
                    speedValues, vehicleCountValues = payload
                    self._DrawLineGraph(self.speedCanvas, speedValues, '#66FF99', ' km/h')
                    self._DrawLineGraph(
                        self.vehicleCountCanvas, vehicleCountValues, '#FF9966', '', decimals=0)
                elif action == 'anchor':
                    # メインウィンドウの矩形(left, top, right, bottom)を右下基準に
                    # 配置する。ウィンドウ自身の現在のサイズ(フォント変更等で変わり
                    # うる)をwinfo_width/heightで都度取得してから計算する
                    left, top, right, bottom = payload
                    self.root.update_idletasks()
                    width = self.root.winfo_width()
                    height = self.root.winfo_height()
                    margin = 24
                    x = right - width - margin
                    y = bottom - height - margin
                    self.root.geometry(f'+{int(x)}+{int(y)}')
                elif action == 'destroy':
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(50, self._PollQueue)

    # values(数値のリスト、古い順)を単純な折れ線としてcanvasに描画する。
    # 目盛りは付けず、最小値・最大値・直近値だけをテキストで添えて相対的な変化が
    # 分かるようにする。matplotlib等の追加ライブラリはUC-win/Roadのプラグイン
    # ホストに入っている保証がないため、tkinter標準のCanvas描画だけで完結させている
    def _DrawLineGraph(self, canvas, values, color, unitSuffix, decimals=1):
        canvas.delete('all')
        width = int(canvas['width'])
        height = int(canvas['height'])
        marginTop = 14
        marginBottom = 4

        if len(values) < 2:
            return

        vMin = min(values)
        vMax = max(values)
        if vMax - vMin < 1e-6:
            vMax = vMin + 1.0

        n = len(values)
        plotHeight = height - marginTop - marginBottom
        points = []
        for i, v in enumerate(values):
            x = i / (n - 1) * (width - 8) + 4
            y = marginTop + plotHeight - (v - vMin) / (vMax - vMin) * plotHeight
            points.append((x, y))

        for i in range(len(points) - 1):
            canvas.create_line(
                points[i][0], points[i][1], points[i + 1][0], points[i + 1][1],
                fill=color, width=2)

        canvas.create_text(
            2, 2, text=f"{vMax:.0f}", fill=color, anchor='nw', font=('Consolas', 8))
        canvas.create_text(
            2, height - 2, text=f"{vMin:.0f}", fill=color, anchor='sw', font=('Consolas', 8))
        canvas.create_text(
            width - 2, 2, text=f"{values[-1]:.{decimals}f}{unitSuffix}", fill=color, anchor='ne',
            font=('Consolas', 9, 'bold'))

    def SetText(self, text):
        self._queue.put(('text', text))

    # speedValues/vehicleCountValuesは、それぞれAvg Speed(km/h)/Vehicles in Zoneの
    # 履歴(古い順の数値リスト)
    def SetHistory(self, speedValues, vehicleCountValues):
        self._queue.put(('history', (speedValues, vehicleCountValues)))

    # mainRectはメインウィンドウの(left, top, right, bottom)。この矩形の右下に
    # 追従して自身を配置する
    def SetAnchorRect(self, mainRect):
        self._queue.put(('anchor', mainRect))

    def Destroy(self):
        self._queue.put(('destroy', None))
        self._thread.join(timeout=2.0)


# このプロセス(UC-win/Road自身。プラグインはUC-win/Roadのプロセス内で動作するため
# os.getpid()が一致する)に属する可視ウィンドウのうち、最も面積が大きいものを
# メインウィンドウとみなして返す。見つからなければNone
def FindMainWindowHandle():
    targetPid = os.getpid()
    candidates = []

    def _enumHandler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid != targetPid:
            return True
        rect = win32gui.GetWindowRect(hwnd)
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if width > 0 and height > 0:
            candidates.append((hwnd, width * height))
        return True

    win32gui.EnumWindows(_enumHandler, None)
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[1], reverse=True)
    return candidates[0][0]


# 計測開始時に固定した座標(monitoredPosition)の周辺(半径radius以内)の車両を
# TrafficSimulation.GetTransientObjectsArroundで取得し、平均速度と現在の車両数を
# 計測する。メインループから毎ティック呼ばれるが、内部で
# TRAFFIC_METRICS_UPDATE_INTERVAL_SECONDSにより間引く(全車両走査は重いため)。
# Barricadeインスタンス自体は参照しないため、Reset等でBarricadeが削除されても
# 同じ場所での計測を継続できる
def UpdateTrafficMetrics():
    global lastTrafficMetricsUpdateTime, currentTrafficMetric
    global metricsHistory

    if not trafficMonitoring or monitoredPosition is None:
        return

    now = time.time()
    if now - lastTrafficMetricsUpdateTime < TRAFFIC_METRICS_UPDATE_INTERVAL_SECONDS:
        return
    lastTrafficMetricsUpdateTime = now

    try:
        radius = float(ribbon.edit_measurement_radius.Text)
    except ValueError:
        radius = 50.0

    traffic = winRoadProxy.SimulationCore.TrafficSimulation
    position = monitoredPosition
    trafficList = traffic.GetTransientObjectsArround(radius, position)
    count = trafficList.Count

    currentVehicleIds = set()
    speeds = []
    for i in range(count):
        obj = trafficList.Items(i)
        if obj is None:
            continue
        if obj.TransientType != const._TransientCar:
            continue
        currentVehicleIds.add(obj.ID)
        speeds.append(obj.Speed(const._KiloMeterPerHour))

    avgSpeedKmh = sum(speeds) / len(speeds) if speeds else 0.0
    vehicleCount = len(currentVehicleIds)

    currentTrafficMetric = {
        'index': monitoredRecordIndex,
        'position': position,
        'vehicleCount': vehicleCount,
        'avgSpeedKmh': avgSpeedKmh,
    }
    metricsHistory.append({'time': now, 'avgSpeedKmh': avgSpeedKmh, 'vehicleCount': vehicleCount})

    if ribbon is not None:
        ribbon.ShowTrafficMetrics(currentTrafficMetric)
    if hudOverlay is not None:
        hudOverlay.SetText(
            f"CSV Record[{monitoredRecordIndex}]\n"
            f"Avg Speed: {avgSpeedKmh:.1f} km/h\n"
            f"Vehicles: {vehicleCount}")
        hudOverlay.SetHistory(
            [m['avgSpeedKmh'] for m in metricsHistory],
            [m['vehicleCount'] for m in metricsHistory])


# HUDオーバーレイウィンドウの位置をUC-win/Roadのメインウィンドウに追従させる。
# tkinter自身のイベントループは専用スレッド側で回っているため、ここではqueue経由で
# 新しい位置を指示するだけでよい。メインループから毎ティック呼ばれるが、内部で
# HUD_POSITION_UPDATE_INTERVAL_SECONDSにより間引く(GetWindowRectは軽いが不要)
def PumpHudOverlay():
    global lastHudPositionUpdateTime
    if hudOverlay is None:
        return

    now = time.time()
    if now - lastHudPositionUpdateTime < HUD_POSITION_UPDATE_INTERVAL_SECONDS:
        return
    lastHudPositionUpdateTime = now

    mainHwnd = FindMainWindowHandle()
    if mainHwnd is not None:
        rect = win32gui.GetWindowRect(mainHwnd)
        hudOverlay.SetAnchorRect(rect)


# 選択中ゾーンの計測状態(直近の計測結果・グラフ履歴)を初期化する。
# 計測対象の切り替え(Start)のタイミングで呼ぶ
def ResetTrafficMetricsState():
    global lastTrafficMetricsUpdateTime, currentTrafficMetric
    lastTrafficMetricsUpdateTime = 0.0
    currentTrafficMetric = None
    metricsHistory.clear()


class RibbonButtonHandlerStartTrafficMonitoring(RibbonButtonHandler):
    def OnClick(self):
        global trafficMonitoring, monitoredRecordIndex, monitoredPosition, hudOverlay
        try:
            if trafficMonitoring:
                return
            if not loadedCsvRecords:
                ribbon.label_traffic_monitor_summary.Caption = "No CSV loaded. Use the CSV tab first."
                return
            try:
                index = int(ribbon.edit_record_index.Text)
            except ValueError:
                ribbon.label_traffic_monitor_summary.Caption = "Invalid CSV record index."
                return
            if index < 0 or index >= len(loadedCsvRecords):
                ribbon.label_traffic_monitor_summary.Caption = (
                    f"Index {index} out of range (0-{len(loadedCsvRecords) - 1}).")
                return

            record = loadedCsvRecords[index]
            position = FindNearestRoadPositionForMonitoring(record['latitude'], record['longitude'])
            if position is None:
                ribbon.label_traffic_monitor_summary.Caption = (
                    f"No road found near CSV record [{index}] "
                    f"({record['latitude']}, {record['longitude']}).")
                return

            monitoredRecordIndex = index
            # 選んだCSVレコードの緯度経度から求めた道路上の位置を固定座標としてコピーする。
            # Barricadeの配置有無とは独立させているため、バリケードを一切配置していなくても
            # (あるいはResetで削除しても)同じ場所での計測を継続できる(ユーザー要望)
            monitoredPosition = position
            ResetTrafficMetricsState()

            if hudOverlay is None:
                hudOverlay = HudOverlayWindow()

            trafficMonitoring = True
            logProxy.logger.info(
                f'RibbonButtonHandlerStartTrafficMonitoring: monitoring started for CSV record {index} '
                f'at fixed position ({monitoredPosition.X:.1f}, {monitoredPosition.Y:.1f}, '
                f'{monitoredPosition.Z:.1f})')
            ribbon.label_traffic_monitor_summary.Caption = f"Monitoring CSV record [{index}]..."
        except Exception:
            logProxy.logger.error(traceback.format_exc())


class RibbonButtonHandlerStopTrafficMonitoring(RibbonButtonHandler):
    def OnClick(self):
        global trafficMonitoring, monitoredRecordIndex, monitoredPosition, currentTrafficMetric, hudOverlay
        try:
            trafficMonitoring = False
            monitoredRecordIndex = None
            monitoredPosition = None
            currentTrafficMetric = None
            if hudOverlay is not None:
                hudOverlay.Destroy()
                hudOverlay = None
            if ribbon is not None:
                ribbon.label_traffic_monitor_summary.Caption = "Monitoring stopped."
                ribbon.ShowTrafficMetrics(None)
            logProxy.logger.info('RibbonButtonHandlerStopTrafficMonitoring: monitoring stopped')
        except Exception:
            logProxy.logger.error(traceback.format_exc())


# 設置中のBarricadeをすべてシミュレーションから削除する。交通流計測(Traffic Monitor)
# はBarricadeインスタンスではなく固定座標(monitoredPosition)を基準に動作するため、
# ここでBarricadeを消しても計測は意図的に止めない(同じ場所での計測を継続したいという
# ユーザー要望による)
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
# 過去の実行がクラッシュ等で正常終了しなかった場合、その回で配置したBarricadeが
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


# 配置済みのBarricadeのうちindex番目(0始まり、currentObstructionsの順)の位置へ
# メインカメラを移動させる。見つからなければNone、見つかればそのPositionを返す。
# 使い方はSamplePlugin/Sample_MainCameraOperation.pyのカメラ設定パターンを踏襲
def JumpToBarricade(index):
    if index < 0 or index >= len(currentObstructions):
        return None
    instance = currentObstructions[index]['instance']
    position = instance.Position
    yawAngle = instance.YawAngle

    # Barricadeの向き(YawAngle = roadYaw + BARRICADE_YAW_OFFSET_RADIANS)から
    # 道路の進行方向を逆算し、その進行方向の手前・上空から見下ろす形にする
    roadYaw = yawAngle - BARRICADE_YAW_OFFSET_RADIANS
    dirX = math.sin(roadYaw)
    dirZ = -math.cos(roadYaw)

    camState = com.Dispatch('UCwinRoad.F8COMMainCameraStateType')
    camState.allowUnderTerrain = True
    camState.cameraMode = const._useTiltAng
    camState.eye = AsF8COMdVec3(position.X - dirX * 20.0, position.Y + 15.0, position.Z - dirZ * 20.0)
    camState.ViewPoint = AsF8COMdVec3(position.X, position.Y, position.Z)
    camState.tiltAngle = 0
    winRoadProxy.MainForm.MainCamera.MainCameraState = camState
    return position


class RibbonButtonHandlerJumpToBarricade(RibbonButtonHandler):
    def OnClick(self):
        try:
            if not currentObstructions:
                ribbon.label_jump_summary.Caption = "Nothing placed."
                return
            try:
                index = int(ribbon.edit_jump_index.Text)
            except ValueError:
                ribbon.label_jump_summary.Caption = "Invalid Barricade index."
                return
            position = JumpToBarricade(index)
            if position is None:
                ribbon.label_jump_summary.Caption = (
                    f"Index {index} out of range (0-{len(currentObstructions) - 1}).")
                return
            logProxy.logger.info(
                f'RibbonButtonHandlerJumpToBarricade: jumped to index {index} '
                f'Pos=({position.X}, {position.Y}, {position.Z})')
            ribbon.label_jump_summary.Caption = (
                f"Jumped to Barricade[{index}] at ({position.X:.1f}, {position.Y:.1f}, {position.Z:.1f}).")
        except Exception:
            logProxy.logger.error(traceback.format_exc())


class RibbonButtonHandlerPlaceBarricadesFromCsv(RibbonButtonHandler):
    def OnClick(self):
        try:
            PlaceBarricadesFromCsv()
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
# 戻り値は dict(road, roadName, description, distance, length) のリスト。
# roadとdistanceは、後でJumpToRoadObstruction()が位置(road.GetPositionAt(distance))を
# 求めるために保持しておく(F8RoadObstructionProxy自体はPositionを持たない読み取り専用データ)
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
            items.append({
                'road': road,
                'roadName': road.Name,
                'description': obstruction.Description,
                'distance': obstruction.Distance,
                'length': obstruction.Length,
            })

    logProxy.logger.info(f"Found {len(items)} obstruction(s).")
    for item in items:
        logProxy.logger.info(
            f"  {item['roadName']} @ {item['distance']:.1f}m: {item['description']} "
            f"(Length={item['length']:.1f}m)")

    return items


class RibbonButtonHandlerListObstructions(RibbonButtonHandler):
    def OnClick(self):
        try:
            global currentRoadObstructionItems
            currentRoadObstructionItems = ListObstructions()
            ribbon.ShowObstructionList(currentRoadObstructionItems)
        except Exception:
            logProxy.logger.error(traceback.format_exc())


# List Obstructionsで取得した道路障害物のうちindex番目(0始まり)の位置へ
# メインカメラを移動させる。F8RoadObstructionProxyはPositionを持たないため、
# road.GetPositionAt(distance)で道路中心線上の位置を求めて代用する。
# 見つからなければNone、見つかればそのPositionを返す
def JumpToRoadObstruction(index):
    if index < 0 or index >= len(currentRoadObstructionItems):
        return None
    item = currentRoadObstructionItems[index]
    position = item['road'].GetPositionAt(item['distance'])

    camState = com.Dispatch('UCwinRoad.F8COMMainCameraStateType')
    camState.allowUnderTerrain = True
    camState.cameraMode = const._useTiltAng
    # 道路障害物には向きの情報が無いため、Pylon時代と同様、固定オフセットで見下ろす
    camState.eye = AsF8COMdVec3(position.X, position.Y + 15.0, position.Z - 20.0)
    camState.ViewPoint = AsF8COMdVec3(position.X, position.Y, position.Z)
    camState.tiltAngle = 0
    winRoadProxy.MainForm.MainCamera.MainCameraState = camState
    return position


class RibbonButtonHandlerJumpToRoadObstruction(RibbonButtonHandler):
    def OnClick(self):
        try:
            if not currentRoadObstructionItems:
                ribbon.label_obstruction_jump_summary.Caption = "Nothing listed. Use List Obstructions first."
                return
            try:
                index = int(ribbon.edit_obstruction_jump_index.Text)
            except ValueError:
                ribbon.label_obstruction_jump_summary.Caption = "Invalid obstruction index."
                return
            position = JumpToRoadObstruction(index)
            if position is None:
                ribbon.label_obstruction_jump_summary.Caption = (
                    f"Index {index} out of range (0-{len(currentRoadObstructionItems) - 1}).")
                return
            logProxy.logger.info(
                f'RibbonButtonHandlerJumpToRoadObstruction: jumped to index {index} '
                f'Pos=({position.X}, {position.Y}, {position.Z})')
            ribbon.label_obstruction_jump_summary.Caption = (
                f"Jumped to Obstruction[{index}] at "
                f"({position.X:.1f}, {position.Y:.1f}, {position.Z:.1f}).")
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

        # CSV タブ: Predictionフォルダ内のCSVファイルの選択・読み込み
        # (1タブに機能を詰め込むとリボンの横幅が足りず正しく表示されなかったため、
        #  機能ごとにタブを分けている。各タブにはグループを1つだけ置く)
        self.tabCsv = None
        self.groupCsv = None
        self.button_browse_csv = None
        self.label_csv_summary = None

        # Barricades タブ: CSVレコードに基づく障害物の設置(検索・ジャンプ機能は
        # Find Objects タブに集約したため、ここには配置系のコントロールのみを置く)
        self.tabBarricades = None
        self.groupBarricades = None
        self.label_probability_threshold = None
        self.edit_probability_threshold = None
        self.label_max_road_distance = None
        self.edit_max_road_distance = None
        self.button_place_barricades = None
        self.label_barricades_summary = None

        # Traffic Monitor タブ: 選択した1件のCSVレコードの位置周辺の交通流
        # (流量・平均速度)の計測とHUD表示
        self.tabTrafficMonitor = None
        self.groupTrafficMonitor = None
        self.label_record_index = None
        self.edit_record_index = None
        self.label_measurement_radius = None
        self.edit_measurement_radius = None
        self.button_start_traffic_monitoring = None
        self.button_stop_traffic_monitoring = None
        self.label_traffic_monitor_summary = None
        self.edit_traffic_monitor_detail = None

        # Find Objects タブ: 配置したBarricade、および道路に既存の道路障害物、
        # それぞれをインデックス指定で検索(一覧・カメラジャンプ)する機能をここに集約する
        self.tabFindObjects = None
        # -- 配置したBarricadeを探すグループ --
        self.groupFindBarricades = None
        self.label_jump_index = None
        self.edit_jump_index = None
        self.button_jump_to_barricade = None
        self.label_jump_summary = None
        # -- 道路に既存の道路障害物(F8RoadObstructionProxy)を探すグループ --
        self.groupObstructions = None
        self.button_list_obstructions = None
        self.panel_obstructions = None
        self.label_obstruction_summary = None
        self.obstructionItemLabels = []
        self.label_obstruction_jump_index = None
        self.edit_obstruction_jump_index = None
        self.button_jump_to_obstruction = None
        self.label_obstruction_jump_summary = None

        # Reset タブ: 配置済みBarricadeの解除・強制リセット
        self.tabReset = None
        self.groupReset = None
        self.button_reset_obstruction = None
        self.button_clear_all = None

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
                item = shown[idx]
                label.Caption = (
                    f"{item['roadName']} @ {item['distance']:.1f}m: "
                    f"{item['description']} (L={item['length']:.1f}m)")
                label.Visible = True
            else:
                label.Caption = ''
                label.Visible = False

    # 選択中1件の計測結果をリボンに表示する(HUDが見えない/未確認の場合の
    # フォールバックも兼ねる)。metricsがNoneなら詳細行を空にする
    # (label_traffic_monitor_summaryの方は呼び出し元がその時点の状況、例:
    # "Monitoring stopped."を先にCaption設定済みなので、ここでは上書きしない)
    def ShowTrafficMetrics(self, metrics):
        if metrics is None:
            self.edit_traffic_monitor_detail.Text = ''
            return
        self.edit_traffic_monitor_detail.Text = (
            f"CSV Record Index: {metrics['index']}\r\n"
            f"Avg Speed: {metrics['avgSpeedKmh']:.1f} km/h\r\n"
            f"Vehicles in Zone: {metrics['vehicleCount']}")

    def MakeRibbonUI(self):
        mainForm = winRoadProxy.MainForm
        self.ribbonMenu = mainForm.MainRibbonMenu

        # === CSV タブ: Predictionフォルダ内のCSVファイルの選択・読み込み ===
        self.tabCsv = self.MakeRibbonTab(self.ribbonMenu, 'RoadBlockagePluginCsv', 'CSV')
        self.groupCsv = self.MakeRibbonGroup(self.tabCsv, 'GroupCsv', 'CSV')

        self.button_browse_csv = self.MakeRibbonButton(
            self.groupCsv, 'ButtonBrowseCsv', 'Browse & Load CSV...', RibbonButtonHandlerBrowseCsv)
        self.button_browse_csv.Width = 160

        self.label_csv_summary = self.MakeRibbonLabel(self.groupCsv, 'LabelCsvSummary', 'No CSV loaded.')
        self.label_csv_summary.Width = 420

        # === Barricades タブ: CSVレコードに基づく障害物の設置(検索・ジャンプ機能は
        # Find Objects タブに集約している) ===
        self.tabBarricades = self.MakeRibbonTab(
            self.ribbonMenu, 'RoadBlockagePluginBarricades', 'Barricades')
        self.groupBarricades = self.MakeRibbonGroup(self.tabBarricades, 'GroupBarricades', 'Barricades')

        # 確率(Probability)がこの値以上のCSVレコードだけを配置対象にする
        self.label_probability_threshold = self.MakeRibbonLabel(
            self.groupBarricades, 'LabelProbabilityThreshold', 'Probability Threshold')
        self.edit_probability_threshold = self.MakeRibbonEdit(
            self.groupBarricades, 'EditProbabilityThreshold', '0.7')

        # 最も近い道路までの距離がこの値(m)を超える場合は「道路上に無い」とみなして
        # 配置しない(CSVはグリッド状の予測点のため、道路から大きく離れた点も含まれるため)
        self.label_max_road_distance = self.MakeRibbonLabel(
            self.groupBarricades, 'LabelMaxRoadDistance', 'Max Distance to Road (m)')
        self.edit_max_road_distance = self.MakeRibbonEdit(
            self.groupBarricades, 'EditMaxRoadDistance', '5000')

        self.button_place_barricades = self.MakeRibbonButton(
            self.groupBarricades, 'ButtonPlaceBarricades', 'Place Barricades from CSV',
            RibbonButtonHandlerPlaceBarricadesFromCsv)
        self.button_place_barricades.Width = 160

        self.label_barricades_summary = self.MakeRibbonLabel(self.groupBarricades, 'LabelBarricadesSummary', '')
        self.label_barricades_summary.Width = 420

        # === Traffic Monitor タブ: 選択した1件のCSVレコード位置の交通流計測・HUD表示 ===
        self.tabTrafficMonitor = self.MakeRibbonTab(
            self.ribbonMenu, 'RoadBlockagePluginTrafficMonitor', 'Traffic Monitor')
        self.groupTrafficMonitor = self.MakeRibbonGroup(
            self.tabTrafficMonitor, 'GroupTrafficMonitor', 'Traffic Monitor')

        # 計測対象とするCSVレコードをインデックスで1件選ぶ(loadedCsvRecordsの順)。
        # Barricadeの配置とは独立しているため、配置していなくても計測できる
        self.label_record_index = self.MakeRibbonLabel(
            self.groupTrafficMonitor, 'LabelRecordIndex', 'CSV Record Index')
        self.edit_record_index = self.MakeRibbonEdit(self.groupTrafficMonitor, 'EditRecordIndex', '0')

        # 選んだ位置からこの半径(m)以内にいる車両を計測対象にする
        self.label_measurement_radius = self.MakeRibbonLabel(
            self.groupTrafficMonitor, 'LabelMeasurementRadius', 'Measurement Radius (m)')
        self.edit_measurement_radius = self.MakeRibbonEdit(
            self.groupTrafficMonitor, 'EditMeasurementRadius', '50')

        self.button_start_traffic_monitoring = self.MakeRibbonButton(
            self.groupTrafficMonitor, 'ButtonStartTrafficMonitoring', 'Start Monitoring',
            RibbonButtonHandlerStartTrafficMonitoring)
        self.button_start_traffic_monitoring.Width = 160

        self.button_stop_traffic_monitoring = self.MakeRibbonButton(
            self.groupTrafficMonitor, 'ButtonStopTrafficMonitoring', 'Stop Monitoring',
            RibbonButtonHandlerStopTrafficMonitoring)
        self.button_stop_traffic_monitoring.Width = 160

        self.label_traffic_monitor_summary = self.MakeRibbonLabel(
            self.groupTrafficMonitor, 'LabelTrafficMonitorSummary', 'Not monitoring.')
        self.label_traffic_monitor_summary.Width = 480

        # 計測結果の詳細(画面上のHUDと同じ内容をリボン側にもテキストエリア風に表示する)。
        # リボンにはコンボボックス/リストボックスや複数行入力欄の専用コントロールが
        # 無いため(IF8MainRibbonGroupProxyで確認できるのはCreateButton/CheckBox/
        # Edit/Label/Panelのみ)、Editの高さを広げてText内で改行して代用している
        self.edit_traffic_monitor_detail = self.MakeRibbonEdit(
            self.groupTrafficMonitor, 'EditTrafficMonitorDetail', '')
        self.edit_traffic_monitor_detail.Width = 260
        self.edit_traffic_monitor_detail.Height = 80

        # === Find Objects タブ: 配置したBarricadeと、道路に既存の道路障害物を、
        # それぞれインデックス指定で検索(一覧・カメラジャンプ)する機能をここに集約する ===
        self.tabFindObjects = self.MakeRibbonTab(
            self.ribbonMenu, 'RoadBlockagePluginObstructions', 'Find Objects')

        # -- 配置したBarricadeを探すグループ --
        self.groupFindBarricades = self.MakeRibbonGroup(
            self.tabFindObjects, 'GroupFindBarricades', 'Barricades')

        # 配置したBarricadeの場所がわかりにくいため、指定したインデックスのBarricadeへ
        # メインカメラをジャンプさせて見た目を確認できるようにする
        self.label_jump_index = self.MakeRibbonLabel(
            self.groupFindBarricades, 'LabelJumpIndex', 'Barricade Index')
        self.edit_jump_index = self.MakeRibbonEdit(self.groupFindBarricades, 'EditJumpIndex', '0')

        self.button_jump_to_barricade = self.MakeRibbonButton(
            self.groupFindBarricades, 'ButtonJumpToBarricade', 'Jump to Barricade',
            RibbonButtonHandlerJumpToBarricade)
        self.button_jump_to_barricade.Width = 160

        self.label_jump_summary = self.MakeRibbonLabel(self.groupFindBarricades, 'LabelJumpSummary', '')
        self.label_jump_summary.Width = 420

        # -- 道路に既存の道路障害物(F8RoadObstructionProxy)を探すグループ --
        self.groupObstructions = self.MakeRibbonGroup(
            self.tabFindObjects, 'GroupObstructions', 'Road Obstructions')

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

        # 一覧の中からindexで指定した1件へメインカメラをジャンプさせて確認できるようにする
        # (F8RoadObstructionProxyはPosition自体は持たないため、road.GetPositionAtで代用する)
        self.label_obstruction_jump_index = self.MakeRibbonLabel(
            self.groupObstructions, 'LabelObstructionJumpIndex', 'Obstruction Index')
        self.edit_obstruction_jump_index = self.MakeRibbonEdit(
            self.groupObstructions, 'EditObstructionJumpIndex', '0')

        self.button_jump_to_obstruction = self.MakeRibbonButton(
            self.groupObstructions, 'ButtonJumpToObstruction', 'Jump to Obstruction',
            RibbonButtonHandlerJumpToRoadObstruction)
        self.button_jump_to_obstruction.Width = 160

        self.label_obstruction_jump_summary = self.MakeRibbonLabel(
            self.groupObstructions, 'LabelObstructionJumpSummary', '')
        self.label_obstruction_jump_summary.Width = 420

        # === Reset タブ: 配置済みBarricadeの解除・強制リセット ===
        self.tabReset = self.MakeRibbonTab(
            self.ribbonMenu, 'RoadBlockagePluginReset', 'Reset')
        self.groupReset = self.MakeRibbonGroup(self.tabReset, 'GroupReset', 'Reset')

        self.button_reset_obstruction = self.MakeRibbonButton(
            self.groupReset, 'ButtonResetObstruction', 'Reset', RibbonButtonHandlerResetObstruction)
        self.button_reset_obstruction.Width = 160

        # 過去の実行がクラッシュ等で正常終了せず、道路上に残ってしまった一時オブジェクトを
        # 一掃するための強制リセット。通常の走行中の交通車両にも影響しうる点に注意
        self.button_clear_all = self.MakeRibbonButton(
            self.groupReset, 'ButtonClearAll', 'Clear All', RibbonButtonHandlerClearAll)
        self.button_clear_all.Width = 160

    def KillRibbonUI(self):
        # MakeRibbonUIが途中で失敗していても後始末できるよう、
        # 各ステップはNoneチェックしてから実行する(MakeRibbonUIと逆順に解体する)
        self.CloseCallbackEvent()

        # --- Reset タブ ---
        if self.groupReset is not None:
            for button in (self.button_clear_all, self.button_reset_obstruction):
                if button is not None:
                    button.UnRegisterEventHandlers()
                    self.groupReset.DeleteControl(button)
            self.button_clear_all = None
            self.button_reset_obstruction = None
            if self.tabReset is not None:
                self.tabReset.DeleteGroup(self.groupReset)
            self.groupReset = None

        if self.tabReset is not None:
            if self.tabReset.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.tabReset)
            self.tabReset = None

        # --- Find Objects タブ ---
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
            if self.button_jump_to_obstruction is not None:
                self.button_jump_to_obstruction.UnRegisterEventHandlers()
                self.groupObstructions.DeleteControl(self.button_jump_to_obstruction)
                self.button_jump_to_obstruction = None
            for ctrl in (self.edit_obstruction_jump_index, self.label_obstruction_jump_index,
                         self.label_obstruction_jump_summary):
                if ctrl is not None:
                    self.groupObstructions.DeleteControl(ctrl)
            self.edit_obstruction_jump_index = None
            self.label_obstruction_jump_index = None
            self.label_obstruction_jump_summary = None
            if self.tabFindObjects is not None:
                self.tabFindObjects.DeleteGroup(self.groupObstructions)
            self.groupObstructions = None

        if self.groupFindBarricades is not None:
            if self.button_jump_to_barricade is not None:
                self.button_jump_to_barricade.UnRegisterEventHandlers()
                self.groupFindBarricades.DeleteControl(self.button_jump_to_barricade)
                self.button_jump_to_barricade = None
            for ctrl in (self.edit_jump_index, self.label_jump_index, self.label_jump_summary):
                if ctrl is not None:
                    self.groupFindBarricades.DeleteControl(ctrl)
            self.edit_jump_index = None
            self.label_jump_index = None
            self.label_jump_summary = None
            if self.tabFindObjects is not None:
                self.tabFindObjects.DeleteGroup(self.groupFindBarricades)
            self.groupFindBarricades = None

        if self.tabFindObjects is not None:
            if self.tabFindObjects.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.tabFindObjects)
            self.tabFindObjects = None

        # --- Traffic Monitor タブ ---
        if self.groupTrafficMonitor is not None:
            for button in (self.button_stop_traffic_monitoring, self.button_start_traffic_monitoring):
                if button is not None:
                    button.UnRegisterEventHandlers()
                    self.groupTrafficMonitor.DeleteControl(button)
            self.button_stop_traffic_monitoring = None
            self.button_start_traffic_monitoring = None
            for ctrl in (self.edit_traffic_monitor_detail, self.label_traffic_monitor_summary,
                         self.edit_measurement_radius, self.label_measurement_radius,
                         self.edit_record_index, self.label_record_index):
                if ctrl is not None:
                    self.groupTrafficMonitor.DeleteControl(ctrl)
            self.edit_traffic_monitor_detail = None
            self.label_traffic_monitor_summary = None
            self.edit_measurement_radius = None
            self.label_measurement_radius = None
            self.edit_record_index = None
            self.label_record_index = None
            if self.tabTrafficMonitor is not None:
                self.tabTrafficMonitor.DeleteGroup(self.groupTrafficMonitor)
            self.groupTrafficMonitor = None

        if self.tabTrafficMonitor is not None:
            if self.tabTrafficMonitor.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.tabTrafficMonitor)
            self.tabTrafficMonitor = None

        # --- Barricades タブ ---
        if self.groupBarricades is not None:
            if self.button_place_barricades is not None:
                self.button_place_barricades.UnRegisterEventHandlers()
                self.groupBarricades.DeleteControl(self.button_place_barricades)
            self.button_place_barricades = None
            for ctrl in (self.label_barricades_summary,
                         self.edit_max_road_distance, self.label_max_road_distance,
                         self.edit_probability_threshold, self.label_probability_threshold):
                if ctrl is not None:
                    self.groupBarricades.DeleteControl(ctrl)
            self.label_barricades_summary = None
            self.edit_max_road_distance = None
            self.label_max_road_distance = None
            self.edit_probability_threshold = None
            self.label_probability_threshold = None
            if self.tabBarricades is not None:
                self.tabBarricades.DeleteGroup(self.groupBarricades)
            self.groupBarricades = None

        if self.tabBarricades is not None:
            if self.tabBarricades.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.tabBarricades)
            self.tabBarricades = None

        # --- CSV タブ ---
        if self.groupCsv is not None:
            if self.button_browse_csv is not None:
                self.button_browse_csv.UnRegisterEventHandlers()
                self.groupCsv.DeleteControl(self.button_browse_csv)
                self.button_browse_csv = None
            if self.label_csv_summary is not None:
                self.groupCsv.DeleteControl(self.label_csv_summary)
                self.label_csv_summary = None
            if self.tabCsv is not None:
                self.tabCsv.DeleteGroup(self.groupCsv)
            self.groupCsv = None

        if self.tabCsv is not None:
            if self.tabCsv.RibbonGroupsCount == 0 and self.ribbonMenu is not None:
                self.ribbonMenu.DeleteTab(self.tabCsv)
            self.tabCsv = None

        self.ribbonMenu = None


def main():
    scriptName = 'RoadBlockagePlugin'
    start = time.perf_counter_ns()

    global winRoadProxy
    global const
    global logProxy
    global ribbon
    global currentObstructions
    global currentRoadObstructionItems
    global pendingObstructions
    global loadedCsvPath
    global loadedCsvRecords
    global hudOverlay
    global trafficMonitoring
    global monitoredRecordIndex
    global monitoredPosition
    winRoadProxy = None
    logProxy = None
    ribbon = None
    currentObstructions = []
    currentRoadObstructionItems = []
    pendingObstructions = []
    loadedCsvPath = None
    loadedCsvRecords = []
    hudOverlay = None
    trafficMonitoring = False
    monitoredRecordIndex = None
    monitoredPosition = None
    ResetTrafficMetricsState()

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
            UpdateTrafficMetrics()
            PumpHudOverlay()
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

        # 交通流計測(HUD)の停止。スクリプト終了後もオーバーレイウィンドウが
        # 残ってしまうと、次回実行時にウィンドウが二重に出てしまう
        try:
            trafficMonitoring = False
            if hudOverlay is not None:
                hudOverlay.Destroy()
                hudOverlay = None
        except Exception:
            if logProxy is not None:
                logProxy.logger.error(traceback.format_exc())

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
