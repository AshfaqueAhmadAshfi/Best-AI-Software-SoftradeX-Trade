/* Shared About Me modal behavior for the login and both dashboards. */
(function () {
  function closeModal(modal) {
    if (modal) modal.classList.add('hidden');
  }

  function openModal() {
    var modal = document.getElementById('about-me-modal');
    if (modal) modal.classList.remove('hidden');
  }

  document.addEventListener('click', function (event) {
    var openButton = event.target.closest('[data-about-open]');
    if (openButton) {
      event.preventDefault();
      openModal();
      return;
    }
    var closeButton = event.target.closest('[data-about-close]');
    if (closeButton) {
      closeModal(document.getElementById('about-me-modal'));
      return;
    }
    var modal = document.getElementById('about-me-modal');
    if (modal && event.target === modal) closeModal(modal);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeModal(document.getElementById('about-me-modal'));
  });
})();
