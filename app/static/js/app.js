(() => {
  const menu = document.getElementById('mobileMenu');
  const sidebar = document.getElementById('sidebar');
  if (menu && sidebar) {
    menu.addEventListener('click', () => sidebar.classList.toggle('open'));
    document.addEventListener('click', (event) => {
      if (window.innerWidth <= 900 && sidebar.classList.contains('open') && !sidebar.contains(event.target) && !menu.contains(event.target)) {
        sidebar.classList.remove('open');
      }
    });
  }

  document.querySelectorAll('.flash-close').forEach((button) => {
    button.addEventListener('click', () => button.closest('.flash').remove());
  });

  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const message = form.getAttribute('data-confirm') || 'Are you sure?';
      if (!window.confirm(message)) event.preventDefault();
    });
  });

  const fileInput = document.getElementById('documentFile');
  const selectedFile = document.getElementById('selectedFile');
  if (fileInput && selectedFile) {
    fileInput.addEventListener('change', () => {
      selectedFile.textContent = fileInput.files.length ? `Selected: ${fileInput.files[0].name}` : '';
    });
  }

  document.querySelectorAll('.upload-zone').forEach((zone) => {
    zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('drag-active'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-active'));
    zone.addEventListener('drop', (event) => {
      event.preventDefault(); zone.classList.remove('drag-active');
      const input = zone.querySelector('input[type="file"]');
      if (input && event.dataTransfer.files.length) {
        input.files = event.dataTransfer.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
  });
})();
