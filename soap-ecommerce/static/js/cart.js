/* ─── Cart Page Logic ─── */

document.addEventListener('DOMContentLoaded', loadCart);

function loadCart() {
  fetch('/api/cart')
    .then((r) => r.json())
    .then((data) => renderCart(data.cart, data.total))
    .catch(() => showEmptyCart());
}

function renderCart(items, total) {
  const container = document.getElementById('cart-items');
  const emptyEl = document.getElementById('cart-empty');
  const summaryEl = document.getElementById('cart-summary');

  if (!items || items.length === 0) {
    showEmptyCart();
    return;
  }

  emptyEl.classList.add('hidden');
  summaryEl.classList.remove('hidden');
  container.innerHTML = '';

  items.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'cart-item';
    row.innerHTML = `
      <a href="/product/${item.id}" class="flex-shrink-0">
        <div class="w-20 h-20 bg-sand/30 rounded-xl flex items-center justify-center p-3">
          <img src="${item.image}" alt="${item.name}" class="w-full h-full object-contain" />
        </div>
      </a>
      <div class="flex-1 min-w-0">
        <a href="/product/${item.id}" class="font-display text-lg text-brown-dark hover:text-brown transition-colors">${item.name}</a>
        <p class="text-sm text-charcoal/50">$${item.price.toFixed(2)} each</p>
      </div>
      <div class="flex items-center gap-3">
        <div class="flex items-center border border-sand rounded-lg overflow-hidden">
          <button onclick="updateCartItem(${item.id}, ${item.qty - 1})" class="px-3 py-2 text-charcoal/60 hover:text-brown transition-colors text-sm">−</button>
          <span class="px-3 py-2 font-medium text-sm min-w-[2.5rem] text-center">${item.qty}</span>
          <button onclick="updateCartItem(${item.id}, ${item.qty + 1})" class="px-3 py-2 text-charcoal/60 hover:text-brown transition-colors text-sm">+</button>
        </div>
        <p class="font-display text-lg text-brown-dark min-w-[5rem] text-right">$${(item.price * item.qty).toFixed(2)}</p>
        <button onclick="removeCartItem(${item.id})" class="p-2 text-charcoal/40 hover:text-red-500 transition-colors" title="Remove">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>
    `;
    container.appendChild(row);
  });

  const shipping = total >= 40 ? 0 : 5.99;
  const grandTotal = total + shipping;

  document.getElementById('cart-subtotal').textContent = `$${total.toFixed(2)}`;
  document.getElementById('cart-total').textContent = `$${grandTotal.toFixed(2)}`;
}

function showEmptyCart() {
  document.getElementById('cart-empty').classList.remove('hidden');
  document.getElementById('cart-summary').classList.add('hidden');
  document.getElementById('cart-items').innerHTML = '';
}

function updateCartItem(productId, newQty) {
  if (newQty <= 0) {
    removeCartItem(productId);
    return;
  }

  fetch('/api/cart/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product_id: productId, qty: newQty }),
  })
    .then((r) => r.json())
    .then((data) => {
      renderCart(data.cart, data.total);
      const count = data.cart.reduce((sum, i) => sum + i.qty, 0);
      updateCartBadge(count);
    });
}

function removeCartItem(productId) {
  fetch('/api/cart/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product_id: productId }),
  })
    .then((r) => r.json())
    .then((data) => {
      renderCart(data.cart, data.total);
      const count = data.cart.reduce((sum, i) => sum + i.qty, 0);
      updateCartBadge(count);
      showToast('Item removed from cart');
    });
}
