const toast = document.querySelector('#toast');
const micButton = document.querySelector('#micButton');
const micLabel = document.querySelector('#micLabel');
let toastTimer;

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
}

micButton.addEventListener('click', () => {
  micButton.classList.add('listening');
  micLabel.textContent = '我认真听着呢，请讲';
  showToast('唤醒词：你好 oii');
  setTimeout(() => {
    micButton.classList.remove('listening');
    micLabel.textContent = '你好 oii';
  }, 1800);
});

document.querySelectorAll('.example-card').forEach((card) => {
  card.addEventListener('click', () => {
    const command = card.dataset.command;
    showToast(`可以对 oii 说：“${command}”`);
  });
});
