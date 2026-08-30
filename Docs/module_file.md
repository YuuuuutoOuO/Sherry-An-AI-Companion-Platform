# 📁 module_file — `file/` 目錄模組文件

> 最後更新：2026-08-30 18:04  |  自動生成自 AST 掃描

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
| `LOREBOOK` | `dict[str, str]` | `{'(橘雪莉\|tachibana.?sher\|sherry).{0,10}(的圖\|照片\|圖片\|看\|搜尋\|找\|搜圖\|本人\|自己)': '【⚠️ 自我角色感知啟動】...` | L41 |


### Functions

#### `_load_default_persona_text`  (L25)

```python
def _load_default_persona_text() -> str
```


---
