# 📁 module_file — `file/` 目錄模組文件

> 最後更新：2026-08-03 17:38  |  自動生成自 AST 掃描

涵蓋 `file/` 目錄：`AI_personality.py`（雪莉人格定義）

---


## 📄 `file/AI_personality.py`

> AI_personality.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `BOT_NAME` | `str` | `'橘雪莉(Tachibana Sherry)'` | L17 |
| `_DEFAULT_SOUL_FILE` | `—` | `Path(__file__).resolve().parent / 'soul' / 'sherry_default.md'` | L22 |
| `CORE_PERSONA` | `str` | `_load_default_persona_text()` | L36 |
| `LOREBOOK` | `dict[str, str]` | `{'(⚠️自我辨識\|圖片疑似為妳自己)': '【⚠️ 跨模組自我感知啟動！最高優先！】：\n系統偵測到這張圖片可能就是妳自己（橘雪莉）！\n請在描述圖片時，除了...` | L41 |


### Functions

#### `_load_default_persona_text`  (L25)

```python
def _load_default_persona_text() -> str
```


---
