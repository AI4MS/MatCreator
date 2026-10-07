document.querySelectorAll('.quiz').forEach(quiz => {
  quiz.querySelectorAll('button').forEach(button => {
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      quiz.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
      quiz.querySelector('.feedback').textContent = button.dataset.correct === 'true' ? quiz.dataset.success : quiz.dataset.retry;
    });
  });
});
