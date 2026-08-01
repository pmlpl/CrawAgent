// remove_overlay.js
// 移除弹窗、遮罩层、Cookie 提示等遮挡正文的元素
// 参考 Crawl4AI js_snippet/remove_overlay.js

// 1. 移除常见的遮罩/弹窗元素
const overlaySelectors = [
    // 通用遮罩
    '[class*="overlay"]',
    '[class*="modal"]',
    '[class*="popup"]',
    '[id*="overlay"]',
    '[id*="modal"]',
    '[id*="popup"]',

    // Cookie 同意栏
    '[class*="cookie-banner"]',
    '[class*="cookie-consent"]',
    '[id*="cookie-banner"]',
    '[id*="cookie-consent"]',
    '#onetrust-banner-sdk',
    '.onetrust-pc-dark-filter',

    // 登录/注册弹窗
    '[class*="login-modal"]',
    '[class*="signup-modal"]',
    '[class*="auth-modal"]',

    // 广告
    '[class*="ad-banner"]',
    '[id*="ad-banner"]',
    '[class*="advertisement"]',
    'ins.adsbygoogle',
    '[id*="google_ads"]',

    // Newsletter 订阅
    '[class*="newsletter"]',
    '[id*="newsletter"]',

    // 固定定位的底部/顶部栏
    '[style*="position: fixed"]',

    // 懒加载占位
    '[class*="loading-placeholder"]',
    '[class*="skeleton"]',
];

overlaySelectors.forEach(function(selector) {
    try {
        var elements = document.querySelectorAll(selector);
        elements.forEach(function(el) {
            el.remove();
        });
    } catch (e) {
        // 忽略无效选择器
    }
});

// 2. 移除 body 的 overflow hidden/restrict
document.body.style.overflow = 'auto';
document.documentElement.style.overflow = 'auto';

// 3. 移除滚动锁定类
document.body.classList.remove('no-scroll', 'modal-open', 'overflow-hidden');
document.documentElement.classList.remove('no-scroll', 'modal-open', 'overflow-hidden');

// 4. 移除 noscript 标签内容（避免干扰内容提取）
document.querySelectorAll('noscript').forEach(function(el) {
    el.remove();
});

// 5. 移除 script 和 style 标签中的 JSON-LD 之外的内容（保留结构化数据）
document.querySelectorAll('script:not([type="application/ld+json"])').forEach(function(el) {
    el.remove();
});
