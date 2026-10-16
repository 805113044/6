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

document.querySelectorAll('.schedule-filters').forEach(filters => {
  if (window.matchMedia('(max-width: 800px)').matches) filters.open = false;
});

// Keep the selected day visible inside the shared, horizontally scrollable grid.
document.querySelectorAll('.schedule-scroll').forEach(viewport => {
  const selected = viewport.querySelector('thead .selected-date');
  const roomHeader = viewport.querySelector('thead th');
  if (!selected || !roomHeader) return;
  const left = selected.getBoundingClientRect().left - viewport.getBoundingClientRect().left;
  viewport.scrollLeft += Math.max(0, left - roomHeader.offsetWidth - 1);
});

const furnitureForm = document.querySelector('[data-furniture-form]');
if (furnitureForm) {
  const rows = furnitureForm.querySelector('[data-loan-items]');
  const template = document.getElementById('loan-item-template');
  furnitureForm.querySelector('[data-add-loan-item]').addEventListener('click', () => {
    if (rows.children.length >= 20) return;
    rows.append(template.content.cloneNode(true));
    rows.lastElementChild.querySelector('input').focus();
  });
  rows.addEventListener('click', event => {
    const button = event.target.closest('[data-remove-loan-item]');
    if (!button) return;
    button.closest('.furniture-item-row').remove();
    if (!rows.children.length) rows.append(template.content.cloneNode(true));
  });
}
