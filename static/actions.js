// Declarative actions in place of inline on* handlers, which the Content-Security-Policy blocks.
//   <button data-action="saveConfig">                  -> saveConfig()
//   <button data-action="showPanel" data-arg="kasa">    -> showPanel('kasa', button)
//   <form data-no-submit>                               -> submit is prevented
// Only global functions declared by the page's own scripts can be called.
(function () {
  'use strict';
  document.addEventListener('click', function (event) {
    var element = event.target.closest('[data-action]');
    if (!element || element.disabled) return;
    var action = window[element.dataset.action];
    if (typeof action !== 'function') {
      console.warn('Unknown action:', element.dataset.action);
      return;
    }
    var result = 'arg' in element.dataset ? action(element.dataset.arg, element) : action();
    // Async actions report their own errors in the page; never leave a rejection unhandled.
    if (result && typeof result.catch === 'function') result.catch(function () {});
  });
  document.addEventListener('submit', function (event) {
    if (event.target.matches('form[data-no-submit]')) event.preventDefault();
  });
})();
