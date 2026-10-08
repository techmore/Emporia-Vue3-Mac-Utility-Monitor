(() => {
  document.querySelectorAll('.aqara-label-form').forEach(form => {
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const button = form.querySelector('button');
      const status = form.querySelector('[role="status"]');
      button.disabled = true;
      status.textContent = 'Saving room label...';
      try {
        const response = await fetch('/api/aqara/local/label', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({device_id: form.dataset.deviceId, name: form.elements.name.value})
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Label could not be saved');
        window.location.reload();
      } catch (error) {
        status.textContent = error.message;
        button.disabled = false;
      }
    });
  });
})();
