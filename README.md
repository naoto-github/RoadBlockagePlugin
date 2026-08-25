# RoadBlockagePlugin

UC-win/Roadで道路閉塞（パイロンによる車線規制）を生成するPythonプラグイン。緯度経度を指定すると、
最も近い道路・車線を自動で見つけてPylonモデルを1本配置する。あわせて、その位置に非表示の停止車両を
配置し、後続の交通車両がその手前で停止・渋滞するようにする。

## 使い方

1. **このプラグインを実行する前に、UC-win/Road側でシミュレーションを手動で再生(Play)した状態に
   しておく。**（実行後にPlayしても正しく動作しない。後述の「既知の制約」参照）
2. [Script Editor]等からこのプラグインを読み込み、[Async]チェックをONにして実行する。
3. リボンに3つのタブ「Road Blockage: Position」「Road Blockage: Placement」
   「Road Blockage: Obstructions」が追加される。

### Road Blockage: Position タブ
- Latitude / Longitude 入力欄。
- **Get Camera Position** ボタン：現在のメインカメラの位置を緯度経度に変換して入力欄に反映する。

### Road Blockage: Placement タブ
- **Place Obstruction** ボタン：入力した緯度経度に最も近い道路を探し、その地点にある車線のうち
  入力座標に最も近い車線1本の中央にPylonを配置する（他の車線には配置しない）。既に配置済みで
  車線・モデルが変わらない場合は、作り直さず位置だけ更新する。同時に、同じ車線・同じ位置へ実際の
  車両を停止状態で(非表示で)配置し、後続車両がその手前で停止・渋滞するようにする。
- **Reset** ボタン：配置したPylonと停止車両をシミュレーションから削除する。
- **Clear All** ボタン：プロジェクト内の全ての一時オブジェクト(`DeleteAllTransientObjects`)を無条件に
  削除する。過去の実行がクラッシュ等で正常終了せず、道路上に残ってしまったPylon・停止車両を一掃する
  ための強制リセット用。通常の走行中の交通車両にも影響しうる点に注意。
- **List Placed** ボタン：現在配置されているPylonの情報（ID・名前・座標）をUC-win/Road側から
  読み直して表示する（見た目に反映されているかの確認用）。

### Road Blockage: Obstructions タブ
- **List Obstructions** ボタン：道路データに元から登録されている道路障害物
  （`F8RoadObstructionProxy`、道路作成時に設定される読み取り専用のデータ）を一覧表示する。
  このプラグインから作成・移動・削除することはできない（COM API上、読み取り専用のため）。

## 既知の制約

- **プラグインを実行する前に、UC-win/Road側でシミュレーションを手動で再生(Play)状態にしておく
  必要がある。** シミュレーションが停止(Reset/Pause)している状態のまま配置(Place Obstruction)を
  行うと、Pylon/停止車両が表示されない、またはUC-win/Road自体がクラッシュすることがある。これは
  `TrafficSimulation.AddNewTransient`/`AddNewVehicle`で生成したオブジェクトの初期化（シーングラフ
  への登録等）がシミュレーションのフレーム更新に紐づいているためと考えられる。プラグイン側から
  `ScriptStatus`を操作して自動的にシミュレーションを開始する対応も試したが改善しなかったため撤回した
  — 必ずユーザ側で事前に手動でPlayすること。
- 生成直後の同フレームで`Position`を設定するとクラッシュにつながることがあるため、生成と位置設定を
  分離し、メインループ側で数ティック（約100ms）待ってから設定している（`PLACEMENT_SETTLE_TICKS`）。
- 道路に元からある道路障害物（`F8RoadObstructionProxy`）はCOM API上完全に読み取り専用であり、
  スクリプトから作成・移動・削除する手段はない（全typelibを走査して確認済み）。UC-win/Roadの
  エディタ画面上で`Ctrl+Alt+Shift+Click`することで手動作成は可能だが、これはCOM API呼び出しではなく
  画面上のマウス操作であり、スクリプトから利用するには別途、画面座標への変換・OSレベルの
  マウス/キーボード入力シミュレーションが必要になるため、本プラグインでは対応していない。
- **他の車両がPylon/停止車両を避けて車線変更する挙動は実現していない。** UC-win/Roadの交通AIには
  車線変更・追い越しの判断機能がAPI経由では存在せず（全COM API走査で確認済み）、スクリプトから
  他車両のSteering/Throttle/Brakeを上書きしても実際の走行には反映されないことも診断ログで確認した
  （`AutomaticControl`が読み取り専用のため、AI側が毎ティック制御入力を再計算して上書きしてしまう）。
  Positionを直接動かして見た目上車線変更させる手法も試したが、AI側の内部状態と食い違ってうまく
  機能しなかったため、この機能は断念した。現状は「非表示の停止車両の手前で後続車両が停止・渋滞する」
  挙動のみを提供する。なお、道路データとして予め作り込まれた道路障害物（`F8RoadObstructionProxy`）
  については、AIが実際に車線変更して回避することを確認している — ただし上記の通りスクリプトからの
  動的な作成はできない。

## 開発メモ

- `.py`ファイルはUTF-8 BOM付き・CRLF改行で保存する必要がある（LFのみだとUC-win/Road側の読み込みで
  `IndentationError`になる）。
- オフラインでの構文チェックは `python -m py_compile RoadBlockagePlugin.py` のみ可能。実際の動作確認は
  UC-win/RoadのPythonプラグインメニューから実行してログ（`RoadBlockagePlugin.log`）を確認する。

詳細な設計方針・COM API調査結果は [CLAUDE.md](CLAUDE.md) を参照。
