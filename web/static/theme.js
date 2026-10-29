// Applies the light or dark theme before the page is drawn, so it never flashes the wrong colors.
// Loaded in <head>. The choice ("system", "light" or "dark") is also saved on the server with the
// other user settings; studio.js reconciles the two once the page has loaded.
(function () {
  var root = document.documentElement;
  var darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  var choice = 'system';
  try { choice = localStorage.getItem('voicecast-theme') || 'system'; } catch (e) { /* storage blocked */ }

  window.applyTheme = function (newChoice) {
    choice = ['light', 'dark'].indexOf(newChoice) >= 0 ? newChoice : 'system';
    var resolved = choice === 'system' ? (darkQuery && darkQuery.matches ? 'dark' : 'light') : choice;
    // Switch every color at once (no half-faded buttons), then allow transitions again.
    root.classList.add('theme-switching');
    root.setAttribute('data-theme-choice', choice);
    root.setAttribute('data-theme', resolved);
    requestAnimationFrame(function () { requestAnimationFrame(function () { root.classList.remove('theme-switching'); }); });
    try { localStorage.setItem('voicecast-theme', choice); } catch (e) { /* storage blocked */ }
    document.dispatchEvent(new CustomEvent('voicecast-theme', { detail: { choice: choice, theme: resolved } }));
  };

  if (darkQuery && darkQuery.addEventListener) {
    darkQuery.addEventListener('change', function () { if (choice === 'system') window.applyTheme('system'); });
  }
  window.applyTheme(choice);
})();
