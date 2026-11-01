/**
 * static/sw_register.js
 * =====================
 * R5：Service Worker 註冊（由 index.html 之 inline <script> 外置化，
 * 使 CSP `script-src` 可朝移除 `'unsafe-inline'` 收斂）。
 */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(reg => console.log('[SW] Active with scope:', reg.scope))
      .catch(err => console.debug('[SW] Notice:', err));
  });
}
