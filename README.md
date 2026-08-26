# RoadBlockagePlugin

UC-win/Roadで、予測CSV（緯度経度＋確率）に基づいて道路閉塞（Barricadeによる車線規制）を一括配置し、
その周辺の交通流（流量・平均速度）を計測してHUD表示するPythonプラグイン。

## 使い方

1. **このプラグインを実行する前に、UC-win/Road側でシミュレーションを手動で再生(Play)した状態に
   しておく。**（実行後にPlayしても正しく動作しない。後述の「既知の制約」参照）
2. [Script Editor]等からこのプラグインを読み込み、[Async]チェックをONにして実行する。
3. リボンに5つのタブ「Road Blockage: CSV」「Road Blockage: Barricades」「Road Blockage: Reset」
   「Road Blockage: Obstructions」「Road Blockage: Traffic Monitor」が追加される。

### Road Blockage: CSV タブ
- **Browse & Load CSV...** ボタン：ネイティブのファイル選択ダイアログを開き、`Prediction/`フォルダ
  内のCSVファイル（`latitude`,`longitude`,`probability`列、`side_m`列は任意）を読み込む。読み込んだ
  ファイル名・件数・確率の範囲をラベルに表示する。

### Road Blockage: Barricades タブ
- **Probability Threshold**（初期値 `0.7`）：この値以上の確率を持つCSVレコードだけを配置対象にする。
- **Max Distance to Road (m)**（初期値 `5000`）：CSVの緯度経度から最も近い道路までの距離がこの値を
  超える場合は「道路上にない」とみなして配置しない（予測CSVは格子状の点群のため、道路から大きく
  離れた点も多く含まれる）。
- **Place Barricades from CSV** ボタン：しきい値・距離条件を満たす各CSVレコードについて、最も近い
  道路・車線上の地点（中心）を求め、そこから道路に沿って**下流5m・上流5mの2箇所**にBarricadeを
  1本ずつ（1レコードあたり計2本）配置する。Barricadeは道路の進行方向に対して垂直（車線をふさぐ
  向き）になるよう自動で回転する。クリックのたびに、既存の配置をすべて削除してから作り直す。
- **Barricade Index** + **Jump to Barricade** ボタン：配置したBarricadeのうち指定した番号
  （0始まり、配置した順）の位置へメインカメラをジャンプさせ、見た目を確認できる。

### Road Blockage: Reset タブ
- **Reset** ボタン：配置したBarricadeをすべてシミュレーションから削除する。
- **Clear All** ボタン：プロジェクト内の全ての一時オブジェクト(`DeleteAllTransientObjects`)を無条件に
  削除する。過去の実行がクラッシュ等で正常終了せず、道路上に残ってしまったBarricadeを一掃するための
  強制リセット用。通常の走行中の交通車両にも影響しうる点に注意。

### Road Blockage: Obstructions タブ
- **List Obstructions** ボタン：道路データに元から登録されている道路障害物
  （`F8RoadObstructionProxy`、道路作成時に設定される読み取り専用のデータ）を一覧表示する。
  このプラグインから作成・移動・削除することはできない（COM API上、読み取り専用のため）。
- **Obstruction Index** + **Jump to Obstruction** ボタン：一覧の中から指定した番号の道路障害物の
  位置へメインカメラをジャンプさせる。

### Road Blockage: Traffic Monitor タブ
配置したBarricadeのうち1つを選び、その周辺の交通流（流量・平均速度）を計測してHUD表示する。
バリケード自体は見た目上のオブジェクトで交通AIには影響しないため、UC-win/Roadのエディタ上で
**同じ場所に手動で道路障害物を追加・削除**（`Ctrl+Alt+Shift+Click`など）しながら、その前後で
流量・速度がどう変化するかを確認する使い方を想定している。

- **Barricade Index**：計測対象とするBarricadeの番号（Barricadesタブでの配置順）。
- **Measurement Radius (m)**（初期値 `50`）：この半径内にいる車両を計測対象にする。
- **Flow Window (s)**（初期値 `60`）：流量（単位時間あたりの新規進入台数）を計算する際の集計時間。
- **Start Monitoring** / **Stop Monitoring** ボタン：計測を開始・停止する。開始時点でのBarricadeの
  位置を固定座標として記録するため、**開始後にBarricadeをReset等で削除しても、同じ場所での計測を
  継続できる**（Barricadeの有無による交通流の変化を比較しやすくするため）。
- 計測中は、画面に重なる形で常時最前面のHUDウィンドウ（黒背景・黄色文字）に「Flow（流量, veh/h）」
  「Avg Speed（平均速度, km/h）」「Vehicles（現在ゾーン内の車両数）」を表示する。このHUDは
  UC-win/Roadのウィンドウの右下に自動で追従する。同じ内容はリボンの詳細欄（テキストエリア風の
  入力欄）にも表示される。

## 既知の制約

- **プラグインを実行する前に、UC-win/Road側でシミュレーションを手動で再生(Play)状態にしておく
  必要がある。** シミュレーションが停止(Reset/Pause)している状態のまま配置(Place Barricades from
  CSV)を行うと、Barricadeが表示されない、またはUC-win/Road自体がクラッシュすることがある。これは
  `TrafficSimulation.AddNewTransient`で生成したオブジェクトの初期化（シーングラフへの登録等）が
  シミュレーションのフレーム更新に紐づいているためと考えられる。プラグイン側から`ScriptStatus`を
  操作して自動的にシミュレーションを開始する対応も試したが改善しなかったため撤回した — 必ず
  ユーザ側で事前に手動でPlayすること。
- 生成直後の同フレームで`Position`/`YawAngle`を設定するとクラッシュにつながることがあるため、
  生成と位置・向きの設定を分離し、メインループ側で数ティック（約100ms）待ってから設定している
  （`PLACEMENT_SETTLE_TICKS`）。
- 道路に元からある道路障害物（`F8RoadObstructionProxy`）はCOM API上完全に読み取り専用であり、
  スクリプトから作成・移動・削除する手段はない（全typelibを走査して確認済み）。UC-win/Roadの
  エディタ画面上で`Ctrl+Alt+Shift+Click`することで手動作成は可能だが、これはCOM API呼び出しではなく
  画面上のマウス操作であり、スクリプトから利用するには別途、画面座標への変換・OSレベルの
  マウス/キーボード入力シミュレーションが必要になるため、本プラグインでは対応していない。
- **他の車両がBarricadeを避けて停止・渋滞したり車線変更したりする挙動は実装していない。**
  Barricadeは見た目上の3Dモデル（`AddNewTransient`）に過ぎず、UC-win/Roadの交通AIはこれを障害物として
  認識しない。実際の車両（`AddNewVehicle`）を同じ場所に停止させて他車両に検知させる方式や、接近車両の
  Steering/Throttle/Brake・Position・Speed・Direction等を上書きして車線変更させる方式を試したが、
  いずれもクラッシュや不自然な挙動（AIが上書きを無視する、車線変更後に元へ戻ってしまう等）が発生し、
  安定して動作させることができなかったため、この機能は断念した。現状はBarricadeの見た目上の配置と、
  その周辺の交通流計測のみを提供する（他車両はBarricadeをすり抜けて走行する）。なお、道路データとして
  予め作り込まれた道路障害物（`F8RoadObstructionProxy`）については、AIが実際に車線変更して回避する
  ことを確認しているが、上記の通りスクリプトからの動的な作成はできない。Traffic Monitorタブは、
  この「本物の道路障害物」をUC-win/Roadのエディタ上で手動追加・削除した際の効果を計測するために
  用意した機能である。
- **リボンにはコンボボックス・リストボックス・複数行専用のテキストエリアといったコントロールは
  存在しない。** COM API（`IF8MainRibbonGroupProxy`）で確認できる作成可能なコントロールは
  ボタン・チェックボックス・編集欄・ラベル・パネルの5種類のみ。「どの配置物を対象にするか」の
  選択は、すべて0始まりのインデックスを編集欄に入力する方式で代替している。「テキストエリア」も
  高さを広げた編集欄（`Text`に改行を含める）で近似しているだけで、専用のマルチライン機能ではない。
- **HUD（Traffic Monitorタブの画面表示）は、独立したtkinterウィンドウとして実装している。**
  UC-win/Roadのメイン3Dビューへの直接描画（`MainOpenGLHandler.OnOpenGLAfterDrawScene`）や、
  Virtual Display経由の描画を試したが、いずれも実用にならなかった（前者はOpenGLの
  `GL_INVALID_OPERATION`エラーが解消できず、後者はVirtual DisplayをUC-win/Road側でどう作成するか
  不明だったため）。代わりに、UC-win/Roadのメインウィンドウを追従する枠なし・最前面のtkinter
  ウィンドウを別途生成している。tkinterはウィジェットを作成したスレッドで`mainloop()`を回し続ける
  必要があるため、専用スレッドを立ててそちらで生成・更新し、プラグイン本体とは`queue.Queue`経由で
  やり取りしている。

## 開発メモ

- `.py`ファイルはUTF-8 BOM付き・CRLF改行で保存する必要がある（LFのみだとUC-win/Road側の読み込みで
  `IndentationError`になる）。
- オフラインでの構文チェックは `python -m py_compile RoadBlockagePlugin.py` のみ可能。実際の動作確認は
  UC-win/RoadのPythonプラグインメニューから実行してログ（`RoadBlockagePlugin.log`）を確認する。
- `Prediction/`フォルダには、別プログラムで予測した道路閉塞地点のサンプルCSV（`latitude`,`longitude`,
  `side_m`,`probability`列）が入っている。CSVタブから読み込んで動作確認に使う。

詳細な設計方針・COM API調査結果は [CLAUDE.md](CLAUDE.md) を参照。
