# RoadBlockagePlugin.py ユーザマニュアル

## プログラムの概要

`RoadBlockagePlugin.py` は、UC-win/Roadで動作する Python プラグインです。地震などによる道路閉塞の発生確率を予測した CSVファイル（緯度・経度・確率のグリッド）を読み込み、確率がしきい値を超える地点の最寄り車線に、視覚的な目印として **'Barricade'（バリケード）** の 3D モデルを自動配置します。

UC-win/Road SDK は UC-win/Road に実際の道路障害物を新規作成する手段を持たないため、バリケードはあくまで「ここに障害物を置くとよい」という目印です。実際の道路障害物は、この目印を参考にユーザーが UC-win/Road ので手動で設置します。

さらに、任意の 1 地点を選んで周辺の交通流（平均速度・車両台数）をリアルタイムに計測・グラフ表示する **Traffic Monitor（交通流モニター）** 機能を備えています。このモニターを見ることで、車両（周辺交通）が実際の道路障害物にどう反応して速度・台数が変化するかを確認できます。

## プログラムの実行方法

1. UC-win/Road でプロジェクトを開く。
2. シミュレーションを再生（Play）状態にする（シミュレーションが再生中でないと正しくバリケードが配置されない）．
3. UC-win/Road でプラグイン `RoadBlockagePlugin.py` を読み込む（このとき非同期のオプションを有効にする）．
4. 実行するとUC-win/Road のリボンに次の 5 つのタブが追加される。
   - **CSV**：CSVファイルの読み込み
   - **Barricades**：バリケードの設置
   - **Traffic Monitor**：交通流モニターの表示
   - **Find Objects**：オブジェクトへのジャンプ
   - **Reset**：リセット

[![Image from Gyazo](https://i.gyazo.com/254dcd30d9ff39a068fb41f9d4dd907c.png)](https://gyazo.com/254dcd30d9ff39a068fb41f9d4dd907c)

## プログラムの操作フロー

### RoadBlockDetectionApp.pyで生成されたCSVの読み込み

1. **CSV** タブの **「Browse & Load CSV...」** ボタンをクリックする。
2. CSVファイル（`Prediction/` フォルダに出力例あり）を選択する。
3. 読み込みに成功するとCSVファイルの概要がラベルに表示される。

[![Image from Gyazo](https://i.gyazo.com/30ac3045c46ad5ed1b0e8655a5cc21c6.png)](https://gyazo.com/30ac3045c46ad5ed1b0e8655a5cc21c6)

### バリケードの配置

1. **Barricades** タブで以下を設定する。
   - **Probability Threshold**：この値以上の確率を持つCSVレコードのみを配置対象にする。
   - **Max Distance to Road (m)**：最寄り道路までの距離がこの値を超えるレコードは「道路上ではない」とみなしてスキップする。
2. **「Place Barricades from CSV」** ボタンをクリックする。緯度経度に最も近い道路・車線を求め、その中心地点から道路に沿って **上流・下流に5mずつ** ずらした位置に、バリケードを **1レコードあたり2箇所** 配置する。
3. ユーザーはこの2箇所のバリケードの中間地点を目安に、手動で道路障害物を設置する。

[![Image from Gyazo](https://i.gyazo.com/7a54ea7898c9d3c81f49d62ea5b12b76.png)](https://gyazo.com/7a54ea7898c9d3c81f49d62ea5b12b76)

[![Image from Gyazo](https://i.gyazo.com/5c0f7ddbe634afda190817ce3ff678f5.png)](https://gyazo.com/5c0f7ddbe634afda190817ce3ff678f5)

### モニターの表示（平均速度の可視化）

1. **Traffic Monitor** タブで以下を設定する。
   - **CSV Record Index**：監視したいCSVレコードのインデックス。
   - **Measurement Radius (m)**：この半径内の車両を計測対象にする。
2. **「Start Monitoring」** をクリックする。
   - 指定したCSVレコードの緯度経度から最も近い道路上の位置を求め監視基準点とする．
3. HUDウィンドウおよびリボンの詳細欄には、0.5秒間隔で以下がリアルタイム表示される。
   - **Avg Speed (km/h)**：半径内にいる車両の平均速度と、その推移を示す折れ線グラフ。
   - **Vehicles in Zone**：半径内の現在の車両台数と、その推移を示す折れ線グラフ。

[![Image from Gyazo](https://i.gyazo.com/e3b54d9fb078359d21b89f917ff3d7ef.png)](https://gyazo.com/e3b54d9fb078359d21b89f917ff3d7ef)

[![Image from Gyazo](https://i.gyazo.com/d86251cc1ba212fc4d158623e8200e48.png)](https://gyazo.com/d86251cc1ba212fc4d158623e8200e48)
