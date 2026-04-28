/* ─── Luxe Lather — Main JavaScript ─── */

document.addEventListener('DOMContentLoaded', () => {
  initScrollAnimations();
  initNavbar();
  initMobileMenu();
  refreshCartBadge();
});

/* ═══════ Scroll-triggered fade-up animations ═══════ */
function initScrollAnimations() {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
        }
      });
    },
    { threshold: 0.1, rootMargin: '0px 0px -40px 0px' }
  );

  document.querySelectorAll('.fade-up').forEach((el) => observer.observe(el));
}

/* ═══════ Navbar shadow on scroll ═══════ */
function initNavbar() {
  const navbar = document.getElementById('navbar');
  if (!navbar) return;

  window.addEventListener('scroll', () => {
    if (window.scrollY > 20) {
      navbar.classList.add('nav-scrolled');
    } else {
      navbar.classList.remove('nav-scrolled');
    }
  });
}

/* ═══════ Mobile menu toggle ═══════ */
function initMobileMenu() {
  const btn = document.getElementById('mobile-menu-btn');
  const menu = document.getElementById('mobile-menu');
  if (!btn || !menu) return;

  btn.addEventListener('click', () => {
    menu.classList.toggle('hidden');
  });
}

/* ═══════ Cart badge ═══════ */
function refreshCartBadge() {
  fetch('/api/cart')
    .then((r) => r.json())
    .then((data) => {
      const count = data.cart.reduce((sum, item) => sum + item.qty, 0);
      updateCartBadge(count);
    })
    .catch(() => {});
}

function updateCartBadge(count) {
  const badge = document.getElementById('cart-count');
  const badgeMobile = document.getElementById('cart-count-mobile');

  if (badge) {
    badge.textContent = count;
    badge.style.opacity = count > 0 ? '1' : '0';
  }
  if (badgeMobile) {
    badgeMobile.textContent = `(${count})`;
  }
}

/* ═══════ Toast notification ═══════ */
function showToast(message) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  document.body.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.add('show');
  });

  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 400);
  }, 2500);
}
