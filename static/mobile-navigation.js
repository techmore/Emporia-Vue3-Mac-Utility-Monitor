/* Navigation only: gestures never submit device commands. */
(() => {
  const nav = document.querySelector('.mobile-sections');
  if (!nav) return;
  const links = [...nav.querySelectorAll('a')];
  const current = links.findIndex(link => link.pathname === location.pathname);
  if (current < 0) return;
  const mobile = matchMedia('(max-width: 760px)');
  const key = `energy-scroll:${location.pathname}`;
  let start = null;
  const excluded = 'a,button,input,select,textarea,canvas,dialog,[role="slider"],[contenteditable],nav';
  document.addEventListener('touchstart', event => {
    start = null;
    if (!mobile.matches || event.touches.length !== 1 || event.target.closest(excluded)) return;
    const touch = event.touches[0];
    // Leave screen-edge gestures to the browser's back/forward navigation.
    if (touch.clientX < 30 || touch.clientX > innerWidth - 30) return;
    start = { x:touch.clientX, y:touch.clientY, time:Date.now() };
  }, { passive:true });
  document.addEventListener('touchcancel', () => { start = null; }, { passive:true });
  document.addEventListener('touchend', event => {
    const origin = start;
    start = null;
    if (!origin || !mobile.matches || event.changedTouches.length !== 1) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - origin.x, dy = touch.clientY - origin.y;
    if (Math.abs(dx) < 90 || Math.abs(dx) < Math.abs(dy) * 2 || Date.now() - origin.time > 700) return;
    const next = links[current + (dx < 0 ? 1 : -1)];
    if (next) location.assign(next.href);
  }, { passive:true });
  addEventListener('pagehide', () => {
    try { sessionStorage.setItem(key, String(scrollY)); } catch (_) { /* Storage may be disabled. */ }
  });
  addEventListener('pageshow', event => {
    if (event.persisted || !mobile.matches) return;
    try {
      const y = Number(sessionStorage.getItem(key));
      if (Number.isFinite(y) && y > 0) requestAnimationFrame(() => scrollTo(0, y));
    } catch (_) { /* Navigation still works without storage. */ }
  });
})();
