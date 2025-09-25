# ✂️ CutOnly - 高機能な動画カット検出・解析ツール

**CutOnly**は、動画ファイルからシーンの切り替わり（カット）を自動検出し、インタラクティブに解析するためのWebアプリケーションです。

最先端のシーン検出ライブラリ`PySceneDetect`をバックエンドに採用し、Streamlit製の直感的なUIを通じて、誰でも簡単に高精度なカット検出を試せます。動画編集者、映像研究者、コンテンツクリエイターの作業効率を劇的に向上させるために開発されました。

---

## ✨ 主な特徴

- **高精度なカット検出**: `PySceneDetect`を利用した3種類の堅牢なアルゴリズム（Content, Adaptive, Threshold）を搭載。
- **インタラクティブなUI**: 検出されたカット区間がタイムラインに視覚化され、クリックするだけで該当シーンにジャンプできます。
- **柔軟な設定**: 検出アルゴリズムや最小カット長（ノイズ除去）を画面上で簡単に調整可能。
- **リアルタイム進捗**: 解析の進捗状況がプログレスバーで表示され、大規模なファイルでも安心。
- **詳細な結果表示**: 各カットの開始・終了フレーム、タイムコード、デュレーションを一覧で確認し、メモを追加できます。
- **データのエクスポート**: 解析結果は詳細なメタデータと共にJSON形式でダウンロードでき、他のツールやワークフローとの連携が容易です。

---

## 📸 アプリケーションのスクリーンショット

*ここにアプリケーションの動作を示すスクリーンショットやGIFを配置します。*

![CutOnly Screenshot](https://example.com/screenshot.png)

---

## 🛠️ 主要技術

- **バックエンド**: [PySceneDetect](https://pyscenedetect.readthedocs.io/), OpenCV
- **フロントエンド**: [Streamlit](https://streamlit.io/)
- **言語**: Python

---

## 動作環境

- Python 3.9 以上
- `pip` と `venv` が利用可能な環境

---

## 🚀 セットアップと実行方法

1.  **リポジトリのクローンと移動**
    ```bash
    git clone https://github.com/your-username/cutonly.git
    cd cutonly
    ```

2.  **Python仮想環境の作成と有効化**
    ```bash
    # Windows
    python -m venv .venv
    .venv\Scripts\activate
    
    # macOS / Linux
    python3 -m venv .venv
    source .venv/bin/activate
    ```

3.  **必要なライブラリのインストール**
    ```bash
    pip install -r requirements.txt
    ```

4.  **アプリケーションの起動**
    ```bash
    streamlit run cut_detector.py
    ```

5.  **ブラウザでアクセス**
    -   ターミナルに表示されるURL（通常は `http://localhost:8501`）をブラウザで開きます。
    -   画面の指示に従って動画ファイルをアップロードし、設定を選択して解析を開始してください。

---

## ⚙️ 検出アルゴリズムの詳細

CutOnlyでは、動画の特性に応じて最適な検出方法を選択できます。

-   **Content ( `ContentDetector` )**:
    -   **概要**: フレーム間の色合いやヒストグラムの差分を比較し、急激な変化を検出します。
    -   **ユースケース**: 最も標準的で汎用性が高いアルゴリズム。一般的なシーンチェンジ（ハードカット）の検出に最適です。
-   **Adaptive ( `AdaptiveDetector` )**:
    -   **概要**: フレームの平均輝度に基づいてカットを検出します。閾値を動的に調整するため、徐々に明るさが変わるシーンにも追従します。
    -   **ユースケース**: フェードイン・フェードアウトや、照明が大きく変化するシーンの検出に優れています。
-   **Threshold ( `ThresholdDetector` )**:
    -   **概要**: フレーム全体のピクセル値の変化が、固定の閾値を超えた場合にカットと判定するシンプルな方法です。
    -   **ユースケース**: フラッシュや非常に高速なカットなど、極端な変化を捉えたい場合に有効です。

---

## 📄 出力JSONのサンプル

解析結果は以下の形式でJSONファイルとして保存され、外部ツールでの利用や分析が可能です。

```json
{
  "input": "my_awesome_video.mp4",
  "method": "content",
  "min_len_frames": 15,
  "fps": 29.97,
  "total_frames": 15000,
  "duration_seconds": 500.50,
  "cuts": [
    {
      "index": 1,
      "start_frame": 0,
      "end_frame": 240,
      "duration_frames": 240,
      "start_time": 0.0,
      "end_time": 8.00,
      "duration_seconds": 8.00,
      "note": "オープニングシーン"
    },
    {
      "index": 2,
      "start_frame": 240,
      "end_frame": 510,
      "duration_frames": 270,
      "start_time": 8.00,
      "end_time": 17.01,
      "duration_seconds": 9.01
    }
  ]
}
```

---

## 🤝 コントリビューション

バグ報告、機能リクエスト、プルリクエストを歓迎します。IssueやPull Requestでお気軽にご連絡ください。

---

## 📜 ライセンス

このプロジェクトは **MIT License** の下で公開されています。詳細は`LICENSE`ファイルをご覧ください。