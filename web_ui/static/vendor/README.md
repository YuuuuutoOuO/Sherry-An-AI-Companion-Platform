# Vendored 前端依賴

本目錄存放離線可用（self-hosted）之第三方前端程式庫，避免依賴外部 CDN，
並配合 CSP `script-src 'self'` 政策。

## purify.min.js（DOMPurify）

- 版本：3.1.6（UMD 發行檔 `dist/purify.min.js`）
- 來源：https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js
- 授權：Apache-2.0 OR MPL-2.0（檔案開頭保留原授權標記）
- 檔案大小：21,496 bytes（2026-10-19 匯入；SHA256 記錄於下方，僅供追蹤）
  - `C0845096A7C4A6741F362AC506C94C1C7D27DC603BCC1BF64A587F76F2DBE3A1`
- 用途（R5）：`web_ui/static/app.js` 之 `sanitizeHtml()` 統一淨化入口；
  動態 HTML 進 `innerHTML` 前之防禦層。降級行為：若本檔未載入，
  `sanitizeHtml()` 退回保守字串過濾（移除 `<script>` 與 `on*=` 事件屬性）。
- 升級程序：更新版本時同步更新上述來源／版本註記與
  `tests/unit/test_security_fixes.py::TestR5FrontendHardening` 之版本斷言。
