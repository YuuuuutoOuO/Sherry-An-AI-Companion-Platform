# 沙盒 NPC SOUL 目錄

這個目錄只給「沙盒群像（SANDBOX_GROUP）」的多角色使用，正式群永遠不會讀取這裡的檔案。

## 規則

1. 檔名只接受英數、底線、減號，副檔名為 `.md` 或 `.txt`。
2. 不可包含 `/` 或 `\`，`PersonaRoster` 會阻擋路徑穿越。
3. 每個 NPC 一個檔案，內容建議包含：個性、說話風格、口頭禪、禁忌。
4. 停用 NPC 不會刪除 Webhook，歷史訊息 attribution 會保留。

## 範例

見 `example_groupmate.md`。在 WebUI 的「沙盒群像 ＆ 決策帳本」分頁新增 NPC 時，
下拉選單會自動列出此目錄下可用的 SOUL 檔。
