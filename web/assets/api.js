/* API origin bridge for the Render API + optional Vercel static frontend. */
(function () {
  'use strict';
  var nativeFetch = window.fetch.bind(window);
  var renderApi = 'https://neos-arpj.onrender.com';
  function sessionId() {
    var id = localStorage.getItem('neosParticipationSession');
    if (!id) {
      id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() :
        'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
          var r = Math.random() * 16 | 0;
          return (c === 'x' ? r : (r & 3 | 8)).toString(16);
        });
      localStorage.setItem('neosParticipationSession', id);
    }
    return id;
  }
  window.fetch = function (input, init) {
    var raw = typeof input === 'string' ? input : (input && input.url) || String(input);
    if (!raw || raw.indexOf('/api/') < 0) return nativeFetch(input, init);
    var url = new URL(raw, window.location.href);
    if (!url.pathname.startsWith('/api/')) return nativeFetch(input, init);
    var configured = (localStorage.getItem('neosApiBase') || '').trim().replace(/\/$/, '');
    var local = /^(localhost|127\.0\.0\.1)$/.test(location.hostname);
    var base = configured || (local ? '' : renderApi);
    if (base) url = new URL(url.pathname + url.search, base);
    if (url.pathname.startsWith('/api/participation')) {
      url.searchParams.set('session_id', sessionId());
    }
    return nativeFetch(url.toString(), init);
  };
}());
