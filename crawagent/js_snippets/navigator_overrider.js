// navigator_overrider.js
// 覆盖 navigator 属性，避免 Playwright/Puppeteer 自动化检测
// 参考 Crawl4AI js_snippet/ 和 puppeteer-extra-stealth

// 1. 覆盖 webdriver 标志
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
});

// 2. 覆盖 languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
    configurable: true,
});

// 3. 覆盖 plugins（模拟真实浏览器有插件）
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        ];
        plugins.length = 5;
        return plugins;
    },
    configurable: true,
});

// 4. 覆盖 platform（避免 "Win32" 被指纹识别）
Object.defineProperty(navigator, 'platform', {
    get: () => 'Win32',
    configurable: true,
});

// 5. 覆盖 hardwareConcurrency
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8,
    configurable: true,
});

// 6. 覆盖 deviceMemory
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8,
    configurable: true,
});

// 7. 覆盖 maxTouchPoints（桌面浏览器）
Object.defineProperty(navigator, 'maxTouchPoints', {
    get: () => 0,
    configurable: true,
});

// 8. 移除 CDP (Chrome DevTools Protocol) 痕迹
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

// 9. 覆盖 permissions API
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);

// 10. 覆盖 chrome 对象
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {},
};

// 11. 覆盖 WebGL 渲染器信息
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) {
        return 'Intel Inc.';
    }
    if (parameter === 37446) {
        return 'Intel Iris OpenGL Engine';
    }
    return getParameter.apply(this, arguments);
};
