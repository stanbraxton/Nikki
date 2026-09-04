// Nikki UI extras: "Create account" link on the login page; Admin/Spaces links in the top header for platform admins.
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
  var me = null, meLoading = false;
  function linkEl(href, label) {
    var a = document.createElement('a');
    a.href = href; a.textContent = label; a.className = 'nikki-admin-link';
    a.style.cssText = 'color:#c9bdff;text-decoration:none;border:1px solid #333;padding:3px 10px;border-radius:999px;background:#171922;font:12px system-ui;line-height:18px;margin-right:4px;white-space:nowrap';
    return a;
  }
  function adminLinks() {
    if (location.pathname.startsWith('/login') || location.pathname.startsWith('/signup')) return;
    if (!me) {
      if (meLoading) return;
      meLoading = true;
      fetch('/api/me').then(function (r) { return r.ok ? r.json() : null; }).then(function (m) {
        meLoading = false; me = m || { role: 'none' }; adminLinks();
      }).catch(function () { meLoading = false; });
      return;
    }
    if (me.role !== 'admin' || document.getElementById('nikki-admin-links')) return;
    // Put the links in the Chainlit header (top-right, before the theme/settings buttons).
    var header = document.getElementById('header');
    var right = header ? header.querySelector(':scope > div.flex.items-center.gap-1') : null;
    var bar = document.createElement('div');
    bar.id = 'nikki-admin-links';
    if (right) {
      bar.style.cssText = 'display:flex;align-items:center;gap:4px;margin-right:6px';
      right.insertBefore(bar, right.firstChild);
    } else if (header) {
      bar.style.cssText = 'display:flex;align-items:center;gap:4px;margin-left:12px';
      header.appendChild(bar);
    } else {
      return; // header not rendered yet; observer will retry
    }
    bar.appendChild(linkEl('/admin', 'Admin'));
    bar.appendChild(linkEl('/spaces', 'Spaces'));
  }
  new MutationObserver(function () { onLogin(); adminLinks(); }).observe(document.documentElement, { childList: true, subtree: true });
  onLogin(); adminLinks();
})();
