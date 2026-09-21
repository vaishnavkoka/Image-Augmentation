// Injected into the page by ui_behaviour.py. Writes results as JSON into a
// results element that the runner reads out of the dumped DOM.
(function () {
  var R = [];
  function check(name, ok, detail) { R.push({ name: name, ok: !!ok, detail: String(detail || '') }); }
  function finish() {
    var pre = document.createElement('pre');
    pre.id = 'uiresults';
    pre.textContent = JSON.stringify(R);
    document.body.appendChild(pre);
    document.title = 'done:' + R.length;
  }

  function fakeFiles(n) {
    // 1x1 PNG, enough for the uploader to accept and count
    var b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
    var bin = atob(b64), arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    var dt = new DataTransfer();
    for (var k = 0; k < n; k++) {
      dt.items.add(new File([arr], 'img' + k + '.png', { type: 'image/png' }));
    }
    return dt.files;
  }

  function uploadedCount() {
    var t = (document.getElementById('uploadInfo') || {}).textContent || '';
    var m = t.match(/(\d+)/);
    return m ? parseInt(m[1], 10) : -1;
  }

  function setMode(name) {
    var b = document.querySelector('.mode-seg button[data-mode="' + name + '"]');
    if (b) b.click();
  }

  // ---------- 1. theme toggle ----------
  function testTheme(next) {
    var t = document.getElementById('themeToggle');
    if (!t) { check('theme toggle exists', false, 'missing'); return next(); }
    var before = document.documentElement.getAttribute('data-theme');
    var bg = getComputedStyle(document.body).backgroundColor;
    t.click();
    // The toggle needs a moment to settle: at 150ms the attribute was still
    // unset and this read as a fault in the toggle rather than in the wait.
    setTimeout(function () {
      var after = document.documentElement.getAttribute('data-theme');
      var bg2 = getComputedStyle(document.body).backgroundColor;
      check('toggle changes data-theme', before !== after, before + ' -> ' + after);
      check('toggle repaints the background', bg !== bg2, bg + ' -> ' + bg2);
      check('aria-checked follows the theme',
        t.getAttribute('aria-checked') === String(after === 'light'),
        'aria-checked=' + t.getAttribute('aria-checked') + ' theme=' + after);
      var stored = null;
      try { stored = localStorage.getItem('imt-theme'); } catch (e) {}
      check('theme is remembered', stored !== null, 'stored=' + stored);
      t.click();   // back to where we started
      setTimeout(function () {
        // Going back does not restore "no attribute": once the user chooses,
        // the choice is stamped. What must come back is the appearance.
        check('toggling back restores the original appearance',
          getComputedStyle(document.body).backgroundColor === bg,
          'bg now ' + getComputedStyle(document.body).backgroundColor);
        next();
      }, 400);
    }, 400);
  }

  // ---------- 2. numeric entry ----------
  function testNumeric(next) {
    setMode('advanced');
    var r = document.getElementById('filterBlur');
    r.checked = true; r.dispatchEvent(new Event('change', { bubbles: true }));
    var slider = document.getElementById('blurSlider');
    var box = document.getElementById('blurNum');
    if (!box) { check('numeric box exists in Advanced', false, 'missing'); return next(); }
    check('numeric box enabled in Advanced', !box.disabled, 'disabled=' + box.disabled);

    function type(v) {
      box.value = v;
      box.dispatchEvent(new Event('change', { bubbles: true }));
    }
    type('7.5');
    check('a valid value reaches the slider', parseFloat(slider.value) === 7.5, 'slider=' + slider.value);

    var keep = slider.value;
    ['0x10', '1e1', '0b101', 'abc', '', '  '].forEach(function (bad) {
      type(bad);
      check('rejects "' + bad + '"', slider.value === keep, 'slider=' + slider.value);
    });

    type('999');
    check('rejects a value above max', slider.value === keep, 'slider=' + slider.value);
    type('-5');
    check('rejects a value below min', slider.value === keep, 'slider=' + slider.value);

    // whole-number filter should refuse a fraction
    var r2 = document.getElementById('filterBorder');
    r2.checked = true; r2.dispatchEvent(new Event('change', { bubbles: true }));
    var s2 = document.getElementById('borderSlider'), b2 = document.getElementById('borderNum');
    if (b2) {
      var k2 = s2.value;
      b2.value = '4.5'; b2.dispatchEvent(new Event('change', { bubbles: true }));
      check('integer filter refuses a fraction', s2.value === k2, 'slider=' + s2.value);
    }

    // Beginner promises no typed values
    setMode('beginner');
    setTimeout(function () {
      var anyEnabled = [].slice.call(document.querySelectorAll('.num-box'))
        .some(function (b) { return !b.disabled && !b.hidden; });
      check('Beginner exposes no numeric boxes', !anyEnabled, 'anyEnabled=' + anyEnabled);
      setMode('advanced');
      setTimeout(next, 150);
    }, 250);
  }

  // ---------- 3. upload caps ----------
  function testCaps(next) {
    var input = document.getElementById('imageInput');
    if (!input) { check('file input exists', false, 'missing'); return next(); }

    function clear() { if (window.clearAllImages) window.clearAllImages(); }

    function load(n, cb) {
      try { input.files = fakeFiles(n); } catch (e) { check('can set files', false, e.message); return cb(); }
      input.dispatchEvent(new Event('change', { bubbles: true }));
      setTimeout(cb, 800);
    }

    // Uploads accumulate, so each mode starts from a cleared gallery.
    function phase(mode, offer, expect, cb) {
      setMode(mode);
      setTimeout(function () {
        clear();
        setTimeout(function () {
          load(offer, function () {
            var c = uploadedCount();
            check(mode + ' takes ' + expect + ' of ' + offer + ' images',
                  c === expect, 'loaded ' + c);
            cb();
          });
        }, 300);
      }, 300);
    }

    phase('beginner', 45, 40, function () {
      phase('intermediate', 45, 40, function () {
        phase('advanced', 60, 60, function () {
          // and the cap is a ceiling, not a filter: clearing lets more in
          clear();
          setTimeout(function () {
            // uploadInfo keeps its old text but is display:none when inactive,
            // so read what is on screen rather than what is in the node.
            var info = document.getElementById('uploadInfo');
            var shown = info && getComputedStyle(info).display !== 'none';
            var tiles = document.querySelectorAll('#imageGallery img, #originalsGallery img').length;
            check('clearing hides the count and empties the gallery',
                  !shown && tiles === 0, 'visible=' + shown + ' tiles=' + tiles);
            next();
          }, 400);
        });
      });
    });
  }

  window.addEventListener('load', function () {
    setTimeout(function () {
      testTheme(function () {
        testNumeric(function () {
          testCaps(finish);
        });
      });
    }, 700);
  });
})();
