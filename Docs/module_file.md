# 📁 module_file — `file/` 目錄模組文件

> 最後更新：2026-09-22 14:34  |  自動生成自 AST 掃描

涵蓋 `file/` 目錄：`AI_personality.py`（雪莉人格定義）

---


## 📄 `file/AI_personality.py`

> AI_personality.py


### 全域變數 / 模組常數

| 名稱 | 型別 | 值（摘要） | 行號 |
|---|---|---|---|
| `BOT_NAME` | `str` | `'橘雪莉(Tachibana Sherry)'` | L22 |
| `_DEFAULT_SOUL_FILE` | `—` | `Path(__file__).resolve().parent / 'soul' / 'sherry_default.md'` | L27 |
| `CORE_PERSONA` | `str` | `_load_default_persona_text()` | L44 |
| `LOREBOOK` | `dict[str, str]` | `{'(性暗示\|色色\|瑟瑟\|開車\|撩\|黃腔\|調情\|曖昧\|色\|做愛\|摸\|胸部?\|乳\|奶\|臀\|屁股\|下面\|裸\|脫\|舔\|插入?\|射精\|弄壞\|發情\|想要妳?\|肉體\|性感\|...` | L49 |


### Functions

#### `_load_default_persona_text`  (L30)

```python
def _load_default_persona_text() -> str
```

> 載入預設雪莉靈魂人設 Markdown 檔案內容。


---
