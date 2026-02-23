const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 3000;

const ITEMS = [
  { id: 'QP-1001', name: 'Precision Sensor Module', uom: 'EA', price: 249.0, currency: 'USD', unspsc: '41111900' },
  { id: 'QP-1002', name: 'Industrial Relay Pack', uom: 'EA', price: 89.5, currency: 'USD', unspsc: '39121500' },
  { id: 'QP-1003', name: 'Calibration Service Kit', uom: 'KIT', price: 315.0, currency: 'USD', unspsc: '41116100' },
  { id: 'QP-1004', name: 'Control Valve Assembly', uom: 'EA', price: 512.75, currency: 'USD', unspsc: '40141600' },
  { id: 'QP-1005', name: 'Safety Inspection Bundle', uom: 'JOB', price: 780.0, currency: 'USD', unspsc: '46171600' }
];

function sendJson(res, statusCode, body) {
  res.writeHead(statusCode, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body, null, 2));
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', chunk => { data += chunk; });
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

function staticFile(filePath, contentType, res) {
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(data);
  });
}

function generatePunchoutOrderMessage(items, returnUrl) {
  const lineItems = items.map((item, idx) => `
      <ItemIn quantity="${item.quantity}" lineNumber="${idx + 1}">
        <ItemID>
          <SupplierPartID>${item.id}</SupplierPartID>
        </ItemID>
        <ItemDetail>
          <UnitPrice><Money currency="USD">${item.price.toFixed(2)}</Money></UnitPrice>
          <Description xml:lang="en-US">${item.name}</Description>
          <UnitOfMeasure>${item.uom}</UnitOfMeasure>
          <Classification domain="UNSPSC">${item.unspsc || ''}</Classification>
        </ItemDetail>
      </ItemIn>`).join('');

  return `<?xml version="1.0" encoding="UTF-8"?>
<cXML payloadID="qp-demo-${Date.now()}" timestamp="${new Date().toISOString()}">
  <Message>
    <PunchOutOrderMessage>
      <BuyerCookie>coupa-session-cookie</BuyerCookie>
      <PunchOutOrderMessageHeader operationAllowed="create" />${lineItems}
    </PunchOutOrderMessage>
  </Message>
</cXML>
<!-- Return this payload via HTTP POST to: ${returnUrl} -->`;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  if (req.method === 'GET' && url.pathname === '/health') {
    return sendJson(res, 200, { status: 'ok' });
  }

  if (req.method === 'GET' && url.pathname === '/api/items') {
    return sendJson(res, 200, ITEMS);
  }

  if (req.method === 'POST' && url.pathname === '/punchout/setup') {
    const xmlBody = await parseBody(req);
    const response = {
      message: 'PunchOutSetupRequest received',
      receivedBytes: xmlBody.length,
      startPageUrl: `http://localhost:${PORT}/?session=qp-demo-session`
    };
    return sendJson(res, 200, response);
  }

  if (req.method === 'POST' && url.pathname === '/api/checkout') {
    const raw = await parseBody(req);
    const payload = JSON.parse(raw || '{}');
    const returnUrl = payload.returnUrl || 'https://buyer.coupahost.com/punchout/return';
    const cartItems = (payload.items || []).map(cartLine => {
      const catalogItem = ITEMS.find(i => i.id === cartLine.id);
      return catalogItem ? { ...catalogItem, quantity: Number(cartLine.quantity || 1) } : null;
    }).filter(Boolean);

    const orderMessage = generatePunchoutOrderMessage(cartItems, returnUrl);
    return sendJson(res, 200, {
      status: 'ready_to_post_back_to_coupa',
      returnUrl,
      punchoutOrderMessage: orderMessage
    });
  }

  if (req.method === 'GET' && url.pathname === '/') {
    return staticFile(path.join(__dirname, 'public', 'index.html'), 'text/html', res);
  }

  if (req.method === 'GET' && url.pathname === '/app.js') {
    return staticFile(path.join(__dirname, 'public', 'app.js'), 'application/javascript', res);
  }

  if (req.method === 'GET' && url.pathname === '/styles.css') {
    return staticFile(path.join(__dirname, 'public', 'styles.css'), 'text/css', res);
  }

  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: 'Not found' }));
});

server.listen(PORT, () => {
  console.log(`Quant Procure PunchOut demo running on http://localhost:${PORT}`);
});
