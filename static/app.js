document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const code = document.getElementById(button.dataset.copy).textContent.trim();
    const status = document.getElementById('copy-status');
    try { await navigator.clipboard.writeText(code); status.textContent = ' 已复制，请保存到安全位置'; }
    catch { status.textContent = ' 请手动选中凭证并复制'; }
  });
});
