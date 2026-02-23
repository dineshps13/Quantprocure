const state = {
  items: [],
  cart: {}
};

function money(value) {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

function renderCatalog() {
  const root = document.getElementById('catalog');
  root.innerHTML = '';

  state.items.forEach(item => {
    const card = document.createElement('article');
    card.className = 'card';
    card.innerHTML = `
      <h3>${item.name}</h3>
      <p><strong>Part:</strong> ${item.id}</p>
      <p><strong>UNSPSC:</strong> ${item.unspsc}</p>
      <p><strong>UOM:</strong> ${item.uom}</p>
      <p class="price">${money(item.price)}</p>
      <button data-id="${item.id}">Add to Cart</button>
    `;

    card.querySelector('button').addEventListener('click', () => {
      state.cart[item.id] = (state.cart[item.id] || 0) + 1;
      renderCart();
    });

    root.appendChild(card);
  });
}

function renderCart() {
  const cartRoot = document.getElementById('cart');
  const ids = Object.keys(state.cart);

  if (ids.length === 0) {
    cartRoot.innerHTML = '<p>Cart is empty.</p>';
    return;
  }

  const rows = ids.map(id => {
    const item = state.items.find(i => i.id === id);
    const qty = state.cart[id];
    const lineTotal = item.price * qty;
    return `<li>${item.name} (${id}) × ${qty} = ${money(lineTotal)}</li>`;
  }).join('');

  const total = ids.reduce((sum, id) => {
    const item = state.items.find(i => i.id === id);
    return sum + item.price * state.cart[id];
  }, 0);

  cartRoot.innerHTML = `<ul>${rows}</ul><p><strong>Total:</strong> ${money(total)}</p>`;
}

async function checkout() {
  const items = Object.entries(state.cart).map(([id, quantity]) => ({ id, quantity }));
  const returnUrl = document.getElementById('returnUrl').value;

  const response = await fetch('/api/checkout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items, returnUrl })
  });

  const payload = await response.json();
  document.getElementById('result').textContent = payload.punchoutOrderMessage || JSON.stringify(payload, null, 2);
}

async function init() {
  const response = await fetch('/api/items');
  state.items = await response.json();
  renderCatalog();
  renderCart();
  document.getElementById('checkoutBtn').addEventListener('click', checkout);
}

init();
