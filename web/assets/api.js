/* Route browser API calls to Render when the static UI is hosted on Vercel. */
(function () {
  'use strict';
  var nativeFetch = window.fetch.bind(window);
  var NativeEventSource = window.EventSource;
  var renderOrigin = 'https://neos-arpj.onrender.com';

  function apiUrl(value) {
    var raw = typeof value === 'string' ? value : (value && value.url) || String(value);
    var url = new URL(raw, window.location.href);
    if (!url.pathname.startsWith('/api/')) return null;
    var configured = (localStorage.getItem('neosApiBase') || '').trim().replace(/\/+$/, '');
    var local = /^(localhost|127\.0\.0\.1)$/.test(window.location.hostname);
    var base = configured || (local ? window.location.origin : renderOrigin);
    return new URL(url.pathname + url.search, base).toString();
  }

  window.fetch = function (input, init) {
    var target = apiUrl(input);
    if (!target) return nativeFetch(input, init);
    if (input instanceof Request) {
      var request = new Request(target, input);
      return nativeFetch(request, init);
    }
    return nativeFetch(target, init);
  };

  if (NativeEventSource) {
    window.EventSource = function (url, options) {
      return new NativeEventSource(apiUrl(url) || url, options);
    };
    window.EventSource.prototype = NativeEventSource.prototype;
    Object.keys(NativeEventSource).forEach(function (key) {
      window.EventSource[key] = NativeEventSource[key];
    });
  }
}());