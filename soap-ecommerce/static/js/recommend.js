/* ─── AI Recommendation Engine — Frontend ─── */

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('recommend-form');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const skinType = document.getElementById('rec-skin').value;
    const scent = document.getElementById('rec-scent').value;

    if (!skinType && !scent) {
      showToast('Please select at least one preference');
      return;
    }

    const btn = form.querySelector('button[type="submit"]');
    const originalText = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span> Analyzing...';
    btn.disabled = true;

    try {
      const response = await fetch('/api/recommend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skin_type: skinType, scent: scent }),
      });

      const data = await response.json();
      renderRecommendations(data.recommendations || []);
    } catch (err) {
      showToast('Something went wrong. Please try again.');
    } finally {
      btn.textContent = originalText;
      btn.disabled = false;
    }
  });
});

function renderRecommendations(products) {
  const container = document.getElementById('rec-results');
  const emptyMsg = document.getElementById('rec-empty');

  if (!products.length) {
    container.classList.add('hidden');
    emptyMsg.classList.remove('hidden');
    return;
  }

  emptyMsg.classList.add('hidden');
  container.classList.remove('hidden');
  container.innerHTML = '';

  products.forEach((product, index) => {
    const card = document.createElement('a');
    card.href = `/product/${product.id}`;
    card.className = 'rec-card group block';
    card.style.animationDelay = `${index * 120}ms`;

    const stars = Array.from({ length: 5 }, (_, i) => {
      const filled = i < Math.floor(product.rating);
      return `<svg class="w-3.5 h-3.5 ${filled ? 'text-brown' : 'text-sand'}" fill="currentColor" viewBox="0 0 20 20">
        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/>
      </svg>`;
    }).join('');

    const skinTags = product.skin_types
      .map(
        (st) =>
          `<span class="text-[10px] uppercase tracking-wider bg-sand/50 text-brown px-2 py-0.5 rounded-full">${st}</span>`
      )
      .join('');

    card.innerHTML = `
      <div class="bg-beige rounded-2xl overflow-hidden transition-all duration-500 group-hover:shadow-xl group-hover:-translate-y-1">
        <div class="aspect-square flex items-center justify-center p-8 bg-sand/30">
          <img src="${product.image}" alt="${product.name}" class="w-3/4 h-3/4 object-contain transition-transform duration-500 group-hover:scale-105" />
        </div>
        <div class="p-6 text-center">
          <h3 class="font-display text-xl text-brown-dark mb-1">${product.name}</h3>
          <p class="text-sm text-charcoal/60 mb-2">${product.tagline}</p>
          <div class="flex items-center justify-center gap-1 mb-3">
            ${stars}
            <span class="text-xs text-charcoal/50 ml-1">${product.rating}</span>
          </div>
          <div class="flex items-center justify-center gap-1 mb-3">${skinTags}</div>
          <p class="font-display text-lg text-brown-dark">$${product.price.toFixed(2)}</p>
        </div>
      </div>
    `;

    container.appendChild(card);
  });

  container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
