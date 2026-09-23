// Injected into the real interface. Intercepts the request the page would send
// for a given filter and parameters, without going near the network, so the
// comparison is between what each front end BUILDS -- which is the only place
// the two can differ, since they share the engine behind them.
(function () {
  const captured = [];
  const realFetch = window.fetch;
  window.fetch = function (url, opts) {
    try {
      if (String(url).includes('/api/mutate') && opts && opts.body instanceof FormData) {
        captured.push({
          mutation: opts.body.get('mutation'),
          parameters: opts.body.get('parameters')
        });
      }
    } catch (e) { /* capture must never break the page */ }
    return realFetch.apply(this, arguments);
  };
  const realXHR = window.XMLHttpRequest.prototype.send;
  window.XMLHttpRequest.prototype.send = function (body) {
    try {
      if (body instanceof FormData && body.get('mutation')) {
        captured.push({
          mutation: body.get('mutation'),
          parameters: body.get('parameters')
        });
      }
    } catch (e) { /* as above */ }
    return realXHR.apply(this, arguments);
  };
  window.__captured = captured;
})();
