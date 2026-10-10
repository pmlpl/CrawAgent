/*
 * PC SSL Bypass — Windows 桌面应用 frida 脚本
 *
 * 覆盖 Windows 常见 SSL 栈：
 *   1. OpenSSL / BoringSSL（内嵌 C/C++ 应用）
 *   2. SChannel（Windows 原生 WinHTTP/WinINet）
 *   3. Node.js TLS（Electron 应用，如 QQ音乐PC版）
 *   4. CryptoAPI 证书链校验（传统 Windows 应用）
 *
 * 用法：frida -n <process_name> --no-pause -l pc_ssl_bypass.js
 * 绕过后可用抓包代理（anything-analyzer MCP）明文查看 HTTPS 请求。
 *
 * 与 Android ssl_pinning_bypass.js 差异：
 *   - 无 Java.perform 外壳（PC 进程无 Java VM）
 *   - 无 OkHttp3/Conscrypt/TrustManagerImpl 段（Java 类不存在）
 *   - 新增 SChannel / Node.js TLS / CryptoAPI 段
 * 标记沿用 [BYPASS] / [SKIP]，上层工具统计逻辑不变。
 */

// ===== 1. OpenSSL / BoringSSL：SSL_get_verify_result 返回 0（X509_V_OK） =====
// 覆盖所有用 OpenSSL / BoringSSL 的 native 代码（含内嵌 OpenSSL 的 C/C++ 应用、
// 某些静态链接 BoringSSL 的跨平台框架）。SSL_get_verify_result 是证书校验结果入口。
try {
  var ssl_get_verify_result = Module.findExportByName(null, 'SSL_get_verify_result');
  if (ssl_get_verify_result) {
    Interceptor.replace(ssl_get_verify_result, new NativeCallback(function (ssl) {
      console.log('[BYPASS] OpenSSL SSL_get_verify_result 放行');
      return 0;
    }, 'long', ['pointer']));
    console.log('[BYPASS] 已 hook OpenSSL SSL_get_verify_result');
  } else {
    console.log('[SKIP] OpenSSL SSL_get_verify_result 符号未找到（可能未加载或用其他 SSL 栈）');
  }
} catch (e) {
  console.log('[SKIP] OpenSSL SSL_get_verify_result hook 失败: ' + e);
}

// SSL_verify_cert_chain（BoringSSL 新版证书链校验入口）也一并覆盖
try {
  var ssl_verify_cert_chain = Module.findExportByName(null, 'SSL_verify_cert_chain');
  if (ssl_verify_cert_chain) {
    Interceptor.replace(ssl_verify_cert_chain, new NativeCallback(function (ssl, cert_chain) {
      console.log('[BYPASS] BoringSSL SSL_verify_cert_chain 放行');
      return 1;  // 1 = 校验通过
    }, 'int', ['pointer', 'pointer']));
    console.log('[BYPASS] 已 hook BoringSSL SSL_verify_cert_chain');
  }
} catch (e) {
  console.log('[SKIP] BoringSSL SSL_verify_cert_chain hook 失败: ' + e);
}

// ===== 2. SChannel（Windows 原生 SSL） =====
// 使用 WinHTTP/WinINet 的 Windows 原生应用走 SChannel；
// SecPkgConnectionVerify / SslVerifyCertificate 是 SChannel 证书校验入口。
try {
  var secpkg_verify = Module.findExportByName('sspicli.dll', 'SecPkgConnectionVerify');
  if (!secpkg_verify) {
    secpkg_verify = Module.findExportByName('schannel.dll', 'SslVerifyCertificate');
  }
  if (secpkg_verify) {
    Interceptor.replace(secpkg_verify, new NativeCallback(function (a, b, c, d, e, f, g) {
      console.log('[BYPASS] SChannel SecPkgConnectionVerify/SslVerifyCertificate 放行');
      return 0;  // 0 = SEC_E_OK
    }, 'int', ['pointer', 'pointer', 'pointer', 'pointer', 'pointer', 'pointer', 'pointer']));
    console.log('[BYPASS] 已 hook SChannel 证书校验');
  } else {
    console.log('[SKIP] SChannel 证书校验符号未找到（sspicli.dll/schannel.dll 未加载或符号不同）');
  }
} catch (e) {
  console.log('[SKIP] SChannel hook 失败: ' + e);
}

// SChannel 的 CertVerifyCertificateChainPolicy 也兜一层（部分应用走它）
try {
  var cert_verify_chain = Module.findExportByName('crypt32.dll', 'CertVerifyCertificateChainPolicy');
  if (cert_verify_chain) {
    Interceptor.replace(cert_verify_chain, new NativeCallback(function (policy, chain, para, status) {
      console.log('[BYPASS] crypt32 CertVerifyCertificateChainPolicy 放行');
      // 写入 TRUE 到 status 指向的 PolicyStatus 结构（dwError=0 表示通过）
      if (status && !status.isNull()) {
        status.writeU32(0);  // dwError = 0
      }
      return 1;  // TRUE
    }, 'int', ['pointer', 'pointer', 'pointer', 'pointer']));
    console.log('[BYPASS] 已 hook crypt32 CertVerifyCertificateChainPolicy');
  }
} catch (e) {
  console.log('[SKIP] crypt32 CertVerifyCertificateChainPolicy hook 失败: ' + e);
}

// ===== 3. Node.js TLS（Electron 应用） =====
// Electron 应用（QQ音乐PC版等）用 Node.js 的 tls.checkServerIdentity 做证书校验；
// 通过 JS hook 拦截 require('tls').checkServerIdentity 让它返回 undefined（放行）。
// 注意：Node.js 环境 hook 用 process.binding / require 拦截，不是 Java.perform。
try {
  // Electron / Node.js 进程才有 process 对象
  if (typeof process !== 'undefined' && process.versions && process.versions.node) {
    // 拦截 require('tls') 调用，拿到 tls 模块后替换 checkServerIdentity
    var origRequire = require;
    var tlsModule = null;
    try {
      tlsModule = origRequire('tls');
    } catch (e) {
      console.log('[SKIP] Node.js tls 模块加载失败: ' + e);
    }
    if (tlsModule && tlsModule.checkServerIdentity) {
      var origCheck = tlsModule.checkServerIdentity;
      tlsModule.checkServerIdentity = function (host, cert) {
        console.log('[BYPASS] Node.js tls.checkServerIdentity 放行: ' + host);
        return undefined;  // undefined = 校验通过
      };
      console.log('[BYPASS] 已 hook Node.js tls.checkServerIdentity');
    } else {
      console.log('[SKIP] Node.js tls.checkServerIdentity 未找到');
    }
  } else {
    console.log('[SKIP] 非 Node.js/Electron 进程，跳过 Node.js TLS 段');
  }
} catch (e) {
  console.log('[SKIP] Node.js TLS hook 失败: ' + e);
}

// ===== 4. CryptoAPI 证书校验（传统 Windows 应用） =====
// 走 CryptoAPI 的传统应用用 CertVerifyCertificateChainPolicy（已在段 2 覆盖）；
// 这里补充 WinVerifyTrust（Authenticode 签名校验，部分应用用它校验证书信任链）。
try {
  var win_verify_trust = Module.findExportByName('wintrust.dll', 'WinVerifyTrust');
  if (win_verify_trust) {
    Interceptor.replace(win_verify_trust, new NativeCallback(function (hwnd, action, data) {
      console.log('[BYPASS] wintrust WinVerifyTrust 放行');
      return 0;  // 0 = ERROR_SUCCESS
    }, 'int', ['pointer', 'pointer', 'pointer']));
    console.log('[BYPASS] 已 hook wintrust WinVerifyTrust');
  }
} catch (e) {
  console.log('[SKIP] wintrust WinVerifyTrust hook 失败: ' + e);
}

console.log('[pc_ssl_bypass.js] 脚本已注入，等待目标进程网络请求触发各 bypass 点');
