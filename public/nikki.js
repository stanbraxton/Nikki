// Nikki UI extras: "Create account" link on the login page; Admin/Spaces links for platform admins.
(function () {
  function onLogin() {
    if (!location.pathname.startsWith('/login') || document.getElementById('nikki-signup')) return;
    var form = document.querySelector('form');
    if (!form) return;
    var p = document.createElement('p');
    p.id = 'nikki-signup';
    p.style.cssText = 'text-align:center;margin-top:14px;font-size:13px;opacity:.8';
    p.innerHTML = 'New to Nikki? <a href="/signup" style="color:#c9bdff">Create an account</a>';
    form.parentNode.appendChild(p);
  }
  var adminChecked = false;
  function adminLinks() {
    if (adminChecked || location.pathname.startsWith('/login')) return;
    adminChecked = true;
    fetch('/api/me').then(function (r) { return r.ok ? r.json() : null; }).then(function (me) {
      if (!me || me.role !== 'admin') return;
      var bar = document.createElement('div');
      bar.id = 'nikki-admin-links';
      bar.style.cssText = 'position:fixed;bottom:12px;right:14px;z-index:50;display:flex;gap:8px;font:12px system-ui;opacity:.75';
      bar.innerHTML = '<a href="/admin" style="color:#c9bdff;text-decoration:none;border:1px solid #333;padding:3px 9px;border-radius:999px;background:#171922">Admin</a>' +
                      '<a href="/spaces" style="color:#c9bdff;text-decoration:none;border:1px solid #333;padding:3px 9px;border-radius:999px;background:#171922">Spaces</a>';
      document.body.appendChild(bar);
    }).catch(function () {});
  }
  new MutationObserver(function () { onLogin(); adminLinks(); }).observe(document.documentElement, { childList: true, subtree: true });
  onLogin(); adminLinks();
})();
