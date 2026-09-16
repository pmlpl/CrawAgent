/*
 * SSL Pinning Bypass — frida 脚本
 * 覆盖常见证书绑定实现：OkHttp3 / TrustManager / Conscrypt / Flutter / 通用 SSLContext / native SSL
 *
 * 用法：frida -D 设备ID -f 包名 --no-pause -l ssl_pinning_bypass.js
 * 绕过后可用抓包代理（如 anything-analyzer MCP）明文查看 HTTPS 请求。
 *
 * 每段用 console.log 打 [BYPASS] / [SKIP] 标记，便于上层工具统计命中点。
 */

Java.perform(function () {

  // ===== 1. 通用 SSLContext：替换默认 TrustManager 为空实现 =====
  // 注册一个不校验任何证书的 X509TrustManager，并把 SSLContext.init 里的
  // TrustManager[] 入参替换成它 —— 覆盖所有走标准 JSSE 的 App。
  var BypassTrustManager = null;
  try {
    var X509TrustManager = Java.use('javax.net.ssl.X509TrustManager');
    BypassTrustManager = Java.registerClass({
      name: 'org.crawagent.BypassTrustManager',
      implements: [X509TrustManager],
      methods: {
        checkClientTrusted: function (chain, authType) {
          console.log('[BYPASS] SSLContext.checkClientTrusted 放行');
        },
        checkServerTrusted: function (chain, authType) {
          console.log('[BYPASS] SSLContext.checkServerTrusted 放行');
        },
        getAcceptedIssuers: function () {
          // 返回空 Java 数组（不能直接返回 JS [] ）
          return Java.array('java.security.cert.X509Certificate', []);
        }
      }
    });
    console.log('[BYPASS] 已注册空 TrustManager: org.crawagent.BypassTrustManager');
  } catch (e) {
    console.log('[SKIP] 注册 BypassTrustManager 失败: ' + e);
  }

  // 用空 TrustManager 替换 SSLContext.init 的 TrustManager[] 入参
  try {
    var SSLContext = Java.use('javax.net.ssl.SSLContext');
    if (BypassTrustManager) {
      var tmInstance = BypassTrustManager.$new();
      var tmArray = Java.array('javax.net.ssl.TrustManager', [tmInstance]);
      SSLContext.init.overload(
        '[Ljavax.net.ssl.KeyManager;',
        '[Ljavax.net.ssl.TrustManager;',
        'java.security.SecureRandom'
      ).implementation = function (km, tm, sr) {
        console.log('[BYPASS] SSLContext.init 替换 TrustManager 为空实现');
        this.init(km, tmArray, sr);
      };
    }
  } catch (e) {
    console.log('[SKIP] SSLContext.init hook 失败: ' + e);
  }

  // ===== 2. OkHttp3 CertificatePinning =====
  // okhttp3.CertificatePinner.check 在证书不匹配时抛异常阻断请求，
  // hook 后直接 return 放行（覆盖 OkHttp 3.x / 4.x / 老可变参数重载）。
  try {
    var CertificatePinner = Java.use('okhttp3.CertificatePinner');
    // OkHttp 3.x：check(String, List)
    CertificatePinner.check.overload('java.lang.String', 'java.util.List').implementation = function (host, peerCertificates) {
      console.log('[BYPASS] OkHttp3 CertificatePinner.check(String,List) 放行: ' + host);
      return;
    };
  } catch (e) {
    console.log('[SKIP] OkHttp3 CertificatePinner.check(String,List) 未找到: ' + e);
  }
  // OkHttp 4.x Kotlin 瓦解后的方法名带 $okhttp 后缀
  try {
    var Pinner4 = Java.use('okhttp3.CertificatePinner');
    Pinner4['check$okhttp'].overload('java.lang.String', 'java.util.List').implementation = function (host, peerCertificates) {
      console.log('[BYPASS] OkHttp4 check$okhttp 放行: ' + host);
      return;
    };
  } catch (e) {
    console.log('[SKIP] OkHttp4 check$okhttp 未找到（可能不是 Kotlin 版本）');
  }
  // OkHttp 老版本可变参数重载：check(String, Certificate[])
  try {
    var PinnerOld = Java.use('okhttp3.CertificatePinner');
    PinnerOld.check.overload('java.lang.String', '[Ljava.security.cert.Certificate;').implementation = function () {
      console.log('[BYPASS] OkHttp3 CertificatePinner.check(String,Certificate[]) 放行');
      return;
    };
  } catch (e) {
    console.log('[SKIP] OkHttp3 CertificatePinner.check(String,Certificate[]) 未找到');
  }

  // ===== 3. Conscrypt（Android 8+ 默认 SSL 引擎）=====
  // com.android.org.conscrypt.TrustManagerImpl 做系统级证书链校验，
  // hook checkTrustedRecursive 让它返回空 List（表示校验通过）。
  try {
    var TrustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
    TrustManagerImpl.checkTrustedRecursive.implementation = function () {
      console.log('[BYPASS] Conscrypt TrustManagerImpl.checkTrustedRecursive 放行');
      return Java.use('java.util.ArrayList').$new();
    };
  } catch (e) {
    console.log('[SKIP] Conscrypt TrustManagerImpl 未找到: ' + e);
  }
  // Conscrypt 旧版 verifyChain：直接把入参链原样返回（绕过黑名单校验）
  try {
    var TMI = Java.use('com.android.org.conscrypt.TrustManagerImpl');
    TMI.verifyChain.implementation = function (untrustedChain) {
      console.log('[BYPASS] Conscrypt TrustManagerImpl.verifyChain 放行');
      return untrustedChain;
    };
  } catch (e) {
    console.log('[SKIP] Conscrypt verifyChain 未找到');
  }

  // ===== 4. Flutter ssl_verify（native 层 BoringSSL）=====
  // Flutter 用静态链接的 BoringSSL，ssl_verify 是证书校验入口；
  // hook 后返回 0（ssl_verify_ok），绕过 Flutter 自带的证书绑定。
  try {
    var ssl_verify = Module.findExportByName(null, 'ssl_verify');
    if (ssl_verify) {
      Interceptor.replace(ssl_verify, new NativeCallback(function (ssl, out_alert) {
        console.log('[BYPASS] Flutter ssl_verify 放行');
        return 0;
      }, 'int', ['pointer', 'pointer']));
      console.log('[BYPASS] 已 hook native ssl_verify');
    } else {
      console.log('[SKIP] native ssl_verify 符号未找到（可能未加载或 Flutter 版本不同）');
    }
  } catch (e) {
    console.log('[SKIP] Flutter ssl_verify hook 失败: ' + e);
  }

  // ===== 5. 通用 native SSL：SSL_get_verify_result =====
  // 覆盖所有用 OpenSSL / BoringSSL 的 native 代码（含 Flutter 之外的跨平台框架），
  // 让 SSL_get_verify_result 总是返回 0（X509_V_OK）。
  try {
    var ssl_get_verify_result = Module.findExportByName(null, 'SSL_get_verify_result');
    if (ssl_get_verify_result) {
      Interceptor.replace(ssl_get_verify_result, new NativeCallback(function (ssl) {
        console.log('[BYPASS] SSL_get_verify_result 放行');
        return 0;
      }, 'long', ['pointer']));
      console.log('[BYPASS] 已 hook SSL_get_verify_result');
    }
  } catch (e) {
    console.log('[SKIP] SSL_get_verify_result hook 失败: ' + e);
  }

  // ===== 6. HostnameVerifier 绕过 =====
  // 某些 App 自定义 HostnameVerifier 做域名校验，
  // hook HttpsURLConnection.setDefaultHostnameVerifier 让设置被忽略。
  try {
    var HttpsURLConnection = Java.use('javax.net.ssl.HttpsURLConnection');
    HttpsURLConnection.setDefaultHostnameVerifier.implementation = function (v) {
      console.log('[BYPASS] HttpsURLConnection.setDefaultHostnameVerifier 拦截（保留默认放行实现）');
      return;
    };
  } catch (e) {
    console.log('[SKIP] HttpsURLConnection.setDefaultHostnameVerifier 未找到: ' + e);
  }

});

console.log('[ssl_pinning_bypass.js] 脚本已注入，等待 App 网络请求触发各 bypass 点');
