# PNGTuber Remix 模型轉換與 Web UI 移植工具 (PNGRemix Tool)

此模組提供將 **PNGTuber Remix (`.pngRemix`)** 桌寵/虛擬主播模型資產一鍵解包、標準化並移植至 **Sherry Web UI 2.5D 看板娘** 的全自動工具。

---

## 🌟 核心功能

1. **自動過濾 3D 法線貼圖 (Normal Maps)**：
   PNGTuber Remix 內嵌有大量藍紫色的法線貼圖，此工具自動分離並僅提取乾淨的漫反射彩色圖層（Diffuse PNG），徹底杜絕圖層著色錯誤。
2. **標準化圖層與部件命名**：
   自動將原作者 PSD/Godot 導出的中文/拼音編號名稱（如 `_0022_A瞳仁`、`_0035_鸽子`、`_0032_放大镜抬手`）映射為 Web UI 標準部件名稱（如 `eye_iris.png`、`bird.png`、`hand_magnifier.png`）。
3. **自動生成 `model.json` 元數據**：
   精準導出各部件的旋轉軸心點 (Pivots)、畫布解析度 (Canvas)、眼睛視線追焦參數 (Eye Tracking)、手勢與表情圖層列表。

---

## 🚀 快速使用方式

### 方式一：拖曳檔案 (最簡單)
直接將任何 `.pngRemix` 檔案拖曳至 `convert.bat` 即可！

### 方式二：命令列執行
```bash
python tools/pngremix_tool/convert.py "路徑\你的模型.pngRemix" --id my_new_model --name "自訂模型名稱"
```

輸出目錄預設為：
`web_ui/static/sherry_live2d/models/<model_id>/`

---

## 📁 輸出模組目錄結構

轉換完成後的模型模組結構如下：

```
web_ui/static/sherry_live2d/models/<model_id>/
├── model.json            # 模型配置（圖層順序、樞紐點、眼動範圍等）
└── textures/             # 乾淨透明的 900x900 Diffuse 圖層
    ├── face_base.png     # 臉部基礎
    ├── eye_iris.png      # 瞳仁 (支援視線跟隨滑鼠)
    ├── eye_pupil.png     # 瞳孔
    ├── eye_contour.png   # 眼眶
    ├── eye_highlight.png # 眼神高光
    ├── eye_closed.png    # 眨眼閉眼
    ├── mouth_closed.png  # 平時閉嘴
    ├── mouth_open.png    # 說話對嘴張嘴
    ├── hair_front.png    # 前髮
    ├── hair_bangs.png    # 瀏海
    ├── hair_ahoge.png    # 呆毛 (彈簧物理)
    ├── hair_back.png     # 後髮
    ├── braid_left.png    # 左麻花辮 (彈簧物理)
    ├── braid_right.png   # 右麻花辮 (彈簧物理)
    ├── body_base.png     # 身體
    ├── necktie.png       # 領結
    ├── hat.png           # 帽子
    ├── bird.png          # 帽子上的小鴿子
    ├── hand_left_down.png
    ├── hand_right_down.png
    ├── hand_magnifier.png # 放大鏡手勢 (切換到日誌/排查時動態喚出)
    ├── hand_thinking.png  # 思考托下巴手勢
    └── ...
```

---

## 🔄 註冊新模型到 Web UI

在 `web_ui/static/sherry_live2d/models/models.json` 中加入新模型的條目即可在 Web UI 自由切換：

```json
{
  "default": "remix_chibi",
  "models": [
    {
      "id": "remix_chibi",
      "name": "橘雪莉桌寵版 (Chibi Remix)",
      "path": "/static/sherry_live2d/models/remix_chibi/model.json"
    }
  ]
}
```
