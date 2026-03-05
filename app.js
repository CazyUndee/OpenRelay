(function () {
  const btn = document.getElementById('download-btn')
  if (!btn) return

  btn.addEventListener('click', () => {
    btn.textContent = 'Downloading...'
    setTimeout(() => {
      btn.textContent = 'Download Script'
    }, 1400)
  })
})()
